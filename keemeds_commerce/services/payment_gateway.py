"""
Payment Gateway Adapter

Gateway-agnostic contract that isolates the payment service from any specific
provider. Adapters translate a :class:`GatewayIntent` into a "gateway-ready"
payload, verify a return/verify-callback, and authenticate webhook events
(signature + event authenticity).

Design notes
------------
- The provided ``TestGateway`` is the built-in, gateway-agnostic adapter. It
  uses HMAC-SHA256 signatures so the full flow (create -> verify -> webhook
  -> Payment Entry) is exercised end-to-end without any external provider.
  Live providers (Razorpay / PhonePe / Cashfree) are added as new adapters
  behind the same interface without changing the Payment service or API.
- Unknown gateway names fail closed (:class:`ValidationError`).
"""

from __future__ import annotations

import hmac
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from frappe import _

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.utils.exceptions import raise_validation_error

#: Gateway product name reported to the storefront and stored on sessions.
_GATEWAY_NAME = "test"
#: Default payment method label for the built-in adapter.
_METHOD = "test"


@dataclass(frozen=True)
class GatewayIntent:
    """
    Immutable intent handed to a gateway adapter for signing / payloading.
    """

    gateway: str = _GATEWAY_NAME
    session: str = ""
    sales_order: str = ""
    idempotency_key: str = ""
    amount: float = 0.0
    currency: str = "INR"
    customer: str = ""
    customer_name: str = ""
    user_email: str = ""
    payment_method: str = _METHOD


@dataclass(frozen=True)
class GatewayVerification:
    """
    Result of verifying a callback (return-verification or webhook event).

    ``status`` is the canonical payment status derived from the gateway event
    (``Paid`` / ``Failed``) or ``None`` when the event carries no status.
    """

    valid: bool = False
    transaction_id: str = ""
    status: str | None = None
    reason: str = ""
    extra: dict = field(default_factory=dict)


class PaymentGatewayAdapter(ABC):
    """Contract every gateway adapter must implement."""

    name: str = _GATEWAY_NAME

    @abstractmethod
    def build_payload(self, intent: GatewayIntent) -> dict:
        """Return the gateway-ready fields posted to the storefront."""

    @abstractmethod
    def verify(
        self, intent: GatewayIntent, amount: float, currency: str, signature: str
    ) -> GatewayVerification:
        """Validate the signature and amount of a return-verification."""

    @abstractmethod
    def authenticate_webhook(self, payload: dict) -> GatewayVerification:
        """Validate signature and event authenticity of a webhook callback."""


class TestGateway(PaymentGatewayAdapter):
    """
    Built-in HMAC-signed gateway used until a live provider is integrated.
    """

    name = _GATEWAY_NAME

    def __init__(self, config: CommerceConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------ #
    # Public contract
    # ------------------------------------------------------------------ #

    def build_payload(self, intent: GatewayIntent) -> dict:
        signature = self._signature(
            self._config.payment_verify_secret,
            intent.gateway,
            intent.idempotency_key,
            intent.session,
            intent.sales_order,
            _fmt_amount(intent.amount),
            intent.currency,
        )
        return {
            "gateway": intent.gateway,
            "method": intent.payment_method,
            "order_id": intent.idempotency_key,
            "session": intent.session,
            "sales_order": intent.sales_order,
            "amount": _fmt_amount(intent.amount),
            "amount_in_paise": _paise(intent.amount),
            "currency": intent.currency,
            "customer": {
                "id": intent.customer,
                "name": intent.customer_name,
                "email": intent.user_email,
            },
            "signature": signature,
            "verify_endpoint": "/api/method/keemeds_commerce.api.payment.verify_payment",
            "status_endpoint": "/api/method/keemeds_commerce.api.payment.status",
        }

    def verify(
        self, intent: GatewayIntent, amount: float, currency: str, signature: str
    ) -> GatewayVerification:
        expected = self._signature(
            self._config.payment_verify_secret,
            intent.gateway,
            intent.idempotency_key,
            intent.session,
            intent.sales_order,
            _fmt_amount(intent.amount),
            intent.currency,
        )
        if not _safe_equal(signature, expected):
            return GatewayVerification(valid=False, reason="Invalid payment signature.")
        return GatewayVerification(
            valid=True,
            transaction_id=f"kc_{intent.idempotency_key[:16]}",
            status="Paid",
        )

    def authenticate_webhook(self, payload: dict) -> GatewayVerification:
        event_id = str(payload.get("event_id") or "").strip()
        signature = str(payload.get("signature") or "").strip()
        if not event_id or not signature:
            return GatewayVerification(valid=False, reason="Webhook event/signature missing.")
        expected = self._signature(
            self._config.payment_webhook_secret,
            payload.get("gateway") or self.name,
            event_id,
            str(payload.get("order_id") or ""),
            str(payload.get("session") or ""),
            str(payload.get("sales_order") or ""),
            str(payload.get("status") or ""),
            str(payload.get("transaction_id") or ""),
            _fmt_amount(_to_amount(payload.get("amount"))),
            str(payload.get("currency") or ""),
        )
        if not _safe_equal(signature, expected):
            return GatewayVerification(valid=False, reason="Invalid webhook signature.")
        status = _canonical_status(payload.get("status"))
        return GatewayVerification(
            valid=True,
            transaction_id=str(payload.get("transaction_id") or "").strip(),
            status=status,
            reason=str(payload.get("failure_reason") or "").strip(),
            extra={"event_id": event_id},
        )

    # ------------------------------------------------------------------ #
    # Signing
    # ------------------------------------------------------------------ #

    def _signature(self, secret: str, *parts: str) -> str:
        canonical = "|".join(parts)
        return hmac.new(
            secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def sign_webhook(self, payload: dict) -> str:
        """Sign a callback payload (used by tests to simulate the gateway)."""
        return self._signature(
            self._config.payment_webhook_secret,
            payload.get("gateway") or self.name,
            str(payload.get("event_id") or ""),
            str(payload.get("order_id") or ""),
            str(payload.get("session") or ""),
            str(payload.get("sales_order") or ""),
            str(payload.get("status") or ""),
            str(payload.get("transaction_id") or ""),
            _fmt_amount(_to_amount(payload.get("amount"))),
            str(payload.get("currency") or ""),
        )

    def sign_intent(self, intent: GatewayIntent) -> str:
        """Sign a payment intent (returned inside the gateway-ready payload)."""
        return self._signature(
            self._config.payment_verify_secret,
            intent.gateway,
            intent.idempotency_key,
            intent.session,
            intent.sales_order,
            _fmt_amount(intent.amount),
            intent.currency,
        )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def get_gateway(config: CommerceConfig) -> PaymentGatewayAdapter:
    """
    Return the gateway adapter for ``config.payment_gateway`` (fail closed).
    """
    name = (config.payment_gateway or "").strip().upper()
    if name in ("", "TEST"):
        return TestGateway(config)
    raise_validation_error(_("Unsupported payment gateway '{0}'.").format(name))


def _fmt_amount(amount: float) -> str:
    return f"{round(float(amount or 0.0), 2):.2f}"


def _paise(amount: float) -> int:
    return int(round(float(amount or 0.0) * 100.0))


def _to_amount(value) -> float:
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _canonical_status(raw) -> str | None:
    status = str(raw or "").strip().lower()
    if status in ("paid", "captured", "success", "authorized"):
        return "Paid"
    if status in ("failed", "declined", "rejected"):
        return "Failed"
    return None


def _safe_equal(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(str(candidate or "").encode("utf-8"), str(expected or "").encode("utf-8"))