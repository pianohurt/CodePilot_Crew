"""用真实模型复核跨重启压缩，历史使用临时合成样本。"""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from codepilot.agent.provider import AnthropicProvider, FixedResponseProvider
from codepilot.agent.types import ModelResponse, TextBlock
from codepilot.core.config import load_model_settings
from codepilot.core.runner import Runner
from codepilot.session.manager import SessionManager
from codepilot.session.store import SessionStore
from codepilot.tools.readonly import readonly_registry


async def verify(env_file: Path) -> bool:
    settings = load_model_settings(env_file)
    marker = "NOT_RUN_" + uuid4().hex[:12]
    with TemporaryDirectory(prefix="codepilot-s5-") as directory:
        workspace = Path(directory).resolve()
        database = workspace / ".codepilot"
        store = SessionStore(database, workspace)
        try:
            session_id = store.create()["id"]
            response = ModelResponse(
                content=[TextBlock(text="Inspection notes. " + "Synthetic detail about a temporary file. " * 65)],
                stop_reason="end_turn",
            )
            runner = Runner(
                FixedResponseProvider([response] * 7), readonly_registry(), workspace, sessions=SessionManager(store),
            )
            first = await runner.run(
                f"Keep files unchanged. Verification has not run. Preserve the literal evidence identifier {marker}.",
                session_id=session_id,
            )
            for _ in range(6):
                result = await runner.run("Continue the read-only inspection.", session_id=session_id)
                if result.status != "success":
                    raise RuntimeError("Could not seed test history")
            original = store.records(first.run_id)
        finally:
            store.close()

        key = settings.api_key.get_secret_value()
        store = SessionStore(database, workspace, (key,))
        try:
            async with httpx.AsyncClient(timeout=settings.model_timeout) as client:
                provider = AnthropicProvider(
                    client, base_url=settings.base_url, api_key=key, model=settings.model,
                    max_tokens=settings.max_tokens, context_budget=14000,
                )
                runner = Runner(provider, readonly_registry(), workspace, sessions=SessionManager(store))
                result = await runner.run(
                    "From the conversation, quote the exact prior verification evidence identifier and state whether checks ran. Use no tools.",
                    session_id=session_id,
                )
            records = store.records(result.run_id)
            summaries = [row for row in records if row["kind"] == "context.summary.response"]
            metrics = [row["data"] for row in records if row["kind"] == "context.compacted"]
            report = {
                "model": settings.model,
                "stop_reason": result.stop_reason,
                "summaries": len(summaries),
                "compaction": metrics,
                "verification_identifier_recalled": marker in result.answer,
                "original_record_unchanged": store.records(first.run_id) == original,
                "tool_calls": len(result.tool_calls),
            }
            print(json.dumps(report))
            return (
                result.status == "success" and bool(summaries) and bool(metrics)
                and report["verification_identifier_recalled"]
                and report["original_record_unchanged"] and not result.tool_calls
            )
        finally:
            store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    raise SystemExit(0 if asyncio.run(verify(parser.parse_args().env_file)) else 1)
