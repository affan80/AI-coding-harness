"""Command-line entry point: one command, one workflow, no mode selection.

Exit codes let scripted runs determine the outcome without parsing prose:

- 0  VERIFIED   (all required checks passed)
- 1  PARTIAL    (some work completed; unverified — see the run directory)
- 2  FAILED     (the session failed; evidence in the run directory)
- 3  CANCELLED  (the user interrupted the session)
- 64 usage error (invalid arguments, limits, or repository path)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from enum import IntEnum
from pathlib import Path

from harness.core.models import (
    Budget,
    CheckpointPolicy,
    SessionStatus,
    UserRequest,
    VerificationDepth,
    WritePolicy,
)
from tui.render import Renderer
from tui.session_runner import run_session


class ExitCode(IntEnum):
    VERIFIED = 0
    PARTIAL = 1
    FAILED = 2
    CANCELLED = 3
    USAGE_ERROR = 64


class CliError(Exception):
    """Actionable user-facing error; mapped to ExitCode.USAGE_ERROR."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D401 - argparse contract
        raise CliError(f"{message}\n{self.format_usage().strip()}")


def exit_code_for(status: SessionStatus) -> int:
    mapping = {
        SessionStatus.VERIFIED: ExitCode.VERIFIED,
        SessionStatus.PARTIAL: ExitCode.PARTIAL,
        SessionStatus.FAILED: ExitCode.FAILED,
        SessionStatus.CANCELLED: ExitCode.CANCELLED,
        SessionStatus.IN_PROGRESS: ExitCode.FAILED,
    }
    return int(mapping[status])


def build_parser() -> _Parser:
    parser = _Parser(
        prog="harness",
        description=(
            "Autonomous AI software engineering harness. Accepts a repository "
            "and one natural-language objective; strategy (build/fix/audit/"
            "refactor) is inferred, never selected."
        ),
    )
    parser.add_argument("repository", nargs="?", help="local repository path ('.' allowed)")
    parser.add_argument("objective", nargs="?", help="natural-language objective")
    parser.add_argument("--scope", action="append", default=[], metavar="PATH",
                        help="restrict writes to this path (repeatable)")
    parser.add_argument("--write-policy", choices=[p.value for p in WritePolicy],
                        default=WritePolicy.SCOPED.value)
    parser.add_argument("--verification-depth", choices=[d.value for d in VerificationDepth],
                        default=VerificationDepth.TARGETED.value)
    parser.add_argument("--max-model-calls", type=int, default=None, metavar="N")
    parser.add_argument("--max-iterations", type=int, default=None, metavar="N")
    parser.add_argument("--max-retries-per-goal", type=int, default=None, metavar="N")
    parser.add_argument("--max-audit-rounds", type=int, default=None, metavar="N")
    parser.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                        help="command timeout in seconds")
    parser.add_argument("--no-audit", action="store_true", help="disable the audit stage")
    parser.add_argument("--checkpoint", choices=[c.value for c in CheckpointPolicy],
                        default=CheckpointPolicy.WHEN.value)
    parser.add_argument("--runs-dir", default="runs", help="where run directories are created")
    parser.add_argument("--non-interactive", action="store_true",
                        help="never prompt; fail instead (CI/demo mode)")
    return parser


def collect_request(
    args: argparse.Namespace,
    input_fn: Callable[[str], str],
    interactive: bool,
) -> UserRequest:
    """Build one UserRequest from flags and/or interactive prompts."""
    repository = args.repository
    objective = args.objective
    if repository is None and interactive:
        repository = input_fn("Repository\n> ").strip() or "."
    if repository is None:
        repository = "."
    if objective is None and interactive:
        objective = input_fn("Objective\n> ").strip()
    objective = (objective or "").strip()
    if not objective:
        raise CliError(
            "an objective is required; pass it as an argument or run interactively"
        )

    defaults = Budget()

    def _limit(value: int | None, fallback: int) -> int:
        return fallback if value is None else value

    budget = Budget(
        max_model_calls=_limit(args.max_model_calls, defaults.max_model_calls),
        max_iterations=_limit(args.max_iterations, defaults.max_iterations),
        max_retries_per_goal=_limit(
            args.max_retries_per_goal, defaults.max_retries_per_goal
        ),
        max_audit_rounds=_limit(args.max_audit_rounds, defaults.max_audit_rounds),
        command_timeout_seconds=_limit(
            args.timeout, defaults.command_timeout_seconds
        ),
    )
    request = UserRequest(
        repository=repository,
        objective=objective,
        allowed_scope=tuple(args.scope),
        write_policy=WritePolicy(args.write_policy),
        verification_depth=VerificationDepth(args.verification_depth),
        audit_enabled=not args.no_audit,
        checkpoint_policy=CheckpointPolicy(args.checkpoint),
        budget=budget,
    )
    try:
        request.validate()
    except ValueError as exc:
        raise CliError(str(exc)) from exc
    if not Path(repository).is_dir():
        raise CliError(
            f"repository path does not exist or is not a directory: {repository}"
        )
    return request


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    stdout=None,
    stderr=None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        interactive = not args.non_interactive and stdout.isatty()
        request = collect_request(args, input_fn, interactive)
    except CliError as exc:
        print(f"error: {exc}", file=stderr)
        return int(ExitCode.USAGE_ERROR)
    except EOFError:
        print("error: input cancelled", file=stderr)
        return int(ExitCode.CANCELLED)

    renderer = Renderer(stdout)
    try:
        result = run_session(request, args.runs_dir, renderer)
    except KeyboardInterrupt:
        print("error: cancelled", file=stderr)
        return int(ExitCode.CANCELLED)
    renderer.show_final(result)
    return exit_code_for(result.status)


def run() -> None:  # console_scripts entry point
    sys.exit(main())


if __name__ == "__main__":
    run()
