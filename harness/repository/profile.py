"""Public entry point: deterministic repository profiling.

``profile_repository`` combines the bounded inventory with manifest-based
detection into a ``RepositoryProfile``. The fingerprint is a sha256 over the
profile's stable content (paths, sizes, detections, totals) and excludes the
root path and wall-clock timing, so unchanged input yields an identical
profile.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .detection import (
    detect_frameworks,
    detect_languages,
    detect_test_locations,
    detect_workspaces,
    find_manifests,
    load_manifest_bodies,
)
from .errors import InvalidRepositoryError
from .inventory import InventoryOptions, inventory_repository
from .models import Manifest, RepositoryProfile, TestLocation


def profile_repository(
    root: str | Path,
    options: InventoryOptions | None = None,
) -> RepositoryProfile:
    """Profile a repository without reading any file body."""
    try:
        root_path = Path(root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise InvalidRepositoryError(f"repository root not accessible: {root}") from exc
    if not root_path.is_dir():
        raise InvalidRepositoryError(f"repository root is not a directory: {root}")

    options = options or InventoryOptions()
    inventory = inventory_repository(root_path, options)

    manifests = find_manifests(inventory.entries)
    bodies = load_manifest_bodies(root_path, manifests)
    languages = detect_languages(inventory.entries)
    frameworks = detect_frameworks(manifests, bodies)
    workspaces, monorepo = detect_workspaces(manifests, bodies)
    test_locations = detect_test_locations(inventory.entries)
    commands = _detect_commands(manifests, bodies, frameworks, test_locations)

    profile = RepositoryProfile(
        root=str(root_path),
        files=inventory.entries,
        languages=languages,
        manifests=manifests,
        workspaces=workspaces,
        test_locations=test_locations,
        frameworks=frameworks,
        commands=commands,
        monorepo=monorepo,
        total_files=inventory.total_files,
        total_bytes=inventory.total_bytes,
        total_dirs=inventory.total_dirs,
        ignored_paths=inventory.ignored_paths,
        ignored_by_pattern=inventory.ignored_by_pattern,
        omitted_by_limit=inventory.omitted_by_limit,
        truncated=inventory.truncated,
    )
    profile.fingerprint = compute_fingerprint(profile)
    return profile


def _detect_commands(
    manifests: list[Manifest],
    bodies: dict[str, object],
    frameworks: list[str],
    test_locations: list[TestLocation],
) -> dict[str, str]:
    """Derive canonical project commands from existing project configuration."""
    commands: dict[str, str] = {}
    python_manifests = [
        m for m in manifests if m.kind in ("pyproject.toml", "setup.py", "setup.cfg")
    ]
    has_requirements = any(m.kind.startswith("requirements") for m in manifests)
    has_tests = bool(test_locations)
    if (python_manifests or has_requirements) and ("pytest" in frameworks or has_tests):
        commands["test"] = "pytest -q"
    pyproject_body = next(
        (bodies[m.path] for m in python_manifests if m.kind == "pyproject.toml"),
        None,
    )
    pyproject_tool = None
    if isinstance(pyproject_body, dict):
        tool = pyproject_body.get("tool")
        if isinstance(tool, dict):
            pyproject_tool = tool
    if pyproject_tool is not None and isinstance(pyproject_tool.get("ruff"), dict):
        commands["lint"] = "ruff check ."
    for manifest in manifests:
        if manifest.kind != "package.json":
            continue
        body = bodies.get(manifest.path)
        if not isinstance(body, dict):
            continue
        scripts = body.get("scripts")
        if not isinstance(scripts, dict):
            continue
        for name in ("test", "build", "lint", "typecheck"):
            if name in scripts and name not in commands:
                commands[name] = f"npm run {name}"
    return dict(sorted(commands.items()))


def compute_fingerprint(profile: RepositoryProfile) -> str:
    """sha256 over the profile's stable content, excluding root and timing."""
    payload = asdict(profile)
    payload.pop("root", None)
    payload.pop("fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
