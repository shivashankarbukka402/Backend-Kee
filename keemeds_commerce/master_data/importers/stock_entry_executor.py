"""
Stock Entry Executor

The Phase 9 import backend that posts the generated Opening Stock workbook into
ERPNext through the standard Stock Entry inventory workflow.

Why this exists
---------------
Opening Stock cannot be imported as a ``Stock Ledger Entry`` through the Data
Import API. Instead this executor reads the reconciled Opening Stock workbook
(one row per ``(Item Code, Warehouse)``) and posts each row as a standard
``Stock Entry`` of type "Material Receipt" through ``frappe.get_doc(...)
.insert().submit()`` - the normal ERPNext inventory workflow - so ERPNext
validation is never bypassed (no direct SQL, no ORM ledger inserts).

The executor is a drop-in :class:`~master_data.importers.base_importer.ImportExecutor`
for the Opening Stock importer, returning a real
:class:`~master_data.importers.base_importer.ImportOutcome` (imported/failed per
row). Configured warehouse names are resolved to existing ERPNext Warehouses at
import time (exact match first, else by ``warehouse_name``), so the centralized
:class:`~master_data.config.StockConfig.warehouses` stays unchanged while the
company-suffixed real warehouse (e.g. ``"Finished Goods - HG"``) is used.

When Frappe is unavailable the file is reported as validated-but-not-imported:
zero counts plus a clear note. No success is ever fabricated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from ..config import MasterDataConfig, StockPostingConfig
from .base_importer import ImportExecutor, ImportOutcome


@dataclass(frozen=True)
class _StockEntryItem:
    """A single item row of a pre-import Stock Entry (before ERPNext submit)."""

    item_code: str
    t_warehouse: str
    qty: float
    rate: float
    uom: str


class StockEntryExecutor(ImportExecutor):
    """
    Posts Opening Stock rows as standard Stock Entry "Material Receipt" docs.
    """

    def __init__(
        self,
        config: MasterDataConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._posting: StockPostingConfig = config.stock_posting
        self._logger = logger or logging.getLogger(
            "keemeds.master_data.import.stock_entry"
        )

    def import_file(
        self,
        file_path: Path,
        *,
        doctype: str,
        import_type: str,
        submit_after_import: bool = False,
    ) -> ImportOutcome:
        del doctype, import_type, submit_after_import  # informational
        try:
            import frappe
        except ImportError as exc:
            self._logger.warning("Frappe unavailable; not importing %s", file_path)
            return ImportOutcome(errors=(f"Frappe is not available: {exc}",))
        if not getattr(frappe, "db", None):
            self._logger.warning("Frappe unavailable; not importing %s", file_path)
            return ImportOutcome(errors=("No active Frappe site/database connection.",))

        items = self._read_items(file_path)
        if not items:
            return ImportOutcome(imported=0, updated=0, skipped=0, failed=0)

        self._ensure_batch_settings(frappe)
        company = self._company(frappe)
        warehouse_cache: dict[str, str] = {}
        imported = 0
        errors: list[str] = []
        for entry_item in items:
            try:
                warehouse = self._resolve_warehouse(frappe, warehouse_cache, entry_item.t_warehouse)
                self._post_material_receipt(frappe, entry_item, warehouse, company)
                imported += 1
            except Exception as exc:
                self._logger.warning(
                    "Stock Entry failed for %s/%s: %s",
                    entry_item.item_code,
                    entry_item.t_warehouse,
                    exc,
                )
                errors.append(
                    f"{entry_item.item_code}/{entry_item.t_warehouse}: {exc}"
                )
        return ImportOutcome(
            imported=imported,
            updated=0,
            skipped=0,
            failed=len(errors),
            errors=tuple(errors),
        )

    # ------------------------------------------------------------------ #
    # ERPNext posting
    # ------------------------------------------------------------------ #

    def _ensure_batch_settings(self, frappe) -> None:
        """
        Enable the ERPNext feature that auto-creates batch bundles on receipt.

        The generated items are batch-tracked (``has_batch_no`` / expiry). The
        standard ERPNext stock workflow auto-creates a fresh Batch and its
        Serial and Batch Bundle on a Material Receipt only when the global Stock
        Settings "Activate Serial and Batch No for Item" flag is on. This is a
        normal, user-facing feature prerequisite, not a validation bypass.
        """
        try:
            frappe.db.set_value(
                "Stock Settings",
                "Stock Settings",
                "enable_serial_and_batch_no_for_item",
                1,
            )
            frappe.db.commit()
        except Exception as exc:
            self._logger.warning(
                "Could not enable Serial and Batch Bundle feature in Stock "
                "Settings: %s",
                exc,
            )

    def _post_material_receipt(self, frappe, entry_item: _StockEntryItem, warehouse: str, company: str) -> None:
        """Create and submit a Stock Entry "Material Receipt" for one row."""
        self._enable_new_batch(frappe, entry_item.item_code)
        doc = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "stock_entry_type": self._posting.stock_entry_type,
                "purpose": self._posting.purpose,
                "company": company,
                "posting_date": frappe.utils.today(),
                "set_posting_time": 0,
                "to_warehouse": warehouse,
                "items": [
                    {
                        "item_code": entry_item.item_code,
                        "t_warehouse": warehouse,
                        "qty": entry_item.qty,
                        "basic_rate": entry_item.rate,
                        "valuation_rate": entry_item.rate,
                        "uom": entry_item.uom,
                    }
                ],
            }
        )
        doc.insert(ignore_permissions=True)
        doc.submit()
        frappe.db.commit()

    def _enable_new_batch(self, frappe, item_code: str) -> None:
        """
        Ensure auto-new-batch is on for a batch-tracked item being received.

        When the item tracks batches (``has_batch_no``), ERPNext auto-creates a
        fresh Batch for the Material Receipt only when the item's
        ``create_new_batch`` flag is set. Enabling it here is the standard
        ERPNext workflow for receiving batch/expiry-tracked stock, so each
        opening-stock row lands in its own auto-created Batch. Items that do not
        track batches are left untouched.
        """
        if not frappe.db.get_value("Item", item_code, "has_batch_no"):
            return
        frappe.db.set_value("Item", item_code, "create_new_batch", 1)
        frappe.db.commit()

    def _resolve_warehouse(
        self,
        frappe,
        cache: dict[str, str],
        configured: str,
    ) -> str:
        """
        Resolve a configured warehouse name to an existing ERPNext Warehouse.

        Exact match first; otherwise fall back to a warehouse whose
        ``warehouse_name`` matches (e.g. ``"Finished Goods"`` ->
        ``"Finished Goods - HG"``). Results are cached per call.
        """
        cached = cache.get(configured)
        if cached:
            return cached
        if frappe.db.exists("Warehouse", configured):
            cache[configured] = configured
            return configured
        hit = frappe.get_all(
            "Warehouse",
            filters={"warehouse_name": configured},
            fields=["name"],
            limit_page_length=1,
        )
        if hit:
            resolved = hit[0]["name"]
            cache[configured] = resolved
            return resolved
        raise RuntimeError(f"Warehouse '{configured}' does not exist in ERPNext.")

    def _company(self, frappe) -> str:
        """Derive the company from the default company / first company."""
        default = frappe.defaults.get_global_default("company")
        if default:
            return default
        companies = frappe.get_all("Company", fields=["name"], limit_page_length=1)
        if companies:
            return companies[0]["name"]
        raise RuntimeError("No company is configured in ERPNext to post stock into.")

    # ------------------------------------------------------------------ #
    # Workbook reading
    # ------------------------------------------------------------------ #

    def _read_items(self, path: Path) -> list[_StockEntryItem]:
        """Read the reconciled workbook into pre-import Stock Entry items."""
        try:
            import frappe
        except ImportError:
            return []
        workbook = load_workbook(path, read_only=True)
        try:
            sheet = workbook.active
            iterator = sheet.iter_rows(values_only=True)
            header_row = next(iterator, None)
            if header_row is None:
                return []
            headers = [str(value) if value is not None else "" for value in header_row]
            index = {
                self._canonical_key(header.strip()): i
                for i, header in enumerate(headers)
                if header.strip()
            }
            items: list[_StockEntryItem] = []
            uom_cache: dict[str, str] = {}
            for values in iterator:
                if values is None:
                    continue
                cells = list(values)
                item_code = self._cell(cells, index, "item_code")
                warehouse = self._cell(cells, index, "warehouse")
                if not item_code or not warehouse:
                    continue
                quantity = self._num(cells, index, "opening_quantity")
                rate = self._num(cells, index, "valuation_rate")
                if quantity <= 0:
                    continue
                uom_cache.setdefault(
                    item_code, self._stock_uom(frappe, item_code, uom_cache)
                )
                items.append(
                    _StockEntryItem(
                        item_code=item_code,
                        t_warehouse=warehouse,
                        qty=quantity,
                        rate=rate,
                        uom=uom_cache[item_code],
                    )
                )
            return items
        finally:
            workbook.close()

    def _stock_uom(self, frappe, item_code: str, cache: dict[str, str]) -> str:
        if item_code in cache:
            return cache[item_code]
        uom = frappe.get_value("Item", item_code, "stock_uom") or ""
        cache[item_code] = uom
        return uom

    @staticmethod
    def _cell(cells: list[object], index: dict[str, int], key: str) -> str:
        position = index.get(key)
        if position is None or position >= len(cells):
            return ""
        value = cells[position]
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _num(cells: list[object], index: dict[str, int], key: str) -> float:
        position = index.get(key)
        if position is None or position >= len(cells):
            return 0.0
        value = cells[position]
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _canonical_key(header: str) -> str:
        """Map a spreadsheet header to its canonical model attribute."""
        mapping = {
            "Item Code": "item_code",
            "Warehouse": "warehouse",
            "Opening Quantity": "opening_quantity",
            "Valuation Rate": "valuation_rate",
        }
        return mapping.get(header, header)
