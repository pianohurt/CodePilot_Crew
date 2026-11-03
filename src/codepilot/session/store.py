"""SQLite 事务保存快照及独立增量；一个数据目录由一个 Core 拥有。"""

import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from pydantic import TypeAdapter

from codepilot.agent.types import Message, RunResult
from codepilot.session.history import validate_history

MESSAGES = TypeAdapter(list[Message])
MAX_HISTORY = 8 * 1024 * 1024
INTERRUPTION = "上次运行未正常完成；可能已有文件或命令副作用。继续前重新检查实际状态，不自动重放旧动作。"


class SessionError(RuntimeError):
    def __init__(self, message: str, code: int = -32023) -> None:
        super().__init__(message)
        self.code = code


def identifier(value: str) -> str:
    if re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise SessionError("Invalid session or run id", -32602)
    return value


def now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    def __init__(self, directory: Path, workspace: Path, secrets: tuple[str, ...] = ()) -> None:
        self.directory, self.workspace = directory.resolve(), workspace.resolve(strict=True)
        self.secrets = secrets
        self._lock: sqlite3.Connection | None = None
        self.db: sqlite3.Connection | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._lock = sqlite3.connect(self.directory / "owner.sqlite3", timeout=0)
            self._lock.execute("BEGIN EXCLUSIVE")
            self.db = sqlite3.connect(self.directory / "sessions.sqlite3", timeout=0)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise SessionError("Unsupported session database version")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, title TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL,
                    history TEXT NOT NULL, digest TEXT NOT NULL, notes TEXT NOT NULL, notes_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    goal TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
                    result TEXT, usage TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS records(
                    run_id TEXT NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(run_id,seq));
                CREATE INDEX IF NOT EXISTS runs_session ON runs(session_id,created);
                PRAGMA user_version=1;
            """)
            self._recover()
        except (sqlite3.Error, OSError, SessionError) as exc:
            self.close()
            if isinstance(exc, SessionError):
                raise
            raise SessionError("Session storage unavailable, corrupt, or already owned by another Core") from exc

    def close(self) -> None:
        for connection in (self.db, self._lock):
            if connection is not None:
                connection.close()
        self.db = self._lock = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            with self.db:
                yield
        except sqlite3.Error as exc:
            raise SessionError("Session storage transaction failed") from exc

    def encode(self, value: Any, limit: int = MAX_HISTORY) -> str:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for secret in self.secrets:
            if secret:
                text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
        if len(text.encode()) > limit:
            raise SessionError("Session data exceeds storage limit; no truncation was applied")
        return text

    @staticmethod
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def create(self, title: str = "新会话") -> dict[str, Any]:
        session_id, timestamp = uuid4().hex, now()
        title = json.loads(self.encode(title))
        with self.transaction():
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?)", (session_id, str(self.workspace), title, timestamp, timestamp, "[]", self.digest("[]"), "{}", self.digest("{}")))
        return self.summary(session_id)

    def load(self, session_id: str) -> tuple[list[Message], dict[str, str]]:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (identifier(session_id),)).fetchone()
        if row is None:
            raise SessionError("Session not found", -32021)
        if row["workspace"] != str(self.workspace):
            raise SessionError("Session belongs to a different workspace")
        try:
            if len(row["history"].encode()) > MAX_HISTORY or self.digest(row["history"]) != row["digest"] or self.digest(row["notes"]) != row["notes_digest"]:
                raise ValueError("History checksum mismatch")
            messages = MESSAGES.validate_json(row["history"])
            validate_history(messages)
            notes = json.loads(row["notes"])
            if not isinstance(notes, dict) or len(notes) > 20 or any(not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", key) or not isinstance(value, str) or len(value) > 4000 for key, value in notes.items()):
                raise ValueError("Invalid notes")
            # 原始增量独立检查，不拿当前 messages 的长度充当保存游标。
            for run in self.db.execute("SELECT id,result,usage FROM runs WHERE session_id=?", (session_id,)):
                if run["result"] is not None:
                    result = RunResult.model_validate_json(run["result"])
                    if result.run_id != run["id"]:
                        raise ValueError("Mismatched result")
                usage = json.loads(run["usage"])
                if not isinstance(usage, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in usage.items()):
                    raise ValueError("Invalid usage")
                records = self.records(run["id"])
                if not records or records[0]["kind"] != "run.begin":
                    raise ValueError("Missing run record")
            return messages, notes
        except (ValueError, TypeError, KeyError) as exc:
            raise SessionError("Session records are corrupt; original data was preserved") from exc

    def records(self, run_id: str) -> list[dict[str, Any]]:
        records = []
        for seq, kind, text, digest in self.db.execute("SELECT seq,kind,data,digest FROM records WHERE run_id=? ORDER BY seq", (identifier(run_id),)):
            if seq != len(records) + 1 or self.digest(text) != digest:
                raise ValueError("Corrupt run journal")
            records.append({"seq": seq, "kind": kind, "data": json.loads(text)})
        return records

    def append(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        text = self.encode(data, 2 * 1024 * 1024)
        self.db.execute("INSERT INTO records VALUES(?,(SELECT COALESCE(MAX(seq),0)+1 FROM records WHERE run_id=?),?,?,?)", (run_id, run_id, kind, text, self.digest(text)))

    def journal(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        with self.transaction():
            self.append(run_id, kind, data)

    def checkpoint(self, session_id: str, run_id: str, history: list[Message], delta: list[Message]) -> None:
        validate_history(history)
        text = self.encode([item.model_dump() for item in history])
        with self.transaction():
            self.append(run_id, "messages.adopted", {"messages": [item.model_dump() for item in delta]})
            self.db.execute("UPDATE sessions SET history=?,digest=?,updated=? WHERE id=?", (text, self.digest(text), now(), session_id))

    def summary(self, session_id: str, *, include_answer: bool = False) -> dict[str, Any]:
        messages, notes = self.load(session_id)
        row = self.db.execute("SELECT id,title,created,updated FROM sessions WHERE id=?", (session_id,)).fetchone()
        runs = self.db.execute("SELECT id,status,result,usage FROM runs WHERE session_id=? ORDER BY created", (session_id,)).fetchall()
        usage: dict[str, int] = {}
        for run in runs:
            for key, value in json.loads(run["usage"]).items():
                usage[key] = usage.get(key, 0) + value
        result = dict(row) | {"run_count": len(runs), "message_count": len(messages), "note_count": len(notes), "usage": usage, "last_run_id": runs[-1]["id"] if runs else None, "status": runs[-1]["status"] if runs else "ready"}
        if include_answer:
            result["last_result"] = json.loads(runs[-1]["result"]) if runs and runs[-1]["result"] else None
        return result

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        result = []
        for row in self.db.execute("SELECT id,title FROM sessions ORDER BY updated DESC LIMIT ?", (limit,)).fetchall():
            try:
                result.append(self.summary(row["id"]))
            except SessionError:
                result.append({"id": row["id"], "title": row["title"], "status": "corrupt"})
        return result

    def history_page(self, session_id: str, before: int | None) -> dict[str, Any]:
        session = self.db.execute("SELECT workspace FROM sessions WHERE id=?", (identifier(session_id),)).fetchone()
        if session is None:
            raise SessionError("Session not found", -32021)
        if session["workspace"] != str(self.workspace):
            raise SessionError("Session belongs to a different workspace")
        rows = self.db.execute(
            """SELECT rowid, goal, result FROM runs
               WHERE session_id=? AND result IS NOT NULL AND (? IS NULL OR rowid < ?)
               ORDER BY rowid DESC LIMIT 21""",
            (session_id, before, before),
        ).fetchall()
        turns = []
        # 给 RPC 包头留空间，按实际 JSON 字节控制分页。
        remaining = 56 * 1024
        for row in rows[:20]:
            try:
                result = RunResult.model_validate_json(row["result"])
            except ValueError as exc:
                raise SessionError("Session result is corrupt") from exc
            turn = {"goal": row["goal"], "answer": result.answer if result.status == "success" else "草稿未采纳；" + (result.error or "")}
            size = len(json.dumps(turn, ensure_ascii=False).encode()) + 1
            if size > remaining:
                if turns:
                    break
                turn = {key: value[:4500] + ("\n…（显示已截断）" if len(value) > 4500 else "") for key, value in turn.items()}
                size = len(json.dumps(turn, ensure_ascii=False).encode()) + 1
            turns.append(turn)
            remaining -= size
        return {"turns": list(reversed(turns)), "before": rows[len(turns) - 1]["rowid"] if len(rows) > len(turns) else None}

    def _recover(self) -> None:
        for row in self.db.execute("SELECT id,session_id FROM runs WHERE status='running'").fetchall():
            result = RunResult(run_id=row["id"], status="failed", stop_reason="interrupted", steps=0, error=INTERRUPTION)
            # 配对良好的已保存快照可继续；损坏快照留原样并由 load 拒绝。
            try:
                messages, _ = self.load(row["session_id"])
            except SessionError:
                messages = None
            with self.transaction():
                self.append(row["id"], "run.interrupted", {"result": result.model_dump()})
                self.db.execute("UPDATE runs SET status='interrupted',result=? WHERE id=?", (self.encode(result.model_dump()), row["id"]))
                if messages is not None:
                    messages.append(Message(role="user", content=[{"type": "text", "text": INTERRUPTION}]))
                    text = self.encode([message.model_dump() for message in messages])
                    self.db.execute("UPDATE sessions SET history=?,digest=?,updated=? WHERE id=?", (text, self.digest(text), now(), row["session_id"]))
