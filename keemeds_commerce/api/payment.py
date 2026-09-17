"""
Payment API

Whitelisted (:mod:`/api/method`) endpoints for the ERPNext-backed payment flow.

- ``POST /api/method/keemeds_commerce.api.payment.create_payment`` — create (or
  replay) a gateway-ready payment session for a Draft Sales Order.
- ``POST /api/method/keemeds_commerce.api.payment.verify_payment`` — validate
  the gateway response (signature + amount + ownership) and complete the
  payment (submitted Payment Entry + Sales Order payment status).
- ``POST /api/method/keemeds_commerce.api.payment.complete_payment`` — idempotent
  completion of an already-verified payment.
- ``POST /api/method/keemeds_commerce.api.payment.retry_payment`` — fresh
  payment session for the same Draft Sales Order after a failure.
- ``POST /api/method/keemeds_commerce.api.payment.fail_payment`` — record a
  failure, keep the Sales Order Draft, allow retry.
- ``GET  /api/method/keemeds_commerce.api.payment.status`` — Pending /
  Processing / Paid / Failed / Cancelled.
- ``GET  /api/method/keemeds_commerce.api.payment.history`` — payment history
  for an order.
- ``POST /api/method/keemeds_commerce.api.payment.webhook`` — gateway callback
  (signature-authenticated, duplicate-safe, guest-accessible).

These controllers are the API/controller layer only: they validate parameters,
delegate to the Payment service and serialize DTOs. All user endpoints require
an authenticated (non-guest) Website User session; the webhook authenticates
itself through the gateway signature.
"""

from __future__ import annotations

from typing import Any

import frappe

from keemeds_commerce.services.payment_service import PaymentService
from keemeds_commerce.utils.api_response import success_response
from keemeds_commerce.validators import checkout_params, payment_params

# ---------------------------------------------------------------------- #
# Dependency wiring (wired once per process; lazily instantiated).
# ---------------------------------------------------------------------- #

_service: PaymentService | None = None


def _get_service() -> PaymentService:
    global _service
    if _service is None:
        _service = PaymentService()
    return _service


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
def create_payment() -> dict[str, Any]:
    """
    Create (or replay) a gateway-ready payment session for a Draft Sales Order.
    """
    args = _form_dict()
    session = _get_service().create_payment(
        sales_order=payment_params.order_name(args),
        shipping_address_name=checkout_params.optional_address_name(
            args, "shipping_address_name"
        ),
        billing_address_name=checkout_params.optional_address_name(
            args, "billing_address_name"
        ),
        method=payment_params.optional_payment_method(args) or None,
    )
    return success_response(
        message="Payment session created successfully.",
        data=session.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def verify_payment() -> dict[str, Any]:
    """
    Verify the gateway response and complete the payment.
    """
    args = _form_dict()
    payment = _get_service().verify_payment(
        sales_order=payment_params.order_name(args) if _has(args, "sales_order") else None,
        session=payment_params.session_name(args) if _has(args, "session") else None,
        amount=payment_params.amount(args),
        signature=payment_params.signature(args),
        method=payment_params.optional_payment_method(args) or None,
    )
    return success_response(
        message="Payment verified and completed successfully.",
        data=payment.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def complete_payment() -> dict[str, Any]:
    """
    Idempotently complete an already-verified payment.
    """
    args = _form_dict()
    payment = _get_service().complete_payment(
        sales_order=payment_params.order_name(args) if _has(args, "sales_order") else None,
        session=payment_params.session_name(args) if _has(args, "session") else None,
        transaction_id=payment_params.optional_reason(args, "transaction_id") or None,
        method=payment_params.optional_payment_method(args) or None,
    )
    return success_response(
        message="Payment completed successfully.",
        data=payment.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def retry_payment() -> dict[str, Any]:
    """
    Open a fresh payment session for the same Draft Sales Order.
    """
    args = _form_dict()
    session = _get_service().retry_payment(
        sales_order=payment_params.order_name(args),
        shipping_address_name=checkout_params.optional_address_name(
            args, "shipping_address_name"
        ),
        billing_address_name=checkout_params.optional_address_name(
            args, "billing_address_name"
        ),
        method=payment_params.optional_payment_method(args) or None,
    )
    return success_response(
        message="Payment retry session created successfully.",
        data=session.to_dict(),
    )


@frappe.whitelist(methods=["POST"])
def fail_payment() -> dict[str, Any]:
    """
    Mark the payment failed (Sales Order stays Draft, retry allowed).
    """
    args = _form_dict()
    state = _get_service().fail_payment(
        sales_order=payment_params.order_name(args) if _has(args, "sales_order") else None,
        session=payment_params.session_name(args) if _has(args, "session") else None,
        reason=payment_params.optional_reason(args, "reason") or "Payment failed.",
    )
    return success_response(
        message="Payment marked as failed.",
        data=state.to_dict(),
    )


@frappe.whitelist(methods=["GET"])
def status() -> dict[str, Any]:
    """
    Return the current payment status for an order or a session.
    """
    args = _form_dict()
    state = _get_service().status(
        sales_order=payment_params.order_name(args, "sales_order")
        if _has(args, "sales_order")
        else None,
        session=payment_params.session_name(args) if _has(args, "session") else None,
    )
    return success_response(message="Payment status fetched successfully.", data=state.to_dict())


@frappe.whitelist(methods=["GET"])
def history() -> dict[str, Any]:
    """
    Return the payment history for a Draft Sales Order.
    """
    args = _form_dict()
    history = _get_service().history(sales_order=payment_params.order_name(args))
    return success_response(
        message="Payment history fetched successfully.",
        data=history.to_dict(),
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook() -> dict[str, Any]:
    """
    Process a gateway callback (signature-authenticated, duplicate-safe).
    """
    payload = _form_dict()
    result = _get_service().webhook(dict(payload or {}))
    return success_response(
        message="Webhook processed successfully.",
        data=result.to_dict(),
    )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _form_dict() -> dict[str, Any]:
    """Return the current request parameters (thread/request-local)."""
    return frappe.local.form_dict or {}


def _has(args: dict[str, Any], field: str) -> bool:
    """True when the raw argument is present and non-blank."""
    value = args.get(field)
    return value is not None and value != ""