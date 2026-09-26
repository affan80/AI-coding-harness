"""Issue #26: patch and verification evidence reconstructable from the run dir."""

import json
import os
from pathlib import Path

import pytest

from harness.core.models import SessionStatus, UserRequest
from harness.telemetry.store import RunStore, reconstruct_run


@pytest.fixture()
def store(tmp_path: Path) -> RunStore:
    request = UserRequest(objective="fix the login bug", repository_path="/repo")
    return RunStore.start(request, runs_root=tmp_path / "runs")


def test_verified_session_leaves_reconstructable_evidence(store: RunStore):
    store.append_patch(
        "--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1 @@\n-raise 500\n+return 401\n",
        label="fix login status",
    )
    store.record_changed_files(["app/auth.py", "tests/test_auth.py"])
    store.write_verification({
        "status": "pass",
        "checks": [
            {"name": "targeted", "command": "pytest tests/test_auth.py -q",
             "exit_code": 0, "ok": True, "summary": "3 passed"},
            {"name": "full", "command": "pytest -q",
             "exit_code": 0, "ok": True, "summary": "142 passed"},
        ],
        "notes": [],
    })
    store.finalize(SessionStatus.VERIFIED)

    evidence = reconstruct_run(store.run_dir)
    assert evidence["status"] == "verified"
    assert evidence["changed_files"] == ["app/auth.py", "tests/test_auth.py"]
    assert evidence["patch_count"] == 1
    assert evidence["patch_labels"][0].startswith("fix login status")
    assert evidence["verification_status"] == "pass"
    assert [c["name"] for c in evidence["checks"]] == ["targeted", "full"]
    assert all(c["ok"] for c in evidence["checks"])


def test_partial_and_failed_sessions_keep_their_evidence(tmp_path: Path):
    request = UserRequest(objective="add rbac", repository_path="/repo")
    store = RunStore.start(request, runs_root=tmp_path / "runs")
    store.append_patch("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n",
                       label="partial edit")
    store.record_changed_files(["app.py"])
    store.write_verification({
        "status": "fail",
        "checks": [{"name": "full", "command": "pytest -q", "exit_code": 1,
                    "ok": False, "summary": "3 failed, 139 passed"}],
        "notes": ["retry budget exhausted"],
    })
    store.finalize(SessionStatus.FAILED, stop_reason="VERIFICATION_FAILED")

    evidence = reconstruct_run(store.run_dir)
    assert evidence["status"] == "failed"
    assert evidence["verification_status"] == "fail"
    assert evidence["checks"][0]["ok"] is False
    assert evidence["checks"][0]["exit_code"] == 1
    assert evidence["changed_files"] == ["app.py"]


def test_patches_diff_contains_inspectable_minimal_diffs(store: RunStore):
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    store.append_patch(diff, label="one")
    store.append_patch(diff, label="two")
    text = (store.run_dir / "patches.diff").read_text(encoding="utf-8")
    assert text.count("--- patch: one") == 1
    assert text.count("--- patch: two") == 1
    assert "+b" in text


def test_reconstruction_degrades_gracefully_on_missing_documents(tmp_path: Path):
    evidence = reconstruct_run(tmp_path / "empty-run")
    assert evidence["changed_files"] == []
    assert evidence["checks"] == []
    assert evidence["verification_status"] == "not_run"
    assert evidence["status"] == ""


def test_interrupted_verification_write_keeps_previous_valid_state(
    store: RunStore, monkeypatch
):
    good = {"status": "pass", "checks": [], "notes": []}
    store.write_verification(good)
    previous = json.loads(
        (store.run_dir / "verification.json").read_text(encoding="utf-8")
    )

    # A crash mid-write leaves a stray temp file but never corrupts the
    # previous valid document.
    def exploding_write(path: Path, content: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content)
        raise OSError("interrupted")

    monkeypatch.setattr("harness.telemetry.store._atomic_write", exploding_write)
    with pytest.raises(OSError):
        store.write_verification({"status": "fail", "checks": [], "notes": []})

    current = json.loads(
        (store.run_dir / "verification.json").read_text(encoding="utf-8")
    )
    assert current == previous  # previous valid state survived
    assert (store.run_dir / "verification.json.tmp").exists()  # partial temp only


def test_run_artifacts_are_gitignored(tmp_path: Path):
    request = UserRequest(objective="obj", repository_path="/repo")
    store = RunStore.start(request, runs_root=tmp_path / "runs")
    gitignore = (Path(store.run_dir).parent / ".gitignore").read_text()
    assert "*" in gitignore.splitlines()
    assert "!.gitignore" in gitignore.splitlines()
    assert os.path.isfile(store.run_dir / "session.json")
