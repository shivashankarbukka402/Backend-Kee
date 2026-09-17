"""
Product Service

Facade over the Commerce (Product Catalog) domain. Orchestrates the
collaborator services (parsing, pricing, stock, images) to assemble listing
and detail DTOs from ERPNext Item data.

Design notes
------------
- All ERPNext reads happen here; controllers only delegate and serialize.
- Listings are assembled with a bounded number of queries (no N+1): item codes
  are fetched once, then the page's Item records, Item Prices and Bin stock are
  resolved in batched queries.
- "Published" is configured via ``CommerceConfig``; by default it means the
  Item is enabled (``disabled = 0``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import frappe

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.product import (
    Pagination,
    ProductDetail,
    ProductListing,
    ProductListItem,
    ProductQuery,
)
from keemeds_commerce.services.image_resolver import ImageResolver
from keemeds_commerce.services.item_name_parser import ItemNameParser
from keemeds_commerce.services.pricing_service import PricingService
from keemeds_commerce.services.stock_service import StockService
from keemeds_commerce.utils.exceptions import raise_not_found

logger = logging.getLogger("keemeds_commerce.services.product")

#: Item fields loaded for listing rows (kept minimal for performance).
_LISTING_FIELDS = (
    "item_code",
    "item_name",
    "item_group",
    "brand",
    "default_item_manufacturer",
)

#: Item fields additionally loaded for detail rows.
_DETAIL_FIELDS = (
    *_LISTING_FIELDS,
    "description",
    "country_of_origin",
)


@dataclass
class _PagePlan:
    """Internal plan describing which item codes belong to a listing page."""

    codes: list[str]
    total: int
    ordered: list[str]


class ProductService:
    """
    Builds product listings and details from ERPNext catalog data.
    """

    def __init__(
        self,
        config: CommerceConfig,
        parser: ItemNameParser | None = None,
        pricing: PricingService | None = None,
        stock: StockService | None = None,
        images: ImageResolver | None = None,
    ) -> None:
        self._config = config
        self._parser = parser or ItemNameParser()
        self._pricing = pricing or PricingService(config)
        self._stock = stock or StockService(config)
        self._images = images or ImageResolver(config)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def list_products(self, query: ProductQuery) -> ProductListing:
        plan = self._plan(query)

        page_codes = _paginate(plan.ordered, query.page, query.page_size)
        items = self._build_list_items(page_codes, plan)

        total_pages = _total_pages(plan.total, query.page_size)
        pagination = Pagination(
            page=query.page,
            page_size=_effective_page_size(query.page_size, query.page, plan.total),
            total_records=plan.total,
            total_pages=total_pages,
        )
        return ProductListing(items=items, pagination=pagination)

    def get_product(self, item_code: str) -> ProductDetail:
        item = _get_published_item(
            item_code,
            fields=list(_DETAIL_FIELDS),
            published=self._config.published_item_groups,
        )
        if not item:
            raise_not_found("Item", item_code)
        return self._build_detail(item)

    # ------------------------------------------------------------------ #
    # Listing plan
    # ------------------------------------------------------------------ #

    def _plan(self, query: ProductQuery) -> _PagePlan:
        codes = self._matching_item_codes(query)

        if query.in_stock:
            stock = self._stock.get_available_qtys(codes)
            codes = [code for code in codes if stock.get(code, 0.0) > 0]

        ordered = self._sort_codes(codes, query.sort)
        return _PagePlan(codes=codes, total=len(codes), ordered=ordered)

    def _matching_item_codes(self, query: ProductQuery) -> list[str]:
        filters: dict = {}
        or_filters: list | None = None

        if self._config.published_item_groups:
            filters["item_group"] = ["in", list(self._config.published_item_groups)]
        else:
            filters["disabled"] = 0

        if query.item_group:
            filters["item_group"] = query.item_group
        if query.brand:
            filters["brand"] = query.brand
        if query.manufacturer:
            filters["default_item_manufacturer"] = query.manufacturer

        if query.search:
            term = f"%{query.search.strip()}%"
            or_filters = [
                ["item_name", "like", term],
                ["item_code", "like", term],
                ["brand", "like", term],
                ["default_item_manufacturer", "like", term],
            ]

        rows = frappe.get_all(
            "Item",
            filters=filters,
            or_filters=or_filters,
            fields=["item_code"],
            order_by="item_code asc",
            ignore_permissions=True,
        )
        return [row["item_code"] for row in rows]

    def _sort_codes(self, codes: list[str], sort: str) -> list[str]:
        if sort == "price":
            prices = self._pricing.get_prices(codes)
            return sorted(
                codes,
                key=lambda code: (prices.get(code, (None, ""))[0] is None, prices.get(code, (None, ""))[0]),
            )
        if sort == "item_name":
            return self._sort_codes_by_field(codes, "item_name")
        if sort == "item_code":
            return sorted(codes)
        if sort == "newest":
            return self._sort_codes_by_field(codes, "modified desc")
        return codes

    def _sort_codes_by_field(self, codes: list[str], order_by: str) -> list[str]:
        if not codes:
            return []
        jump = 1000
        ordered_codes: list[str] = []
        for start in range(0, len(codes), jump):
            chunk = codes[start : start + jump]
            rows = frappe.get_all(
                "Item",
                filters={"item_code": ["in", chunk]},
                fields=["item_code"],
                order_by=order_by,
                ignore_permissions=True,
            )
            ordered_codes.extend(row["item_code"] for row in rows if row["item_code"] in set(codes))
        return ordered_codes

    # ------------------------------------------------------------------ #
    # Builders
    # ------------------------------------------------------------------ #

    def _build_list_items(
        self,
        page_codes: list[str],
        plan: _PagePlan,
    ) -> list[ProductListItem]:
        if not page_codes:
            return []
        items = _get_items(page_codes, list(_LISTING_FIELDS))
        prices = self._pricing.get_prices(page_codes)
        stock = self._stock.get_available_qtys(page_codes)
        index = {row["item_code"]: row for row in items}
        return [self._build_list_item(index[code], prices, stock) for code in page_codes if code in index]

    def _build_list_item(
        self,
        row: dict,
        prices: dict[str, tuple],
        stock: dict[str, float],
    ) -> ProductListItem:
        parsed = self._parser.parse(row["item_name"] or "")
        rate, currency = prices.get(row["item_code"], (None, ""))
        available = stock.get(row["item_code"], 0.0)
        return ProductListItem(
            item_code=row["item_code"],
            item_name=row["item_name"],
            item_group=row.get("item_group") or "",
            brand=row.get("brand") or "",
            manufacturer=row.get("default_item_manufacturer") or "",
            strength=parsed.strength,
            dosage_form=parsed.dosage_form,
            salt_composition=parsed.salt_composition,
            selling_price=rate,
            currency=currency,
            in_stock=available > 0,
            available_qty=available,
            images=self._images.resolve(row["item_code"]),
        )

    def _build_detail(self, row: dict) -> ProductDetail:
        parsed = self._parser.parse(row["item_name"] or "")
        rate, currency = self._pricing.get_price(row["item_code"])
        available = self._stock.get_available_qty(row["item_code"])
        return ProductDetail(
            item_code=row["item_code"],
            item_name=row["item_name"],
            brand=row.get("brand") or "",
            manufacturer=row.get("default_item_manufacturer") or "",
            item_group=row.get("item_group") or "",
            description=row.get("description") or "",
            strength=parsed.strength,
            dosage_form=parsed.dosage_form,
            salt_composition=parsed.salt_composition,
            country_of_origin=row.get("country_of_origin") or "",
            selling_price=rate,
            currency=currency,
            in_stock=available > 0,
            available_qty=available,
            images=self._images.resolve(row["item_code"]),
        )


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #

def _get_published_item(
    item_code: str,
    fields: list[str],
    published: tuple[str, ...],
) -> dict | None:
    filters: dict = {"item_code": item_code}
    if published:
        filters["item_group"] = ["in", list(published)]
    else:
        filters["disabled"] = 0
    rows = frappe.get_all(
        "Item",
        filters=filters,
        fields=fields,
        limit=1,
        ignore_permissions=True,
    )
    return rows[0] if rows else None


def _get_items(item_codes: list[str], fields: list[str]) -> list[dict]:
    jump = 1000
    result: list[dict] = []
    for start in range(0, len(item_codes), jump):
        chunk = item_codes[start : start + jump]
        result.extend(
            frappe.get_all(
                "Item",
                filters={"item_code": ["in", chunk]},
                fields=fields,
                ignore_permissions=True,
            )
        )
    return result


def _effective_page_size(page_size: int, page: int, total: int) -> int:
    if page_size <= 0:
        return 0
    start = (page - 1) * page_size
    return max(0, min(page_size, total - start))


def _total_pages(total: int, page_size: int) -> int:
    if page_size <= 0:
        return 0
    import math

    return math.ceil(total / page_size)


def _paginate(ordered: list[str], page: int, page_size: int) -> list[str]:
    if page_size <= 0:
        return []
    start = (page - 1) * page_size
    return ordered[start : start + page_size]
