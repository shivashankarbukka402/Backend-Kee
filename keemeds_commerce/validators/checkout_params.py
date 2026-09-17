"""
Checkout Parameter Validators

Normalize and validate the raw request arguments for the Checkout API.
Validation is pure logic (no database access) and lives here so the API
controllers stay thin and consistent.

The Checkout service additionally validates business rules (cart ownership,
item/stock bounds, address ownership) because those require database reads.
"""

from __future__ import annotations

from typing import Any

from frappe import _

from keemeds_commerce.utils.exceptions import raise_validation_error


def optional_address_name(args: dict[str, Any], field: str) -> str | None:
    """
    Return a cleaned, corridor-checked address name or ``None``.

    Missing, ``None`` or blank values resolve to ``None`` (meaning "use the
    customer's default shipping/billing address"). A present, non-string value
    is rejected.
    """
    value = args.get(field)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise_validation_error(_("{0} must be a valid address name.").format(field))
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned