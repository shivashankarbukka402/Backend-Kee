"""
Wishlist Service

Business logic for the ERP-backed wishlist. Each Website User owns exactly one
:doc:`Wishlist` document (with :doc:`Wishlist Item` children); every operation
is scoped to ``frappe.session.user`` so no cross-user access is possible.

Design notes
------------
- Duplicate items are prevented: adding an item already present is a no-op.
- Writes are atomic: persisted in a single document save wrapped in
  commit-on-success / rollback-on-failure.
- All ERPNext reads/writes happen here; controllers only delegate and serialize.
"""

from __future__ import annotations

import logging
from typing import Any

import frappe
from frappe import _

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.cart import WishlistDTO, WishlistItemDTO
from keemeds_commerce.services.image_resolver import ImageResolver
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.wishlist")

_WISHLIST_DOCTYPE = "Wishlist"

#: Minimum Item fields resolved when validating and enriching a wishlist item.
_ITEM_FIELDS = (
    "item_code",
    "item_name",
    "brand",
)


class WishlistService:
    """
    Builds and mutates a Website User's wishlist from ERPNext data.
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

    def get_wishlist(self) -> WishlistDTO:
        """
        Return the current user's wishlist with enriched, up-to-date items.
        """
        user = self._current_user()
        doc = self._find_wishlist(user)
        return self._to_dto(doc)

    def add_item(self, item_code: str) -> WishlistDTO:
        """
        Add an item to the wishlist. Adding a duplicate is a no-op.
        """
        user = self._current_user()
        doc = self._find_wishlist(user)
        if doc is None:
            doc = self._create_wishlist(user)

        if item_code in {row.item_code for row in doc.items}:
            return self._to_dto(doc)

        item = self._require_active_item(item_code)
        doc.append(
            "items",
            {
                "item_code": item_code,
                "item_name": item.get("item_name") or "",
                "brand": item.get("brand") or "",
                "image": self._images.resolve(item_code).primary_image,
            },
        )
        return self._persist(doc)

    def remove_item(self, item_code: str) -> WishlistDTO:
        """
        Remove an item from the wishlist. Idempotent when the item is absent.
        """
        user = self._current_user()
        doc = self._find_wishlist(user)
        if doc is None:
            return self._to_dto(None)
        if item_code not in {row.item_code for row in doc.items}:
            return self._to_dto(doc)

        doc.items = [row for row in doc.items if row.item_code != item_code]
        return self._persist(doc)

    # ------------------------------------------------------------------ #
    # Ownership
    # ------------------------------------------------------------------ #

    def _current_user(self) -> str:
        user = frappe.session.user
        if not user or user == "Guest":
            raise_validation_error(_("You must be logged in to perform this action."))
        return user

    def _find_wishlist(self, user: str) -> Any:
        """
        Return the current user's Wishlist document, or ``None``.
        """
        name = frappe.db.get_value(_WISHLIST_DOCTYPE, {"user": user}, "name")
        if not name:
            return None
        doc = frappe.get_doc(_WISHLIST_DOCTYPE, name)
        if doc.user != user:
            raise_validation_error(_("You do not own this wishlist."))
        return doc

    def _create_wishlist(self, user: str) -> Any:
        doc = frappe.get_doc(
            {
                "doctype": _WISHLIST_DOCTYPE,
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

    # ------------------------------------------------------------------ #
    # Persistence (atomic)
    # ------------------------------------------------------------------ #

    def _persist(self, doc: Any) -> WishlistDTO:
        """
        Save the wishlist atomically, returning the rebuilt DTO.
        """
        try:
            doc.save(ignore_permissions=True)
        except Exception:
            frappe.db.rollback()
            raise
        else:
            frappe.db.commit()
        return self._to_dto(doc)

    # ------------------------------------------------------------------ #
    # DTO assembly
    # ------------------------------------------------------------------ #

    def _to_dto(self, doc: Any) -> WishlistDTO:
        if doc is None:
            return WishlistDTO()

        codes = [row.item_code for row in doc.items]
        if not codes:
            return WishlistDTO()

        rows = frappe.get_all(
            "Item",
            filters={"item_code": ["in", codes]},
            fields=list(_ITEM_FIELDS),
            ignore_permissions=True,
        )
        index = {row["item_code"]: row for row in rows}

        built: list[WishlistItemDTO] = []
        for row in doc.items:
            item = index.get(row.item_code, {})
            built.append(
                WishlistItemDTO(
                    item_code=row.item_code,
                    item_name=item.get("item_name") or "",
                    brand=item.get("brand") or "",
                    image=self._images.resolve(row.item_code).primary_image,
                )
            )

        return WishlistDTO(items=built, total_items=len(built))