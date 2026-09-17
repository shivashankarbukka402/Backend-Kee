"""
Order Parameter Validators

Normalize and validate the raw request arguments for the Order API. Validation
is pure logic (no database access) and lives here so the API controllers stay
thin and consistent.

Business rules (Sales Order ownership, customer linkage, cancellable status)
are enforced by the Order service, which needs database reads.
"""

from __future__ import annotations

from typing import Any

from frappe import _

from keemeds_commerce.utils.exceptions import raise_validation_error

__all__ = ["order_name", "order_id", "reason"]


def order_name(args: dict[str, Any], field: str = "order_id") -> str:
    """
    Return a required, cleaned Sales Order name.
    """
    value = args.get(field)
    if not isinstance(value, str) or not value.strip():
        raise_validation_error(_("{0} is required.").format(field))
    return value.strip()


def order_id(args: dict[str, Any]) -> str:
    """
    Return a required, cleaned ``order_id`` argument.
    """
    return order_name(args, "order_id")


def reason(args: dict[str, Any]) -> str:
    """
    Return a required, cleaned cancellation reason.
    """
    value = args.get("reason")
    if not isinstance(value, str) or not value.strip():
        raise_validation_error(_("A reason is required to cancel an order."))
    return value.strip()
