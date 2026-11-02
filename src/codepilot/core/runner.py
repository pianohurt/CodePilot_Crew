"""运行入口装配独立上下文，依赖可共享、消息不可共享。"""

import asyncio
from pathlib import Path

from codepilot.agent.loop import AgentLoop, finish
from codepilot.agent.provider import Provider
from codepilot.agent.types import Message, RunResult, TextBlock
from codepilot.core.context import ExecutionContext
from codepilot.tools.registry import ToolRegistry


class Runner:
    def __init__(
        self, provider: Provider, tools: ToolRegistry, workspace: Path, *, run_timeout: float = 90.0
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory")
        self._tools = tools
        self._loop = AgentLoop(provider, tools)
        self._run_timeout = run_timeout

    async def run(self, goal: str, *, max_steps: int = 8) -> RunResult:
        if not goal.strip() or len(goal) > 8000:
            raise ValueError("Goal must contain 1 to 8000 characters")
        if type(max_steps) is not int or not 1 <= max_steps <= 32:
            raise ValueError("max_steps must be an integer from 1 to 32")
        context = ExecutionContext(workspace=self.workspace, allowed_tools=self._tools.names)
        context.messages.append(Message(role="user", content=[TextBlock(text=goal)]))
        try:
            async with asyncio.timeout(self._run_timeout):
                return await self._loop.run(context, max_steps=max_steps)
        except TimeoutError:
            return finish(context, "run_timeout", error="Run time budget exhausted")
