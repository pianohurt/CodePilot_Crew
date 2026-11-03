"""演示与排错记录，不是会话历史；密钥与 opaque 推理内容不落盘。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codepilot.core.events import RunEvent


def evidence_view(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        if value.get("type") == "thinking":
            return {"type": "thinking", "thinking": "[opaque omitted]", "signature": "[opaque omitted]"}
        return {key: evidence_view(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [evidence_view(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


class RunRecorder:
    def __init__(self, directory: Path, run_id: str, secrets: tuple[str, ...] = ()) -> None:
        self.directory = directory / run_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id, self.secrets = run_id, secrets

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        safe = evidence_view(record, self.secrets)
        serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode()) > 128 * 1024:
            safe["data"] = {"truncated": True, "preview": json.dumps(safe.get("data"), ensure_ascii=False)[:16000]}
            serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        with (self.directory / filename).open("a", encoding="utf-8") as stream:
            stream.write(serialized + "\n")

    def event(self, event: RunEvent) -> None:
        self._append("events.jsonl", event.model_dump())

    def trace(self, kind: str, step: int, **data: Any) -> None:
        self._append("trace.jsonl", {
            "run_id": self.run_id, "step": step, "kind": kind,
            "timestamp": datetime.now(UTC).isoformat(), "data": data,
        })
