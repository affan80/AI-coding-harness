"""Standard toolset factory: registers the PRD §10 MVP tools on a registry."""

from __future__ import annotations

from pathlib import Path

from . import files, git_tools
from .policy import CommandPolicy
from .registry import ToolRegistry
from .shell import run_command


def build_registry(
    repo_root: Path | str,
    allowed_scope: list[str],
    *,
    policy: CommandPolicy | None = None,
    artifacts_dir: Path | None = None,
    checkpoint_dir: Path | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry with the MVP file/shell/git tools bound to root."""
    policy = policy or CommandPolicy()
    registry = ToolRegistry(repo_root, allowed_scope)

    from .registry import ToolSpec

    registry.register(
        ToolSpec("read_file", capability="read", mutates=False,
                 handler=lambda path: files.read_file(path))
    )
    registry.register(
        ToolSpec("read_range", capability="read", mutates=False,
                 handler=lambda path, start_line, end_line: files.read_range(
                     path, start_line, end_line))
    )
    registry.register(
        ToolSpec("create_file", capability="patch", mutates=True,
                 handler=lambda path, content: files.create_file(path, content))
    )
    registry.register(
        ToolSpec("apply_patch", capability="patch", mutates=True,
                 handler=lambda path, diff, expected_old_hash: files.apply_patch(
                     path, diff, expected_old_hash))
    )
    registry.register(
        ToolSpec("run_command", capability="shell", mutates=False,
                 handler=lambda command, timeout_seconds=None: run_command(
                     command,
                     policy=policy,
                     working_dir=registry.repo_root,
                     timeout_seconds=timeout_seconds,
                     artifacts_dir=artifacts_dir,
                 ))
    )
    registry.register(
        ToolSpec("git_status", capability="git", mutates=False,
                 handler=lambda: git_tools.git_status(registry.repo_root))
    )
    registry.register(
        ToolSpec("git_diff", capability="git", mutates=False,
                 handler=lambda path=None: git_tools.git_diff(
                     registry.repo_root, path, artifacts_dir=artifacts_dir)
                 )
    )
    registry.register(
        ToolSpec("create_checkpoint", capability="git", mutates=False,
                 handler=lambda files_list: git_tools.create_checkpoint(
                     registry.repo_root, files_list,
                     checkpoint_dir=checkpoint_dir)
                 )
    )
    return registry
