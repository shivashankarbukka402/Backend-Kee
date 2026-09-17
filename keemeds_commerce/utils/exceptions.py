"""
Exception Utilities

Provides reusable exception helpers for the application.

Responsibilities
----------------
- Raise validation errors
- Raise not found errors
- Raise permission errors
- Raise duplicate record errors

This module standardizes exception handling across all services.
"""

from __future__ import annotations

from frappe import _
from frappe.exceptions import (
    DoesNotExistError,
    DuplicateEntryError,
    PermissionError,
    ValidationError,
)


def raise_validation_error(message: str) -> None:
    """
    Raise a validation error.
    """
    raise ValidationError(_(message))


def raise_not_found(doctype: str, name: str) -> None:
    """
    Raise a standardized document-not-found error.
    """
    raise DoesNotExistError(
        _("{} '{}' does not exist.").format(doctype, name)
    )


def raise_permission_error(message: str | None = None) -> None:
    """
    Raise a permission error.
    """
    raise PermissionError(
        _(message or "You do not have permission to perform this action.")
    )


def raise_duplicate_entry(doctype: str, value: str) -> None:
    """
    Raise a duplicate record error.
    """
    raise DuplicateEntryError(
        _("{} '{}' already exists.").format(doctype, value)
    )


def require(value: object, message: str) -> None:
    """
    Ensure a required value is provided.
    """
    if not value:
        raise_validation_error(message)
