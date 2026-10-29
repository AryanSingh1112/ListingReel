"""Typed, user-facing errors. The CLI/UI print `message` + `hint`; never a traceback."""
from __future__ import annotations


class ListingReelError(Exception):
    exit_code = 1

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        return self.message + (f"\n  hint: {self.hint}" if self.hint else "")


class InputError(ListingReelError):
    exit_code = 2


class DependencyError(ListingReelError):
    exit_code = 3


class RenderError(ListingReelError):
    exit_code = 4
