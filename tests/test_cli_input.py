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

    request, budget = collect_request(args, input_fn=lambda _prompt: "", interactive=False)

    assert request.repository_path == str(tmp_path)
    assert request.objective == "Fix the login bug"
    assert request.scope_paths == ("backend/auth", "tests")
    assert "write_policy=all" in request.constraints
    assert "verification_depth=full" in request.constraints
    assert "audit_enabled=false" in request.constraints
    assert "checkpoint_policy=always" in request.constraints
    assert budget.max_model_calls == 5
    assert budget.max_iterations == 7
    assert budget.max_retries_per_goal == 2
    assert budget.max_audit_rounds == 1
    assert budget.command_timeout_seconds == 30


def test_defaults_apply_for_minimal_invocation(tmp_path) -> None:
    args = _args([str(tmp_path), "Add RBAC"])

    request, budget = collect_request(args, input_fn=lambda _prompt: "", interactive=False)

    assert budget.max_model_calls == 20
    assert budget.command_timeout_seconds == 120
    assert request.scope_paths == ()
    assert "audit_enabled=true" in request.constraints


def test_interactive_prompts_collect_missing_input(tmp_path) -> None:
    args = build_parser().parse_args([])
    answers = iter([str(tmp_path), "Audit the auth module"])

    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    request, _budget = collect_request(args, input_fn=fake_input, interactive=True)

    assert len(prompts) == 2
    assert request.repository_path == str(tmp_path)
    assert request.objective == "Audit the auth module"


def test_non_interactive_without_objective_is_a_usage_error(tmp_path) -> None:
    args = _args([str(tmp_path), "--non-interactive"])

    with pytest.raises(CliError, match="objective is required"):
        collect_request(args, input_fn=lambda _p: "", interactive=False)  # type: ignore[arg-type]


def test_missing_repository_path_is_actionable(tmp_path) -> None:
    missing = tmp_path / "does-not-exist"
    args = _args([str(missing), "objective"])

    with pytest.raises(CliError, match="does-not-exist"):
        collect_request(args, input_fn=lambda _p: "", interactive=False)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "argv, message",
    [
        (["x", "obj", "--max-iterations", "-1"], "Budget.max_iterations must be >= 0"),
        (["x", "obj", "--max-model-calls", "-3"], "Budget.max_model_calls must be >= 0"),
        (["x", "obj", "--timeout", "0"], "Budget.command_timeout_seconds must be > 0"),
        (["--scope", "/etc", "x", "obj"], "relative paths inside the repository"),
        (["--scope", "../outside", "x", "obj"], "relative paths inside the repository"),
    ],
)
def test_invalid_limits_and_scopes_are_rejected(argv, message, tmp_path) -> None:
    argv = [str(tmp_path) if a == "x" else a for a in argv]
    args = _args(argv)

    with pytest.raises(CliError, match=message):
        collect_request(args, input_fn=lambda _p: "", interactive=False)  # type: ignore[arg-type]


def test_zero_budget_counters_are_valid(tmp_path) -> None:
    args = _args([str(tmp_path), "obj", "--max-iterations", "0", "--timeout", "5"])

    _request, budget = collect_request(
        args, input_fn=lambda _p: "", interactive=False
    )  # type: ignore[arg-type]

    assert budget.max_iterations == 0
    assert budget.command_timeout_seconds == 5


def test_empty_objective_is_rejected(tmp_path) -> None:
    args = _args([str(tmp_path), "   "])

    with pytest.raises(CliError, match="objective"):
        collect_request(args, input_fn=lambda _p: "", interactive=False)  # type: ignore[arg-type]
