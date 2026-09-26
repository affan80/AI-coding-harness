"""Read-only Git evidence tools and file-level checkpoints/rollback (#55, #56).

git_status/git_diff never mutate the repository. Checkpoints snapshot the
content of session-owned files only, so a pre-existing dirty worktree is
preserved untouched across checkpoint -> failure -> rollback flows.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .result import MISSING, SPAWN_ERROR, ToolResult

DIFF_ARTIFACT_BYTES = 40_000


def _git(root: Path, *args: str) -> tuple[int, str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout + completed.stderr


def git_status(root: Path) -> ToolResult:
    """Porcelain status: session changes and pre-existing user changes alike."""
    code, output = _git(root, "status", "--porcelain")
    if code != 0:
        return ToolResult.failure(SPAWN_ERROR, f"git status failed: {output}")
    lines = output.splitlines()
    return ToolResult(
        ok=True,
        summary=f"{len(lines)} changed/untracked path(s)",
        data={"entries": lines[:200], "total": len(lines), "path": str(root)},
    )


def git_diff(
    root: Path,
    path: str | None = None,
    *,
    artifacts_dir: Path | None = None,
) -> ToolResult:
    """Bounded diff of the worktree; large diffs are persisted as artifacts."""
    args = ["diff", "--"]
    if path:
        args.append(path)
    code, output = _git(root, *args)
    if code != 0:
        return ToolResult.failure(SPAWN_ERROR, f"git diff failed: {output}")
    truncated = len(output.encode("utf-8")) > DIFF_ARTIFACT_BYTES
    artifacts: list[str] = []
    summary_diff = output
    if truncated:
        artifacts_dir = artifacts_dir or Path("/tmp")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifacts_dir / f"diff-{uuid.uuid4().hex[:8]}.patch"
        artifact.write_text(output, encoding="utf-8")
        artifacts.append(str(artifact))
        summary_diff = output[:DIFF_ARTIFACT_BYTES]
    stat_code, stat_output = _git(root, "diff", "--stat")
    stat = stat_output.strip() if stat_code == 0 else ""
    return ToolResult(
        ok=True,
        summary=stat or f"diff is {len(output)} bytes",
        data={"diff": summary_diff, "path": str(root)},
        artifacts=artifacts,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Checkpoints (content snapshots of session-owned files only)
# ---------------------------------------------------------------------------


@dataclass
class Checkpoint:
    checkpoint_id: str
    root: str
    # rel_path -> previous content (None means the file did not exist yet)
    snapshots: dict[str, str | None] = field(default_factory=dict)

    def tracked_paths(self) -> list[str]:
        return sorted(self.snapshots)


def create_checkpoint(
    root: Path, files: list[str], checkpoint_dir: Path | None = None
) -> ToolResult:
    """Snapshot the given session-owned files before a risky change.

    Only the named files are snapshotted; unrelated dirty files in the
    worktree are never included, hidden, or reverted.
    """
    if not files:
        return ToolResult.failure(MISSING, "checkpoint needs at least one file")
    snapshots: dict[str, str | None] = {}
    for rel in files:
        path = root / rel
        snapshots[rel] = path.read_text(encoding="utf-8") if path.is_file() else None
    checkpoint = Checkpoint(
        checkpoint_id=f"ckpt-{uuid.uuid4().hex[:8]}",
        root=str(root),
        snapshots=snapshots,
    )
    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / f"{checkpoint.checkpoint_id}.json").write_text(
            json.dumps(_serialize_checkpoint(checkpoint), indent=2),
            encoding="utf-8",
        )
    return ToolResult(
        ok=True,
        summary=f"checkpoint {checkpoint.checkpoint_id}: "
        f"{len(snapshots)} file(s) snapshotted",
        data={"checkpoint": _serialize_checkpoint(checkpoint)},
    )


def rollback(checkpoint: Checkpoint, root: Path) -> ToolResult:
    """Restore exactly the snapshotted files; everything else is untouched."""
    restored: list[str] = []
    deleted: list[str] = []
    for rel, previous in checkpoint.snapshots.items():
        path = root / rel
        if previous is None:
            if path.exists():
                path.unlink()
                deleted.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(previous, encoding="utf-8")
        restored.append(rel)
    return ToolResult(
        ok=True,
        summary=f"rollback {checkpoint.checkpoint_id}: restored "
        f"{len(restored)}, removed {len(deleted)}",
        data={"restored": restored, "deleted": deleted},
    )


def _serialize_checkpoint(checkpoint: Checkpoint) -> dict:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "root": checkpoint.root,
        "snapshots": checkpoint.snapshots,
    }


def deserialize_checkpoint(data: dict) -> Checkpoint:
    return Checkpoint(
        checkpoint_id=data["checkpoint_id"],
        root=data["root"],
        snapshots=data["snapshots"],
    )
