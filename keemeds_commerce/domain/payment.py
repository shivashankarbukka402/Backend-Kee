"""
Payment Domain DTOs

Plain data holders for the Payment API layer. They carry no logic beyond
serialization and never touch the database. They decouple the Payment service
and API controllers from ERPNext documents and gateway payloads.

Status model
------------
A ``Payment Session`` transitions ``Pending -> Processing -> Paid`` or
``Pending -> Failed`` / ``Pending -> Cancelled``. Payment history keeps every
session; retrying a failed/cancelled attempt creates a new session against the
same Draft Sales Order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PaymentSessionDTO:
    """
    A created (gateway-ready) payment session.
    """

    session: str = ""
    session_token: str = ""
    idempotency_key: str = ""
    sales_order: str = ""
    customer: str = ""
    customer_name: str = ""
    user_email: str = ""
    gateway: str = ""
    payment_method: str = "test"
    amount: float = 0.0
    currency: str = "INR"
    amount_in_paise: int = 0
    status: str = "Pending"
    signature: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_on: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "session_token": self.session_token,
            "idempotency_key": self.idempotency_key,
            "sales_order": self.sales_order,
            "customer": self.customer,
            "customer_name": self.customer_name,
            "user_email": self.user_email,
            "gateway": self.gateway,
            "payment_method": self.payment_method,
            "amount": self.amount,
            "currency": self.currency,
            "amount_in_paise": self.amount_in_paise,
            "status": self.status,
            "signature": self.signature,
            "payload": dict(self.payload),
            "created_on": self.created_on,
        }


@dataclass(frozen=True)
class PaymentCompletionDTO:
    """
    The result of a successfully completed (paid) payment.
    """

    success: bool = True
    session: str = ""
    sales_order: str = ""
    payment_entry: str = ""
    transaction_id: str = ""
    status: str = "Paid"
    amount: float = 0.0
    currency: str = "INR"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaymentStatusDTO:
    """
    Current payment status for an order.
    """

    status: str = "Pending"
    sales_order: str = ""
    session: str = ""
    amount: float = 0.0
    currency: str = "INR"
    transaction_id: str = ""
    payment_entry: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaymentHistoryItemDTO:
    """
    A single attempt in the payment history of an order.
    """

    transaction_id: str = ""
    gateway: str = ""
    payment_method: str = "test"
    amount: float = 0.0
    currency: str = "INR"
    status: str = "Pending"
    timestamp: str = ""
    session: str = ""
    sales_order: str = ""
    payment_entry: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PaymentHistoryDTO:
    """
    Ordered payment history for an order.
    """

    sales_order: str = ""
    items: list[PaymentHistoryItemDTO] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sales_order": self.sales_order,
            "items": [item.to_dict() for item in self.items],
            "total": self.total,
        }


@dataclass(frozen=True)
class PaymentWebhookDTO:
    """
    Result of processing a gateway webhook callback.
    """

    accepted: bool = True
    duplicate: bool = False
    event_id: str = ""
    session: str = ""
    sales_order: str = ""
    status: str = ""
    transaction_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)