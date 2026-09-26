"""Issue #27 — CLI arguments, interactive input, and request validation."""

from __future__ import annotations

import pytest

from tui.cli import CliError, build_parser, collect_request


def _args(argv: list[str]):
    return build_parser().parse_args(argv)


def test_full_flag_set_produces_typed_request(tmp_path) -> None:
    args = _args([
        str(tmp_path), "Fix the login bug",
        "--scope", "backend/auth", "--scope", "tests",
        "--write-policy", "all",
        "--verification-depth", "full",
        "--max-model-calls", "5",
        "--max-iterations", "7",
        "--max-retries-per-goal", "2",
        "--max-audit-rounds", "1",
        "--timeout", "30",
        "--no-audit",
        "--checkpoint", "always",
    ])

    request = collect_request(args, input_fn=lambda _prompt: "", interactive=False)

    assert request.repository_path == str(tmp_path)
    assert request.objective == "Fix the login bug"
    assert request.scope_paths == ("backend/auth", "tests")
    assert request.write_policy.value == "all"
    assert request.verification_depth.value == "full"
    assert request.audit_enabled is False
    assert request.checkpoint_policy.value == "always"
    assert request.budget.max_model_calls == 5
    assert request.budget.max_iterations == 7
    assert request.budget.max_retries_per_goal == 2
    assert request.budget.max_audit_rounds == 1
    assert request.budget.command_timeout_seconds == 30


def test_defaults_apply_for_minimal_invocation(tmp_path) -> None:
    args = _args([str(tmp_path), "Add RBAC"])

    request = collect_request(args, input_fn=lambda _prompt: "", interactive=False)

    assert request.budget.max_model_calls == 20
    assert request.budget.command_timeout_seconds == 120
    assert request.scope_paths == ()
    assert request.audit_enabled is True


def test_interactive_prompts_collect_missing_input(tmp_path) -> None:
    args = build_parser().parse_args([])
    answers = iter([str(tmp_path), "Audit the auth module"])

    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    request = collect_request(args, input_fn=fake_input, interactive=True)

    assert len(prompts) == 2
    assert request.repository_path == str(tmp_path)
    assert request.objective == "Audit the auth module"


def test_non_interactive_without_objective_is_a_usage_error(tmp_path) -> None:
    args = _args([str(tmp_path), "--non-interactive"])

    with pytest.raises(CliError, match="objective is required"):
        collect_request(args, input_fn=lambda _p: "", interactive=False)


def test_missing_repository_path_is_actionable(tmp_path) -> None:
    missing = tmp_path / "does-not-exist"
    args = _args([str(missing), "objective"])

    with pytest.raises(CliError, match="does-not-exist"):
        collect_request(args, input_fn=lambda _p: "", interactive=False)


@pytest.mark.parametrize(
    "argv, message",
    [
        (["x", "obj", "--max-iterations", "0"], "max_iterations must be at least 1"),
        (["x", "obj", "--max-model-calls", "-3"], "max_model_calls must be at least 1"),
        (["x", "obj", "--timeout", "0"], "command_timeout_seconds must be at least 1"),
        (["--scope", "/etc", "x", "obj"], "relative paths inside the repository"),
        (["--scope", "../outside", "x", "obj"], "relative paths inside the repository"),
    ],
)
def test_invalid_limits_and_scopes_are_rejected(argv, message, tmp_path) -> None:
    argv = [str(tmp_path) if a == "x" else a for a in argv]
    args = _args(argv)

    with pytest.raises(CliError, match=message):
        collect_request(args, input_fn=lambda _p: "", interactive=False)


def test_empty_objective_is_rejected(tmp_path) -> None:
    args = _args([str(tmp_path), "   "])

    with pytest.raises(CliError, match="objective"):
        collect_request(args, input_fn=lambda _p: "", interactive=False)
