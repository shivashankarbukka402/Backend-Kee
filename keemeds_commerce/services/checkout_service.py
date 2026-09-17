"""
Checkout Service

Business logic for the ERP-backed checkout. Converts an authenticated Website
User's :doc:`Cart` into a validated purchase preview ({:class:`CheckoutSummaryDTO`})
and, on :meth:`create_order`, into a Draft :doc:`Sales Order`.

Design notes
------------
- Every operation is scoped to ``frappe.session.user``; Guest sessions and
  another user's Cart/Address documents are rejected.
- Item pricing reuses the ERPNext pricing layer (:class:`PricingService`) — the
  checkout never re-derives selling prices.
- Totals: ``subtotal`` from item pricing; ``discount``/``tax``/``shipping``
  come from ``CommerceConfig`` (flat values; ``0`` when the ERPNext site has no
  tax/shipping rules configured yet). The Draft Sales Order persists the same
  values as ERPNext tax rows (with ``apply_discount_on='Net Total'``) so the
  saved document recomputes to the same grand total the summary reported.
- Writes are atomic: the Sales Order is saved in a single commit, with
  rollback on failure. ``create_order`` only drafts the order — it never
  submits, clears the cart or touches payment.
- Duplicate protection: repeated ``create_order`` calls carrying an identical
  order (same items, quantities, addresses and total) within the configured
  window replay the existing Draft Sales Order instead of creating a second
  one. Per-customer creation is serialised with a MySQL advisory lock so even
  concurrent storefront retries cannot produce duplicate drafts. The frontend
  API contract is unchanged — the replayed response has the same shape and
  simply returns the same Draft Sales Order name.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime, today

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.address import AddressDTO
from keemeds_commerce.domain.checkout import (
    CheckoutItemDTO,
    CheckoutOrderDTO,
    CheckoutSummaryDTO,
)
from keemeds_commerce.services.image_resolver import ImageResolver
from keemeds_commerce.services.pricing_service import PricingService
from keemeds_commerce.services.stock_service import StockService
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_permission_error,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.checkout")

_CART_DOCTYPE = "Cart"

#: Seconds a customer is waited on while another request serialises the same
#: customer's order creation (advisory lock watchdog).
_LOCK_TIMEOUT_SECONDS = 5

#: Minimum Item fields resolved while building checkout lines.
_ITEM_FIELDS = ("item_code", "item_name", "brand")


class CheckoutService:
    """
    Builds a validated checkout and Draft Sales Order from a user's cart.
    """

    def __init__(
        self,
        config: CommerceConfig | None = None,
        pricing: PricingService | None = None,
        stock: StockService | None = None,
        images: ImageResolver | None = None,
    ) -> None:
        self._config = config or CommerceConfig()
        self._pricing = pricing or PricingService(self._config)
        self._stock = stock or StockService(self._config)
        self._images = images or ImageResolver(self._config)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get_summary(
        self,
        shipping_address_name: str | None = None,
        billing_address_name: str | None = None,
    ) -> CheckoutSummaryDTO:
        """
        Return a validated preview of the payable order for the current cart.

        Raises when the cart is empty or any line/address fails validation, so
        the summary is always a valid order preview.
        """
        return self._build_summary(shipping_address_name, billing_address_name)

    def validate(
        self,
        shipping_address_name: str | None = None,
        billing_address_name: str | None = None,
    ) -> CheckoutSummaryDTO:
        """
        Return the validated summary without creating any document.
        """
        return self._build_summary(shipping_address_name, billing_address_name)

    def create_order(
        self,
        shipping_address_name: str | None = None,
        billing_address_name: str | None = None,
    ) -> CheckoutOrderDTO:
        """
        Create a Draft Sales Order for the current user's valid cart.

        Replays an identical pending Draft (same content within the configured
        window) instead of creating a duplicate, and serialises per-customer
        creation so concurrent storefront retries cannot double-submit. The
        cart is left untouched and no payment is processed.
        """
        user = self._current_user()
        summary = self._build_summary(shipping_address_name, billing_address_name)
        customer = summary.customer_id or self._customer_for(user)["name"]

        lock_name = _lock_name(customer)
        try:
            if not _acquire_lock(lock_name):
                raise_validation_error(
                    _("Order processing is busy. Please try again.")
                )
            pending = self._pending_identical_order(customer, summary)
            if pending:
                logger.info(
                    "Replaying pending Draft Sales Order %s for %s",
                    pending,
                    user,
                )
                return self._order_dto_from_doc(frappe.get_doc("Sales Order", pending))
            return self._create_draft(customer, summary)
        finally:
            _release_lock(lock_name)

    # ------------------------------------------------------------------ #
    # Draft creation
    # ------------------------------------------------------------------ #

    def _create_draft(
        self, customer: str, summary: CheckoutSummaryDTO
    ) -> CheckoutOrderDTO:
        """Build, persist and return a Draft Sales Order for ``summary``."""
        delivery_date = add_days(today(), self._config.checkout_delivery_lead_days)
        so = frappe.get_doc(
            {
                "doctype": "Sales Order",
                "customer": customer,
                "transaction_date": today(),
                "delivery_date": delivery_date,
                "currency": summary.currency,
                "shipping_address_name": (
                    summary.shipping_address.name if summary.shipping_address else ""
                ),
                "customer_address": (
                    summary.billing_address.name if summary.billing_address else ""
                ),
                "items": [
                    {
                        "item_code": item.item_code,
                        "item_name": item.item_name,
                        "qty": item.quantity,
                        "rate": item.selling_price,
                        "amount": item.subtotal,
                        "delivery_date": delivery_date,
                    }
                    for item in summary.items
                ],
            }
        )

        if summary.discount > 0:
            so.apply_discount_on = "Net Total"
            so.discount_amount = summary.discount

        if summary.tax > 0:
            so.append(
                "taxes",
                {
                    "charge_type": "On Net Total",
                    "account_head": self._config.checkout_tax_account,
                    "rate": self._config.checkout_tax_rate,
                    "description": "Sales Tax",
                },
            )

        if summary.shipping_charge > 0:
            so.append(
                "taxes",
                {
                    "charge_type": "Actual",
                    "account_head": self._config.checkout_shipping_account,
                    "tax_amount": summary.shipping_charge,
                    "description": "Shipping Charge",
                },
            )

        try:
            so.flags.ignore_permissions = True
            so.save()
        except Exception:
            frappe.db.rollback()
            raise
        else:
            frappe.db.commit()

        logger.info(
            "Draft Sales Order %s created for %s (grand total %s %s)",
            so.name,
            summary.user_email,
            so.grand_total,
            so.currency,
        )
        return self._order_dto_from_doc(so)

    # ------------------------------------------------------------------ #
    # Duplicate protection
    # ------------------------------------------------------------------ #

    def _pending_identical_order(
        self, customer: str, summary: CheckoutSummaryDTO
    ) -> str | None:
        """
        Return the name of an identical, still-draft Sales Order created within
        the configured window, or ``None``.
        """
        window_minutes = self._config.checkout_duplicate_window_minutes
        if not window_minutes or window_minutes <= 0:
            return None

        since = now_datetime() - timedelta(minutes=window_minutes)
        names = frappe.db.sql(
            """
            SELECT name
            FROM `tabSales Order`
            WHERE customer = %s AND docstatus = 0 AND creation >= %s
            ORDER BY creation DESC
            """,
            (customer, since),
            as_dict=True,
        )
        expected = _summary_fingerprint(summary)
        for row in names:
            if _so_fingerprint(row["name"]) == expected:
                return row["name"]
        return None

    def _order_dto_from_doc(self, so) -> CheckoutOrderDTO:
        return CheckoutOrderDTO(
            sales_order=so.name,
            status=so.status or "Draft",
            docstatus=so.docstatus,
            grand_total=round(float(so.grand_total or 0.0), 2),
            currency=so.currency or "INR",
        )

    # ------------------------------------------------------------------ #
    # Summary assembly
    # ------------------------------------------------------------------ #

    def _build_summary(
        self,
        shipping_address_name: str | None,
        billing_address_name: str | None,
    ) -> CheckoutSummaryDTO:
        user = self._current_user()
        customer = self._customer_for(user)

        cart = self._find_cart(user)
        rows = self._cart_lines(cart)
        if not rows:
            raise_validation_error(_("Your cart is empty."))

        items = self._get_items([code for code, _ in rows])
        index = {row["item_code"]: row for row in items}
        prices = self._pricing.get_prices([code for code, _ in rows])
        stock = self._stock.get_available_qtys([code for code, _ in rows])

        subtotal = 0.0
        built: list[CheckoutItemDTO] = []
        for item_code, quantity in rows:
            self._require_buyable(item_code, quantity, index, stock)
            item = index.get(item_code, {})
            price = float(prices.get(item_code, (0.0, ""))[0] or 0.0)
            line_total = round(quantity * price, 2)
            subtotal = round(subtotal + line_total, 2)
            built.append(
                CheckoutItemDTO(
                    item_code=item_code,
                    item_name=item.get("item_name") or "",
                    brand=item.get("brand") or "",
                    image=self._images.resolve(item_code).primary_image,
                    quantity=quantity,
                    selling_price=price,
                    subtotal=line_total,
                    stock_status=_stock_status(stock.get(item_code, 0.0)),
                )
            )

        subtotal = round(subtotal, 2)
        discount = self._config.checkout_discount_amount
        discount = round(min(max(discount, 0.0), subtotal), 2)
        taxable_base = subtotal - discount
        tax = round(taxable_base * self._config.checkout_tax_rate / 100.0, 2)
        shipping = round(self._config.checkout_shipping_charge, 2)
        grand_total = round(taxable_base + tax + shipping, 2)

        currency = self._currency(prices)

        shipping_addr = self._resolve_shipping_address(
            customer, shipping_address_name
        )
        billing_addr = self._resolve_billing_address(
            customer, billing_address_name, shipping_addr
        )

        return CheckoutSummaryDTO(
            user_email=user,
            customer_id=customer.get("name") or "",
            customer_name=customer.get("customer_name") or "",
            currency=currency,
            items=built,
            subtotal=subtotal,
            discount=discount,
            tax=tax,
            shipping_charge=shipping,
            grand_total=grand_total,
            shipping_address=shipping_addr,
            billing_address=billing_addr,
        )

    # ------------------------------------------------------------------ #
    # Ownership / cart
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

    def _find_cart(self, user: str) -> Any:
        name = frappe.db.get_value(_CART_DOCTYPE, {"user": user}, "name")
        if not name:
            return None
        doc = frappe.get_doc(_CART_DOCTYPE, name)
        if doc.user != user:
            raise_permission_error(_("You do not own this cart."))
        return doc

    def _cart_lines(self, cart: Any) -> list[tuple[str, float]]:
        if cart is None or not getattr(cart, "items", None):
            return []
        lines = [(row.item_code, float(row.quantity)) for row in cart.items]
        return lines

    # ------------------------------------------------------------------ #
    # Item validation
    # ------------------------------------------------------------------ #

    def _get_items(self, item_codes: list[str]) -> list[dict]:
        if not item_codes:
            return []
        return frappe.get_all(
            "Item",
            filters={"item_code": ["in", item_codes], "disabled": 0},
            fields=list(_ITEM_FIELDS),
            ignore_permissions=True,
        )

    def _require_buyable(
        self,
        item_code: str,
        quantity: float,
        index: dict[str, dict],
        stock: dict[str, float],
    ) -> None:
        """
        Verify the line is buyable: item exists, enabled, quantity valid and
        covered by available stock.
        """
        item = index.get(item_code)
        if item is None:
            if frappe.db.exists("Item", item_code):
                raise_validation_error(_("Item '{0}' is unavailable.").format(item_code))
            raise_not_found("Item", item_code)
        if not quantity or quantity <= 0:
            raise_validation_error(
                _("Quantity must be greater than zero for '{0}'.").format(item_code)
            )
        available = float(stock.get(item_code, 0.0) or 0.0)
        if available <= 0:
            raise_validation_error(_("Item '{0}' is out of stock.").format(item_code))
        if quantity > available:
            raise_validation_error(
                _("Quantity {0} exceeds available stock {1} for '{2}'.").format(
                    quantity, available, item_code
                )
            )

    # ------------------------------------------------------------------ #
    # Addresses
    # ------------------------------------------------------------------ #

    def _resolve_shipping_address(
        self, customer: dict, address_name: str | None
    ) -> AddressDTO:
        if address_name:
            return self._owned_address(address_name, customer["name"])
        addr = _find_customer_address(
            customer["name"], is_shipping_address=1, limit=1
        )
        if not addr:
            raise_validation_error(
                _(
                    "No default shipping address is set on your account. "
                    "Please add one before checkout."
                )
            )
        return _row_to_address(addr[0])

    def _resolve_billing_address(
        self,
        customer: dict,
        address_name: str | None,
        fallback: AddressDTO,
    ) -> AddressDTO:
        if address_name:
            return self._owned_address(address_name, customer["name"])
        addr = _find_customer_address(customer["name"], is_primary_address=1, limit=1)
        if not addr:
            return fallback
        return _row_to_address(addr[0])

    def _owned_address(self, address_name: str, customer_name: str) -> AddressDTO:
        try:
            addr = frappe.get_doc("Address", address_name)
        except frappe.DoesNotExistError:
            raise_not_found("Address", address_name)
        for link in addr.links:
            if link.link_doctype == "Customer" and link.link_name == customer_name:
                return _doc_to_address(addr)
        raise_permission_error(_("You do not have permission to access this address."))

    # ------------------------------------------------------------------ #
    # Misc
    # ------------------------------------------------------------------ #

    def _currency(self, prices: dict[str, tuple[float | None, str]]) -> str:
        for code in prices:
            _, currency = prices[code]
            if currency:
                return currency
        return frappe.db.get_single_value("System Settings", "currency") or "INR"


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _summary_fingerprint(summary: CheckoutSummaryDTO) -> str:
    """A stable fingerprint of a checkout preview's content."""
    items = sorted(
        (item.item_code, round(item.quantity, 2)) for item in summary.items
    )
    return _order_fingerprint(
        items=items,
        shipping=summary.shipping_address.name if summary.shipping_address else "",
        billing=summary.billing_address.name if summary.billing_address else "",
        grand_total=summary.grand_total,
        currency=summary.currency,
    )


