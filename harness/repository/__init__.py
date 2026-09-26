"""Repository intelligence: inventory, ignore handling, detection, and profiling."""

from .errors import InvalidRepositoryError, RepositoryError
from .inventory import InventoryOptions, InventoryResult, inventory_repository
from .models import (
    FileEntry,
    LanguageStat,
    Manifest,
    RepositoryProfile,
    TestLocation,
    WorkspaceRoot,
)
from .profile import compute_fingerprint, profile_repository

__all__ = [
    "FileEntry",
    "InventoryOptions",
    "InventoryResult",
    "InvalidRepositoryError",
    "LanguageStat",
    "Manifest",
    "RepositoryError",
    "RepositoryProfile",
    "TestLocation",
    "WorkspaceRoot",
    "compute_fingerprint",
    "inventory_repository",
    "profile_repository",
]
