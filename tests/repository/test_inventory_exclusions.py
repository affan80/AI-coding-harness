"""Issue #30: the full exclusion matrix and bounded-traversal boundaries.

Complements the existing inventory tests by pinning every category the
acceptance criteria name — generated, dependency, cache, run, and VCS
directories — including members of those categories that earlier tests did
not exercise, and proving the traversal stays bounded on pathological trees.
"""

from pathlib import Path

from harness.repository import InventoryLimits, build_inventory
from tests.conftest import make_file


def _paths(result) -> set[str]:
    return {entry.path for entry in result.files}


def test_every_exclusion_category_is_enforced(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    # One representative per accepted category, plus members that earlier
    # tests did not cover.
    categories: dict[str, list[str]] = {
        "generated": ["__pycache__/a.pyc", "app/__pycache__/b.pyc",
                      "module.pyc", "project.egg-info/PKG-INFO"],
        "dependency": ["node_modules/pkg/index.js", "vendor/lib.js",
                       "packages/app/node_modules/deep.js"],
        "cache": [".pytest_cache/v/cache", ".mypy_cache/0.9", ".ruff_cache/1.0",
                  ".cache/entries", ".coverage"],
        "run": ["runs/s-1/events.jsonl", "artifacts/tool-0001-output.txt"],
        "vcs": [".git/HEAD", ".svn/wc.db", ".hg/store"],
        "os-noise": [".DS_Store", "Thumbs.db"],
    }
    expected: set[str] = set()
    index = 0
    for category, members in categories.items():
        for member in members:
            make_file(root, member, f"{category}-{index}\n")
            index += 1
        # every category keeps at least one tracked neighbour
        make_file(root, f"src/kept_{category}.py", "x = 1\n")
        expected.add(f"src/kept_{category}.py")

    result = build_inventory(root, InventoryLimits())

    paths = _paths(result)
    assert paths == expected
    assert not result.truncated
    for members in categories.values():
        for member in members:
            assert member not in paths


def test_traversal_is_bounded_by_file_cap_even_with_deep_trees(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    deep = "d/" * 40 + "deep.txt"  # far beyond any sane depth bound
    make_file(root, deep)
    make_file(root, "shallow.txt")

    result = build_inventory(root, InventoryLimits(max_depth=5, max_files=10))

    assert "shallow.txt" in _paths(result)
    assert deep not in _paths(result)
    # The deep file is omitted by the depth bound — recorded as a truncation
    # reason, distinct from the file cap.
    assert "max_depth" in result.truncation_reasons


def test_traversal_is_bounded_by_file_cap_across_wide_trees(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(50):
        make_file(root, f"w{index}.txt")

    result = build_inventory(root, InventoryLimits(max_files=10))

    assert len(result.files) == 10
    assert result.truncated
    # The walk aborts at the cap on purpose (bounded traversal): the exact
    # total of the remaining files is unknowable, and the truncation reason
    # is the honest record of that bound.
    assert "max_files" in result.truncation_reasons
