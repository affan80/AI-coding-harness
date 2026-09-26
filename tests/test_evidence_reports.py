"""Issue #26 — patch and verification evidence artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from harness.core.models import SessionStatus, UserRequest
from harness.telemetry import RunStore

_AUTH_DIFF = """diff --git a/app/auth.py b/app/auth.py
--- a/app/auth.py
+++ b/app/auth.py
@@ -1,3 +1,4 @@
+import logging
 def login(user):
     return user
"""


def _store(tmp_path: Path) -> RunStore:
    return RunStore.start(
        UserRequest(repository_path="/tmp/sample", objective="Fix login"),
        runs_root=tmp_path / "runs",
    )


def test_patches_accumulate_with_labeled_headers(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.append_patch(_AUTH_DIFF, label="patch app/auth.py")
    store.append_patch(
        "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+new\n",
        label="second file",
    )

    text = (store.run_dir / "patches.diff").read_text()
    assert text.count("--- patch: ") == 2
    assert "patch app/auth.py" in text
    assert "+++ b/app/auth.py" in text
    assert "+++ b/b.py" in text
    # Raw diffs are stored verbatim; no JSON line wrappers.
    assert not text.lstrip().startswith("{")


def test_verification_report_persists(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = {
        "status": "pass",
        "checks": [
            {"name": "target-tests", "command": "pytest -q", "exit_code": 0, "ok": True},
        ],
        "notes": [],
    }

    store.write_verification(report)

    restored = json.loads((store.run_dir / "verification.json").read_text())
    assert restored == report


def test_reviewer_reconstructs_outcome_from_run_directory_alone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_document(
        "goals.json",
        {"goals": [{"id": "G1", "description": "handle invalid password"}]},
    )
    store.record_tool_call(
        name="apply_patch", args_summary="app/auth.py", status="ok",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=7,
    )
    store.append_patch(_AUTH_DIFF, label="handle invalid password")
    store.write_verification(
        {
            "status": "pass",
            "checks": [
                {"name": "target-tests", "command": "pytest tests/test_auth.py -q",
                 "exit_code": 0, "ok": True, "summary": "2 passed"},
                {"name": "full-suite", "command": "pytest -q",
                 "exit_code": 1, "ok": False, "summary": "1 failed in test_reports"},
            ],
            "notes": ["full suite regression pending replan"],
        }
    )
    store.finalize(SessionStatus.PARTIAL, stop_reason="full-suite regression")

    run_dir = store.run_dir
    session = json.loads((run_dir / "session.json").read_text())
    diff_text = (run_dir / "patches.diff").read_text()
    verification = json.loads((run_dir / "verification.json").read_text())

    assert session["status"] == "partial"
    changed_files = sorted(
        line.split(" b/", 1)[1]
        for line in diff_text.splitlines()
        if line.startswith("+++ b/")
    )
    assert changed_files == ["app/auth.py"]
    assert [check["name"] for check in verification["checks"]] == [
        "target-tests",
        "full-suite",
    ]
    assert verification["checks"][1]["exit_code"] == 1


def test_failed_session_without_patches_keeps_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.record_tool_call(
        name="run_command", args_summary="pytest -q", status="error",
        started_at="2026-09-27T12:00:00+00:00", duration_ms=400,
        summary="2 failed",
    )
    store.write_verification(
        {
            "status": "fail",
            "checks": [
                {"name": "full-suite", "command": "pytest -q",
                 "exit_code": 2, "ok": False, "summary": "collection error"},
            ],
            "notes": ["baseline already failing before any patch"],
        }
    )
    store.finalize(SessionStatus.FAILED, stop_reason="baseline verification failed")

    assert not (store.run_dir / "patches.diff").exists()
    session = json.loads((store.run_dir / "session.json").read_text())
    verification = json.loads((store.run_dir / "verification.json").read_text())
    assert session["status"] == "failed"
    assert verification["status"] == "fail"
    tools = [
        json.loads(line)
        for line in (store.run_dir / "tool-calls.jsonl").read_text().splitlines()
    ]
    assert tools[0]["status"] == "error"
