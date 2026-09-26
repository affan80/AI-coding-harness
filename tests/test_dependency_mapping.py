"""Issue #44: dependency edges and test-to-source mapping, with reasons."""

from pathlib import Path

from harness.repository.mapping import map_dependencies_and_tests


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text(
        "from helpers import verify_password\n"
        "import billing\n"
        "\n"
        "def login(user, password):\n"
        "    return billing.charge(user) and verify_password(password)\n"
    )
    (root / "helpers.py").write_text(
        "def verify_password(password):\n    return bool(password)\n"
    )
    (root / "billing.py").write_text(
        "def charge(user):\n    return True\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text(
        "from app import login\n"
        "\n"
        "def test_login():\n    assert login('u', 'p')\n"
    )
    # ignored/generated content must never enter the mapping
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "app.cpython-313.pyc").write_text("junk")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "pkg.js").write_text("var x = 1;\n")
    return root


def test_import_edges_resolve_to_repository_files(tmp_path):
    root = _make_repo(tmp_path)
    mapping = map_dependencies_and_tests(root)

    app_imports = mapping.imports_by_file["app.py"]
    assert "helpers.py" in app_imports
    assert "billing.py" in app_imports
    relations = [r for r in mapping.relations if r.kind == "import"]
    assert any(
        r.source == "app.py" and r.target == "helpers.py"
        and "imports 'helpers'" in r.reason
        for r in relations
    )


def test_from_package_import_resolves_to_module(tmp_path):
    root = tmp_path / "pkgrepo"
    root.mkdir()
    (root / "services").mkdir()
    (root / "services" / "__init__.py").write_text("")
    (root / "services" / "auth.py").write_text("def check():\n    return True\n")
    (root / "main.py").write_text("from services import auth\n\nauth.check()\n")

    mapping = map_dependencies_and_tests(root)

    assert mapping.imports_by_file["main.py"] == ["services/auth.py"]
    assert any(
        r.target == "services/auth.py" and "resolved to services/auth.py" in r.reason
        for r in mapping.relations
    )


def test_tests_map_to_source_by_import_and_name(tmp_path):
    root = _make_repo(tmp_path)
    mapping = map_dependencies_and_tests(root)

    assert mapping.tests_by_source.get("app.py") == ["tests/test_app.py"]
    relations = [r for r in mapping.relations if r.kind == "test_of"]
    assert any(
        r.source == "tests/test_app.py" and r.target == "app.py"
        and r.reason == "test imports app.py"
        for r in relations
    )


def test_unrelated_test_falls_back_to_stem_match(tmp_path):
    root = tmp_path / "stemrepo"
    root.mkdir()
    (root / "invoice.py").write_text("def total():\n    return 0\n")
    (root / "test_invoice.py").write_text("def test_total_exists():\n    pass\n")

    mapping = map_dependencies_and_tests(root)

    assert mapping.tests_by_source.get("invoice.py") == ["test_invoice.py"]
    assert any(
        r.reason == "test name matches source stem 'invoice'"
        for r in mapping.relations
    )


def test_ignored_and_generated_content_never_enters_the_mapping(tmp_path):
    root = _make_repo(tmp_path)
    mapping = map_dependencies_and_tests(root)

    all_paths = (
        {r.source for r in mapping.relations}
        | {r.target for r in mapping.relations}
        | set(mapping.imports_by_file)
    )
    assert not any("__pycache__" in p or "node_modules" in p for p in all_paths)
    assert all(p.endswith(".py") for p in all_paths)


def test_empty_repository_yields_empty_mapping(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()

    mapping = map_dependencies_and_tests(root)

    assert mapping.relations == []
    assert mapping.imports_by_file == {}
    assert mapping.tests_by_source == {}
    assert mapping.to_dict() == {
        "relations": [], "imports_by_file": {}, "tests_by_source": {},
    }
