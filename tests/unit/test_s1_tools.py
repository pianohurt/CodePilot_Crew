import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from codepilot.agent.types import ToolUse
from codepilot.core.context import ExecutionContext
from codepilot.tools.readonly import MAX_FILE_BYTES, ReadFileArgs, readonly_registry
from codepilot.tools.registry import ToolRegistry


def context(root: Path, allowed: frozenset[str] = frozenset({"read_file", "list_dir"})) -> ExecutionContext:
    return ExecutionContext(workspace=root.resolve(), allowed_tools=allowed)


async def test_read_actual_file_and_select_lines(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    result = await readonly_registry().execute(
        ToolUse(id="read-1", name="read_file", input={"path": "sample.txt", "start_line": 2, "end_line": 2}), context(tmp_path)
    )
    assert not result.is_error
    assert result.content == "2: beta"
    assert result.tool_use_id == "read-1"


@pytest.mark.parametrize(
    ("name", "arguments", "error"),
    [
        ("missing", {}, "unknown_tool"),
        ("read_file", {}, "invalid_arguments"),
        ("read_file", {"path": "missing.txt"}, "file_not_found"),
        ("read_file", {"path": "sample.txt", "start_line": "1"}, "invalid_arguments"),
        ("read_file", {"path": "sample.txt", "start_line": 3, "end_line": 1}, "invalid_arguments"),
        ("read_file", {"path": "sample.txt", "extra": True}, "invalid_arguments"),
        ("read_file", {"path": ".env"}, "restricted_path"),
        ("read_file", {"path": "sample.txt:stream"}, "restricted_path"),
        ("list_dir", {"path": ".git"}, "restricted_path"),
        ("read_file", {"path": "../outside.txt"}, "path_outside_workspace"),
    ],
)
async def test_tool_failures_stay_paired(
    tmp_path: Path, name: str, arguments: dict[str, object], error: str
) -> None:
    result = await readonly_registry().execute(ToolUse(id="failed-call", name=name, input=arguments), context(tmp_path))
    assert result.is_error
    assert result.tool_use_id == "failed-call"
    assert error in result.content


async def test_registered_but_disallowed_tool_never_reads(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("do not expose", encoding="utf-8")
    result = await readonly_registry().execute(
        ToolUse(id="denied", name="read_file", input={"path": "sample.txt"}), context(tmp_path, frozenset())
    )
    assert result.is_error and result.content == "permission_denied"
    assert "do not expose" not in result.content


async def test_link_outside_workspace_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sample.txt").write_text("outside", encoding="utf-8")
    alias = workspace / "alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if sys.platform != "win32":
            pytest.skip(f"Creating links is unavailable: {type(exc).__name__}")
        junction = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(outside)], capture_output=True)
        if junction.returncode:
            pytest.skip("Neither directory symlinks nor Windows junctions are available")
    result = await readonly_registry().execute(
        ToolUse(id="linked", name="read_file", input={"path": "alias/sample.txt"}), context(workspace)
    )
    assert result.is_error and result.content == "path_outside_workspace"


async def test_large_and_non_utf8_files_are_errors(tmp_path: Path) -> None:
    registry = readonly_registry()
    (tmp_path / "large").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    (tmp_path / "binary").write_bytes(b"\xff\xfe\x00")
    for name, error in [("large", "file_too_large"), ("binary", "file_is_not_utf8")]:
        result = await registry.execute(ToolUse(id=name, name="read_file", input={"path": name}), context(tmp_path))
        assert result.is_error and result.content == error


async def test_directory_and_output_limits(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("hidden", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    for index in range(110):
        (tmp_path / f"file-{index:03}").touch()
    listing = await readonly_registry().execute(ToolUse(id="list", name="list_dir", input={}), context(tmp_path))
    assert not listing.is_error
    assert ".env" not in listing.content and ".git" not in listing.content
    assert "[truncated]" in listing.content
    (tmp_path / "long.txt").write_text("汉字" * 5000, encoding="utf-8")
    reading = await readonly_registry().execute(ToolUse(id="long", name="read_file", input={"path": "long.txt"}), context(tmp_path))
    assert not reading.is_error and len(reading.content.encode()) <= 8192
    assert reading.content.endswith("[truncated]")


async def test_tool_timeout_and_duplicate_registration(tmp_path: Path) -> None:
    async def wait(args: ReadFileArgs, ctx: ExecutionContext) -> str:
        await asyncio.sleep(1)
        return "unreachable"

    registry = ToolRegistry(timeout=0.01)
    registry.register("read_file", "slow", ReadFileArgs, wait)
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register("read_file", "duplicate", ReadFileArgs, wait)
    result = await registry.execute(ToolUse(id="slow", name="read_file", input={"path": "sample"}), context(tmp_path))
    assert result.is_error and result.content == "tool_timeout"
