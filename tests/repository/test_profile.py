"""Profile-level behavior: empty repositories, scale bounds, stability, serialization.

Covers sub-issue #32 (profile empty and large repositories efficiently) plus the
parent acceptance criterion that RepositoryProfile is stable for unchanged input.
"""

from __future__ import annotations

import json
import shutil

import pytest

from harness.contracts.repository import InventoryLimits
from harness.repository import profile_repository


def test_empty_directory_profiles_cleanly(tmp_path):
    profile = profile_repository(tmp_path)
    assert profile.empty is True
    assert profile.summary.total_files == 0
    assert profile.summary.total_bytes == 0
    assert profile.files == ()
    assert profile.manifests == ()
    assert profile.warnings == ()
    assert profile.fingerprint()  # non-empty stable hash
    assert profile.fingerprint() == profile_repository(tmp_path).fingerprint()


def test_empty_git_repository_profiles_cleanly(tmp_path):
    import subprocess

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    profile = profile_repository(tmp_path)
    assert profile.empty is True
    assert profile.is_git_repository is True
    assert profile.stats.backend == "git"


def test_fingerprint_stable_for_unchanged_input(write_tree):
    spec = {
        "pyproject.toml": "[project]\nname = \"stable\"\n",
        "src/app.py": "print('hi')\n",
        "tests/test_app.py": "def test_ok(): pass\n",
    }
    root_a = write_tree(spec)
    first = profile_repository(root_a)
    second = profile_repository(root_a)
    assert first.fingerprint() == second.fingerprint()

    # Identical tree at a different root: same fingerprint (root_path is volatile).
    root_b = root_a.parent / "copy_of_repo"
    shutil.copytree(root_a, root_b)
    third = profile_repository(root_b)
    assert first.fingerprint() == third.fingerprint()
    assert first.root_path != third.root_path  # volatile field still recorded


def test_fingerprint_changes_when_inventory_changes(write_tree):
    root = write_tree({"app.py": "x = 1\n"})
    before = profile_repository(root)
    (root / "extra.py").write_text("y = 2\n")
    after = profile_repository(root)
    assert before.fingerprint() != after.fingerprint()
    # L0 metadata cannot see equal-size content edits (no body reads by design);
    # it does see size changes.
    (root / "extra.py").write_text("y = 22\n")
    assert after.fingerprint() != profile_repository(root).fingerprint()


def test_serialization_round_trip_preserves_fingerprint(write_tree):
    root = write_tree(
        {
            "pyproject.toml": "[project]\nname = \"wire\"\ndependencies = [\"fastapi\"]\n",
            "src/app.py": "1\n",
            "package.json": json.dumps({"name": "web", "scripts": {"test": "vitest"}}),
        }
    )
    profile = profile_repository(root)
    wire = json.loads(json.dumps(profile.to_dict()))
    restored = type(profile).from_dict(wire)
    assert restored.fingerprint() == profile.fingerprint()
    assert restored == profile  # full value equality, not just the hash


def test_from_dict_rejects_unknown_schema(write_tree):
    root = write_tree({"app.py": "1\n"})
    profile = profile_repository(root)
    payload = profile.to_dict()
    payload["schema_version"] = 999
    from harness.contracts.repository import ProfileContractError

    with pytest.raises(ProfileContractError):
        type(profile).from_dict(payload)


def _build_large_tree(root, dirs: int, files_per_dir: int):
    for d in range(dirs):
        directory = root / f"pkg/mod{d:03d}"
        directory.mkdir(parents=True)
        for f in range(files_per_dir):
            (directory / f"mod_{f:03d}.py").write_text(f"x = {d * files_per_dir + f}\n")
    deps = root / "node_modules" / "bulk"
    deps.mkdir(parents=True)
    for i in range(200):
        (deps / f"dep_{i:03d}.js").write_text("module.exports = 1;\n")


def test_large_tree_profiled_without_reading_bodies(tmp_path):
    _build_large_tree(tmp_path, dirs=40, files_per_dir=50)  # 2000 source files
    profile = profile_repository(tmp_path)
    assert profile.summary.total_files == 2000
    assert profile.empty is False
    assert not profile.summary.truncated
    # No source body was read: only .gitignore (none here) and manifests (none here).
    assert profile.stats.file_bodies_read == 0
    # node_modules never entered the candidate set.
    assert not any(entry.path.startswith("node_modules") for entry in profile.files)
    excluded = {rule.name: rule.hits for rule in profile.summary.excluded_dirs}
    assert excluded["node_modules"] == 1
    # Totals are computed from metadata, not bodies.
    assert profile.summary.total_bytes > 0
    assert profile.summary.text_files == 2000
    assert profile.summary.binary_files == 0
    python_stat = next(s for s in profile.summary.languages if s.language == "python")
    assert python_stat.file_count == 2000
    # Largest-file ranking works off metadata.
    assert len(profile.summary.largest_files) == 10
    sizes = [entry.size_bytes for entry in profile.summary.largest_files]
    assert sizes == sorted(sizes, reverse=True)


def test_large_tree_profiles_quickly(tmp_path):
    import time

    _build_large_tree(tmp_path, dirs=40, files_per_dir=50)
    started = time.monotonic()
    profile_repository(tmp_path)
    elapsed = time.monotonic() - started
    assert elapsed < 10.0, f"profile took {elapsed:.2f}s"


def test_large_tree_respects_max_files(tmp_path):
    _build_large_tree(tmp_path, dirs=40, files_per_dir=50)
    profile = profile_repository(tmp_path, InventoryLimits(max_files=100))
    assert len(profile.files) == 100
    assert profile.summary.truncated is True
    assert profile.summary.truncation_reasons == ("max_files",)


def test_profiler_rejects_missing_root(tmp_path):
    from harness.repository import RepositoryProfileError

    with pytest.raises(RepositoryProfileError) as excinfo:
        profile_repository(tmp_path / "does-not-exist")
    assert excinfo.value.reason == "root-not-found"

    file_path = tmp_path / "afile.txt"
    file_path.write_text("x")
    with pytest.raises(RepositoryProfileError) as excinfo:
        profile_repository(file_path)
    assert excinfo.value.reason == "root-not-a-directory"


def test_profiler_rejects_unknown_backend():
    from harness.repository import RepositoryProfiler

    with pytest.raises(ValueError):
        RepositoryProfiler(backend="rsync")
