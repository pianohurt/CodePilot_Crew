"""草稿、步骤完成文本和最终回答分开；不从界面文本推断运行结果。"""

from dataclasses import dataclass, field

from codepilot.agent.types import RunResult
from codepilot.core.events import RunEvent


@dataclass
class RunView:
    run_id: str
    goal: str = ""
    status: str = "running"
    step: int = 0
    seq: int = 0
    drafts: dict[int, str] = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=dict)
    answer: str = ""
    error: str = ""
    result: RunResult | None = None
    delta_count: int = 0
    approvals: dict[str, dict] = field(default_factory=dict)

    def apply(self, event: RunEvent) -> None:
        if event.run_id != self.run_id or event.seq <= self.seq or self.result is not None:
            return
        self.seq = event.seq
        self.step = event.step
        if event.type == "run.started":
            self.goal = str(event.data.get("goal", ""))
        elif event.type == "model.delta":
            self.delta_count += 1
            self.drafts[event.step] = self.drafts.get(event.step, "") + str(event.data.get("text", ""))
        elif event.type == "model.completed":
            self.drafts[event.step] = str(event.data.get("text", ""))
        elif event.type == "tool.started":
            self.tools[event.tool_use_id] = f"{event.data.get('name')} {event.data.get('input')}\n执行中"
        elif event.type == "tool.completed":
            self.tools[event.tool_use_id] = f"{event.data.get('name')} [{event.tool_use_id}]\n{'错误' if event.data.get('is_error') else '完成'}: {event.data.get('content')}"
        elif event.type == "approval.requested":
            self.approvals[event.data["approval_id"]] = event.data
        elif event.type == "approval.resolved":
            self.approvals.pop(event.data["approval_id"], None)
        elif event.type == "run.finished":
            self.approvals.clear()
            self.result = RunResult.model_validate(event.data["result"])
            self.status = self.result.status
            self.answer = self.result.answer if self.status == "success" else ""
            self.error = self.result.error or ""

    @property
    def text(self) -> str:
        if self.result is not None:
            return self.answer if self.status == "success" else "草稿未采纳；" + self.error
        return self.drafts.get(self.step, "")
