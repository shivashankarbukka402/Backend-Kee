"""
Pricing Service

Resolves storefront selling prices using ERPNext's stored :doc:`Item Price`
records (Price List data). Prices are produced by ERPNext's pricing model and
never calculated here.

The service supports both single- and batch lookups in a single query (no
N+1), so a full listing page can be priced with one round trip.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import frappe

from keemeds_commerce.config.commerce_config import CommerceConfig

logger = logging.getLogger("keemeds_commerce.services.pricing")


class PricingService:
    """
    Provides selling price / currency lookups from ERPNext pricing.
    """

    def __init__(self, config: CommerceConfig, logger=None) -> None:
        self._config = config
        self._logger = logger or logger

    def get_price(self, item_code: str) -> tuple[float | None, str]:
        """Return ``(rate, currency)`` for a single item.

        The currency is derived from the price list when a rate exists; otherwise
        the company's default currency is used.
        """
        records = self._fetch({item_code})
        data = records.get(item_code)
        if data and data["rate"] is not None:
            return data["rate"], data["currency"]
        return None, self._default_currency()

    def get_prices(
        self,
        item_codes: Sequence[str],
    ) -> dict[str, tuple[float | None, str]]:
        """Return ``item_code -> (rate, currency)`` for many items in one query."""
        codes = set(item_codes)
        records = self._fetch(codes)
        default_currency = self._default_currency()
        result: dict[str, tuple[float | None, str]] = {}
        for code in codes:
            data = records.get(code)
            if data and data["rate"] is not None:
                result[code] = (data["rate"], data["currency"])
            else:
                result[code] = (None, default_currency)
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _fetch(self, item_codes: set[str]) -> dict[str, dict]:
        if not item_codes:
            return {}
        rows = frappe.get_all(
            "Item Price",
            filters={
                "price_list": self._config.selling_price_list,
                "item_code": ["in", sorted(item_codes)],
                "selling": 1,
            },
            fields=["item_code", "price_list_rate", "currency"],
            order_by="modified desc",
        )
        # Keep the latest priced row when duplicate Item Price rows exist.
        result: dict[str, dict] = {}
        for row in rows:
            result[row["item_code"]] = {
                "rate": row.get("price_list_rate"),
                "currency": row.get("currency") or "",
            }
        return result

    def _default_currency(self) -> str:
        try:
            company = frappe.defaults.get_global_default("company")
            if company:
                currency = frappe.db.get_value("Company", company, "default_currency")
                if currency:
                    return currency
        except Exception:
            pass
        return frappe.db.get_single_value("System Settings", "currency") or "INR"
