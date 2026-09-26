"""Structured errors for repository intelligence."""

from __future__ import annotations


class RepositoryError(Exception):
    """Base class with a stable machine-readable code (kept from the merged wip)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class InvalidRepositoryError(RepositoryError):
    """The requested path is not a usable repository root."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_repository", message)


class RepositoryProfileError(RepositoryError):
    """Raised when a repository cannot be profiled at all.

    Per-file and per-directory problems never raise: they become warnings on
    the profile so that one bad path cannot take down a session.
    """

    def __init__(self, reason: str, path: str = "") -> None:
        self.reason = reason
        self.path = path
        super().__init__("repository_profile_error", f"{reason}: {path}" if path else reason)
