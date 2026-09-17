"""
Stock Service

Resolves stock availability (``actual_qty``) for products using ERPNext's Bin
records — the single source of truth for current on-hand inventory.

The service aggregates usable (on-hand) quantity across the configured
warehouses. When no warehouses are configured it aggregates across every
non-group warehouse. Batch lookups happen in a single query (no N+1).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import frappe

from keemeds_commerce.config.commerce_config import CommerceConfig

logger = logging.getLogger("keemeds_commerce.services.stock")


class StockService:
    """
    Provides on-hand quantities from ERPNext Stock (Bin) data.
    """

    def __init__(self, config: CommerceConfig, logger=None) -> None:
        self._config = config
        self._logger = logger or logger

    def get_available_qty(self, item_code: str) -> float:
        data = self.get_available_qtys({item_code})
        return data.get(item_code, 0.0)

    def get_available_qtys(self, item_codes: Iterable[str]) -> dict[str, float]:
        """Return ``item_code -> actual_qty`` for many items in one query."""
        codes = list(dict.fromkeys(item_codes))
        if not codes:
            return {}
        return self._aggregate(codes)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _aggregate(self, item_codes: list[str]) -> dict[str, float]:
        warehouse_filter, params = self._warehouse_condition()
        result = {code: 0.0 for code in item_codes}
        chunk_size = 1000
        for start in range(0, len(item_codes), chunk_size):
            chunk = item_codes[start : start + chunk_size]
            placeholders = ",".join(["%s"] * len(chunk))
            query = f"""
                SELECT bin.item_code, SUM(COALESCE(bin.actual_qty, 0)) AS qty
                FROM `tabBin` bin
                WHERE bin.item_code IN ({placeholders})
                  AND bin.actual_qty <> 0
                  {warehouse_filter}
                GROUP BY bin.item_code
            """
            rows = frappe.db.sql(query, list(chunk) + list(params), as_list=True)
            for item_code, qty in rows:
                result[item_code] = float(qty or 0.0)
        return result

    def _warehouse_condition(self) -> tuple[str, list]:
        """Return (SQL condition fragment, bound parameter list)."""
        warehouses = list(self._config.warehouses)
        if not warehouses:
            return "", []
        placeholders = ",".join(["%s"] * len(warehouses))
        return (
            f"AND bin.warehouse IN ({placeholders})",
            list(warehouses),
        )
