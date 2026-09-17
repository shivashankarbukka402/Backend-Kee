"""
Customer Profile & Address API

Whitelisted (:mod:`/api/method`) endpoints for customer profile management
and address CRUD.

These controllers are the API/controller layer only: they validate parameters,
delegate to the Customer service and serialize DTOs. They never touch the
database directly, and they never expose raw ERPNext documents or stack traces.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from keemeds_commerce.services.customer_service import CustomerService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import customer_params

# ---------------------------------------------------------------------- #
# Dependency wiring
# ---------------------------------------------------------------------- #

_service: CustomerService | None = None


def _get_service() -> CustomerService:
    global _service
    if _service is None:
        _service = CustomerService()
    return _service


# ---------------------------------------------------------------------- #
# Profile endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def get_profile() -> dict[str, Any]:
    """
    Return the extended customer profile.
    """
    profile = _get_service().get_profile()
    return success_response(
        message="Profile fetched successfully.",
        data=profile.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def update_profile() -> dict[str, Any]:
    """
    Partially update the customer profile.
    """
    args = _form_dict()
    params = customer_params.validate_profile_update_args(args)
    profile = _get_service().update_profile(
        first_name=params.get("first_name"),
        last_name=params.get("last_name"),
        full_name=params.get("full_name"),
        mobile_no=params.get("mobile_no"),
        gender=params.get("gender"),
    )
    return success_response(
        message="Profile updated successfully.",
        data=profile.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Address — List
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def list_addresses() -> dict[str, Any]:
    """
    Return all non-disabled addresses for the current customer.
    """
    result = _get_service().list_addresses()
    return success_response(
        message="Addresses fetched successfully.",
        data=result.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Address — Get
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def get_address() -> dict[str, Any]:
    """
    Return a single address by name.
    """
    args = _form_dict()
    address_name = customer_params.validate_address_id(args)
    addr = _get_service().get_address(address_name)
    return success_response(
        message="Address fetched successfully.",
        data=addr.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Address — Create
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
def create_address() -> dict[str, Any]:
    """
    Create a new address linked to the current customer.
    """
    args = _form_dict()
    params = customer_params.validate_address_args(args)
    addr = _get_service().create_address(**params)
    return success_response(
        message="Address created successfully.",
        data=addr.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Address — Update
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
def update_address() -> dict[str, Any]:
    """
    Update an existing address.
    """
    args = _form_dict()
    address_name = customer_params.validate_address_id(args)
    params = customer_params.validate_address_args(args)
    addr = _get_service().update_address(address_name, **params)
    return success_response(
        message="Address updated successfully.",
        data=addr.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Address — Delete
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
def delete_address() -> dict[str, Any]:
    """
    Delete an address.
    """
    args = _form_dict()
    address_name = customer_params.validate_address_id(args)
    _get_service().delete_address(address_name)
    return success_response(message="Address deleted successfully.")


# ---------------------------------------------------------------------- #
# Address — Default Shipping
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
def set_default_shipping() -> dict[str, Any]:
    """
    Mark an address as the preferred shipping address.
    """
    args = _form_dict()
    address_name = customer_params.validate_address_id(args)
    addr = _get_service().set_default_shipping(address_name)
    return success_response(
        message="Default shipping address updated successfully.",
        data=addr.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Address — Default Billing
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
def set_default_billing() -> dict[str, Any]:
    """
    Mark an address as the preferred billing address.
    """
    args = _form_dict()
    address_name = customer_params.validate_address_id(args)
    addr = _get_service().set_default_billing(address_name)
    return success_response(
        message="Default billing address updated successfully.",
        data=addr.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}
