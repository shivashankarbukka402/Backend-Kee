"""
Auth Parameter Validators

Validate and normalize the raw request arguments for the Authentication API.

Validation is pure logic (no database access) and lives here so the API
controllers stay thin and consistent.
"""

from __future__ import annotations

import re
from typing import Any

from frappe import _

from keemeds_commerce.utils.exceptions import raise_validation_error

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_PHONE_RE = re.compile(r"^\+?[\d\s\-]{7,15}$")


def validate_register_args(args: dict[str, Any]) -> dict[str, str]:
    """
    Validate registration parameters and return normalized values.

    Returns a dict with keys: ``email``, ``full_name``, ``mobile_no``,
    ``password``, ``first_name``, ``last_name``.

    Raises ``ValidationError`` for any invalid input.
    """
    email = _clean_text(args.get("email")).lower()
    full_name = _clean_text(args.get("full_name"))
    mobile_no = _clean_text(args.get("mobile_no"))
    password = str(args.get("password") or "")

    if not email:
        raise_validation_error(_("Email is required."))
    if not _EMAIL_RE.match(email):
        raise_validation_error(_("Invalid email format."))

    if not full_name:
        raise_validation_error(_("Full name is required."))
    if len(full_name) < 2:
        raise_validation_error(_("Full name must be at least 2 characters."))

    if not mobile_no:
        raise_validation_error(_("Phone number is required."))
    if not _PHONE_RE.match(mobile_no):
        raise_validation_error(_("Invalid phone number format."))

    if not password:
        raise_validation_error(_("Password is required."))
    if len(password) < 8:
        raise_validation_error(_("Password must be at least 8 characters."))

    parts = full_name.strip().split(None, 1)
    first_name = parts[0]
    last_name = parts[1] if len(parts) > 1 else ""

    return {
        "email": email,
        "full_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "mobile_no": mobile_no,
        "password": password,
    }


def validate_login_args(args: dict[str, Any]) -> dict[str, str]:
    """
    Validate login parameters and return normalized values.

    Returns a dict with keys: ``email``, ``password``.

    Raises ``ValidationError`` for any invalid input.
    """
    email = _clean_text(args.get("email")).lower()
    password = str(args.get("password") or "")

    if not email:
        raise_validation_error(_("Email is required."))
    if not password:
        raise_validation_error(_("Password is required."))

    return {"email": email, "password": password}


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