def _so_fingerprint(so_name: str) -> str:
    """A stable fingerprint of a stored Sales Order's content."""
    rows = frappe.db.sql(
        "SELECT item_code, qty FROM `tabSales Order Item` WHERE parent = %s",
        (so_name,),
        as_dict=True,
    )
    items = sorted((r["item_code"], round(float(r["qty"]), 2)) for r in rows)
    shipping, billing, grand_total, currency = frappe.db.get_value(
        "Sales Order",
        so_name,
        ["shipping_address_name", "customer_address", "grand_total", "currency"],
    )
    return _order_fingerprint(
        items=items,
        shipping=shipping or "",
        billing=billing or "",
        grand_total=grand_total or 0.0,
        currency=currency or "",
    )


def _order_fingerprint(
    *,
    items: list[tuple[str, float]],
    shipping: str,
    billing: str,
    grand_total: float,
    currency: str,
) -> str:
    payload = {
        "items": items,
        "shipping": str(shipping).strip(),
        "billing": str(billing).strip(),
        "grand_total": round(float(grand_total or 0.0), 2),
        "currency": str(currency).strip(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _lock_name(customer: str) -> str:
    return f"keemeds_commerce_cx:{customer}"


def _acquire_lock(name: str) -> bool:
    """
    Acquire a MySQL advisory lock, blocking up to ``_LOCK_TIMEOUT_SECONDS``.

    Advisory locks are connection-scoped and shared across processes, which
    serialises concurrent order creation for the same customer.
    """
    value = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (name, _LOCK_TIMEOUT_SECONDS))
    return bool(value and value[0][0] == 1)


def _release_lock(name: str) -> None:
    """Best-effort release of the advisory lock (never masks errors)."""
    try:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (name,))
    except Exception:
        pass


