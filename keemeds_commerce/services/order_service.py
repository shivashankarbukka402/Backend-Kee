"""
Order Service

Business logic for the ERP-backed order management layer. Reads the
authenticated Website User's Draft/Submitted/Cancelled Sales Orders and
serialises them into storefront DTOs (list/detail/invoice/tracking) and
handles Draft order cancellation.

Design notes
------------
- Every operation is scoped to ``frappe.session.user``; Guest sessions and a
  Sales Order belonging to another customer are rejected. Ownership is
  resolved through the same ``Customer <-> Portal User`` link the checkout
  service uses.
- The API never exposes raw ERPNext documents: all rows are mapped to DTOs
  (mirroring the ``SummaryDTO`` family) so the storefront contract stays
  stable.
- An order is always scoped and returned as a whole — cancellation and detail
  both resolve the owning customer from the logged-in user and refuse to read
  another customer's order.
- ``invoice`` derives a printable invoice from the Sales Order (a real
  ``Sales Invoice`` is not yet automatically created at payment time, so the
  DTO is built from the Sales Order + the payment transaction recorded against
  the latest Payment Session). ``tracking`` derives a canonical fulfilment
  timeline from the order status when no explicit tracking is present.
"""

from __future__ import annotations

import logging
from typing import Any

import frappe
from frappe import _

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.address import AddressDTO
from keemeds_commerce.domain.order import (
    CancelResultDTO,
    InvoiceDTO,
    InvoiceLineDTO,
    OrderDTO,
    OrderItemDTO,
    OrderListDTO,
    TrackingDTO,
    TrackingEventDTO,
)
from keemeds_commerce.services.image_resolver import ImageResolver
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_permission_error,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.order")

_SO_ITEM_FIELDS = ("item_code", "item_name", "brand")


