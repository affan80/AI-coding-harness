"""Structured run directory with atomic state persistence (PRD §21; FR-12).

One ``RunStore`` owns ``runs/<session-id>/``. Critical state documents are
written atomically (temp file + fsync + ``os.replace``) so an interrupted
write leaves the previous valid state readable. Event and tool-call streams
are append-only JSONL; large tool output goes to ``artifacts/`` and is
referenced by path and sha256 instead of being copied into records.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from harness.core.models import SessionStatus, UserRequest, new_session_id

# JSON document files written as whole atomic units. JSONL streams
# (events.jsonl, tool-calls.jsonl) and patches.diff are appended instead.
_DOCUMENT_NAMES = (
    "session.json",
    "request.json",
    "goals.json",
    "plan.json",
    "baseline.json",
    "repository.json",
    "findings.json",
    "verification.json",
    "metrics.json",
)

_RUNS_GITIGNORE = "*\n!.gitignore\n"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RunPaths:
    """Absolute locations of everything a run produces."""

    run_dir: Path
    artifacts_dir: Path

    def document(self, name: str) -> Path:
        return self.run_dir / name

    def stream(self, name: str) -> Path:
        return self.run_dir / name


class RunStore:
    """Creates and fills one run directory for one session."""

    def __init__(
        self,
        run_dir: Path,
        session_id: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.run_dir = run_dir
        self.paths = RunPaths(run_dir=run_dir, artifacts_dir=run_dir / "artifacts")
        self.session_id = session_id
        self._clock = clock
        self._event_seq = 0

    # -- construction ----------------------------------------------------

    @classmethod
    def start(
        cls,
        request: UserRequest,
        runs_root: str | Path = "runs",
        clock: Callable[[], datetime] = _utc_now,
    ) -> RunStore:
        """Create runs/<session-id>/ and the initial state documents."""
        now = clock()
        session_id = new_session_id(now)
        root = Path(runs_root)
        run_dir = root / session_id
        run_dir.mkdir(parents=True)
        (root / ".gitignore").write_text(_RUNS_GITIGNORE)
        store = cls(run_dir, session_id, clock)
        store.write_document("request.json", request.to_dict())
        store._write_session(status=SessionStatus.IN_PROGRESS, stop_reason="")
        store.append_event(
            "info",
            "session started",
            data={"repository": request.repository},
        )
        return store

    # -- atomic documents --------------------------------------------------

    def write_document(self, name: str, payload: dict) -> None:
        """Atomically (re)write one JSON state document."""
        if name not in _DOCUMENT_NAMES:
            raise ValueError(f"unknown run document: {name}")
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        _atomic_write(self.paths.document(name), serialized.encode("utf-8"))

    def finalize(self, status: SessionStatus, stop_reason: str = "") -> None:
        """Record the terminal session status and timestamp."""
        self._write_session(status=status, stop_reason=stop_reason)
        self.append_event("state", f"session {status.value}", data={"stop_reason": stop_reason})

    def _write_session(self, status: SessionStatus, stop_reason: str) -> None:
        now = self._clock().isoformat()
        existing: dict = {}
        session_path = self.paths.document("session.json")
        if session_path.exists():
            existing = json.loads(session_path.read_text(encoding="utf-8"))
        session = {
            "session_id": self.session_id,
            "status": status.value,
            "stop_reason": stop_reason,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        serialized = json.dumps(session, indent=2, sort_keys=True) + "\n"
        _atomic_write(session_path, serialized.encode("utf-8"))

    # -- event stream ------------------------------------------------------

    def append_event(
        self,
        kind: str,
        message: str,
        state: str | None = None,
        data: dict | None = None,
        tool_call_id: str | None = None,
    ) -> dict:
        """Append one JSONL event; returns the stored record."""
        self._event_seq += 1
        record = {
            "seq": self._event_seq,
            "timestamp": self._clock().isoformat(),
            "kind": kind,
            "state": state,
            "message": _summarize(message),
            "data": data or {},
            "tool_call_id": tool_call_id,
        }
        _append_line(self.paths.stream("events.jsonl"), record)
        return record


def _atomic_write(path: Path, content: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _append_line(path: Path, record: dict) -> None:
    """Append one JSONL record as a single O_APPEND write."""
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _summarize(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [{len(text)} chars total]"
