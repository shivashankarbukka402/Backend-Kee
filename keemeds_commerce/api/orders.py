"""
Order API

Whitelisted (:mod:`/api/method`) endpoints for the ERPNext-backed order
management flow.

- ``GET  /api/method/keemeds_commerce.api.orders.list`` — the authenticated
  user's order history.
- ``GET  /api/method/keemeds_commerce.api.orders.detail`` — a single order
  (scoped to the user).
- ``GET  /api/method/keemeds_commerce.api.orders.invoice`` — the printable
  invoice for an order.
- ``GET  /api/method/keemeds_commerce.api.orders.tracking`` — fulfilment
  timeline events for an order.
- ``POST /api/method/keemeds_commerce.api.orders.cancel`` — cancel a Draft
  (or otherwise cancellable) order.

These controllers are the API/controller layer only: they validate parameters,
delegate to the Order service and serialize DTOs. They never touch the database
directly, and they never expose raw ERPNext documents. All endpoints require an
authenticated (non-guest) Website User session.
"""

from __future__ import annotations

from typing import Any

import frappe

from keemeds_commerce.services.order_service import OrderService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import order_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_service: OrderService | None = None


def _get_service() -> OrderService:
    global _service
    if _service is None:
        _service = OrderService()
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def list() -> dict[str, Any]:
    """
    Return the authenticated customer's order history (newest first).
    """
    args = _form_dict()
    result = _get_service().list_orders(
        limit=_int_arg(args, "limit"),
        offset=_int_arg(args, "offset"),
    )
    return success_response(
        message="Orders fetched successfully.",
        data=result.to_dict(),
    )


@frappe.whitelist(methods=["GET"])
def detail() -> dict[str, Any]:
    """
    Return a single order scoped to the current user.
    """
    args = _form_dict()
    order = _get_service().order_detail(order_params.order_id(args))
    return success_response(
        message="Order fetched successfully.",
        data=order.to_dict(),
    )


@frappe.whitelist(methods=["GET"])
def invoice() -> dict[str, Any]:
    """
    Return the printable invoice for an order.
    """
    args = _form_dict()
    invoice = _get_service().invoice(order_params.order_id(args))
    return success_response(
        message="Invoice fetched successfully.",
        data=invoice.to_dict(),
    )


@frappe.whitelist(methods=["GET"])
def tracking() -> dict[str, Any]:
    """
    Return the fulfilment timeline for an order.
    """
    args = _form_dict()
    tracking = _get_service().tracking(order_params.order_id(args))
    return success_response(
        message="Tracking fetched successfully.",
        data=tracking.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def cancel() -> dict[str, Any]:
    """
    Cancel a Draft (or cancellable) order.
    """
    args = _form_dict()
    result = _get_service().cancel(
        order_id=order_params.order_id(args),
        reason=order_params.reason(args),
    )
    return success_response(
        message="Order cancelled successfully.",
        data=result.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}


def _int_arg(args: dict[str, Any], field: str) -> int:
    value = args.get(field)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
