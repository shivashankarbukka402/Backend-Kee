"""
Cart & Wishlist Domain DTOs

Plain data holders for the Cart and Wishlist APIs. They carry no logic beyond
serialization and never touch the database. They decouple the services and API
controllers from ERPNext DocType objects so services return DTOs (not ERPNext
documents) and controllers serialize DTOs to JSON.

Cart contract
-------------
- Each cart item carries ``item_code``, ``quantity``, ``selling_price``,
  ``item_name``, ``brand``, ``image``, ``stock_status`` and ``subtotal``.
- The cart response carries ``items``, ``total_items``, ``subtotal`` and
  ``grand_total``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class CartItemDTO:
    """
    A single line item of a cart.

    Attributes
    ----------
    item_code:
        ERPNext Item code (public identifier).
    quantity:
        Quantity of the item in the cart.
    selling_price:
        Storefront selling price per unit at the time of the read.
    item_name:
        Display name of the item.
    brand:
        Brand of the item.
    image:
        Public primary-image URL for the item.
    stock_status:
        ``"in_stock"`` when available quantity covers the requirement and is
        positive, otherwise ``"out_of_stock"``.
    subtotal:
        ``quantity * selling_price`` for this line item.
    """

    item_code: str = ""
    quantity: float = 0.0
    selling_price: float = 0.0
    item_name: str = ""
    brand: str = ""
    image: str = ""
    stock_status: str = "out_of_stock"
    subtotal: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CartDTO:
    """
    A user's complete cart with computed totals.

    Attributes
    ----------
    items:
        Ordered list of cart line items.
    total_items:
        Sum of all line-item quantities.
    subtotal:
        Sum of all line-item subtotals (base selling price).
    grand_total:
        The amount payable. Currently equals ``subtotal`` (no taxes or delivery
        charges are modelled yet).
    """

    items: list[CartItemDTO] = field(default_factory=list)
    total_items: float = 0.0
    subtotal: float = 0.0
    grand_total: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_items": self.total_items,
            "subtotal": self.subtotal,
            "grand_total": self.grand_total,
        }


@dataclass(frozen=True)
class WishlistItemDTO:
    """
    A single wishlist entry.

    Attributes
    ----------
    item_code:
        ERPNext Item code (public identifier).
    item_name:
        Display name of the item.
    brand:
        Brand of the item.
    image:
        Public primary-image URL for the item.
    """

    item_code: str = ""
    item_name: str = ""
    brand: str = ""
    image: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WishlistDTO:
    """
    A user's wishlist.

    Attributes
    ----------
    items:
        Ordered list of wishlist entries.
    total_items:
        Number of wishlist entries.
    """

    items: list[WishlistItemDTO] = field(default_factory=list)
    total_items: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_items": self.total_items,
        }