class OrderService:
    """
    Reads and cancels the authenticated customer's Sales Orders.
    """

    def __init__(
        self,
        config: CommerceConfig | None = None,
        images: ImageResolver | None = None,
    ) -> None:
        self._config = config or CommerceConfig()
        self._images = images or ImageResolver(self._config)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def list_orders(self, limit: int = 20, offset: int = 0) -> OrderListDTO:
        """
        Return the authenticated customer's Sales Orders (newest first).
        """
        customer = self._customer_for(self._current_user())
        limit = max(0, int(limit or 0))
        offset = max(0, int(offset or 0))
        rows = frappe.get_all(
            "Sales Order",
            filters={"customer": customer["name"]},
            fields=["name"],
            order_by="creation desc",
            limit_page_length=limit or 50,
            start=offset,
            ignore_permissions=True,
        )
        names = [row["name"] for row in rows]
        orders = [self._order_dto(so_name) for so_name in names]
        total = frappe.db.count("Sales Order", {"customer": customer["name"]})
        return OrderListDTO(orders=orders, total=total)

    def order_detail(self, order_id: str) -> OrderDTO:
        """
        Return a single Sales Order scoped to the authenticated customer.
        """
        self._owned_order(order_id)
        return self._order_dto(order_id)

    def invoice(self, order_id: str) -> InvoiceDTO:
        """
        Return a printable invoice derived from the (owned) Sales Order.
        """
        self._owned_order(order_id)
        return self._invoice_dto(order_id)

    def tracking(self, order_id: str) -> TrackingDTO:
        """
        Return the fulfilment timeline for an (owned) Sales Order.
        """
        self._owned_order(order_id)
        return self._tracking_dto(order_id)

    def cancel(self, order_id: str, reason: str) -> CancelResultDTO:
        """
        Cancel a Draft (or otherwise cancellable) Sales Order.

        Draft orders are deleted (cancelled) in place; submitted orders that do
        not yet have payments are cancelled if they are still cancellable.
        """
        self._owned_order(order_id)
        so = frappe.get_doc("Sales Order", order_id)
        if so.docstatus == 0:
            frappe.delete_doc("Sales Order", order_id, force=1)
            logger.info("Draft Sales Order %s cancelled (%s)", order_id, reason)
            return CancelResultDTO(
                sales_order=order_id,
                status="Cancelled",
                cancelled=True,
                message=_("Order cancelled successfully."),
            )
        if so.docstatus == 2:
            return CancelResultDTO(
                sales_order=order_id,
                status="Cancelled",
                cancelled=True,
                message=_("Order is already cancelled."),
            )
        raise_validation_error(
            _("Only Draft orders can be cancelled; '{0}' is submitted.").format(order_id)
        )

    # ------------------------------------------------------------------ #
    # DTO assembly
    # ------------------------------------------------------------------ #

    def _order_dto(self, so_name: str) -> OrderDTO:
        doc = frappe.get_doc("Sales Order", so_name)
        items = [_item_dto(row, self._images) for row in doc.items]
        return OrderDTO(
            name=doc.name,
            status=doc.status or "Draft",
            docstatus=doc.docstatus or 0,
            creation=_str_dt(doc.creation),
            transaction_date=_str_date(doc.transaction_date),
            delivery_date=_str_date(doc.delivery_date),
            currency=doc.currency or "INR",
            grand_total=_num(doc.grand_total),
            subtotal=_num(doc.net_total),
            discount=_num(doc.discount_amount),
            tax=_num(_tax_total(doc)),
            shipping_charge=_num(_shipping_total(doc)),
            items=items,
            shipping_address=_address_from_so(doc, "shipping_address_name"),
            billing_address=_address_from_so(doc, "customer_address"),
            payment_method=doc.get("payment_method") or "",
            payment_status=doc.get("payment_status") or "Pending",
            tracking_id=doc.get("tracking_id") or "",
            invoice_id=f"INV-{doc.name}",
        )

    def _invoice_dto(self, so_name: str) -> InvoiceDTO:
        doc = frappe.get_doc("Sales Order", so_name)
        document_date = _str_date(doc.transaction_date) or _str_dt(doc.creation)
        items = [
            InvoiceLineDTO(
                name=row.name or "",
                item_code=row.item_code or "",
                quantity=_num(row.qty),
                unit_price=_num(row.rate),
                selling_price=_num(row.rate),
                amount=_num(row.amount),
            )
            for row in doc.items
        ]
        transaction_id = self._latest_transaction_id(so_name)
        return InvoiceDTO(
            id=f"INV-{doc.name}",
            invoice_id=f"INV-{doc.name}",
            order_id=doc.name,
            issued_at=document_date,
            seller=_seller_info(doc.get("company")),
            billing_address=_address_from_so(doc, "customer_address"),
            items=items,
            subtotal=_num(doc.net_total),
            discount=_num(doc.discount_amount),
            delivery_charge=_num(_shipping_total(doc)),
            tax=_num(_tax_total(doc)),
            tax_rate=_num(_tax_rate(doc)),
            grand_total=_num(doc.grand_total),
            payment_method=doc.get("payment_method") or "",
            transaction_id=transaction_id,
        )

    def _tracking_dto(self, so_name: str) -> TrackingDTO:
        doc = frappe.get_doc("Sales Order", so_name)
        status = _normalise_status(doc.status or "Draft")
        events = _build_tracking_events(doc)
        return TrackingDTO(status=status, events=events)

    # ------------------------------------------------------------------ #
    # Ownership / helpers
    # ------------------------------------------------------------------ #

    def _current_user(self) -> str:
        user = frappe.session.user
        if not user or user == "Guest":
            raise_validation_error(_("You must be logged in to perform this action."))
        return user

    def _customer_for(self, user: str) -> dict:
        rows = frappe.db.sql(
            """
            SELECT c.name, c.customer_name
            FROM `tabCustomer` c
            INNER JOIN `tabPortal User` pu ON pu.parent = c.name
            WHERE pu.user = %s AND pu.parenttype = 'Customer'
            LIMIT 1
            """,
            (user,),
            as_dict=True,
        )
        if not rows:
            raise_validation_error(_("No customer account is linked to this user."))
        return rows[0]

    def _owned_order(self, so_name: str) -> dict:
        """
        Return the Sales Order's customer mapping after verifying it belongs to
        the authenticated user.
        """
        customer = self._customer_for(self._current_user())
        row = frappe.db.get_value(
            "Sales Order", so_name, ["customer", "docstatus"], as_dict=True
        )
        if not row:
            raise_not_found("Sales Order", so_name)
        if row["customer"] != customer["name"]:
            raise_permission_error(_("You do not have permission to access this order."))
        return customer

    def _latest_transaction_id(self, so_name: str) -> str:
        """Return the latest Payment Session transaction id for the order."""
        value = frappe.db.sql(
            """
            SELECT transaction_id
            FROM `tabPayment Session`
            WHERE sales_order = %s AND transaction_id != ''
            ORDER BY creation DESC
            LIMIT 1
            """,
            (so_name,),
        )
        return (value[0][0] if value and value[0][0] else "") or ""


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _item_dto(row, images: ImageResolver) -> OrderItemDTO:
    image = _resolve_image(images, row.item_code or "")
    return OrderItemDTO(
        item_code=row.item_code or "",
        item_name=row.item_name or "",
        brand=row.brand or "",
        image=image,
        quantity=_num(row.qty),
        selling_price=_num(row.rate),
        subtotal=_num(row.amount),
    )


