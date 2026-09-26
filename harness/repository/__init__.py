"""Repository intelligence (Workstream B): inventory, ignores, and profiling.

Public entry points:

- :func:`profile_repository` / :class:`RepositoryProfiler` — build a
  :class:`~harness.contracts.repository.RepositoryProfile`.
- :func:`build_inventory` — just the bounded candidate inventory.

Dataclasses and enums live in :mod:`harness.contracts.repository` so other
workstreams can code against the contract without importing this package.
"""

from harness.contracts.repository import (
    SCHEMA_VERSION,
    InventoryLimits,
    InventorySummary,
    Language,
    ManifestInfo,
    ProfileContractError,
    ProfileStats,
    RepositoryProfile,
    TestLocation,
)
from harness.repository.detect import detect_structure
from harness.repository.errors import (
    InvalidRepositoryError,
    RepositoryError,
    RepositoryProfileError,
)
from harness.repository.inventory import InventoryResult, build_inventory
from harness.repository.profile import RepositoryProfiler, profile_repository

__all__ = [
    "SCHEMA_VERSION",
    "InventoryLimits",
    "InventoryResult",
    "InventorySummary",
    "InvalidRepositoryError",
    "Language",
    "ManifestInfo",
    "ProfileContractError",
    "ProfileStats",
    "RepositoryError",
    "RepositoryProfile",
    "RepositoryProfileError",
    "RepositoryProfiler",
    "TestLocation",
    "build_inventory",
    "detect_structure",
    "profile_repository",
]
