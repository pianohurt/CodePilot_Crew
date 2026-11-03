"""会话存储的配对、隔离、事务和故障边界。"""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import Message, ModelResponse, TextBlock, ThinkingBlock, ToolResult, ToolUse
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.runner import Runner
from codepilot.session.history import validate_history
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionError, SessionStore
from codepilot.tools.builtin import builtin_registry


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SessionStore]:
    instance = SessionStore(tmp_path / '.codepilot', tmp_path, ('private-test-key',))
    try:
        yield instance
    finally:
        instance.close()


def answer(text: str = 'answer') -> ModelResponse:
    return ModelResponse(content=[TextBlock(text=text)], stop_reason='end_turn', usage={'input_tokens': 7, 'output_tokens': 2})


@pytest.mark.parametrize('messages', [
    [Message(role='user', content=[ToolResult(tool_use_id='a', content='orphan')])],
    [Message(role='assistant', content=[ToolUse(id='a', name='read_file', input={})])],
    [Message(role='assistant', content=[ToolUse(id='a', name='read_file', input={})]), Message(role='user', content=[ToolResult(tool_use_id='b', content='wrong')])],
    [Message(role='assistant', content=[ToolUse(id='a', name='read_file', input={}), ToolUse(id='a', name='read_file', input={})])],
])
def test_history_rejects_incomplete_or_mismatched_tools(messages: list[Message]) -> None:
    with pytest.raises(ValueError):
        validate_history(messages)


async def test_two_turns_preserve_observations_opaque_usage_and_raw(store: SessionStore, tmp_path: Path) -> None:
    (tmp_path / 'source.txt').write_text('actual evidence', encoding='utf-8')
    provider = FixedResponseProvider([
        ModelResponse(content=[ThinkingBlock(thinking='opaque-body', signature='opaque-signature'), ToolUse(id='read-1', name='read_file', input={'path': 'source.txt'})], stop_reason='tool_use'),
        answer('first'), answer('second'),
    ])
    manager = SessionManager(store)
    runner = Runner(provider, builtin_registry(), tmp_path, sessions=manager)
    session_id = store.create()['id']
    first = await runner.run('read source', session_id=session_id)
    second = await runner.run('recall evidence', session_id=session_id)
    assert first.status == second.status == 'success'
    assert any(isinstance(block, ToolResult) and 'actual evidence' in block.content for message in provider.requests[-1] for block in message.content)
    messages, _ = store.load(session_id)
    assert validate_history(messages) == {'read-1'}
    assert messages[1].content[0].thinking == 'opaque-body'
    summary = manager.resume(session_id)
    assert summary['run_count'] == 2 and summary['usage'] == {'input_tokens': 14, 'output_tokens': 4}
    raw = store.records(first.run_id)
    assert any(record['kind'] == 'tool.result' and 'actual evidence' in str(record['data']) for record in raw)
    # 回归未来工作快照变短的边界：不使用旧 messages 偏移推算原始增量。
    store.checkpoint(session_id, second.run_id, [], [])
    assert store.records(first.run_id) == raw


async def test_background_notes_are_explicit_session_scoped_and_persisted(store: SessionStore, tmp_path: Path) -> None:
    (tmp_path / 'CODEPILOT.md').write_text('PROJECT_CONTEXT', encoding='utf-8')
    provider = FixedResponseProvider([ModelResponse(content=[ToolUse(id='note-1', name='note_save', input={'key': 'fact', 'content': 'SESSION_FACT'})], stop_reason='tool_use'), answer(), answer(), answer()])
    manager = SessionManager(store)
    runner = Runner(provider, builtin_registry(), tmp_path, sessions=manager)
    one, two = store.create()['id'], store.create()['id']
    previews = []

    async def allow(call: ToolUse, preview: dict, context: object) -> bool:
        previews.append(preview)
        assert store.load(one)[1] == {}
        return True

    await runner.run('save note', session_id=one, approver=allow)
    await runner.run('recall', session_id=one)
    await runner.run('independent', session_id=two)
    assert previews == [{'session_id': one, 'key': 'fact', 'content': 'SESSION_FACT'}]
    assert 'PROJECT_CONTEXT' in str(provider.requests[-1])
    assert 'SESSION_FACT' in str(provider.requests[-2]) and 'SESSION_FACT' not in str(provider.requests[-1])
    store.close()
    recovered = SessionStore(tmp_path / '.codepilot', tmp_path)
    try:
        assert recovered.load(one)[1] == {'fact': 'SESSION_FACT'}
        assert recovered.load(two)[1] == {}
    finally:
        recovered.close()


@pytest.mark.parametrize('mode', ['deny', 'unavailable'])
async def test_note_denied_or_unavailable_does_not_save(store: SessionStore, tmp_path: Path, mode: str) -> None:
    manager = SessionManager(store)
    provider = FixedResponseProvider([ModelResponse(content=[ToolUse(id='note', name='note_save', input={'key': 'fact', 'content': 'no'})], stop_reason='tool_use'), answer()])
    runner = Runner(provider, builtin_registry(), tmp_path, sessions=manager, policy=PermissionPolicy(note_save='deny' if mode == 'deny' else 'ask'))
    session_id = store.create()['id']
    result = await runner.run('save', session_id=session_id)
    assert result.tool_calls[0].is_error and not store.load(session_id)[1]


