"""
Product Domain DTOs

Plain data holders for the Commerce (Product Catalog) API. They carry no logic
beyond serialization and never touch the database. They decouple the services
and API controllers from ERPNext DocType objects so:

- Services return DTOs (not ERPNext documents).
- Controllers serialize DTOs to JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProductImages:
    """
    Public image URLs for a product (never filesystem paths).

    Attributes
    ----------
    primary_image:
        The primary product image public URL.
    gallery:
        Ordered list of product gallery public URLs.
    """

    primary_image: str = ""
    gallery: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductStock:
    """
    Stock availability summary for a product.

    Attributes
    ----------
    in_stock:
        Whether the product currently has available quantity.
    available_qty:
        The currently available quantity across the configured warehouses.
    """

    in_stock: bool = False
    available_qty: float = 0.0


@dataclass(frozen=True)
class ProductListItem:
    """
    Lightweight product record returned by the listing API.

    Attributes
    ----------
    item_code:
        ERPNext Item code (public identifier).
    item_name:
        Display name of the product.
    item_group:
        Item Group (category) of the product.
    brand:
        Brand of the product.
    manufacturer:
        Manufacturer of the product.
    strength:
        Parsed strength, e.g. ``650 mg``.
    dosage_form:
        Parsed dosage form, e.g. ``Tablet``.
    salt_composition:
        Parsed salt composition, e.g. ``Paracetamol``.
    selling_price:
        Storefront selling price from ERPNext pricing.
    currency:
        Currency of the selling price.
    in_stock:
        Whether the product is currently in stock.
    available_qty:
        Currently available quantity.
    images:
        Public image URLs (primary + gallery).
    """

    item_code: str = ""
    item_name: str = ""
    item_group: str = ""
    brand: str = ""
    manufacturer: str = ""
    strength: str = ""
    dosage_form: str = ""
    salt_composition: str = ""
    selling_price: float | None = None
    currency: str = ""
    in_stock: bool = False
    available_qty: float = 0.0
    images: ProductImages = field(default_factory=ProductImages)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductDetail:
    """
    Full product record returned by the detail API.

    Includes every field the storefront detail page needs, in addition to the
    listing fields.
    """

    item_code: str = ""
    item_name: str = ""
    brand: str = ""
    manufacturer: str = ""
    item_group: str = ""
    description: str = ""
    strength: str = ""
    dosage_form: str = ""
    salt_composition: str = ""
    country_of_origin: str = ""
    selling_price: float | None = None
    currency: str = ""
    in_stock: bool = False
    available_qty: float = 0.0
    images: ProductImages = field(default_factory=ProductImages)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Pagination:
    """
    Pagination metadata returned by the listing API.
    """

    page: int = 1
    page_size: int = 20
    total_records: int = 0
    total_pages: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductListing:
    """
    Complete listing response: the page of products and its pagination.
    """

    items: list[ProductListItem] = field(default_factory=list)
    pagination: Pagination = field(default_factory=Pagination)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "pagination": self.pagination.to_dict(),
        }


@dataclass(frozen=True)
class ProductQuery:
    """
    Normalized, validated query parameters for the listing API.

    Attributes
    ----------
    page:
        1-based page number.
    page_size:
        Number of records per page (clamped to the configured maximum).
    search:
        Free-text search term (item code, item name, brand, manufacturer,
        salt composition).
    item_group:
        Exact Item Group filter.
    brand:
        Exact Brand filter.
    manufacturer:
        Exact Manufacturer filter.
    in_stock:
        When ``True`` only items with available quantity are returned.
    sort:
        Sort key (``item_name``, ``item_code``, ``price``, ``newest``).
    """

    page: int = 1
    page_size: int = 20
    search: str = ""
    item_group: str = ""
    brand: str = ""
    manufacturer: str = ""
    in_stock: bool = False
    sort: str = "item_name"
