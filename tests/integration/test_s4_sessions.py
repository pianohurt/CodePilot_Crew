import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
import json
import sys

import pytest
from textual.widgets import Button, Input, Select, Static

from codepilot.agent.provider import FixedResponseProvider
from codepilot.agent.types import Message, ModelResponse, TextBlock, ToolDefinition, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings
from codepilot.core.runner import Runner
from codepilot.session.history import validate_history
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionStore
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.rpc import RemoteError, RpcClient
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp


async def eventually(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def terminal(client: RpcClient) -> dict:
    async with asyncio.timeout(5):
        while True:
            event = await client.next_event()
            if event.type == 'run.finished':
                return event.data['result']


class WaitingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> ModelResponse:
        self.started.set()
        await asyncio.Event().wait()


async def test_session_claim_precedes_ack_and_different_sessions_are_isolated(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / '.codepilot', tmp_path)
    manager, provider = SessionManager(store), WaitingProvider()
    service = CoreService(Runner(provider, builtin_registry(), tmp_path, sessions=manager))
    server = SocketServer(service.handle, port=0)
    await server.start()
    one, two = RpcClient(server.host, server.port), RpcClient(server.host, server.port)
    try:
        await one.connect()
        await two.connect()
        session = (await one.call('session.create', {}))['id']
        first = await one.call('agent.start', {'goal':'wait','session_id':session})
        for method, params in [('agent.start',{'goal':'other','session_id':session}),('agent.run',{'goal':'other','session_id':session}),('session.resume',{'session_id':session})]:
            with pytest.raises(RemoteError) as error:
                await two.call(method, params)
            assert error.value.code == -32022
        other = (await two.call('session.create', {}))['id']
        await two.call('agent.start', {'goal':'separate','session_id':other})
        await two.close()
        await one.call('agent.cancel', {'run_id':first['run_id']})
        assert (await terminal(one))['status'] == 'cancelled'
        await eventually(lambda: not manager._active)
        assert (await one.call('session.resume', {'session_id':session}))['status'] == 'cancelled'
        for session_id in (session, other):
            validate_history(store.load(session_id)[0])
            assert '不自动重放' in str(store.load(session_id)[0])
    finally:
        await one.close()
        await two.close()
        await server.close()
        store.close()


async def test_tui_continues_switches_and_restores_sessions_without_replaying(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / '.codepilot', tmp_path)
    manager = SessionManager(store)
    provider = FixedResponseProvider([ModelResponse(content=[TextBlock(text=text)], stop_reason='end_turn') for text in ('FIRST_CONTEXT','SECOND_CONTEXT','OTHER_CONTEXT','RESTORED_CONTEXT')])
    server = SocketServer(CoreService(Runner(provider, builtin_registry(), tmp_path, sessions=manager)).handle, port=0)
    await server.start()
    app = CodePilotApp(Settings(port=server.port))
    try:
        async with app.run_test(size=(110, 45)) as pilot:
            first_session = app.session_id
            for goal in ('first', 'follow up'):
                previous_run = app.active_run
                app.query_one('#goal', Input).value = goal
                if previous_run is None:
                    await pilot.click('#start')
                else:
                    # 连续追问走输入框 Enter；避免把 Button 的防连点动画当作第二次点击。
                    app.query_one('#goal', Input).focus()
                    await pilot.press('enter')
                await eventually(lambda: app.active_run != previous_run and app.active_run is not None and app.views[app.active_run].result is not None)
                await pilot.pause()
            assert 'FIRST_CONTEXT' in str(provider.requests[1])
            await pilot.click('#new-session')
            await pilot.pause()
            assert app.session_id != first_session
            app.query_one('#goal', Input).value = 'independent'
            app.query_one('#goal', Input).focus()
            await pilot.press('enter')
            await eventually(lambda: app.active_run is not None and app.views[app.active_run].result is not None)
            assert 'FIRST_CONTEXT' not in str(provider.requests[2])
            await pilot.pause()
            app.query_one('#sessions', Select).value = first_session
            await pilot.click('#resume-session')
            await pilot.pause()
            assert app.session_id == first_session
            assert str(app.query_one('#answer', Static).content) == 'SECOND_CONTEXT'
            assert len(provider.requests) == 3
            app.query_one('#goal', Input).value = 'continue restored'
            app.query_one('#goal', Input).focus()
            await pilot.press('enter')
            await eventually(lambda: app.active_run is not None and app.views[app.active_run].result is not None)
            assert 'SECOND_CONTEXT' in str(provider.requests[3])
            await pilot.press('f10')
            await eventually(lambda: not app.is_running)
    finally:
        await server.close()
        store.close()


async def test_partial_tool_batch_cancel_keeps_raw_effect_and_never_replays(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / '.codepilot', tmp_path)
    manager = SessionManager(store)
    provider = FixedResponseProvider([ModelResponse(content=[
        ToolUse(id='write-done',name='write_file',input={'path':'done.txt','content':'already committed'}),
        ToolUse(id='write-wait',name='write_file',input={'path':'waiting.txt','content':'not committed'}),
    ], stop_reason='tool_use')])
    runner = Runner(provider, builtin_registry(), tmp_path, sessions=manager)
    service = CoreService(runner)
    server = SocketServer(service.handle, port=0)
    await server.start()
    client = RpcClient(server.host, server.port)
    try:
        await client.connect()
        session = (await client.call('session.create', {}))['id']
        run = await client.call('agent.start', {'goal':'two writes','session_id':session})
        while True:
            event = await client.next_event()
            if event.type == 'approval.requested':
                await client.call('approval.respond',{'approval_id':event.data['approval_id'],'allow':True})
                break
        while True:
            event = await client.next_event()
            if event.type == 'approval.requested':
                break
        await client.call('agent.cancel',{'run_id':run['run_id']})
        assert (await terminal(client))['status'] == 'cancelled'
        messages, _ = store.load(session)
        assert not validate_history(messages)
        raw = store.records(run['run_id'])
        assert any(record['kind']=='tool.result' and record['data']['result']['tool_use_id']=='write-done' for record in raw)
        assert any(record['kind']=='tool.cancelled' for record in raw)
        assert (tmp_path/'done.txt').read_text() == 'already committed'
        assert not (tmp_path/'waiting.txt').exists()
        assert (await client.call('session.resume',{'session_id':session}))['status'] == 'cancelled'
        assert len(provider.requests) == 1
    finally:
        await client.close()
        await server.close()
        store.close()


async def test_cli_create_list_run_and_resume_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path/'.codepilot', tmp_path)
    manager = SessionManager(store)
    provider = FixedResponseProvider([ModelResponse(content=[TextBlock(text='CLI_SESSION_OK')],stop_reason='end_turn')])
    server = SocketServer(CoreService(Runner(provider,builtin_registry(),tmp_path,sessions=manager)).handle,port=0)
    await server.start()

    async def cli(*arguments: str) -> dict:
        process = await asyncio.create_subprocess_exec(sys.executable,'-m','codepilot.cli',*arguments,'--port',str(server.port),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(),10)
        assert process.returncode == 0, stderr.decode(errors='replace')
        return json.loads(stdout)

    try:
        session_id = (await cli('session-new','--title','CLI fixture'))['id']
        assert any(item['id']==session_id for item in (await cli('sessions'))['sessions'])
        result = await cli('run','goal','--session',session_id,'--json')
        assert result['answer']=='CLI_SESSION_OK'
        restored = await cli('resume',session_id)
        assert restored['last_result']['run_id']==result['run_id'] and restored['run_count']==1
    finally:
        await server.close()
        store.close()


@pytest.mark.parametrize('damage', ['missing','corrupt'])
async def test_initial_bad_session_can_be_replaced_without_model_call(tmp_path: Path, damage: str) -> None:
    store = SessionStore(tmp_path/'.codepilot',tmp_path)
    session_id = 'a'*32 if damage == 'missing' else store.create()['id']
    if damage == 'corrupt':
        with store.transaction():
            store.db.execute("UPDATE sessions SET history='broken' WHERE id=?",(session_id,))
    provider = FixedResponseProvider([])
    server = SocketServer(CoreService(Runner(provider,builtin_registry(),tmp_path,sessions=SessionManager(store))).handle,port=0)
    await server.start()
    app = CodePilotApp(Settings(port=server.port),session_id=session_id)
    try:
        async with app.run_test(size=(110,45)) as pilot:
            assert '会话恢复失败' in str(app.query_one('#status',Static).content)
            assert app.query_one('#start',Button).disabled
            await pilot.click('#new-session')
            await eventually(lambda: app.session_id != session_id and not app.query_one('#start',Button).disabled)
            assert not provider.requests
            await pilot.press('f10')
    finally:
        await server.close()
        store.close()
