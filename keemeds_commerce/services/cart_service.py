"""
Cart Service

Business logic for the ERP-backed shopping cart. Each Website User owns exactly
one :doc:`Cart` document (with :doc:`Cart Item` children); every operation is
scoped to ``frappe.session.user`` so no cross-user access is possible.

Design notes
------------
- Reuses ERPNext collaborators: Pricing (Item Price), Stock (Bin) and the
  ImageResolver so cart rows always reflect the currently-published catalog.
- All ERPNext reads/writes happen here; controllers only delegate and serialize.
- Writes are atomic: mutations are persisted in a single document save wrapped
  in commit-on-success / rollback-on-failure.
- Validations: item exists, item enabled, stock > 0, quantity > 0 and
  quantity <= available stock.
"""

from __future__ import annotations

import logging
from typing import Any

import frappe
from frappe import _

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.cart import CartDTO, CartItemDTO
from keemeds_commerce.services.image_resolver import ImageResolver
from keemeds_commerce.services.pricing_service import PricingService
from keemeds_commerce.services.stock_service import StockService
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.cart")

_CART_DOCTYPE = "Cart"
_CART_ITEM_DOCTYPE = "Cart Item"

#: Minimum Item fields resolved when validating and enriching a cart item.
_ITEM_FIELDS = (
    "item_code",
    "item_name",
    "brand",
)