def _resolve_image(images: ImageResolver, item_code: str) -> str:
    try:
        return images.resolve(item_code).primary_image
    except Exception:
        return ""


def _address_from_so(doc, field: str) -> AddressDTO | None:
    name = doc.get(field)
    if not name:
        return None
    try:
        addr = frappe.get_doc("Address", name)
    except frappe.DoesNotExistError:
        return None
    return AddressDTO(
        name=addr.name or "",
        address_type=addr.address_type or "",
        address_title=addr.address_title or "",
        address_line1=addr.address_line1 or "",
        address_line2=addr.address_line2 or "",
        city=addr.city or "",
        state=addr.state or "",
        country=addr.country or "",
        pincode=addr.pincode or "",
        phone=addr.phone or "",
        email_id=addr.email_id or "",
        is_primary_address=bool(addr.is_primary_address),
        is_shipping_address=bool(addr.is_shipping_address),
    )


def _tax_total(doc) -> float:
    total = 0.0
    for row in doc.get("taxes") or []:
        if row.get("category") == "Valuation":
            continue
        charge = row.get("charge_type") or ""
        if charge == "Actual":
            total += _num(row.get("tax_amount"))
        elif charge == "On Net Total" or charge == "On Previous Row Amount":
            total += _num(row.get("tax_amount"))
    return total


def _shipping_total(doc) -> float:
    total = 0.0
    for row in doc.get("taxes") or []:
        if row.get("charge_type") == "Actual":
            total += _num(row.get("tax_amount"))
    return total


def _tax_rate(doc) -> float:
    rate = 0.0
    for row in doc.get("taxes") or []:
        if row.get("charge_type") == "On Net Total":
            rate = _num(row.get("rate"))
            break
    return rate


def _normalise_status(status: str) -> str:
    s = (status or "").lower()
    if "cancelled" in s or "deleted" in s:
        return "Cancelled"
    if "closed" in s or "completed" in s:
        return "Delivered"
    if "draft" in s:
        return "Draft"
    if "out for delivery" in s:
        return "Out for Delivery"
    if "shipped" in s:
        return "Shipped"
    if "packed" in s:
        return "Packed"
    if "on hold" in s:
        return "On Hold"
    return "Confirmed"


