"""
Checkout API

Whitelisted (:mod:`/api/method`) endpoints for the checkout flow.

- ``GET  /api/method/keemeds_commerce.api.checkout.summary`` — validated order
  preview for the current cart.
- ``POST /api/method/keemeds_commerce.api.checkout.validate`` — same preview,
  raised for any invalid cart/address state.
- ``POST /api/method/keemeds_commerce.api.checkout.create_order`` — create a
  Draft Sales Order (never submitted, cart untouched, no payment).

These controllers are the API/controller layer only: they validate parameters,
delegate to the Checkout service and serialize DTOs. They never touch the
database directly. All endpoints require an authenticated (non-guest) Website
User session.
"""

from __future__ import annotations

from typing import Any

import frappe

from keemeds_commerce.services.checkout_service import CheckoutService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import checkout_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_service: CheckoutService | None = None


def _get_service() -> CheckoutService:
    global _service
    if _service is None:
        _service = CheckoutService()
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def summary() -> dict[str, Any]:
    """
    Return a validated preview of the payable order for the current cart.
    """
    args = _form_dict()
    checkout = _get_service().get_summary(
        shipping_address_name=checkout_params.optional_address_name(
            args, "shipping_address_name"
        ),
        billing_address_name=checkout_params.optional_address_name(
            args, "billing_address_name"
        ),
    )
    return success_response(
        message="Checkout summary generated successfully.",
        data=checkout.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def validate() -> dict[str, Any]:
    """
    Validate the current cart and addresses; raise on any failure.
    """
    args = _form_dict()
    checkout = _get_service().validate(
        shipping_address_name=checkout_params.optional_address_name(
            args, "shipping_address_name"
        ),
        billing_address_name=checkout_params.optional_address_name(
            args, "billing_address_name"
        ),
    )
    return success_response(
        message="Checkout validated successfully.",
        data=checkout.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def create_order() -> dict[str, Any]:
    """
    Create a Draft Sales Order for the current user's valid cart.
    """
    args = _form_dict()
    order = _get_service().create_order(
        shipping_address_name=checkout_params.optional_address_name(
            args, "shipping_address_name"
        ),
        billing_address_name=checkout_params.optional_address_name(
            args, "billing_address_name"
        ),
    )
    return success_response(
        message="Draft Sales Order created successfully.",
        data=order.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}