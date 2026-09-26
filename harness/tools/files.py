"""File tools: reads, creates, and minimal inspectable patches (issue #52).

Safety rules enforced here:
- every write re-validates the resolved path (traversal + scope) upstream;
- mutating calls take ``expected_old_hash`` — when the file changed underneath
  us the write is refused (STALE_HASH) instead of overwriting newer work;
- patches are minimal unified diffs applied against exact context.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .result import CONFLICT, MISSING, STALE_HASH, ToolResult

MAX_READ_BYTES = 256 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


@dataclass
class PatchApplication:
    """What one patch did — the mutation evidence payload (issue #53)."""

    patch_id: str
    file: str
    old_hash: str
    new_hash: str
    changed_lines: list[int] = field(default_factory=list)  # 1-based, new file
    diff: str = ""


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def read_file(path: Path) -> ToolResult:
    if not path.is_file():
        return ToolResult.failure(MISSING, f"file not found: {path}")
    raw = path.read_bytes()
    truncated = len(raw) > MAX_READ_BYTES
    text = raw[:MAX_READ_BYTES].decode("utf-8", "replace")
    return ToolResult(
        ok=True,
        summary=f"read {path.name} ({len(text)} bytes)",
        data={
            "content": text,
            "sha256": sha256_bytes(raw),
            "path": str(path),
        },
        truncated=truncated,
    )


def read_range(path: Path, start_line: int, end_line: int) -> ToolResult:
    if not path.is_file():
        return ToolResult.failure(MISSING, f"file not found: {path}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)
    if start > end:
        return ToolResult.failure(
            CONFLICT, f"empty range {start_line}-{end_line} in {path.name}"
        )
    numbered = {i: lines[i - 1] for i in range(start, end + 1)}
    return ToolResult(
        ok=True,
        summary=f"read {path.name} lines {start}-{end}",
        data={"lines": numbered, "sha256": file_hash(path), "path": str(path)},
    )


def create_file(path: Path, content: str) -> ToolResult:
    if path.exists():
        return ToolResult.failure(
            CONFLICT, f"refusing to overwrite existing file: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return ToolResult(
        ok=True,
        summary=f"created {path.name}",
        data={
            "path": str(path),
            "sha256": file_hash(path),
            "patch_id": f"p-{uuid.uuid4().hex[:8]}",
        },
    )


# ---------------------------------------------------------------------------
# apply_patch: minimal unified-diff engine
# ---------------------------------------------------------------------------


def _parse_hunks(diff: str) -> list[tuple[int, list[str]]]:
    """Return [(new_start, [hunk lines])]; raises ValueError when malformed."""
    hunks: list[tuple[int, list[str]]] = []
    current: list[str] = []
    new_start = 0
    for raw_line in diff.splitlines():
        if raw_line.startswith("@@"):
            if current:
                hunks.append((new_start, current))
            parts = raw_line.split()
            if len(parts) < 3 or not parts[2].startswith("+"):
                raise ValueError(f"malformed hunk header: {raw_line}")
            range_part = parts[2][1:]  # e.g. "12,5"
            new_start = int(range_part.split(",")[0])
            current = []
            continue
        if raw_line.startswith(("--- ", "+++ ", "diff ", "index ", "new file",
                                "deleted file")):
            continue
        if current or new_start:
            if raw_line.startswith((" ", "+", "-")):
                current.append(raw_line)
            elif raw_line == "":
                current.append(" ")
            else:
                raise ValueError(f"unexpected patch line: {raw_line!r}")
    if current:
        hunks.append((new_start, current))
    return hunks


def apply_patch_text(old_text: str, diff: str) -> tuple[str, list[int]]:
    """Apply a unified diff to ``old_text``; return (new_text, changed_lines).

    Context lines must match exactly; otherwise ValueError is raised so the
    caller can report a clean conflict instead of corrupting the file.
    """
    old_lines = old_text.splitlines()
    hunks = _parse_hunks(diff)
    if not hunks:
        raise ValueError("patch contains no hunks")

    new_lines: list[str] = []
    changed: list[int] = []
    cursor = 0  # index into old_lines, lines already consumed

    for new_start, hunk in hunks:
        # Context before the hunk's first change: @@ gives the new-file start;
        # leading context lines tell us where the hunk begins in the old file.
        leading_context = 0
        for line in hunk:
            if line.startswith(" "):
                leading_context += 1
            else:
                break
        old_start = new_start - leading_context
        if old_start < cursor:
            raise ValueError("overlapping hunks in patch")
        # Copy untouched lines up to the hunk.
        copy_upto = max(0, old_start - 1)
        new_lines.extend(old_lines[cursor:copy_upto])
        cursor = copy_upto
        new_line_no = len(new_lines) + 1

        for line in hunk:
            tag, body = (line[0], line[1:])
            if tag == " ":
                if cursor >= len(old_lines) or old_lines[cursor] != body:
                    raise ValueError(
                        f"context mismatch at old line {cursor + 1}: "
                        f"expected {body!r}"
                    )
                new_lines.append(old_lines[cursor])
                cursor += 1
                new_line_no += 1
            elif tag == "-":
                if cursor >= len(old_lines) or old_lines[cursor] != body:
                    raise ValueError(
                        f"deletion mismatch at old line {cursor + 1}: "
                        f"expected {body!r}"
                    )
                changed.append(new_line_no)
                cursor += 1  # removed: not copied to new file
            else:  # '+'
                new_lines.append(body)
                changed.append(new_line_no)
                new_line_no += 1

    new_lines.extend(old_lines[cursor:])
    return "\n".join(new_lines) + ("\n" if old_text.endswith("\n") else ""), changed


def apply_patch(path: Path, diff: str, expected_old_hash: str) -> ToolResult:
    """Apply a minimal unified diff when the file still matches ``expected_old_hash``."""
    if not path.is_file():
        return ToolResult.failure(MISSING, f"file not found: {path}")
    raw = path.read_bytes()
    current_hash = sha256_bytes(raw)
    if current_hash != expected_old_hash:
        return ToolResult.failure(
            STALE_HASH,
            f"{path.name} changed since the patch was computed "
            f"(expected {expected_old_hash[:12]}, found {current_hash[:12]}); "
            "write refused",
            data={"current_sha256": current_hash},
        )
    old_text = raw.decode("utf-8")
    try:
        new_text, changed = apply_patch_text(old_text, diff)
    except ValueError as exc:
        return ToolResult.failure(CONFLICT, f"patch does not apply: {exc}")

    path.write_text(new_text, encoding="utf-8")
    application = PatchApplication(
        patch_id=f"p-{uuid.uuid4().hex[:8]}",
        file=str(path),
        old_hash=current_hash,
        new_hash=sha256_bytes(new_text.encode("utf-8")),
        changed_lines=sorted(set(changed)),
        diff=diff,
    )
    return ToolResult(
        ok=True,
        summary=(
            f"patched {path.name}: {len(changed)} changed line(s) "
            f"({application.patch_id})"
        ),
        data={
            "application": {
                "patch_id": application.patch_id,
                "file": application.file,
                "old_sha256": application.old_hash,
                "new_sha256": application.new_hash,
                "changed_lines": application.changed_lines,
                "diff": application.diff,
            }
        },
    )
