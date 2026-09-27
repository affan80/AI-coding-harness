"""Structured run directory with atomic state persistence (PRD §21; FR-12).

One ``RunStore`` owns ``runs/<session-id>/``. Critical state documents are
written atomically (temp file + fsync + ``os.replace`` + directory fsync) so an
interrupted write leaves the previous valid state readable. Event and
tool-call streams are append-only JSONL; large tool output goes to
``artifacts/`` (also written atomically) and is referenced by path and sha256
instead of being copied into records. The :func:`read_jsonl` reader tolerates
a torn final line — a crash mid-append can only ever damage the last record,
which is then skipped instead of poisoning the stream.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from harness.core.models import SessionStatus, UserRequest, new_session_id
from harness.model.redaction import Redactor
from harness.telemetry.models import ArtifactRef, ToolCallRecord

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
    "recovery.json",
    "metrics.json",
    "changed-files.json",
)

# Free-form text documents written atomically (not JSON-serialized).
_TEXT_DOCUMENT_NAMES = ("final-report.md",)

# Raw tool output at or below this size stays inline in the tool record;
# anything larger becomes an artifacts/ file referenced by path and hash.
ARTIFACT_INLINE_LIMIT = 4096

_TOOL_STATUSES = ("ok", "error", "denied", "timeout")

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
        secrets: tuple[str, ...] | list[str] = (),
    ) -> None:
        self.run_dir = run_dir
        self.paths = RunPaths(run_dir=run_dir, artifacts_dir=run_dir / "artifacts")
        self.session_id = session_id
        self._clock = clock
        self._redactor = Redactor(secrets)
        self._event_seq = 0
        self._tool_seq = 0
        self._patch_count = 0

    # -- construction ----------------------------------------------------

    @classmethod
    def start(
        cls,
        request: UserRequest,
        runs_root: str | Path = "runs",
        clock: Callable[[], datetime] = _utc_now,
        secrets: tuple[str, ...] | list[str] = (),
    ) -> RunStore:
        """Create runs/<session-id>/ and the initial state documents.

        ``secrets`` are scrubbed from every stored summary, message, and
        data payload; raw output stays in artifacts/ so it remains
        retrievable by its path + sha256 evidence reference (issue #41).
        """
        now = clock()
        session_id = new_session_id(now)
        root = Path(runs_root)
        run_dir = root / session_id
        run_dir.mkdir(parents=True)
        (root / ".gitignore").write_text(_RUNS_GITIGNORE)
        store = cls(run_dir, session_id, clock, secrets=secrets)
        store.write_document("request.json", request.to_dict())
        store._write_session(status=SessionStatus.IN_PROGRESS, stop_reason="")
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

    def write_text_document(self, name: str, text: str) -> None:
        """Atomically (re)write one free-form text document (e.g. final-report.md)."""
        if name not in _TEXT_DOCUMENT_NAMES:
            raise ValueError(f"unknown run text document: {name}")
        _atomic_write(self.paths.document(name), text.encode("utf-8"))

    def finalize(self, status: SessionStatus, stop_reason: str = "") -> None:
        """Record the terminal session status and timestamp."""
        self._write_session(status=status, stop_reason=stop_reason)
        self.append_event("state", f"session {status.value}", data={"stop_reason": stop_reason})

    def _write_session(self, status: SessionStatus, stop_reason: str) -> None:
        now = self._clock().isoformat()
        existing: dict = {}
        session_path = self.paths.document("session.json")
        if session_path.exists():
            try:
                existing = json.loads(session_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                # A hand-edited or previously damaged document must never
                # prevent recording the terminal status.
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
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
            "message": _summarize(self._redactor.text(message)),
            "data": self._redactor.details(data or {}),
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
            args_summary=_summarize(self._redactor.text(args_summary)),
            status=status,
            started_at=started_at,
            duration_ms=duration_ms,
            summary=_summarize(self._redactor.text(summary)),
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
        # Atomic like the state documents: an interrupted write leaves the
        # previous artifact (if any) valid and only a stray .tmp behind.
        _atomic_write(artifact_path, content)
        return ArtifactRef(
            path=str(artifact_path.relative_to(self.run_dir)),
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
        )

    # -- reading the evidence back -------------------------------------------

    def read_events(self) -> list[dict]:
        """Return every complete event record; a torn final line is skipped."""
        return read_jsonl(self.paths.stream("events.jsonl"))

    def read_tool_calls(self) -> list[ToolCallRecord]:
        """Return every complete tool-call record; malformed lines are skipped."""
        records: list[ToolCallRecord] = []
        for raw in read_jsonl(self.paths.stream("tool-calls.jsonl")):
            try:
                records.append(ToolCallRecord.from_dict(raw))
            except (KeyError, TypeError, ValueError):
                continue  # unreadable record: keep the valid prefix
        return records

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

    def record_changed_files(self, files: list[str]) -> None:
        """Persist changed-files.json: every path the session modified.

        Written together with patches.diff and verification.json so a
        reviewer can reconstruct what changed and how it was checked from
        the run directory alone (issue #26).
        """
        self.write_document(
            "changed-files.json",
            {"count": len(files), "files": sorted(files)},
        )


def _atomic_write(path: Path, content: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Make the rename itself durable; a crash can then only show the old or
    the new file, never a missing or half-renamed one."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # best effort on filesystems that do not support directory fsync
    finally:
        os.close(fd)


def _append_line(path: Path, record: dict) -> None:
    """Append one JSONL record as a single O_APPEND write."""
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL stream, skipping blank lines and a torn final record.

    A crash mid-append can damage only the last line of an O_APPEND stream;
    every complete record before it stays valid and readable.
    """
    path = Path(path)
    if not path.is_file():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue  # torn or corrupted line: skip, keep the valid prefix
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _summarize(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [{len(text)} chars total]"


def reconstruct_run(run_dir: Path) -> dict:
    """Reconstruct what happened from a run directory (#2, #26).

    Reads ``session.json``, ``request.json``, ``goals.json``, ``plan.json``,
    ``changed-files.json``, ``verification.json``, ``events.jsonl``,
    ``tool-calls.jsonl``, and ``patches.diff`` — everything a reviewer gets
    after any terminal outcome (verified, partial, or failed). Missing
    documents degrade to empty values instead of failing the reconstruction,
    and a torn final JSONL line never poisons the read.
    """
    run_dir = Path(run_dir)

    def _load(name: str) -> dict:
        path = run_dir / name
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {}

    session = _load("session.json")
    verification = _load("verification.json")
    changed = _load("changed-files.json")
    goals_doc = _load("goals.json")
    plan_doc = _load("plan.json")

    tool_calls: list[dict] = []
    for raw in read_jsonl(run_dir / "tool-calls.jsonl"):
        try:
            record = ToolCallRecord.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            continue  # unreadable record: keep the valid prefix
        tool_calls.append(
            {
                "id": record.id,
                "name": record.name,
                "status": record.status,
                "started_at": record.started_at,
                "duration_ms": record.duration_ms,
                "truncated": record.truncated,
                "artifact": (
                    {"path": record.artifact.path, "sha256": record.artifact.sha256}
                    if record.artifact
                    else None
                ),
            }
        )

    events = read_jsonl(run_dir / "events.jsonl")

    patch_labels: list[str] = []
    diff_path = run_dir / "patches.diff"
    if diff_path.is_file():
        for line in diff_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("--- patch:") and line.endswith(" ---"):
                patch_labels.append(line[len("--- patch:") : -len(" ---")].strip())

    plan_steps = plan_doc.get("steps", [])
    return {
        "session_id": session.get("session_id", ""),
        "status": session.get("status", ""),
        "stop_reason": session.get("stop_reason", ""),
        "objective": request_objective(_load("request.json")),
        "goals": goals_doc.get("goals", []),
        "plan_present": bool(plan_doc),
        "plan_steps": len(plan_steps) if isinstance(plan_steps, list) else 0,
        "changed_files": sorted(changed.get("files", [])),
        "patch_count": len(patch_labels),
        "patch_labels": patch_labels,
        "checks": verification.get("checks", []),
        "verification_status": verification.get("status", "not_run"),
        "event_count": len(events),
        "tool_calls": tool_calls,
    }


def request_objective(request_doc: dict) -> str:
    """Best-effort objective from request.json for the reconstruction view."""
    objective = request_doc.get("objective", "")
    return objective if isinstance(objective, str) else ""