class CartService:
    """
    Builds and mutates a Website User's cart from ERPNext data.
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

    def get_cart(self) -> CartDTO:
        """
        Return the current user's cart with enriched, up-to-date items.
        """
        user = self._current_user()
        doc = self._find_cart(user)
        return self._to_dto(doc)

    def add_item(self, item_code: str, quantity: float) -> CartDTO:
        """
        Add an item to the cart (or increase its quantity if already present).
        """
        user = self._current_user()
        cart = self._find_cart(user)
        if cart is None:
            cart = self._create_cart(user)

        rows = {row.item_code: row for row in cart.items}

        if item_code in rows:
            new_quantity = float(rows[item_code].quantity) + quantity
            self._validate_cartable(item_code, new_quantity)
            self._set_row(cart, item_code, new_quantity)
        else:
            self._validate_cartable(item_code, quantity)
            cart.append(
                "items",
                self._build_row(item_code, quantity),
            )

        return self._persist(cart)

    def update_item(self, item_code: str, quantity: float) -> CartDTO:
        """
        Set the quantity of an existing cart item.
        """
        user = self._current_user()
        cart = self._find_cart(user)
        if cart is None or item_code not in {row.item_code for row in cart.items}:
            raise_validation_error(_("Item '{0}' is not in the cart.").format(item_code))

        self._validate_cartable(item_code, quantity)
        self._set_row(cart, item_code, quantity)
        return self._persist(cart)

    def remove_item(self, item_code: str) -> CartDTO:
        """
        Remove an item from the cart. Idempotent when the item is absent.
        """
        user = self._current_user()
        cart = self._find_cart(user)
        if cart is None:
            return self._to_dto(None)
        if item_code not in {row.item_code for row in cart.items}:
            return self._to_dto(cart)

        cart.items = [row for row in cart.items if row.item_code != item_code]
        return self._persist(cart)

    def clear_cart(self) -> CartDTO:
        """
        Remove every item from the cart.
        """
        user = self._current_user()
        cart = self._find_cart(user)
        if cart is None:
            return self._to_dto(None)
        cart.items = []
        return self._persist(cart)

    # ------------------------------------------------------------------ #
    # Ownership
    # ------------------------------------------------------------------ #

    def _current_user(self) -> str:
        user = frappe.session.user
        if not user or user == "Guest":
            raise_validation_error(_("You must be logged in to perform this action."))
        return user

    def _find_cart(self, user: str) -> Any:
        """
        Return the current user's Cart document, or ``None``.
        """
        name = frappe.db.get_value(_CART_DOCTYPE, {"user": user}, "name")
        if not name:
            return None
        doc = frappe.get_doc(_CART_DOCTYPE, name)
        if doc.user != user:
            raise_validation_error(_("You do not own this cart."))
        return doc

    def _create_cart(self, user: str) -> Any:
        doc = frappe.get_doc(
            {
                "doctype": _CART_DOCTYPE,
                "user": user,
                "customer": self._customer_for(user),
            }
        )
        doc.insert(ignore_permissions=True)
        return doc

    def _customer_for(self, user: str) -> str:
        rows = frappe.db.sql(
            """
            SELECT c.name
            FROM `tabCustomer` c
            INNER JOIN `tabPortal User` pu ON pu.parent = c.name
            WHERE pu.user = %s AND pu.parenttype = 'Customer'
            LIMIT 1
            """,
            (user,),
        )
        return rows[0][0] if rows else ""

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def _require_active_item(self, item_code: str) -> dict:
        """
        Return the Item row, raising when the item is missing or disabled.
        """
        rows = frappe.get_all(
            "Item",
            filters={"item_code": item_code, "disabled": 0},
            fields=list(_ITEM_FIELDS),
            limit=1,
            ignore_permissions=True,
        )
        if not rows:
            if frappe.db.exists("Item", item_code):
                raise_validation_error(_("Item '{0}' is unavailable.").format(item_code))
            raise_not_found("Item", item_code)
        return rows[0]

    def _available_qty(self, item_code: str) -> float:
        return float(self._stock.get_available_qty(item_code) or 0.0)

    def _validate_cartable(self, item_code: str, quantity: float) -> None:
        """
        Verify item exists, is enabled, in stock, and quantity within bounds.
        """
        item = self._require_active_item(item_code)
        if not quantity or quantity <= 0:
            raise_validation_error(
                _("Quantity must be greater than zero for '{0}'.").format(item_code)
            )
        available = self._available_qty(item_code)
        if available <= 0:
            raise_validation_error(
                _("Item '{0}' is out of stock.").format(item_code)
            )
        if quantity > available:
            raise_validation_error(
                _("Quantity {0} exceeds available stock {1} for '{2}'.").format(
                    quantity, available, item_code
                )
            )

    # ------------------------------------------------------------------ #
    # Mutation helpers
    # ------------------------------------------------------------------ #

    def _build_row(self, item_code: str, quantity: float) -> dict[str, Any]:
        item = self._require_active_item(item_code)
        price = float(self._pricing.get_price(item_code)[0] or 0.0)
        available = self._available_qty(item_code)
        return {
            "item_code": item_code,
            "item_name": item.get("item_name") or "",
            "brand": item.get("brand") or "",
            "image": self._images.resolve(item_code).primary_image,
            "quantity": round(quantity, 2),
            "selling_price": price,
            "subtotal": round(quantity * price, 2),
            "stock_status": _stock_status(available),
        }

    def _set_row(self, cart: Any, item_code: str, quantity: float) -> None:
        row = self._build_row(item_code, quantity)
        existing = {r.item_code: r for r in cart.items}
        if item_code not in existing:
            raise_validation_error(_("Item '{0}' is not in the cart.").format(item_code))
        existing[item_code].update(row)

    # ------------------------------------------------------------------ #
    # Persistence (atomic)
    # ------------------------------------------------------------------ #

    def _persist(self, cart: Any) -> CartDTO:
        """
        Save the cart atomically, returning the rebuilt DTO.
        """
        try:
            cart.save(ignore_permissions=True)
        except Exception:
            frappe.db.rollback()
            raise
        else:
            frappe.db.commit()
        return self._to_dto(cart)

    # ------------------------------------------------------------------ #
    # DTO assembly
    # ------------------------------------------------------------------ #

    def _to_dto(self, cart: Any) -> CartDTO:
        if cart is None:
            return CartDTO()

        codes = [row.item_code for row in cart.items]
        if not codes:
            return CartDTO()

        items = _get_items(codes)
        index = {row["item_code"]: row for row in items}
        prices = self._pricing.get_prices(codes)
        stock = self._stock.get_available_qtys(codes)

        qty_by_code = {
            row.item_code: float(row.quantity) for row in cart.items
        }

        built: list[CartItemDTO] = []
        for code in codes:
            quantity = qty_by_code.get(code, 0.0)
            price = float(prices.get(code, (0.0, ""))[0] or 0.0)
            subtotal = round(quantity * price, 2)
            item = index.get(code, {})
            built.append(
                CartItemDTO(
                    item_code=code,
                    quantity=quantity,
                    selling_price=price,
                    item_name=item.get("item_name") or "",
                    brand=item.get("brand") or "",
                    image=self._images.resolve(code).primary_image,
                    stock_status=_stock_status(stock.get(code, 0.0)),
                    subtotal=subtotal,
                )
            )

        total_items = round(sum(item.quantity for item in built), 2)
        subtotal = round(sum(item.subtotal for item in built), 2)
        return CartDTO(
            items=built,
            total_items=total_items,
            subtotal=subtotal,
            grand_total=subtotal,
        )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _get_items(item_codes: list[str]) -> list[dict]:
    rows = frappe.get_all(
        "Item",
        filters={"item_code": ["in", item_codes]},
        fields=list(_ITEM_FIELDS),
        ignore_permissions=True,
    )
    return rows


def _stock_status(available_qty: float) -> str:
    return "in_stock" if available_qty > 0 else "out_of_stock"