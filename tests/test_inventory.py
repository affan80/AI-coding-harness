"""Issue #30 — inventory honors built-in ignores and traversal controls."""

from __future__ import annotations

import os
from pathlib import Path

from conftest import make_file

from harness.repository import InventoryOptions, inventory_repository


def _paths(result) -> set[str]:
    return {entry.path for entry in result.entries}


def test_builtin_ignores_never_enter_inventory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for generated in (
        ".git/objects/ab/cd",
        "node_modules/react/index.js",
        "src/node_modules/deep/index.js",
        "__pycache__/mod.cpython-314.pyc",
        "dist/bundle.js",
        "build/output.o",
        ".venv/lib/python.py",
        "runs/abc123/events.jsonl",
        "mod.pyc",
        ".DS_Store",
    ):
        make_file(root, generated)
    make_file(root, "app/main.py")
    make_file(root, "README.md")

    result = inventory_repository(root)

    paths = _paths(result)
    assert paths == {"app/main.py", "README.md"}
    assert result.ignored_paths >= 10
    assert result.ignored_by_pattern["node_modules/"] == 2
    assert not result.truncated


def test_gitignore_matching_and_negation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("*.log\nsecret.txt\n!keep.log\n# comment\n")
    make_file(root, "debug.log")
    make_file(root, "keep.log")
    make_file(root, "secret.txt")
    make_file(root, "app.txt")

    result = inventory_repository(root)

    assert _paths(result) == {"keep.log", "app.txt", ".gitignore"}


def test_gitignore_scoping_anchoring_and_dir_patterns(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("logs/\n/topsecret.txt\ndata/*.tmp\n!data/keep.tmp\n")
    (root / "sub" / ".gitignore").write_text("local.txt\n")
    make_file(root, "logs/run.txt")
    make_file(root, "deep/logs/run.txt")
    make_file(root, "topsecret.txt")
    make_file(root, "sub/topsecret.txt")
    make_file(root, "data/skip.tmp")
    make_file(root, "data/keep.tmp")
    make_file(root, "sub/local.txt")
    make_file(root, "local.txt")

    result = inventory_repository(root)

    paths = _paths(result)
    # "logs/" excludes directories at any depth; "/topsecret.txt" only at root;
    # "data/*.tmp" with negation keeps data/keep.tmp; sub/.gitignore only
    # scopes to sub/.
    assert "deep/logs/run.txt" not in paths
    assert "sub/topsecret.txt" in paths
    assert "data/keep.tmp" in paths
    assert "local.txt" in paths
    assert "sub/local.txt" not in paths
    assert {"topsecret.txt", "data/skip.tmp", "logs/run.txt"}.isdisjoint(paths)


def test_double_star_pattern(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("**/generated/*.py\na/**/snap.bin\n")
    make_file(root, "generated/g.py")
    make_file(root, "x/y/generated/g.py")
    make_file(root, "generated/keep.txt")
    make_file(root, "a/b/c/snap.bin")
    make_file(root, "a/snap.bin")
    make_file(root, "main.py")

    result = inventory_repository(root)

    assert _paths(result) == {"generated/keep.txt", "main.py", ".gitignore"}


def test_symlinked_directories_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "real/inner.py")
    os.symlink(root / "real", root / "link")
    os.symlink(root, root / "selfloop")

    result = inventory_repository(root)

    paths = _paths(result)
    assert "real/inner.py" in paths
    assert "link/inner.py" not in paths
    assert "selfloop/real/inner.py" not in paths
    # the walk terminates despite the self-referencing symlink


def test_file_cap_sets_truncated(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(20):
        make_file(root, f"dir{index % 4}/file{index}.txt")

    result = inventory_repository(root, InventoryOptions(max_files=5))

    assert result.truncated
    assert len(result.entries) == 5
    assert result.total_files == 5


def test_depth_cap_omits_deeper_entries(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "top.txt")
    make_file(root, "level1/one.txt")
    make_file(root, "level1/level2/two.txt")

    result = inventory_repository(root, InventoryOptions(max_depth=1))

    paths = _paths(result)
    assert "top.txt" in paths
    assert "level1/one.txt" in paths
    assert "level1/level2/two.txt" not in paths
    assert result.omitted_by_limit == 1
    assert not result.truncated


def test_inventory_is_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "b/second.py")
    make_file(root, "a/first.py")
    make_file(root, "z.py")

    first = inventory_repository(root)
    second = inventory_repository(root)

    assert first.entries == second.entries
    paths = [entry.path for entry in first.entries]
    assert paths == sorted(paths)


def test_inventory_result_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    make_file(root, "app.py")
    make_file(root, "node_modules/x.js")

    result = inventory_repository(root)
    restored = type(result).from_dict(result.to_dict())

    assert restored == result
