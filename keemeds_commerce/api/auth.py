"""
Auth API

Whitelisted (:mod:`/api/method`) endpoints for customer authentication.

- ``GET  /api/method/keemeds_commerce.api.auth.csrf_token`` -- session CSRF token.
- ``POST /api/method/keemeds_commerce.api.auth.register`` -- create account.
- ``POST /api/method/keemeds_commerce.api.auth.login`` -- authenticate.
- ``POST /api/method/keemeds_commerce.api.auth.logout`` -- end session.
- ``GET  /api/method/keemeds_commerce.api.auth.me`` -- current profile.

These controllers are the API/controller layer only: they validate parameters,
delegate to the Auth service and serialize DTOs. They never touch the
database directly, and they never expose raw ERPNext documents or stack traces.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from keemeds_commerce.services.auth_service import AuthService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import auth_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_service: AuthService | None = None


def _get_service() -> AuthService:
    global _service
    if _service is None:
        _service = AuthService()
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(allow_guest=True, methods=["GET"])
def csrf_token() -> dict[str, Any]:
    """
    Return the current session's CSRF token, generating one if absent.

    ERPNext enforces CSRF on POST requests once the session carries a token
    (e.g. after a Desk boot in the same browser). Clients should fetch this
    token *before* calling ``register``/``login``/``logout`` and send it back
    in the ``X-Frappe-CSRF-Token`` header (or ``csrf_token`` field) — the
    standard ERPNext session-authentication behaviour.
    """
    from frappe.sessions import get_csrf_token

    return {"csrf_token": get_csrf_token()}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def register() -> dict[str, Any]:
    """
    Create a new customer account (User + Customer).
    """
    args = _form_dict()
    params = auth_params.validate_register_args(args)
    profile = _get_service().register(
        email=params["email"],
        first_name=params["first_name"],
        last_name=params["last_name"],
        full_name=params["full_name"],
        mobile_no=params["mobile_no"],
        password=params["password"],
    )
    return success_response(
        message="Account created successfully.",
        data=profile.to_dict(),
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login() -> dict[str, Any]:
    """
    Authenticate using ERPNext standard session.
    """
    args = _form_dict()
    params = auth_params.validate_login_args(args)
    _get_service().login(email=params["email"], password=params["password"])
    return success_response(message="Login successful.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def logout() -> dict[str, Any]:
    """
    Destroy the current ERPNext session.
    """
    _get_service().logout()
    return success_response(message="Logged out successfully.")


@frappe.whitelist(methods=["GET"])
def me() -> dict[str, Any]:
    """
    Return the currently logged-in user's profile and linked Customer.
    """
    profile = _get_service().get_profile()
    return success_response(
        message="Profile fetched successfully.",
        data=profile.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}
