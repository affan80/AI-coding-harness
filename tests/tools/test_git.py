"""Issues #55/#56: read-only git evidence tools, checkpoints, targeted rollback."""

import subprocess
from pathlib import Path

from harness.tools.factory import build_registry
from harness.tools.git_tools import (
    DIFF_ARTIFACT_BYTES,
    create_checkpoint,
    deserialize_checkpoint,
    git_diff,
    git_status,
    rollback,
)
from harness.tools.result import DENIED, SPAWN_ERROR


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _init_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "tracked.py").write_text("original = True\n")
    (tmp_path / "user_file.txt").write_text("user's own dirty work\n")
    _git(tmp_path, "add", "tracked.py")
    _git(tmp_path, "commit", "-q", "-m", "init")
    # pre-existing dirty worktree the session must never touch
    (tmp_path / "tracked.py").write_text("original = True\n# user tweak\n")


def test_git_status_and_diff_are_read_only(tmp_path):
    _init_repo(tmp_path)
    status = git_status(tmp_path)
    assert status.ok
    assert any("user_file.txt" in e for e in status.data["entries"])

    (tmp_path / "tracked.py").write_text("original = False\n")
    diff = git_diff(tmp_path)
    assert diff.ok
    assert "-original = True" in diff.data["diff"]
    assert (tmp_path / "tracked.py").read_text() == "original = False\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != ".git") == [
        "tracked.py", "user_file.txt"
    ]  # no new files written into the repo


def test_large_diff_is_persisted_as_artifact(tmp_path):
    _init_repo(tmp_path)
    # One modified line large enough to exceed the 40 KB in-band cap.
    (tmp_path / "tracked.py").write_text('payload = "' + "A" * 50_000 + '"\n')
    _git(tmp_path, "add", "tracked.py")
    (tmp_path / "tracked.py").write_text('payload = "' + "B" * 50_000 + '"\n')

    artifacts_dir = tmp_path / "artifacts"
    result = git_diff(tmp_path, artifacts_dir=artifacts_dir)

    assert result.ok
    assert result.truncated
    assert len(result.data["diff"]) == DIFF_ARTIFACT_BYTES  # capped in-band
    assert len(result.artifacts) == 1
    artifact = Path(result.artifacts[0])
    assert artifact.parent == artifacts_dir
    full_diff = artifact.read_text(encoding="utf-8")
    assert len(full_diff.encode("utf-8")) > DIFF_ARTIFACT_BYTES
    assert full_diff.startswith(result.data["diff"])  # in-band is the prefix
    assert '+payload = "B' in full_diff and '-payload = "A' in full_diff


def test_git_tools_fail_cleanly_outside_a_repository(tmp_path):
    # Failure boundary: no crash, a structured SPAWN_ERROR result instead.
    status = git_status(tmp_path)
    assert not status.ok
    assert status.error_kind == SPAWN_ERROR
    assert "git status failed" in status.summary

    diff = git_diff(tmp_path)
    assert not diff.ok
    assert diff.error_kind == SPAWN_ERROR
    assert "git diff failed" in diff.summary


def test_verifier_can_inspect_changes_through_the_registry_but_not_mutate(tmp_path):
    _init_repo(tmp_path)
    registry = build_registry(tmp_path, [])

    # Executor and verifier both get read-only git evidence (issue #55 AC).
    for actor in ("executor", "verification"):
        status = registry.call(actor, "git_status")
        assert status.ok, status.summary
        assert status.data["total"] >= 1  # sees pre-existing dirty worktree
        diff = registry.call(actor, "git_diff")
        assert diff.ok, diff.summary

    # Inspection does not become a mutation path for the verifier.
    denied = registry.call("verification", "create_file",
                           path="x.py", content="mutate = True\n")
    assert not denied.ok
    assert denied.error_kind == DENIED
    assert not (tmp_path / "x.py").exists()


def test_checkpoint_and_rollback_preserve_unrelated_user_changes(tmp_path):
    _init_repo(tmp_path)
    # Session owns tracked.py; user_file.txt belongs to the user.
    checkpoint = create_checkpoint(tmp_path, ["tracked.py"])
    assert checkpoint.ok
    snapshots = checkpoint.data["checkpoint"]["snapshots"]
    assert snapshots["tracked.py"] == "original = True\n# user tweak\n"

    # Session makes a risky change.
    (tmp_path / "tracked.py").write_text("original = False\n# session edit\n")

    data = checkpoint.data["checkpoint"]
    result = rollback(deserialize_checkpoint(data), tmp_path)

    assert result.ok
    assert result.data["restored"] == ["tracked.py"]
    assert (tmp_path / "tracked.py").read_text() == (
        "original = True\n# user tweak\n"
    )
    # The user's unrelated dirty file survived untouched.
    assert (tmp_path / "user_file.txt").read_text() == (
        "user's own dirty work\n"
    )


def test_rollback_removes_files_created_after_checkpoint(tmp_path):
    _init_repo(tmp_path)
    # Checkpoint taken before the session creates its new module.
    checkpoint = create_checkpoint(
        tmp_path, ["tracked.py", "new_module.py"]
    )
    data = checkpoint.data["checkpoint"]
    assert data["snapshots"]["new_module.py"] is None  # did not exist yet

    (tmp_path / "new_module.py").write_text("created = False\n")
    result = rollback(deserialize_checkpoint(data), tmp_path)
    assert result.data["deleted"] == ["new_module.py"]
    assert not (tmp_path / "new_module.py").exists()
    assert (tmp_path / "user_file.txt").exists()


def test_checkpoint_requires_files(tmp_path):
    result = create_checkpoint(tmp_path, [])
    assert not result.ok
