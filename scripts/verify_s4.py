"""独立 Core 进程重启后恢复追问；真实模型和固定响应分开记录。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from textual.widgets import Button, Input, Static

from codepilot import get_version
from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock, ToolUse
from codepilot.core.app import CoreService
from codepilot.core.config import Settings, load_model_settings
from codepilot.core.permissions import PermissionPolicy
from codepilot.core.records import evidence_view
from codepilot.core.runner import Runner
from codepilot.session.history import validate_history
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionStore
from codepilot.tools.builtin import builtin_registry
from codepilot.transport.rpc import RpcClient
from codepilot.transport.server import SocketServer
from codepilot.tui.app import CodePilotApp


class CrashFixture(FixedResponseProvider):
    async def complete(self, messages: object, tools: object) -> ModelResponse:
        if not self._responses:
            await asyncio.Event().wait()
        return await super().complete(messages, tools)


async def serve(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient() as http:
        secrets = ()
        if args.provider == 'real':
            settings = load_model_settings(args.env_file)
            secrets = (settings.api_key.get_secret_value(),)
            http.timeout = httpx.Timeout(settings.model_timeout)
            provider = AnthropicProvider(http, base_url=settings.base_url, api_key=secrets[0], model=settings.model, max_tokens=settings.max_tokens)
        elif args.crash_fixture:
            provider = CrashFixture([ModelResponse(content=[ToolUse(id='write-once',name='write_file',input={'path':'effect.txt','content':'committed once'})],stop_reason='tool_use')])
        else:
            responses = [] if args.phase == 'recall' else [ModelResponse(content=[ToolUse(id='read-marker', name='read_file', input={'path':'sample.txt'})], stop_reason='tool_use')]
            responses.append(ModelResponse(content=[TextBlock(text='固定响应：' + args.marker)], stop_reason='end_turn'))
            provider = FixedResponseProvider(responses)
        store = SessionStore(args.workspace / '.codepilot', args.workspace, secrets)
        runner = Runner(provider, builtin_registry(), args.workspace, sessions=SessionManager(store), secrets=secrets, record_dir=args.record_dir,
                        policy=PermissionPolicy(write_file='allow') if args.crash_fixture else PermissionPolicy())
        server = SocketServer(CoreService(runner).handle, port=0)
        try:
            await server.start()
            args.ready.write_text(json.dumps({'port':server.port,'pid':os.getpid()}), encoding='utf-8')
            command = await asyncio.to_thread(sys.stdin.readline)
            if command.strip() == 'crash':
                os._exit(23)  # 仅验收子进程：模拟进程突然死亡，不走业务 finally。
        finally:
            await server.close()
            store.close()
    return 0


async def start(args: argparse.Namespace, workspace: Path, phase: str, marker: str, *, crash_fixture: bool = False) -> tuple[asyncio.subprocess.Process, dict]:
    ready = workspace / f'ready-{uuid4().hex}.json'
    command = [sys.executable, str(Path(__file__).resolve()), '--serve', '--provider', args.provider, '--workspace', str(workspace), '--phase', phase,
               '--ready', str(ready), '--marker', marker, '--env-file', str(args.env_file.resolve()), '--record-dir', str(args.output.parent.resolve() / f's4-{args.provider}-runs')]
    if crash_fixture:
        command.append('--crash-fixture')
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async with asyncio.timeout(10):
            while not ready.exists():
                if process.returncode is not None:
                    raise RuntimeError('Verification Core failed to start')
                await asyncio.sleep(0.02)
        return process, json.loads(ready.read_text(encoding='utf-8'))
    except BaseException:
        await stop(process)
        raise


async def stop(process: asyncio.subprocess.Process, *, crash: bool = False) -> None:
    try:
        async with asyncio.timeout(10):
            stdout, stderr = await process.communicate(b'crash\n' if crash else b'stop\n')
        if process.returncode != (23 if crash else 0):
            raise RuntimeError('Verification Core exit was not confirmed')
    except BaseException:
        if process.returncode is None:
            # 控制实际服务的 stdin，而不只终止 Windows venv redirector。
            if process.stdin and not process.stdin.is_closing():
                process.stdin.write(b'crash\n')
                await process.stdin.drain()
            await asyncio.wait_for(process.wait(), 5)
        raise


async def run_tui(port: int, goal: str, session_id: str | None, output: Path) -> tuple[str, dict, str]:
    app = CodePilotApp(Settings(port=port), session_id=session_id)
    async with app.run_test(size=(110, 45)) as pilot:
        async with asyncio.timeout(100):
            while app.query_one('#start', Button).disabled:
                await asyncio.sleep(0.02)
            restored = str(app.query_one('#answer', Static).content)
            app.query_one('#goal', Input).value = goal
            await pilot.click('#start')
            while app.active_run is None or app.views[app.active_run].result is None:
                await asyncio.sleep(0.02)
        result = app.views[app.active_run].result.model_dump()
        output.write_text(app.export_screenshot(title='CodePilot Crew S4'), encoding='utf-8')
        session_id = app.session_id
        await pilot.press('f10')
    return session_id, result, restored


async def crash_check(args: argparse.Namespace, workspace: Path) -> bool:
    process, ready = await start(args, workspace, 'first', '', crash_fixture=True)
    client = RpcClient('127.0.0.1', ready['port'])
    try:
        await client.connect()
        session_id = (await client.call('session.create', {'title':'crash fixture'}))['id']
        await client.call('agent.start', {'goal':'fixed crash fixture','session_id':session_id})
        async with asyncio.timeout(10):
            while True:
                event = await client.next_event()
                if event.type == 'model.started' and event.step == 2:
                    break
        before = (workspace/'effect.txt').stat().st_mtime_ns
        await stop(process, crash=True)
    finally:
        await client.close()
        if process.returncode is None:
            await stop(process)
    process, ready = await start(args, workspace, 'recall', '')
    client = RpcClient('127.0.0.1', ready['port'])
    try:
        await client.connect()
        summary = await client.call('session.resume', {'session_id':session_id})
        assert summary['status'] == 'interrupted'
        assert (workspace/'effect.txt').read_text() == 'committed once'
        assert (workspace/'effect.txt').stat().st_mtime_ns == before
        return True
    finally:
        await client.close()
        await stop(process)


async def verify(args: argparse.Namespace) -> bool:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    marker = 'MEMORY_' + uuid4().hex
    with TemporaryDirectory(prefix='codepilot-s4-') as temporary:
        workspace = Path(temporary).resolve()
        sample = workspace/'sample.txt'
        sample.write_text(marker, encoding='utf-8')
        process, first_ready = await start(args, workspace, 'first', marker)
        try:
            session_id, first, _ = await run_tui(first_ready['port'], '读取 sample.txt，记住其中完整标记并在最终回答引用。仅使用 read_file。', None, args.output.with_suffix('.first.svg'))
        finally:
            await stop(process)
        sample.unlink()
        process, second_ready = await start(args, workspace, 'recall', marker)
        client = RpcClient('127.0.0.1', second_ready['port'])
        try:
            await client.connect()
            listing = await client.call('session.list', {})
            listed = any(item['id']==session_id for item in listing['sessions'])
            await client.close()
            _, second, restored = await run_tui(second_ready['port'], '根据本会话前次实际读取记录回答完整标记。原文件已移除，不调用任何工具，不猜测；仅引用前次观察。', session_id, args.output.with_suffix('.recall.svg'))
        finally:
            await client.close()
            await stop(process)
        store = SessionStore(workspace/'.codepilot', workspace)
        try:
            messages, notes = store.load(session_id)
            validate_history(messages)
            requests = [record['data'] for record in store.records(second['run_id']) if record['kind']=='model.request']
            observed = any(block.get('type')=='tool_result' and not block.get('is_error') and marker in block.get('content','') for request in requests for message in request['messages'] for block in message['content'])
            raw = {result['run_id']:store.records(result['run_id']) for result in (first,second)}
            summary = store.summary(session_id)
        finally:
            store.close()
        crash = await crash_check(args, workspace) if args.provider=='fixed' else None
        passed = first['status']==second['status']=='success' and marker in first['answer'] and marker in second['answer'] and marker in restored and listed and observed and not second['tool_calls'] and first_ready['pid']!=second_ready['pid'] and not sample.exists()
        report = {'version':get_version(),'provider':args.provider,'passed':passed,'different_core_pids':first_ready['pid']!=second_ready['pid'],'listed_after_restart':listed,'file_removed':not sample.exists(),'restored_widget':marker in restored,'prior_observation_in_request':observed,'recall_tool_calls':len(second['tool_calls']),'first_steps':first['steps'],'recall_steps':second['steps'],'crash_recovery_no_replay':crash,'summary':summary}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        args.output.with_suffix('.records.json').write_text(json.dumps(evidence_view(raw),ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
        return passed and crash is not False


def main() -> int:
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['fixed','real'],default='fixed')
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--output',type=Path)
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--ready',type=Path)
    parser.add_argument('--record-dir',type=Path)
    parser.add_argument('--phase',default='first')
    parser.add_argument('--marker',default='')
    parser.add_argument('--crash-fixture',action='store_true')
    args=parser.parse_args()
    args.output=args.output or Path('artifacts')/f's4-{args.provider}.json'
    return asyncio.run(serve(args)) if args.serve else (0 if asyncio.run(verify(args)) else 1)


if __name__=='__main__':
    raise SystemExit(main())