def _build_tracking_events(doc) -> list[TrackingEventDTO]:
    events: list[TrackingEventDTO] = []
    creation = _str_dt(doc.creation)
    status = _normalise_status(doc.status or "Draft")
    if status == "Draft":
        events.append(
            TrackingEventDTO(
                key="placed",
                type="order_placed",
                label="Order Placed",
                timestamp=creation,
                description="Your order has been placed successfully.",
                is_completed=True,
                is_current=True,
            )
        )
        return events
    events.append(
        TrackingEventDTO(
            key="placed",
            type="order_placed",
            label="Order Placed",
            timestamp=creation,
            description="Your order has been placed successfully.",
            is_completed=True,
            is_current=status == "Confirmed",
        )
    )
    confirmed_ts = _str_dt(doc.transaction_date or doc.creation)
    events.append(
        TrackingEventDTO(
            key="confirmed",
            type="order_confirmed",
            label="Order Confirmed",
            timestamp=confirmed_ts,
            description="Your order has been confirmed.",
            is_completed=status not in ("Confirmed",),
            is_current=status == "Confirmed",
        )
    )
    completed = status in ("Shipped", "Out for Delivery", "Delivered", "Cancelled")
    events.append(
        TrackingEventDTO(
            key="packed",
            type="order_packed",
            label="Item Packed",
            timestamp="",
            description="Your items are being packed.",
            is_completed=completed,
            is_current=False,
        )
    )
    events.append(
        TrackingEventDTO(
            key="shipped",
            type="order_shipped",
            label="Shipped",
            timestamp=_str_dt(doc.delivery_date),
            description="Your items have been handed over to the delivery partner.",
            is_completed=status in ("Delivered", "Cancelled"),
            is_current=status == "Shipped",
        )
    )
    delivered = status in ("Delivered",)
    events.append(
        TrackingEventDTO(
            key="delivered",
            type="order_delivered",
            label="Delivered",
            timestamp=_str_dt(doc.delivery_date),
            description="Your order has been delivered.",
            is_completed=delivered,
            is_current=delivered,
        )
    )
    if status == "Cancelled":
        events.append(
            TrackingEventDTO(
                key="cancelled",
                type="order_cancelled",
                label="Cancelled",
                timestamp=_str_dt(doc.modified),
                description="Your order has been cancelled.",
                is_completed=True,
                is_current=True,
                is_cancelled=True,
            )
        )
    return events


def _seller_info(company: str | None) -> dict[str, Any]:
    try:
        company_doc = frappe.get_doc("Company", company) if company else None
    except frappe.DoesNotExistError:
        company_doc = None
    info: dict[str, Any] = {
        "name": company_doc.get("company_name") if company_doc else (company or ""),
        "address": "",
        "gstin": "",
        "contact": "",
    }
    if not company_doc:
        return info
    info["name"] = company_doc.get("company_name") or company or ""
    info["gstin"] = company_doc.get("gstin") or ""
    info["contact"] = company_doc.get("phone") or ""
    primary = _company_address(company_doc.name)
    if primary:
        info["address"] = " ".join(
            filter(
                None,
                [primary.address_line1, primary.address_line2, primary.city, primary.pincode],
            )
        )
    return info


def _company_address(company: str) -> Any:
    try:
        rows = frappe.get_all(
            "Address",
            filters=[
                ["Dynamic Link", "link_doctype", "=", "Company"],
                ["Dynamic Link", "link_name", "=", company],
                ["Dynamic Link", "parenttype", "=", "Address"],
                ["is_primary_address", "=", 1],
            ],
            fields=["address_line1", "address_line2", "city", "pincode"],
            limit=1,
            order_by="creation asc",
        )
        return rows[0] if rows else None
    except Exception:
        return None


def _num(value: Any) -> float:
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _str_date(value: Any) -> str:
    if not value:
        return ""
    return str(value)


def _str_dt(value: Any) -> str:
    if not value:
        return ""
    return str(value)
