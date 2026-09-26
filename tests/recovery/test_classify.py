"""Issue #63: failure classification and focused evidence collection."""

from harness.recovery.classify import FailureKind, classify_failure


def test_timeout_is_distinct_from_code_defects():
    result = classify_failure(
        command="pytest -q", exit_code=124, timed_out=True,
        output="command exceeded timeout of 120s and was killed",
        stage="full_suite",
    )
    assert result.kind is FailureKind.TIMEOUT
    assert result.is_environment is False
    assert result.to_dict()["metadata"]["timed_out"] is True
    assert result.stage == "full_suite"


def test_missing_tool_is_environment_not_code_defect():
    result = classify_failure(
        command="ruff check .", exit_code=None, ran=False,
        output="executable not found: ruff",
    )
    assert result.kind is FailureKind.ENVIRONMENT
    assert result.is_environment is True
    assert "could not run" in result.summary


def test_missing_third_party_module_is_environment():
    result = classify_failure(
        command="pytest -q", exit_code=1,
        output=(
            "Traceback (most recent call last):\n"
            "ModuleNotFoundError: No module named 'httpx'\n"
        ),
    )
    assert result.kind is FailureKind.ENVIRONMENT
    assert result.is_environment is True
    assert result.missing_module == "httpx"


def test_syntax_patch_and_test_failures_are_code_defects():
    syntax = classify_failure(
        command="python -m compileall .", exit_code=1,
        output="SyntaxError: invalid syntax (app.py, line 3)",
    )
    assert syntax.kind is FailureKind.SYNTAX
    assert syntax.is_environment is False

    patch = classify_failure(
        command="apply_patch", exit_code=1,
        output="error: patch does not apply",
    )
    assert patch.kind is FailureKind.PATCH

    test = classify_failure(
        command="pytest -q", exit_code=1,
        output="FAILED tests/test_auth.py::test_bad_password - AssertionError: 500 != 401",
    )
    assert test.kind is FailureKind.TEST
    assert "500 != 401" in test.focused_output


def test_focused_output_excludes_unrelated_history():
    noisy = "\n".join(
        [f"unrelated log line {i}: all good here" for i in range(200)]
        + ["FAILED tests/test_auth.py::test_bad_password - assert 500 == 401",
           "1 failed, 139 passed in 2.1s"]
    )
    result = classify_failure(
        command="pytest -q", exit_code=1, output=noisy,
    )
    assert "unrelated log line 0" not in result.focused_output
    assert "unrelated log line 150" not in result.focused_output
    assert "FAILED tests/test_auth.py" in result.focused_output
    assert len(result.focused_output.splitlines()) <= 12
    assert all(len(line) <= 240 for line in result.focused_output.splitlines())


def test_exit_zero_is_not_a_failure_and_unknown_is_honest():
    ok = classify_failure(command="pytest -q", exit_code=0, output="all green")
    assert ok.kind is FailureKind.NONE

    mystery = classify_failure(command="make", exit_code=7, output="some odd output")
    assert mystery.kind is FailureKind.UNKNOWN
    assert mystery.is_environment is False


def test_serializable_for_the_run_directory():
    result = classify_failure(
        command="pytest -q", exit_code=1,
        output="FAILED tests/test_auth.py - AssertionError",
        stage="targeted_tests",
    )
    import json

    payload = result.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["kind"] == "test"
