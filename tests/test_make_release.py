"""Issue #80: release notes, deterministic archive, and checksum verification."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.make_release import (  # noqa: E402
    build_archive,
    generate_notes,
    prepare_release,
    verify_archive,
    write_notes,
)


def test_notes_document_setup_demos_and_limitations(tmp_path):
    notes = write_notes(tmp_path, "v0.1.0")
    text = notes.read_text(encoding="utf-8")

    assert text.startswith("# ai-coding-harness v0.1.0")
    assert "## Setup" in text and "pip install -e" in text
    assert "python -m demos.run_demo --scenario all" in text
    assert "python -m scripts.release_checks" in text
    assert "## Known limitations" in text
    assert "HARNESS_MODEL_PROVIDER" in text  # honest limitation is stated
    assert "## Verification" in text and "PASSED" in text


def test_notes_include_git_history(tmp_path):
    text = generate_notes("v9.9.9")
    assert "## Shipped (recent history)" in text
    assert text.count("\n- ") >= 3  # recent commits listed


def test_archive_builds_with_matching_checksum(tmp_path):
    bundle = build_archive("v0.1.0-test", tmp_path / "dist")

    assert bundle.archive.is_file()
    assert bundle.checksum.read_text().endswith(
        f"{bundle.archive.name}\n"
    )
    import hashlib

    assert (
        hashlib.sha256(bundle.archive.read_bytes()).hexdigest()
        == bundle.sha256
    )
    # the archive contains the harness package under the prefix
    import tarfile

    with tarfile.open(bundle.archive) as tar:
        names = tar.getnames()
    assert any("ai-coding-harness/harness/core/models.py" in n for n in names)
    assert not any("__pycache__" in n for n in names)  # source-only


def test_archive_is_reproducible(tmp_path):
    bundle = build_archive("v0.1.0-test", tmp_path / "first")
    write_notes(tmp_path, "v0.1.0-test")  # the bundle includes release notes
    ok, detail = verify_archive(bundle, tmp_path)

    assert ok, detail
    assert "reproducible=True" in detail and "checksum_ok=True" in detail


def test_prepare_release_produces_the_full_bundle(tmp_path):
    bundle, notes, (ok, detail) = prepare_release("v0.1.0-rc1", tmp_path)

    assert bundle.archive.is_file() and bundle.checksum.is_file()
    assert notes.is_file()
    assert ok, detail


def test_verify_detects_a_tampered_checksum(tmp_path):
    bundle = build_archive("v0.1.0-test", tmp_path / "dist")
    bundle.checksum.write_text(
        "0" * 64 + f"  {bundle.archive.name}\n", encoding="utf-8"
    )
    ok, detail = verify_archive(bundle, tmp_path)

    assert not ok
    assert "checksum_ok=False" in detail


def test_tagging_is_documented_as_manual_and_gated():
    source = Path(generate_notes.__globals__["__file__"]).read_text()
    assert "human decision" in source or "must only ever be pushed" in source
    assert "release_checks" in source  # the gate is named in the module
