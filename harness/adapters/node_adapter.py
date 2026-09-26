"""Node / workspace project adapter (#59).

Discovery rule: package scripts from package.json win over anything we could
guess; when a needed script does not exist the adapter returns None/[] and
the caller reports a structured result instead of a false success.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .base import AdapterCommand, CommandKind, DetectionResult, EnvironmentStatus

_TEST_FILE_TARGETS = {"--run"}  # passed through to the configured test script


@dataclass
class NodeConfig:
    package_manager: str = "npm"  # npm | pnpm | yarn (lockfile-derived)
    scripts: dict[str, str] = field(default_factory=dict)
    workspaces: list[str] = field(default_factory=list)


def read_node_config(root: Path) -> NodeConfig | None:
    manifest = root / "package.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return NodeConfig()  # present but unreadable: defaults, no scripts
    config = NodeConfig(
        scripts=dict(data.get("scripts", {})),
        workspaces=list(data.get("workspaces", []) or []),
    )
    if (root / "pnpm-lock.yaml").is_file():
        config.package_manager = "pnpm"
    elif (root / "yarn.lock").is_file():
        config.package_manager = "yarn"
    return config


def _run(manager: str, script: str) -> list[str]:
    if manager == "yarn":
        return ["yarn", script]  # yarn runs scripts without a subcommand
    return [manager, "run", script]


class NodeAdapter:
    name = "node"

    def detect(self, root: Path) -> DetectionResult:
        if (root / "package.json").is_file():
            return DetectionResult(supported=True, adapter_name=self.name)
        return DetectionResult.unsupported("no package.json at the root")

    def setup_commands(self, root: Path) -> list[AdapterCommand]:
        config = read_node_config(root)
        if config is None:
            return []
        manager = config.package_manager
        argv = (
            ["npm", "ci"]
            if manager == "npm" and (root / "package-lock.json").is_file()
            else [manager, "install"]
        )
        return [
            AdapterCommand(
                kind=CommandKind.SETUP, argv=argv,
                description=f"install dependencies with {manager}",
                requires=manager,
            )
        ]

    def build_commands(self, root: Path) -> list[AdapterCommand]:
        config = read_node_config(root)
        if config and "build" in config.scripts:
            return [
                AdapterCommand(
                    kind=CommandKind.BUILD,
                    argv=_run(config.package_manager, "build"),
                    description="package.json build script",
                    requires=config.package_manager,
                )
            ]
        return []

    def lint_commands(self, root: Path) -> list[AdapterCommand]:
        config = read_node_config(root)
        if config and "lint" in config.scripts:
            return [
                AdapterCommand(
                    kind=CommandKind.LINT,
                    argv=_run(config.package_manager, "lint"),
                    description="package.json lint script",
                    requires=config.package_manager,
                )
            ]
        return []

    def typecheck_commands(self, root: Path) -> list[AdapterCommand]:
        config = read_node_config(root)
        if config and "typecheck" in config.scripts:
            return [
                AdapterCommand(
                    kind=CommandKind.TYPECHECK,
                    argv=_run(config.package_manager, "typecheck"),
                    description="package.json typecheck script",
                    requires=config.package_manager,
                )
            ]
        return []

    def target_test_command(
        self, root: Path, tests: list[str]
    ) -> AdapterCommand | None:
        config = read_node_config(root)
        if config is None or "test" not in config.scripts or not tests:
            return None
        return AdapterCommand(
            kind=CommandKind.TARGET_TEST,
            argv=_run(config.package_manager, "test") + tests,
            description="configured test script scoped to target tests",
            requires=config.package_manager,
        )

    def full_test_command(self, root: Path) -> AdapterCommand | None:
        config = read_node_config(root)
        if config is None or "test" not in config.scripts:
            return None
        return AdapterCommand(
            kind=CommandKind.FULL_TEST,
            argv=_run(config.package_manager, "test"),
            description="package.json test script",
            requires=config.package_manager,
        )

    def workspace_roots(self, root: Path) -> list[Path]:
        config = read_node_config(root)
        if config is None:
            return []
        roots: list[Path] = []
        for pattern in config.workspaces:
            if (root / pattern).is_dir():
                roots.append(root / pattern)
            else:
                roots.extend(
                    p for p in sorted(root.glob(pattern)) if p.is_dir()
                )
        return roots

    def check_environment(self, root: Path) -> EnvironmentStatus:
        config = read_node_config(root)
        if config is None:
            return EnvironmentStatus(ok=False, missing=["package.json"])
        required = [config.package_manager]
        missing = [b for b in required if shutil.which(b) is None]
        return EnvironmentStatus(ok=not missing, missing=missing)