@pytest.mark.parametrize('damage', ['history', 'notes', 'journal', 'pairing'])
async def test_corrupt_session_isolated_and_preserved(store: SessionStore, tmp_path: Path, damage: str) -> None:
    bad, good = store.create('bad')['id'], store.create('good')['id']
    result = await Runner(FixedResponseProvider([answer()]), builtin_registry(), tmp_path, sessions=SessionManager(store)).run('goal', session_id=bad)
    with store.transaction():
        if damage == 'history':
            store.db.execute("UPDATE sessions SET history='broken' WHERE id=?", (bad,))
        elif damage == 'notes':
            store.db.execute("UPDATE sessions SET notes='broken' WHERE id=?", (bad,))
        elif damage == 'journal':
            store.db.execute("UPDATE records SET data='broken' WHERE run_id=? AND seq=1", (result.run_id,))
        else:
            text = store.encode([Message(role='user', content=[ToolResult(tool_use_id='orphan', content='bad')]).model_dump()])
            store.db.execute('UPDATE sessions SET history=?,digest=? WHERE id=?', (text, store.digest(text), bad))
    with pytest.raises(SessionError, match='corrupt'):
        store.load(bad)
    assert {item['id']:item['status'] for item in store.list_sessions()} == {bad:'corrupt',good:'ready'}
    assert store.load(good) == ([], {})


def test_atomic_checkpoint_rolls_back_raw_and_snapshot_on_write_failure(store: SessionStore) -> None:
    manager = SessionManager(store)
    session_id, run_id = store.create()['id'], uuid4().hex
    manager.begin(session_id, run_id, 'goal')
    before = store.records(run_id)
    store.db.execute("CREATE TRIGGER fail_snapshot BEFORE UPDATE OF history ON sessions BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(SessionError, match='transaction'):
        store.checkpoint(session_id, run_id, [Message(role='user', content=[TextBlock(text='intent')])], [Message(role='user', content=[TextBlock(text='intent')])])
    assert store.load(session_id)[0] == [] and store.records(run_id) == before


def test_single_core_owner_and_recovery_do_not_replay_actions(store: SessionStore, tmp_path: Path) -> None:
    manager = SessionManager(store)
    session_id, run_id = store.create()['id'], uuid4().hex
    manager.begin(session_id, run_id, 'unfinished')
    store.journal(run_id, 'tool.intent', {'call': {'name': 'write_file', 'input': {'path':'never.txt','content':'no'}}})
    with pytest.raises(SessionError):
        SessionStore(store.directory, tmp_path)
    store.close()
    recovered = SessionStore(tmp_path / '.codepilot', tmp_path)
    try:
        summary = SessionManager(recovered).resume(session_id)
        assert summary['status'] == 'interrupted'
        assert '不自动重放' in str(recovered.load(session_id)[0])
        assert not (tmp_path / 'never.txt').exists()
        assert recovered.records(run_id)[-1]['kind'] == 'run.interrupted'
    finally:
        recovered.close()


def test_physical_database_corruption_is_not_silently_recreated(store: SessionStore, tmp_path: Path) -> None:
    store.close()
    path = tmp_path / '.codepilot' / 'sessions.sqlite3'
    path.write_bytes(b'broken-sqlite-database')
    with pytest.raises(SessionError):
        SessionStore(path.parent, tmp_path)
    assert path.read_bytes() == b'broken-sqlite-database'


async def test_configured_key_redacted_but_opaque_content_not_omitted(store: SessionStore, tmp_path: Path) -> None:
    manager = SessionManager(store)
    session_id = store.create('private-test-key')['id']
    provider = FixedResponseProvider([answer('private-test-key')])
    result = await Runner(provider, builtin_registry(), tmp_path, sessions=manager).run('private-test-key', session_id=session_id)
    assert 'private-test-key' not in str(store.summary(session_id, include_answer=True))
    assert 'private-test-key' not in str(store.records(result.run_id))
    assert '[REDACTED]' in str(store.load(session_id))


@pytest.mark.parametrize('content', [b'x' * (16*1024+1), b'\xff\xfe'])
async def test_invalid_project_background_prevents_model_run(store: SessionStore, tmp_path: Path, content: bytes) -> None:
    (tmp_path/'CODEPILOT.md').write_bytes(content)
    manager = SessionManager(store)
    session_id = store.create()['id']
    provider = FixedResponseProvider([answer()])
    result = await Runner(provider,builtin_registry(),tmp_path,sessions=manager).run('goal',session_id=session_id)
    assert result.status == 'failed' and result.stop_reason == 'session_error'
    assert not provider.requests and not manager._active
    assert manager.resume(session_id)['status'] == 'failed'


async def test_observability_failure_does_not_leave_session_running(store: SessionStore, tmp_path: Path) -> None:
    bad_directory = tmp_path/'not-directory'
    bad_directory.write_text('file')
    manager = SessionManager(store)
    session_id = store.create()['id']
    runner = Runner(FixedResponseProvider([answer()]),builtin_registry(),tmp_path,sessions=manager,record_dir=bad_directory)
    with pytest.raises(OSError):
        await runner.run('goal',session_id=session_id)
    assert not manager._active
    assert manager.resume(session_id)['status'] == 'failed'


def test_failed_final_commit_blocks_continuation_until_recovery(store: SessionStore) -> None:
    from codepilot.agent.types import RunResult
    manager = SessionManager(store)
    session_id = store.create()['id']
    turn = manager.begin(session_id,uuid4().hex,'goal')
    store.db.execute("CREATE TRIGGER fail_finish BEFORE UPDATE OF status ON runs BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(SessionError):
        manager.finish(turn,RunResult(run_id=turn.run_id,status='success',stop_reason='end_turn',steps=1),[])
    with pytest.raises(SessionError,match='persistence failed'):
        manager.begin(session_id,uuid4().hex,'retry')
    with pytest.raises(SessionError,match='persistence failed'):
        manager.resume(session_id)
