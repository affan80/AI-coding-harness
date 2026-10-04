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
import os
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

    if interactive:
        # 1. API Key check
        has_key = any(
            os.environ.get(k)
            for k in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "HARNESS_OPENAI_API_KEY",
                "HARNESS_ANTHROPIC_API_KEY",
            )
        )
        if not has_key:
            print("\n┌──────────────────────────────────────────────────────────┐")
            print("│ AI CODING HARNESS - INTERACTIVE SETUP                    │")
            print("└──────────────────────────────────────────────────────────┘")
            key = input_fn("🔑 Enter API Key (OpenAI / Anthropic) [press Enter for offline fake mode]: ").strip()
            if key:
                os.environ["HARNESS_OPENAI_API_KEY"] = key
                if "anthropic" in key.lower():
                    os.environ["HARNESS_MODEL_PROVIDER"] = "anthropic"
                else:
                    os.environ["HARNESS_MODEL_PROVIDER"] = "openai"
            else:
                os.environ["HARNESS_MODEL_PROVIDER"] = "fake"

        # 2. Repository / GitHub Link or Folder
        if repository is None:
            print("\n📂 Repository Target:")
            repo_input = input_fn("Enter GitHub repository URL or local folder path [default: .]: ").strip()
            repository = repo_input if repo_input else "."

        # Handle GitHub URL cloning
        if repository.startswith("http://") or repository.startswith("https://") or repository.startswith("git@"):
            import subprocess
            repo_name = repository.rstrip("/").split("/")[-1].replace(".git", "")
            clone_dir = Path("runs") / "cloned_repos" / repo_name
            clone_dir.parent.mkdir(parents=True, exist_ok=True)
            if not clone_dir.exists():
                print(f"📥 Cloning repository from {repository} ...")
                subprocess.run(["git", "clone", repository, str(clone_dir)], check=True)
            repository = str(clone_dir)

        # 3. What you want to do (Action / Objective selection)
        if objective is None:
            print("\n🎯 Select what you want to do:")
            print("  [1] Add feature / write code with tests")
            print("  [2] Fix bug / audit security vulnerabilities")
            print("  [3] Refactor codebase / improve architecture")
            print("  [4] Custom objective")
            choice = input_fn("Select option [1-4] or enter objective directly: ").strip()
            if choice == "1":
                objective = input_fn("Enter feature description / objective: ").strip() or "Add feature with tests"
            elif choice == "2":
                objective = input_fn("Enter bug description / vulnerability to fix: ").strip() or "Fix bugs and audit security"
            elif choice == "3":
                objective = input_fn("Enter refactoring goal: ").strip() or "Refactor code for cleanliness and maintainability"
            elif choice == "4" or not choice:
                objective = input_fn("Enter custom objective: ").strip()
            else:
                objective = choice

    if repository is None:
        repository = "."
    objective = (objective or "").strip()
    if not objective:
        raise CliError(
            "an objective is required; pass it as an argument or run interactively"
        )

    defaults = Budget()

    def _limit(value: int | None, fallback: int) -> int:
        return fallback if value is None else value

    # Budget(0, ...) is a valid "exhaust immediately" model, so the CLI-level
    # minimum of 1 is checked here before construction, not on Budget itself.
    for attr in ("max_model_calls", "max_iterations", "max_retries_per_goal"):
        value = getattr(args, attr)
        if value is not None and value < 1:
            raise CliError(f"budget.{attr} must be at least 1 (got {value})")
    if args.max_audit_rounds is not None and args.max_audit_rounds < 0:
        raise CliError(
            f"budget.max_audit_rounds must be at least 0 (got {args.max_audit_rounds})"
        )
    if args.timeout is not None and args.timeout < 1:
        raise CliError(f"budget.command_timeout_seconds must be at least 1 (got {args.timeout})")

    try:
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
            repository_path=repository,
            objective=objective,
            scope_paths=tuple(args.scope),
            write_policy=WritePolicy(args.write_policy),
            verification_depth=VerificationDepth(args.verification_depth),
            audit_enabled=not args.no_audit,
            checkpoint_policy=CheckpointPolicy(args.checkpoint),
            budget=budget,
        )
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
