"""
Order Domain DTOs

Plain data holders for the Order API layer. They carry no logic beyond
serialization and never touch the database. They decouple the Order service and
API controllers from ERPNext Sales Order / Address / Company documents.

The DTOs mirror the ERPNext Sales Order document (name/status/docstatus/
transaction_date/delivery_date/grand_total/items/addresses) so they are
consistent with the checkout SummaryDTO family already used by the cart and
checkout services, and map 1:1 to the storefront ``ErpOrderDTO`` type.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from keemeds_commerce.domain.address import AddressDTO


@dataclass(frozen=True)
class OrderItemDTO:
    """
    A single order line (mirrors a Sales Order Item row).
    """

    item_code: str = ""
    item_name: str = ""
    brand: str = ""
    image: str = ""
    quantity: float = 0.0
    selling_price: float = 0.0
    subtotal: float = 0.0
    stock_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderDTO:
    """
    Full storefront order as returned by the list / detail endpoints.
    """

    name: str = ""
    status: str = "Draft"
    docstatus: int = 0
    creation: str = ""
    transaction_date: str = ""
    delivery_date: str = ""
    currency: str = "INR"
    grand_total: float = 0.0
    subtotal: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    shipping_charge: float = 0.0
    items: list[OrderItemDTO] = field(default_factory=list)
    shipping_address: AddressDTO | None = None
    billing_address: AddressDTO | None = None
    payment_method: str = ""
    payment_status: str = ""
    tracking_id: str = ""
    invoice_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "docstatus": self.docstatus,
            "creation": self.creation,
            "transaction_date": self.transaction_date,
            "delivery_date": self.delivery_date,
            "currency": self.currency,
            "grand_total": self.grand_total,
            "subtotal": self.subtotal,
            "discount": self.discount,
            "tax": self.tax,
            "shipping_charge": self.shipping_charge,
            "items": [item.to_dict() for item in self.items],
            "shipping_address": (
                self.shipping_address.to_dict() if self.shipping_address else None
            ),
            "billing_address": (
                self.billing_address.to_dict() if self.billing_address else None
            ),
            "payment_method": self.payment_method,
            "payment_status": self.payment_status,
            "tracking_id": self.tracking_id,
            "invoice_id": self.invoice_id,
        }


@dataclass(frozen=True)
class OrderListDTO:
    """
    Paginated list of orders belonging to the authenticated customer.
    """

    orders: list[OrderDTO] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "orders": [order.to_dict() for order in self.orders],
            "total": self.total,
        }


@dataclass(frozen=True)
class InvoiceLineDTO:
    """
    A single printable invoice line.
    """

    name: str = ""
    item_code: str = ""
    quantity: float = 0.0
    unit_price: float = 0.0
    selling_price: float = 0.0
    amount: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InvoiceDTO:
    """
    A printable invoice derived from a paid (or Draft, for preview) Sales Order.
    """

    id: str = ""
    invoice_id: str = ""
    order_id: str = ""
    issued_at: str = ""
    seller: dict[str, Any] = field(default_factory=dict)
    billing_address: AddressDTO | None = None
    items: list[InvoiceLineDTO] = field(default_factory=list)
    subtotal: float = 0.0
    discount: float = 0.0
    delivery_charge: float = 0.0
    tax: float = 0.0
    tax_rate: float = 0.0
    grand_total: float = 0.0
    payment_method: str = ""
    transaction_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "invoice_id": self.invoice_id,
            "order_id": self.order_id,
            "issued_at": self.issued_at,
            "seller": dict(self.seller),
            "billing_address": (
                self.billing_address.to_dict() if self.billing_address else None
            ),
            "items": [item.to_dict() for item in self.items],
            "subtotal": self.subtotal,
            "discount": self.discount,
            "delivery_charge": self.delivery_charge,
            "tax": self.tax,
            "tax_rate": self.tax_rate,
            "grand_total": self.grand_total,
            "payment_method": self.payment_method,
            "transaction_id": self.transaction_id,
        }


@dataclass(frozen=True)
class TrackingEventDTO:
    """
    A single fulfilment timeline event for an order.
    """

    key: str = ""
    type: str = ""
    label: str = ""
    timestamp: str = ""
    description: str = ""
    is_completed: bool = False
    is_current: bool = False
    is_cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrackingDTO:
    """
    Fulfilment tracking timeline for an order.
    """

    status: str = ""
    events: list[TrackingEventDTO] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True)
class CancelResultDTO:
    """
    The result of cancelling a Draft (or cancellable) Sales Order.
    """

    sales_order: str = ""
    status: str = "Cancelled"
    cancelled: bool = True
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