def _find_customer_address(
    customer_name: str, *, is_shipping_address: int = 0, is_primary_address: int = 0, limit: int = 1
) -> list[dict]:
    """Return Address rows linked to ``customer_name`` matching the flags."""
    filters = [
        ["Dynamic Link", "link_doctype", "=", "Customer"],
        ["Dynamic Link", "link_name", "=", customer_name],
        ["Dynamic Link", "parenttype", "=", "Address"],
        ["disabled", "=", 0],
    ]
    if is_shipping_address:
        filters.append(["is_shipping_address", "=", 1])
    if is_primary_address:
        filters.append(["is_primary_address", "=", 1])
    return frappe.get_all(
        "Address",
        filters=filters,
        fields=[
            "name",
            "address_type",
            "address_title",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "country",
            "pincode",
            "phone",
            "email_id",
            "is_primary_address",
            "is_shipping_address",
        ],
        order_by="creation ASC",
        limit=limit,
    )


def _row_to_address(row: dict) -> AddressDTO:
    return AddressDTO(
        name=row.get("name") or "",
        address_type=row.get("address_type") or "",
        address_title=row.get("address_title") or "",
        address_line1=row.get("address_line1") or "",
        address_line2=row.get("address_line2") or "",
        city=row.get("city") or "",
        state=row.get("state") or "",
        country=row.get("country") or "",
        pincode=row.get("pincode") or "",
        phone=row.get("phone") or "",
        email_id=row.get("email_id") or "",
        is_primary_address=bool(row.get("is_primary_address")),
        is_shipping_address=bool(row.get("is_shipping_address")),
    )


def _doc_to_address(doc) -> AddressDTO:
    return AddressDTO(
        name=doc.name or "",
        address_type=doc.address_type or "",
        address_title=doc.address_title or "",
        address_line1=doc.address_line1 or "",
        address_line2=doc.address_line2 or "",
        city=doc.city or "",
        state=doc.state or "",
        country=doc.country or "",
        pincode=doc.pincode or "",
        phone=doc.phone or "",
        email_id=doc.email_id or "",
        is_primary_address=bool(doc.is_primary_address),
        is_shipping_address=bool(doc.is_shipping_address),
    )


def _stock_status(available_qty: float) -> str:
    return "in_stock" if available_qty > 0 else "out_of_stock"