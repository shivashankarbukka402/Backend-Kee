"""
Cart API

Whitelisted (:mod:`/api/method`) endpoints exposing the ERP-backed shopping
cart to the storefront.

- ``GET    /api/method/keemeds_commerce.api.cart.get_cart`` — current cart.
- ``POST   /api/method/keemeds_commerce.api.cart.add_item`` — add/increase item.
- ``PUT    /api/method/keemeds_commerce.api.cart.update_item`` — set quantity.
- ``DELETE /api/method/keemeds_commerce.api.cart.remove_item`` — remove item.
- ``DELETE /api/method/keemeds_commerce.api.cart.clear_cart`` — empty the cart.

These controllers are the API/controller layer only: they validate parameters,
delegate to the Cart service and serialize DTOs. They never touch the database
directly, and they never expose raw ERPNext documents or stack traces. All
endpoints require an authenticated (non-guest) Website User session.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from keemeds_commerce.services.cart_service import CartService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import cart_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_service: CartService | None = None


def _get_service() -> CartService:
    global _service
    if _service is None:
        _service = CartService()
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def get_cart() -> dict[str, Any]:
    """
    Return the current user's cart.
    """
    cart = _get_service().get_cart()
    return success_response(message="Cart fetched successfully.", data=cart.to_dict())


@frappe.whitelist(methods=["POST"])
def add_item() -> dict[str, Any]:
    """
    Add an item to the cart (or increase its quantity if already present).
    """
    args = _form_dict()
    item_code, quantity = cart_params.parse_add_item_args(args)
    cart = _get_service().add_item(item_code=item_code, quantity=quantity)
    return success_response(message="Item added to cart.", data=cart.to_dict())


@frappe.whitelist(methods=["PUT"])
def update_item() -> dict[str, Any]:
    """
    Set the quantity of an item already in the cart.
    """
    args = _form_dict()
    item_code, quantity = cart_params.parse_add_item_args(args)
    cart = _get_service().update_item(item_code=item_code, quantity=quantity)
    return success_response(message="Cart item updated.", data=cart.to_dict())


@frappe.whitelist(methods=["DELETE"])
def remove_item() -> dict[str, Any]:
    """
    Remove an item from the cart.
    """
    args = _form_dict()
    item_code = cart_params.require_item_code(args)
    cart = _get_service().remove_item(item_code=item_code)
    return success_response(message="Item removed from cart.", data=cart.to_dict())


@frappe.whitelist(methods=["DELETE"])
def clear_cart() -> dict[str, Any]:
    """
    Empty the current user's cart.
    """
    cart = _get_service().clear_cart()
    return success_response(message="Cart cleared.", data=cart.to_dict())


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}