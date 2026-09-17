"""
Product Catalog API

Whitelisted (:mod:`/api/method`) endpoints exposing the ERPNext medicine catalog
to the React storefront.

- ``GET /api/method/keemeds_commerce.api.products.list_products`` — paginated
  product listing with filtering, search and sorting.
- ``GET /api/method/keemeds_commerce.api.products.get_product`` — full product
  detail by ``item_code``.

These controllers are the API/controller layer only: they validate parameters,
delegate to the Product service and serialize DTOs. They never touch the
database directly, and they never expose raw ERPNext documents or stack traces.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.product import ProductListing
from keemeds_commerce.services.product_service import ProductService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import product_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_config: CommerceConfig | None = None
_service: ProductService | None = None


def _get_config() -> CommerceConfig:
    global _config
    if _config is None:
        _config = CommerceConfig()
    return _config


def _get_service() -> ProductService:
    global _service
    if _service is None:
        _service = ProductService(
            config=_get_config(),
        )
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(allow_guest=True)
def list_products() -> dict[str, Any]:
    """
    Return a paginated, filterable product listing.
    """
    args = _form_dict()
    query = product_params.build_product_query(args, _get_config())
    listing: ProductListing = _get_service().list_products(query)
    return success_response(message="Products listed successfully.", data=listing.to_dict())


@frappe.whitelist(allow_guest=True)
def get_product() -> dict[str, Any]:
    """
    Return a single product's full detail.
    """
    args = _form_dict()
    item_code = (args.get("item_code") or "").strip()
    if not item_code:
        from keemeds_commerce.utils.exceptions import raise_validation_error

        raise_validation_error(_("item_code is required."))

    product = _get_service().get_product(item_code)
    return success_response(message="Product detail fetched successfully.", data=product.to_dict())


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}
