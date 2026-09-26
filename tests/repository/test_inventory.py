"""Inventory behavior: ignore rules, built-in excludes, and traversal bounds.

Covers sub-issue #30 (inventory files with ignore and traversal controls).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from harness.contracts.repository import InventoryLimits
from harness.repository import profile_repository


def paths_of(profile) -> set[str]:
    return {entry.path for entry in profile.files}


def test_builtin_dirs_never_enter_inventory(write_tree):
    root = write_tree(
        {
            "src/app.py": "x = 1\n",
            "node_modules/left-pad/index.js": "junk\n",
            "__pycache__/app.pyc": b"\x00",
            ".git/HEAD": "ref: refs/heads/main\n",
            "dist/bundle.js": "out\n",
            "build/obj.o": b"\x00",
            ".venv/lib/py.py": "v\n",
            "target/debug/x": b"\x00",
            ".next/page.js": "n\n",
            ".pytest_cache/v/cache": "c\n",
        }
    )
    profile = profile_repository(root)
    assert paths_of(profile) == {"src/app.py"}
    excluded = {rule.name: rule.hits for rule in profile.summary.excluded_dirs}
    assert excluded["node_modules"] == 1
    assert excluded["__pycache__"] == 1
    assert excluded[".git"] == 1
    assert excluded["dist"] == 1
    assert excluded["build"] == 1
    assert excluded[".venv"] == 1
    assert excluded["target"] == 1
    assert excluded[".next"] == 1
    assert excluded[".pytest_cache"] == 1


def test_builtin_file_excludes_and_env_secrets(write_tree):
    root = write_tree(
        {
            "app.py": "x = 1\n",
            ".env": "SECRET=1\n",
            ".env.local": "SECRET=2\n",
            ".env.example": "SECRET=\n",
            ".DS_Store": b"\x00",
            "keep.pyc": b"\x00",
        }
    )
    profile = profile_repository(root)
    assert paths_of(profile) == {"app.py", ".env.example"}
    assert profile.summary.ignored_by_builtins == 4  # .env, .env.local, .DS_Store, keep.pyc
    assert "keep.pyc" not in paths_of(profile)


def test_gitignore_rules_and_negation(write_tree):
    root = write_tree(
        {
            ".gitignore": "*.log\n!important.log\nbuild_out/\n",
            "keep.py": "x = 1\n",
            "debug.log": "noise\n",
            "important.log": "needed\n",
            "build_out/x.txt": "x\n",
            "nested/build_out/y.txt": "y\n",
        }
    )
    profile = profile_repository(root)
    assert paths_of(profile) == {".gitignore", "keep.py", "important.log"}
    assert profile.summary.ignored_by_gitignore == 3  # debug.log, two build_out dirs


def test_nested_gitignore_reincludes_within_its_scope(write_tree):
    root = write_tree(
        {
            ".gitignore": "*.md\n",
            "docs/.gitignore": "!README.md\n",
            "notes.md": "bye\n",
            "docs/README.md": "hello\n",
            "docs/guide.md": "guide\n",
        }
    )
    profile = profile_repository(root)
    paths = paths_of(profile)
    assert "docs/README.md" in paths  # deeper .gitignore re-includes within docs/
    assert "notes.md" not in paths  # root rule still applies outside docs/
    assert "docs/guide.md" not in paths  # negation only matched README.md


def test_anchor_and_doublestar_rules(write_tree):
    root = write_tree(
        {
            ".gitignore": "/top_only.txt\n**/deep/*.gen\n",
            "top_only.txt": "root\n",
            "sub/top_only.txt": "nested-kept\n",
            "a/deep/x.gen": "x\n",
            "deep/y.gen": "y\n",
            "keep.py": "k\n",
        }
    )
    profile = profile_repository(root)
    assert paths_of(profile) == {".gitignore", "keep.py", "sub/top_only.txt"}


def test_git_backend_matches_filesystem_backend(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    (tmp_path / ".gitignore").write_text("*.log\nnode_modules/\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "README.md").write_text("doc\n")
    (tmp_path / "debug.log").write_text("noise\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("j\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    (tmp_path / "src" / "untracked.py").write_text("u = 2\n")  # relevant untracked
    (tmp_path / "uncommitted.log").write_text("ignored untracked\n")

    git_profile = profile_repository(tmp_path, backend="git")
    fs_profile = profile_repository(tmp_path, backend="filesystem")
    assert git_profile.stats.backend == "git"
    assert paths_of(git_profile) == paths_of(fs_profile)
    assert paths_of(git_profile) == {
        ".gitignore",
        "README.md",
        "src/app.py",
        "src/untracked.py",
    }
    assert git_profile.fingerprint() == fs_profile.fingerprint()


def test_untracked_relevant_files_included_in_git_repo(tmp_path):
    (tmp_path / "tracked.py").write_text("a = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    (tmp_path / "fresh.py").write_text("b = 2\n")
    profile = profile_repository(tmp_path)
    assert paths_of(profile) == {"tracked.py", "fresh.py"}
    assert profile.is_git_repository is True


def test_git_failure_falls_back_to_filesystem(tmp_path):
    # A junk .git directory makes the auto backend try git; git fails on it.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "bogus").write_text("not a repo\n")
    (tmp_path / "app.py").write_text("x = 1\n")
    profile = profile_repository(tmp_path)
    assert profile.stats.backend == "filesystem"
    assert any("git inventory unavailable" in warning for warning in profile.warnings)
    assert paths_of(profile) == {"app.py"}  # built-in excludes keep .git out


def test_max_files_bound_truncates(write_tree):
    spec = {f"gen/dir{i:02d}/file{i:02d}.py": "x = 1\n" for i in range(30)}
    root = write_tree(spec)
    profile = profile_repository(root, InventoryLimits(max_files=10))
    assert profile.summary.truncated is True
    assert profile.summary.truncation_reasons == ("max_files",)
    assert len(profile.files) == 10
    # Deterministic truncation: the first 10 paths in sorted order.
    assert [entry.path for entry in profile.files] == sorted(spec)[:10]


def test_max_depth_bound_skips_deep_dirs_only(write_tree):
    root = write_tree({"a/b/c/d/deep.py": "d\n", "shallow.py": "s\n", "a/mid.py": "m\n"})
    profile = profile_repository(root, InventoryLimits(max_depth=2))
    assert paths_of(profile) == {"shallow.py", "a/mid.py"}
    assert profile.summary.truncated is True
    assert profile.summary.truncation_reasons == ("max_depth",)


def test_deadline_bound_truncates(write_tree):
    root = write_tree({f"f{i}.py": "x\n" for i in range(5)})
    profile = profile_repository(root, InventoryLimits(deadline_seconds=0.0))
    assert profile.summary.truncated is True
    assert "deadline" in profile.summary.truncation_reasons


@pytest.mark.skipif(os.name != "posix", reason="symlinks not available")
def test_symlinks_recorded_but_never_followed(write_tree):
    root = write_tree({"app.py": "x = 1\n", "real/data.txt": "data\n"})
    os.symlink(root / "app.py", root / "link.py")
    os.symlink(root / "real", root / "dirlink")
    (root / "real" / "through_link.txt").write_text("reachable directly, not via link\n")
    profile = profile_repository(root)
    paths = paths_of(profile)
    assert "link.py" in paths  # recorded like git records symlinks
    assert "dirlink" in paths  # the link itself, not its contents
    assert "dirlink/through_link.txt" not in paths
    assert "real/data.txt" in paths
    assert "real/through_link.txt" in paths


@pytest.mark.skipif(os.geteuid() == 0, reason="permission tests fail as root")
def test_unreadable_directory_becomes_warning(write_tree):
    root = write_tree({"ok.py": "x = 1\n", "locked/secret.py": "s\n"})
    (root / "locked").chmod(0o000)
    try:
        profile = profile_repository(root)
        assert "ok.py" in paths_of(profile)
        assert any("locked" in warning for warning in profile.warnings)
    finally:
        (root / "locked").chmod(0o755)
