"""
Payment Parameter Validators

Normalize and validate the raw request arguments for the Payment API. Validation
is pure logic (no database access) and lives here so the API controllers stay
thin and consistent.

Business rules (Draft Sales Order ownership, customer linkage, amount bounds,
signatures) are enforced by the Payment service, which needs database reads.
"""

from __future__ import annotations

from typing import Any

from frappe import _

from keemeds_commerce.utils.exceptions import raise_validation_error
from keemeds_commerce.validators.checkout_params import optional_address_name

__all__ = [
    "optional_address_name",
    "order_name",
    "session_name",
    "amount",
    "signature",
    "optional_reason",
    "optional_payment_method",
]


def order_name(args: dict[str, Any], field: str = "sales_order") -> str:
    """
    Return a required, cleaned Sales Order name.
    """
    value = args.get(field)
    if not isinstance(value, str) or not value.strip():
        raise_validation_error(_("{0} is required.").format(field))
    return value.strip()


def session_name(args: dict[str, Any], field: str = "session") -> str:
    """
    Return a required, cleaned Payment Session name.
    """
    return order_name(args, field)


def amount(args: dict[str, Any], field: str = "amount") -> float:
    """
    Return a validated positive payable amount.
    """
    value = args.get(field)
    if value is None or value == "":
        raise_validation_error(_("{0} is required.").format(field))
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise_validation_error(_("{0} must be a valid amount.").format(field))
    if not parsed or parsed <= 0:
        raise_validation_error(_("{0} must be greater than zero.").format(field))
    return round(parsed, 2)


def signature(args: dict[str, Any], field: str = "signature") -> str:
    """
    Return a required, cleaned signature string.
    """
    value = args.get(field)
    if not isinstance(value, str) or not value.strip():
        raise_validation_error(_("A signature is required."))
    return value.strip()


def optional_reason(args: dict[str, Any], field: str = "reason") -> str:
    """
    Return an optional failure/cancellation reason, trimmed.
    """
    value = args.get(field)
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise_validation_error(_("{0} must be text.").format(field))
    return value.strip()


def optional_payment_method(args: dict[str, Any], field: str = "payment_method") -> str:
    """
    Return an optional gateway-level payment method (e.g. "upi", "cod", "card"),
    trimmed and lowercased.
    """
    value = args.get(field)
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise_validation_error(_("{0} must be text.").format(field))
    return value.strip().lower()