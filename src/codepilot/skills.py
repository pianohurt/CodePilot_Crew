"""在运行创建前加载 Markdown Skill 并固定本轮输入和工具范围。"""

from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
import re
from typing import Annotated, BinaryIO

from pydantic import BaseModel, ConfigDict, Field, ValidationError

NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,47}$"
type SkillName = Annotated[str, Field(pattern=NAME_PATTERN)]
type ToolName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
MAX_FILE_BYTES = 16 * 1024


class SkillError(ValueError):
    pass


class Metadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: SkillName
    description: str = Field(min_length=1, max_length=500)
    allowed_tools: list[ToolName] | None = Field(default=None, max_length=128)


@dataclass(frozen=True)
class SkillInvocation:
    name: str
    source: str
    prompt: str
    allowed_tools: frozenset[str] | None


def _disk_skill(directory: Path, name: str, *, workspace: Path | None = None) -> Path | None:
    candidate = directory / f"{name}.md"
    if not candidate.exists() and not candidate.is_symlink():
        return None
    resolved = candidate.resolve(strict=True)
    if resolved.parent != directory.resolve() or (workspace is not None and not resolved.is_relative_to(workspace)):
        raise SkillError(f"Skill {name} resolves outside its allowed directory")
    if not resolved.is_file():
        raise SkillError(f"Skill {name} must be a regular Markdown file")
    return resolved


def _select(workspace: Path, name: str) -> tuple[str, Path | Traversable]:
    project = _disk_skill(workspace / ".codepilot" / "skills", name, workspace=workspace)
    if project is not None:
        return "project", project
    user = _disk_skill(Path.home() / ".codepilot" / "skills", name)
    if user is not None:
        return "user", user
    builtin = files("codepilot").joinpath("builtin_skills", f"{name}.md")
    if builtin.is_file():
        return "builtin", builtin
    raise SkillError(f"Unknown skill: {name}")


def _read(stream: BinaryIO, name: str) -> tuple[Metadata, str]:
    raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise SkillError(f"Skill {name} exceeds the Markdown file size limit")
    document = raw.decode("utf-8-sig").replace("\r\n", "\n")
    metadata, delimiter, body = document.removeprefix("---\n").partition("\n---\n")
    if not document.startswith("---\n") or not delimiter or not body.strip():
        raise SkillError(f"Skill {name} requires JSON metadata between --- lines and a Markdown body")
    try:
        parsed = Metadata.model_validate_json(metadata)
    except ValidationError as exc:
        error = exc.errors()[0]
        field = ".".join(map(str, error["loc"])) or "metadata"
        raise SkillError(f"Invalid skill {name} {field}: {error['msg']}") from exc
    if parsed.name != name or not parsed.description.strip():
        raise SkillError(f"Skill {name} metadata must match its filename and have a description")
    if "$ARGUMENTS" not in body:
        raise SkillError(f"Skill {name} body requires a $ARGUMENTS placeholder")
    return parsed, body


def prepare_skill(goal: str, workspace: Path, registered_tools: frozenset[str]) -> SkillInvocation | None:
    parts = goal.split(maxsplit=2)
    if not parts or parts[0] != "/skill":
        return None
    if len(parts) != 3 or re.fullmatch(NAME_PATTERN, parts[1]) is None or not parts[2].strip():
        raise SkillError("Use /skill <name> <argument>; names use lowercase ASCII letters, digits, _ and -")
    _, name, argument = parts
    try:
        source, path = _select(workspace, name)
        with path.open("rb") as stream:
            metadata, body = _read(stream, name)
    except (OSError, UnicodeError, RuntimeError) as exc:
        raise SkillError(f"Cannot read skill {name} as UTF-8 Markdown") from exc
    allowed = None if metadata.allowed_tools is None else frozenset(metadata.allowed_tools)
    if allowed is not None and (unknown := allowed - registered_tools):
        raise SkillError(f"Skill {name} names unregistered tools: {', '.join(sorted(unknown))}")
    prompt = f"Skill {name} for this run\n\n" + body.strip().replace("$ARGUMENTS", argument.strip())
    if len(prompt) > 8000:
        raise SkillError(f"Rendered skill {name} exceeds 8000 characters")
    return SkillInvocation(name, source, prompt, allowed)
