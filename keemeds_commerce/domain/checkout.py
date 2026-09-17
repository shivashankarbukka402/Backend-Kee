"""
Checkout Domain DTOs

Plain data holders for the Checkout API layer. They carry no logic beyond
serialization and never touch the database. They decouple the Checkout service
and API controllers from ERPNext documents.

Total model
-----------
- ``subtotal``: sum of every line ``quantity * selling_price``.
- ``discount``: flat configured discount (clamped to the subtotal).
- ``tax``: configured tax rate applied to the discounted net total.
- ``shipping_charge``: flat configured delivery charge.
- ``grand_total``: ``subtotal - discount + tax + shipping_charge``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from keemeds_commerce.domain.address import AddressDTO


@dataclass(frozen=True)
class CheckoutItemDTO:
    """
    A single purchase line of the checkout summary.
    """

    item_code: str = ""
    item_name: str = ""
    brand: str = ""
    image: str = ""
    quantity: float = 0.0
    selling_price: float = 0.0
    subtotal: float = 0.0
    stock_status: str = "out_of_stock"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckoutSummaryDTO:
    """
    A validated preview of the payable order for the current cart.
    """

    user_email: str = ""
    customer_id: str = ""
    customer_name: str = ""
    currency: str = "INR"
    items: list[CheckoutItemDTO] = field(default_factory=list)
    subtotal: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    shipping_charge: float = 0.0
    grand_total: float = 0.0
    shipping_address: AddressDTO | None = None
    billing_address: AddressDTO | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_email": self.user_email,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "currency": self.currency,
            "items": [item.to_dict() for item in self.items],
            "subtotal": self.subtotal,
            "discount": self.discount,
            "tax": self.tax,
            "shipping_charge": self.shipping_charge,
            "grand_total": self.grand_total,
            "shipping_address": (
                self.shipping_address.to_dict() if self.shipping_address else None
            ),
            "billing_address": (
                self.billing_address.to_dict() if self.billing_address else None
            ),
        }


@dataclass(frozen=True)
class CheckoutOrderDTO:
    """
    The result of converting a checkout into a Draft Sales Order.
    """

    sales_order: str = ""
    status: str = "Draft"
    docstatus: int = 0
    grand_total: float = 0.0
    currency: str = "INR"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)