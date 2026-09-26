"""CLI arguments and interactive session input (issue #27, PRD §5; FR-01, FR-02).

One workflow, no mode selection: the user provides a repository path and a
natural-language objective (via flags or interactive prompts), plus optional
advanced constraints. Interactive and non-interactive invocations validate
the same way and produce the exact same ``UserRequest`` / ``Budget`` shapes.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from harness.core.errors import HarnessError
from harness.core.models import Budget, UserRequest

WRITE_POLICIES = ("allow", "confirm", "deny")
VERIFICATION_DEPTHS = ("none", "targeted", "related", "full")
CHECKPOINT_POLICIES = ("always", "risky", "never")


class InputError(HarnessError):
    """Invalid CLI input; the message is written to be shown to the user."""

    code = "invalid_input"


def build_parser() -> argparse.ArgumentParser:
    """The full argument surface: repository, objective, advanced constraints."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Autonomous software-engineering harness: give it a "
        "repository and a natural-language objective; it infers the rest. "
        "There are no build/fix/audit/refactor modes by design.",
    )
    parser.add_argument("repository", nargs="?", default=None,
                        help="path to the target repository (default: prompt)")
    parser.add_argument("objective", nargs="?", default=None,
                        help="natural-language objective (default: prompt)")
    parser.add_argument("--scope", action="append", default=[],
                        help="approved write path; repeatable (default: whole repo)")
    parser.add_argument("--write-policy", choices=WRITE_POLICIES, default="allow",
                        help="how writes are approved (default: allow)")
    parser.add_argument("--verification-depth", choices=VERIFICATION_DEPTHS,
                        default="full", help="how deep verification runs (default: full)")
    parser.add_argument("--checkpoint-policy", choices=CHECKPOINT_POLICIES,
                        default="risky", help="when git checkpoints are taken")
    parser.add_argument("--max-model-calls", type=int, default=Budget().max_model_calls)
    parser.add_argument("--max-iterations", type=int, default=Budget().max_iterations)
    parser.add_argument("--max-retries-per-goal", type=int,
                        default=Budget().max_retries_per_goal)
    parser.add_argument("--max-audit-rounds", type=int,
                        default=Budget().max_audit_rounds)
    parser.add_argument("--timeout", type=float, default=Budget().command_timeout_seconds,
                        help="command timeout in seconds (default: 120)")
    parser.add_argument("--runs-root", default="runs",
                        help="where run directories are created (default: runs/)")
    return parser


def validate_inputs(
    repository: str,
    objective: str,
    *,
    max_model_calls: int,
    max_iterations: int,
    max_retries_per_goal: int,
    max_audit_rounds: int,
    timeout: float,
    write_policy: str = "allow",
    verification_depth: str = "full",
    checkpoint_policy: str = "risky",
) -> tuple[Path, str]:
    """Validate everything actionable before a session starts."""
    path = Path(repository).expanduser()
    if not path.exists():
        raise InputError(
            f"repository path does not exist: {path}",
            details={"field": "repository", "value": str(path)},
        )
    if not path.is_dir():
        raise InputError(
            f"repository path is not a directory: {path}",
            details={"field": "repository", "value": str(path)},
        )
    if not objective.strip():
        raise InputError(
            "objective is empty; describe what the harness should do",
            details={"field": "objective"},
        )
    limits = {
        "max_model_calls": max_model_calls,
        "max_iterations": max_iterations,
        "max_retries_per_goal": max_retries_per_goal,
        "max_audit_rounds": max_audit_rounds,
    }
    for name, value in limits.items():
        if value < 1:
            raise InputError(
                f"--{name.replace('_', '-')} must be >= 1, got {value}",
                details={"field": name, "value": value},
            )
    if timeout <= 0:
        raise InputError(
            f"--timeout must be > 0 seconds, got {timeout}",
            details={"field": "timeout", "value": timeout},
        )
    if write_policy not in WRITE_POLICIES:
        raise InputError(
            f"write policy must be one of {WRITE_POLICIES}, got {write_policy!r}",
            details={"field": "write_policy"},
        )
    if verification_depth not in VERIFICATION_DEPTHS:
        raise InputError(
            f"verification depth must be one of {VERIFICATION_DEPTHS}",
            details={"field": "verification_depth"},
        )
    if checkpoint_policy not in CHECKPOINT_POLICIES:
        raise InputError(
            f"checkpoint policy must be one of {CHECKPOINT_POLICIES}",
            details={"field": "checkpoint_policy"},
        )
    return path.resolve(), objective.strip()


def gather_request(
    args: argparse.Namespace | None = None,
    *,
    prompt: Callable[[str], str] = input,
    env: Mapping[str, str] | None = None,
) -> tuple[UserRequest, Budget, Path]:
    """Turn CLI args (prompting for anything missing) into validated inputs.

    Returns the validated ``UserRequest``, ``Budget``, and resolved repository
    path. ``prompt`` is injectable so interactive mode is testable; ``env``
    allows HARNESS_OBJECTIVE / HARNESS_REPO non-automation overrides.
    """
    if args is None:
        args = build_parser().parse_args([])
    environment = env if env is not None else os.environ

    repository = args.repository or environment.get("HARNESS_REPO") or ""
    objective = args.objective or environment.get("HARNESS_OBJECTIVE") or ""
    if not repository:
        repository = prompt("Repository\n> ")
    if not objective:
        objective = prompt("Objective\n> ")

    resolved, clean_objective = validate_inputs(
        repository,
        objective,
        max_model_calls=args.max_model_calls,
        max_iterations=args.max_iterations,
        max_retries_per_goal=args.max_retries_per_goal,
        max_audit_rounds=args.max_audit_rounds,
        timeout=args.timeout,
        write_policy=args.write_policy,
        verification_depth=args.verification_depth,
        checkpoint_policy=args.checkpoint_policy,
    )

    constraints = [
        f"write-policy:{args.write_policy}",
        f"verification-depth:{args.verification_depth}",
        f"checkpoint-policy:{args.checkpoint_policy}",
    ]
    request = UserRequest(
        objective=clean_objective,
        repository_path=str(resolved),
        constraints=tuple(constraints),
        scope_paths=tuple(args.scope),
    )
    budget = Budget(
        max_model_calls=args.max_model_calls,
        max_iterations=args.max_iterations,
        max_retries_per_goal=args.max_retries_per_goal,
        max_audit_rounds=args.max_audit_rounds,
        command_timeout_seconds=args.timeout,
    )
    return request, budget, resolved
