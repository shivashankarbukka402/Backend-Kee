"""
Wishlist API

Whitelisted (:mod:`/api/method`) endpoints exposing the ERP-backed wishlist to
the storefront.

- ``GET    /api/method/keemeds_commerce.api.wishlist.get_wishlist`` — current wishlist.
- ``POST   /api/method/keemeds_commerce.api.wishlist.add_item`` — add item (dedupe).
- ``DELETE /api/method/keemeds_commerce.api.wishlist.remove_item`` — remove item.

These controllers are the API/controller layer only: they validate parameters,
delegate to the Wishlist service and serialize DTOs. They never touch the
database directly, and they never expose raw ERPNext documents or stack traces.
All endpoints require an authenticated (non-guest) Website User session.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from keemeds_commerce.services.wishlist_service import WishlistService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import cart_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_service: WishlistService | None = None


def _get_service() -> WishlistService:
    global _service
    if _service is None:
        _service = WishlistService()
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def get_wishlist() -> dict[str, Any]:
    """
    Return the current user's wishlist.
    """
    wishlist = _get_service().get_wishlist()
    return success_response(
        message="Wishlist fetched successfully.",
        data=wishlist.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def add_item() -> dict[str, Any]:
    """
    Add an item to the wishlist. Duplicates are prevented (no-op).
    """
    args = _form_dict()
    item_code = cart_params.require_item_code(args)
    wishlist = _get_service().add_item(item_code=item_code)
    return success_response(
        message="Item added to wishlist.",
        data=wishlist.to_dict(),
    )


@frappe.whitelist(methods=["DELETE"])
def remove_item() -> dict[str, Any]:
    """
    Remove an item from the wishlist.
    """
    args = _form_dict()
    item_code = cart_params.require_item_code(args)
    wishlist = _get_service().remove_item(item_code=item_code)
    return success_response(
        message="Item removed from wishlist.",
        data=wishlist.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}