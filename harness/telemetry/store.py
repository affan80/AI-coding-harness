"""Structured run directory with atomic state persistence (PRD §21; FR-12).

One ``RunStore`` owns ``runs/<session-id>/``. Critical state documents are
written atomically (temp file + fsync + ``os.replace``) so an interrupted
write leaves the previous valid state readable. Event and tool-call streams
are append-only JSONL; large tool output goes to ``artifacts/`` and is
referenced by path and sha256 instead of being copied into records.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from harness.core.models import Budget, UserRequest
from harness.telemetry.models import ArtifactRef, RunStatus, ToolCallRecord

# JSON document files written as whole atomic units. JSONL streams
# (events.jsonl, tool-calls.jsonl) and patches.diff are appended instead.
_DOCUMENT_NAMES = (
    "session.json",
    "request.json",
    "budget.json",
    "goals.json",
    "plan.json",
    "baseline.json",
    "repository.json",
    "findings.json",
    "verification.json",
    "metrics.json",
)

# Raw tool output at or below this size stays inline in the tool record;
# anything larger becomes an artifacts/ file referenced by path and hash.
ARTIFACT_INLINE_LIMIT = 4096

_TOOL_STATUSES = ("ok", "error", "denied", "timeout")

_RUNS_GITIGNORE = "*\n!.gitignore\n"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def new_session_id(now: datetime | None = None) -> str:
    """Sortable run-directory identifier: s-<UTC timestamp>-<random suffix>."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"s-{stamp}-{uuid.uuid4().hex[:8]}"


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
        self._tool_seq = 0
        self._patch_count = 0

    # -- construction ----------------------------------------------------

    @classmethod
    def start(
        cls,
        request: UserRequest,
        budget: Budget | None = None,
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
        if budget is not None:
            store.write_document("budget.json", budget.to_dict())
        store._write_session(status=RunStatus.IN_PROGRESS, stop_reason="")
        store.append_event(
            "info",
            "session started",
            data={"repository": request.repository_path},
        )
        return store

    # -- atomic documents --------------------------------------------------

    def write_document(self, name: str, payload: dict) -> None:
        """Atomically (re)write one JSON state document."""
        if name not in _DOCUMENT_NAMES:
            raise ValueError(f"unknown run document: {name}")
        serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        _atomic_write(self.paths.document(name), serialized.encode("utf-8"))

    def finalize(self, status: RunStatus, stop_reason: str = "") -> None:
        """Record the terminal session status and timestamp."""
        self._write_session(status=status, stop_reason=stop_reason)
        self.append_event("state", f"session {status.value}", data={"stop_reason": stop_reason})

    def _write_session(self, status: RunStatus, stop_reason: str) -> None:
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

    # -- tool calls and artifacts -------------------------------------------

    def record_tool_call(
        self,
        name: str,
        args_summary: str,
        status: str,
        started_at: str,
        duration_ms: int,
        summary: str | None = None,
        output: str | None = None,
    ) -> ToolCallRecord:
        """Persist one tool call; route large output to artifacts/.

        The record and its event never embed raw output larger than
        ``ARTIFACT_INLINE_LIMIT``; oversized output is stored under
        ``artifacts/`` and referenced by path and sha256.
        """
        if status not in _TOOL_STATUSES:
            raise ValueError(
                f"invalid tool status {status!r}: expected one of {', '.join(_TOOL_STATUSES)}"
            )
        self._tool_seq += 1
        tool_id = f"tool-{self._tool_seq:04d}"
        artifact: ArtifactRef | None = None
        truncated = False
        if output is not None and len(output.encode("utf-8")) > ARTIFACT_INLINE_LIMIT:
            artifact = self._store_artifact(tool_id, output)
            truncated = True
        if summary is None:
            summary = output if output is not None else ""

        record = ToolCallRecord(
            id=tool_id,
            seq=self._tool_seq,
            name=name,
            args_summary=_summarize(args_summary),
            status=status,
            started_at=started_at,
            duration_ms=duration_ms,
            summary=_summarize(summary),
            truncated=truncated,
            artifact=artifact,
        )
        _append_line(
            self.paths.stream("tool-calls.jsonl"),
            record.to_dict(),
        )
        self.append_event(
            "tool",
            record.summary or f"{name} {status}",
            data={
                "tool": name,
                "status": status,
                "duration_ms": duration_ms,
                "artifact": artifact.path if artifact else None,
                "truncated": truncated,
            },
            tool_call_id=tool_id,
        )
        return record

    def _store_artifact(self, tool_id: str, output: str) -> ArtifactRef:
        self.paths.artifacts_dir.mkdir(exist_ok=True)
        artifact_path = self.paths.artifacts_dir / f"{tool_id}-output.txt"
        content = output.encode("utf-8")
        artifact_path.write_bytes(content)
        return ArtifactRef(
            path=str(artifact_path.relative_to(self.run_dir)),
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
        )

    # -- patch and verification evidence --------------------------------------

    def append_patch(self, diff_text: str, label: str = "") -> None:
        """Append one inspectable diff to patches.diff under a labeled header."""
        stamp = self._clock().isoformat()
        header = f"--- patch: {label or 'unnamed'} ({stamp}) ---\n"
        body = diff_text if diff_text.endswith("\n") else diff_text + "\n"
        separator = "\n" if self._patch_count > 0 else ""
        # patches.diff holds human-readable diffs, not JSON lines; write the
        # header and body directly as one O_APPEND write.
        fd = os.open(self.paths.stream("patches.diff"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (separator + header + body).encode("utf-8"))
        finally:
            os.close(fd)
        self._patch_count += 1

    def write_verification(self, report: dict) -> None:
        """Persist verification.json.

        Expected shape (produced by the verification ladder, issue #14):
        ``{"status": "pass"|"fail"|"not_run", "checks": [{"name", "command",
        "exit_code", "ok", "summary"}], "notes": [...]}``. The store persists
        the document as-is so failed and partial sessions keep their evidence.
        """
        self.write_document("verification.json", report)


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
