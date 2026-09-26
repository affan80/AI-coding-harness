"""Issue #43: symbol declarations and likely references with lexical fallback.

Resolution must work even when optional tooling (git) is absent — the
lexical filesystem fallback has to carry the whole flow, with distinct
match reasons for declarations, exact fallbacks, and references.
"""

from pathlib import Path

from harness.repository.discovery import DiscoveryEngine


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"  # deliberately NOT a git repository
    root.mkdir()
    (root / "auth.py").write_text(
        "class AuthService:\n"
        "    def authenticate(self, user, password):\n"
        "        return verify_password(password)\n"
    )
    (root / "routes.py").write_text(
        "from auth import AuthService\n"
        "\n"
        "service = AuthService()\n"
        "ok = service.authenticate('amy', 'pw')\n"
    )
    (root / "helpers.py").write_text(
        "def verify_password(password):\n"
        "    return bool(password)\n"
    )
    return root


def test_symbol_resolves_to_its_declaration_without_git(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    engine = DiscoveryEngine(str(tmp_path / "repo"))

    results = engine.symbol_search("AuthService")

    declarations = [r for r in results if "Symbol declaration" in r.match_reason]
    assert declarations, "declaration must be found via the lexical path"
    assert declarations[0].path == "auth.py"
    assert declarations[0].line_start == 1
    assert declarations[0].evidence_ref.startswith("search-")


def test_reference_lookup_finds_likely_callers(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    engine = DiscoveryEngine(str(tmp_path / "repo"))

    results = engine.reference_lookup("authenticate")

    assert results, "callers in routes.py must be found"
    paths = {r.path for r in results}
    assert "routes.py" in paths
    assert all("Reference to" in r.match_reason for r in results)
    # the call site is a bounded one-line range with evidence
    call = next(r for r in results if "service.authenticate" in r.context)
    assert call.line_start == call.line_end == 4
    assert call.evidence_ref.startswith("search-")


def test_symbol_fallback_marks_exact_matches_when_no_declaration_exists(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    engine = DiscoveryEngine(str(tmp_path / "repo"))

    results = engine.symbol_search("verify_password")

    # verify_password is declared with `def`, so it resolves as a declaration
    # in helpers.py; routes.py usage comes back as a reference-style match.
    assert any(r.path == "helpers.py" for r in results)
    assert any("Symbol declaration" in r.match_reason for r in results)


def test_fallback_used_when_git_is_not_installed(tmp_path, monkeypatch) -> None:
    _make_repo(tmp_path)
    """Optional tooling absent: the lexical fallback still resolves everything."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    engine = DiscoveryEngine(str(tmp_path / "repo"))

    declarations = engine.symbol_search("AuthService")
    references = engine.reference_lookup("authenticate")

    assert any("Symbol declaration" in r.match_reason for r in declarations)
    assert any(r.path == "routes.py" for r in references)


def test_unknown_symbol_degrades_to_empty_results(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    engine = DiscoveryEngine(str(tmp_path / "repo"))

    assert engine.symbol_search("NeverDefinedAnywhere") == []
    assert engine.reference_lookup("NeverDefinedAnywhere") == []


def test_symbol_results_are_bounded(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(80):
        (root / f"use_{index}.py").write_text("value = authenticate\n")
    (root / "auth.py").write_text("def authenticate():\n    return 1\n")
    engine = DiscoveryEngine(str(tmp_path / "repo"))

    results = engine.reference_lookup("authenticate", max_results=10)

    assert len(results) == 10
    assert results[-1].truncation_status is True
