"""
Cart & Wishlist Parameter Validators

Normalize and validate the raw request arguments for the Cart and Wishlist
APIs. Validation is pure logic (no database access) and lives here so the API
controllers stay thin and consistent.

The services additionally validate business rules (item exists, enabled, stock
available, quantity bounds) because those require database reads.
"""

from __future__ import annotations

import math
from typing import Any

from frappe import _

from keemeds_commerce.utils.exceptions import raise_validation_error


def require_item_code(args: dict[str, Any], field: str = "item_code") -> str:
    """
    Return a cleaned, non-empty item code from ``args``.
    """
    value = args.get(field)
    if value is None:
        raise_validation_error(_("{0} is required.").format(field))
    cleaned = str(value).strip()
    if not cleaned:
        raise_validation_error(_("{0} is required.").format(field))
    return cleaned


def require_quantity(value: Any, field: str = "quantity") -> float:
    """
    Return a positive, finite quantity.

    Raises a validation error for missing, zero, negative, non-numeric or
    non-finite input.
    """
    if value is None or value == "":
        raise_validation_error(_("{0} is required.").format(field))
    if isinstance(value, bool):
        raise_validation_error(_("{0} must be a positive number.").format(field.title()))
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise_validation_error(_("{0} must be a positive number.").format(field.title()))
    if not math.isfinite(parsed):
        raise_validation_error(_("{0} must be a finite number.").format(field.title()))
    if parsed <= 0:
        raise_validation_error(_("{0} must be greater than zero.").format(field.title()))
    return parsed


def parse_add_item_args(args: dict[str, Any]) -> tuple[str, float]:
    """
    Normalize and validate ``item_code`` + ``quantity`` for add/update.
    """
    return require_item_code(args), require_quantity(args.get("quantity"))