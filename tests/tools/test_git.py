"""Issues #55/#56: read-only git evidence tools, checkpoints, targeted rollback."""

import subprocess

from harness.tools.git_tools import (
    create_checkpoint,
    deserialize_checkpoint,
    git_diff,
    git_status,
    rollback,
)


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
    (tmp_path / "tracked.py").write_text("x = [" + ",".join(
        str(i) for i in range(5000)
    ) + "]\n")
    result = git_diff(tmp_path, artifacts_dir=tmp_path / "artifacts")
    if result.truncated:
        assert result.artifacts
        assert len(result.artifacts[0]) > 0


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
