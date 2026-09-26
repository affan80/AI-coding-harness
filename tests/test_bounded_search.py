"""Issue #42: bounded tree + text search with ranges, reasons, and evidence."""

from pathlib import Path

from harness.repository.discovery import DiscoveryEngine


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(120):
        (root / f"module_{index:03d}.py").write_text(f"# filler {index}\n")
    (root / "auth.py").write_text(
        "def authenticate(user, password):\n"
        "    if password != 'secret':\n"
        "        raise PermissionError('no')\n"
        "    return True\n"
    )
    (root / ".gitignore").write_text("module_1*.py\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg.js").write_text("var x = 1;\n")
    return root


def test_text_search_reports_path_range_reason_and_evidence(tmp_path):
    root = _make_repo(tmp_path)
    engine = DiscoveryEngine(root)

    results = engine.text_search("PermissionError")

    assert len(results) == 1
    hit = results[0]
    assert hit.path == "auth.py"
    assert hit.line_start == 3 and hit.line_end == 3  # bounded range
    assert "Text match" in hit.match_reason
    assert hit.evidence_ref.startswith("search-")
    assert hit.truncation_status is False


def test_text_search_is_truncated_at_the_cap_with_status(tmp_path):
    root = _make_repo(tmp_path)
    engine = DiscoveryEngine(root)

    results = engine.text_search("filler", max_results=10)

    assert len(results) == 10
    assert results[-1].truncation_status is True
    assert all(r.evidence_ref == results[0].evidence_ref for r in results)
    # one search call = one evidence reference
    assert results[0].evidence_ref != engine.text_search("filler")[0].evidence_ref


def test_bounded_tree_is_ignore_aware_and_hard_capped(tmp_path):
    root = _make_repo(tmp_path)
    engine = DiscoveryEngine(root)

    tree = engine.bounded_tree(max_entries=50)

    assert tree.total_files == 50
    assert tree.truncated  # 120 files, minus gitignored, still > 50
    assert tree.truncation_reasons
    assert all("node_modules" not in entry for entry in tree.entries)
    assert not any(e.startswith("module_1") for e in tree.entries)  # gitignored
    assert tree.evidence_ref.startswith("search-")


def test_bounded_tree_reports_untruncated_when_under_the_cap(tmp_path):
    root = tmp_path / "small"
    root.mkdir()
    (root / "only.py").write_text("x = 1\n")
    engine = DiscoveryEngine(root)

    tree = engine.bounded_tree()

    assert tree.entries == ["only.py"]
    assert tree.total_files == 1
    assert not tree.truncated
    assert tree.truncation_reasons == []


def test_regex_search_still_works_with_caps(tmp_path):
    root = _make_repo(tmp_path)
    engine = DiscoveryEngine(root)

    results = engine.text_search(r"def \w+\(", is_regex=True, max_results=5)

    assert results
    assert all("authenticate" in r.context or "def" in r.context for r in results)
    assert results[-1].truncation_status in (True, False)
    assert all(r.evidence_ref for r in results)
