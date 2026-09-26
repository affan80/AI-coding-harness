"""Structured errors raised by repository intelligence operations."""

from __future__ import annotations


class RepositoryError(Exception):
    """Base class with a stable machine-readable code."""

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
