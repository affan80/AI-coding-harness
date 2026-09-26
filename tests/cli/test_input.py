"""Issue #27: CLI arguments and interactive input produce the same shapes."""

import argparse
import json

import pytest

from harness.cli.input import (
    InputError,
    build_parser,
    gather_request,
    validate_inputs,
)
from harness.core.models import Budget, UserRequest


def _args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def test_non_interactive_invocation_builds_validated_shapes(tmp_path):
    args = _args([
        str(tmp_path), "Fix the login bug and add tests",
        "--scope", "backend/", "--scope", "tests/",
        "--write-policy", "confirm",
        "--verification-depth", "targeted",
        "--max-model-calls", "10",
        "--timeout", "30",
    ])
    request, budget, resolved = gather_request(args)

    assert isinstance(request, UserRequest)
    assert request.objective == "Fix the login bug and add tests"
    assert request.repository_path == str(tmp_path.resolve())
    assert request.scope_paths == ("backend/", "tests/")
    assert "write-policy:confirm" in request.constraints
    assert "verification-depth:targeted" in request.constraints
    assert isinstance(budget, Budget)
    assert budget.max_model_calls == 10
    assert budget.command_timeout_seconds == 30


def test_interactive_prompts_produce_the_same_shapes(tmp_path):
    args = _args([])  # nothing supplied: everything is prompted
    answers = iter([str(tmp_path), "Audit the auth module"])

    def scripted_prompt(label: str) -> str:
        return next(answers)

    request, budget, resolved = gather_request(args, prompt=scripted_prompt)
    assert request.objective == "Audit the auth module"
    assert request.repository_path == str(tmp_path.resolve())
    assert budget == Budget()  # defaults when the user accepts them

    # identical inputs through either path produce identical request shapes
    # (request_id is freshly generated per request and excluded)
    args2 = _args([str(tmp_path), "Audit the auth module"])
    request2, _, _ = gather_request(args2)

    def without_id(r):
        return {k: v for k, v in r.to_dict().items() if k != "request_id"}

    assert without_id(request2) == without_id(request)


def test_invalid_repository_path_is_actionable(tmp_path):
    with pytest.raises(InputError) as excinfo:
        validate_inputs(
            tmp_path / "does-not-exist", "obj",
            max_model_calls=5, max_iterations=5, max_retries_per_goal=1,
            max_audit_rounds=1, timeout=10,
        )
    assert "does not exist" in excinfo.value.message
    assert excinfo.value.details["field"] == "repository"

    a_file = tmp_path / "file.txt"
    a_file.write_text("not a directory")
    with pytest.raises(InputError) as excinfo:
        validate_inputs(
            str(a_file), "obj",
            max_model_calls=5, max_iterations=5, max_retries_per_goal=1,
            max_audit_rounds=1, timeout=10,
        )
    assert "not a directory" in excinfo.value.message


@pytest.mark.parametrize("field", [
    "max_model_calls", "max_iterations", "max_retries_per_goal",
    "max_audit_rounds",
])
def test_invalid_limits_name_the_offending_flag(tmp_path, field):
    kwargs = dict(
        max_model_calls=5, max_iterations=5, max_retries_per_goal=1,
        max_audit_rounds=1, timeout=10,
    )
    kwargs[field] = 0
    with pytest.raises(InputError) as excinfo:
        validate_inputs(str(tmp_path), "obj", **kwargs)
    assert field.replace("_", "-") in excinfo.value.message
    with pytest.raises(InputError):
        validate_inputs(str(tmp_path), "obj", **{**kwargs, field: -3})


def test_invalid_timeout_and_objective_are_rejected(tmp_path):
    base = dict(
        max_model_calls=5, max_iterations=5, max_retries_per_goal=1,
        max_audit_rounds=1,
    )
    with pytest.raises(InputError):
        validate_inputs(str(tmp_path), "obj", **base, timeout=0)
    with pytest.raises(InputError) as excinfo:
        validate_inputs(str(tmp_path), "   ", **base, timeout=10)
    assert "objective" in excinfo.value.message


def test_env_overrides_support_scripted_automation(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_REPO", str(tmp_path))
    monkeypatch.setenv("HARNESS_OBJECTIVE", "fix issue #123")
    request, _, _ = gather_request(_args([]))
    assert request.objective == "fix issue #123"


def test_user_request_serializes_for_the_run_directory(tmp_path):
    request, _, _ = gather_request(_args([str(tmp_path), "obj"]))
    payload = request.to_dict()
    assert json.loads(json.dumps(payload)) == payload
