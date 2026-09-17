"""
Customer Parameter Validators

Validate and normalize the raw request arguments for the Customer Profile &
Address API.

Validation is pure logic (no database access) and lives here so the API
controllers stay thin and consistent.
"""

from __future__ import annotations

import re
from typing import Any

from frappe import _

from keemeds_commerce.utils.exceptions import raise_validation_error

_PHONE_RE = re.compile(r"^\+?[\d\s\-]{7,15}$")
_ADDRESS_TYPES = {
    "Billing",
    "Shipping",
    "Office",
    "Personal",
    "Plant",
    "Postal",
    "Shop",
    "Subsidiary",
    "Warehouse",
    "Current",
    "Permanent",
    "Other",
}


# ---------------------------------------------------------------------- #
# Profile validators
# ---------------------------------------------------------------------- #


def validate_profile_update_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    Validate profile update parameters.

    Only fields present in *args* are validated and returned.  Missing or
    empty fields are silently ignored (partial update).
    """
    result: dict[str, Any] = {}

    if "full_name" in args and args["full_name"] is not None:
        full_name = _clean_text(args["full_name"])
        if full_name:
            if len(full_name) < 2:
                raise_validation_error(_("Full name must be at least 2 characters."))
            parts = full_name.strip().split(None, 1)
            result["first_name"] = parts[0]
            result["last_name"] = parts[1] if len(parts) > 1 else ""
            result["full_name"] = full_name

    if "mobile_no" in args and args["mobile_no"] is not None:
        mobile_no = _clean_text(args["mobile_no"])
        if mobile_no:
            if not _PHONE_RE.match(mobile_no):
                raise_validation_error(_("Invalid phone number format."))
            result["mobile_no"] = mobile_no

    if "gender" in args and args["gender"] is not None:
        gender = _clean_text(args["gender"])
        if gender:
            result["gender"] = gender

    if not result:
        raise_validation_error(_("No valid fields provided for update."))

    return result


# ---------------------------------------------------------------------- #
# Address validators
# ---------------------------------------------------------------------- #


def validate_address_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and normalize address parameters for create/update.

    Returns a dict of validated, stripped values ready for the service layer.
    """
    address_type = _clean_text(args.get("address_type"))
    address_line1 = _clean_text(args.get("address_line1"))
    city = _clean_text(args.get("city"))
    country = _clean_text(args.get("country"))

    if not address_type:
        raise_validation_error(_("Address type is required."))
    if address_type not in _ADDRESS_TYPES:
        raise_validation_error(
            _("Invalid address type '{}'. Must be one of: {}.").format(
                address_type, ", ".join(sorted(_ADDRESS_TYPES))
            )
        )
    if not address_line1:
        raise_validation_error(_("Address line 1 is required."))
    if not city:
        raise_validation_error(_("City is required."))
    if not country:
        raise_validation_error(_("Country is required."))

    result: dict[str, Any] = {
        "address_type": address_type,
        "address_line1": address_line1,
        "city": city,
        "country": country,
    }

    for opt_field in ("address_line2", "state", "pincode", "phone", "email_id", "address_title"):
        val = args.get(opt_field)
        if val is not None:
            cleaned = _clean_text(val)
            if cleaned:
                result[opt_field] = cleaned

    return result


def validate_address_id(args: dict[str, Any]) -> str:
    """
    Validate that an address name/id is provided.
    """
    address_name = _clean_text(args.get("address_name"))
    if not address_name:
        raise_validation_error(_("address_name is required."))
    return address_name


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
