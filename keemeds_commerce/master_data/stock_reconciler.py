"""
Opening Stock Reconciler

The Phase 9 idempotency seam for the ERPNext Opening Stock import.

Why this exists
---------------
Opening Stock cannot be imported through the standard Data Import API as a
``Stock Ledger Entry``: SLE is a ledger (not a master) and cannot be Data
Imported. Phase 9 posts opening stock through the normal ERPNext inventory
workflow instead - a ``Stock Entry`` of type "Material Receipt" per row - and
achieves idempotency *before* import, without duplicating exporter/importer
logic:

1. The reconciler reads the canonical Opening Stock workbook
   (``output/stock/Opening_Stock.xlsx``).
2. It queries the ``(Item Code, Warehouse)`` pairs that already have stock in
   ERPNext (a read-only check via ``frappe.db`` - no direct SQL, no record
   creation).
3. It keeps only the rows whose pairing is not yet present and writes them to a
   reconciled workbook pointed at by the centralized Opening Stock importer
   configuration.
4. The Opening Stock importer posts only the reconciled rows as Stock Entries.

The canonical workbook is left untouched, so ``--verify`` and ``--all`` still
see the full catalog. When ``frappe`` / a site database is not available the
reconciler degrades gracefully: it copies the canonical workbook to the
reconciled path without filtering (an import cannot actually run without a site
anyway), so pre-flight file detection keeps working.

The module is Frappe-lazy and dependency-injected: the existing-pairs lookup is
injectable so the reconciler stays testable outside a live site, and it reuses
the shared record exporter (no export logic is duplicated).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from openpyxl import load_workbook

from .config import ExportConfig, MasterDataConfig, StockReconciliationConfig
from .enrichment_models import OpeningStock
from .exporters import export_records
from .logging_setup import get_logger

#: Injectable provider of the existing ``(Item Code, Warehouse)`` pairs.
ExistingStockPairsProvider = Callable[[], set[tuple[str, str]]]

#: Injectable provider that maps a configured warehouse name to the existing
#: ERPNext Warehouse name (e.g. ``"Finished Goods"`` -> ``"Finished Goods - HG"``).
WarehouseResolver = Callable[[str], str]


@dataclass(frozen=True)
class StockReconcileOutcome:
    """
    The result of reconciling the Opening Stock workbook before import.

    Attributes
    ----------
    source_path:
        Path of the canonical (full) Opening Stock workbook that was read.
    reconciled_path:
        Path of the reconciled (to-import) workbook that was written, or
        ``None`` when nothing could be produced.
    kept:
        Number of Opening Stock rows retained for import (not yet present).
    skipped:
        Number of Opening Stock rows skipped because their ``(Item Code,
        Warehouse)`` pairing already has stock in ERPNext.
    notes:
        Free-text notes about the reconciliation run.
    """

    source_path: str = ""
    reconciled_path: str | None = None
    kept: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)


class StockReconciler:
    """
    Produces an idempotent Opening Stock workbook from the canonical one.

    The reconciler reuses the generic record exporter
    (:func:`~master_data.exporters.export_records`) so no export logic is
    duplicated, and it never creates records directly - the actual posting is
    left to the Opening Stock importer via the standard Stock Entry workflow.
    """

    def __init__(
        self,
        config: MasterDataConfig,
        *,
        existing_pairs_provider: ExistingStockPairsProvider | None = None,
        warehouse_resolver: WarehouseResolver | None = None,
        export_config: ExportConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._reconciliation: StockReconciliationConfig = config.stock_reconciliation
        self._export_config = export_config or config.export
        self._existing_pairs_provider = existing_pairs_provider
        self._warehouse_resolver = warehouse_resolver
        self._logger = logger or get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def reconcile(self) -> StockReconcileOutcome:
        """
        Read the canonical Opening Stock workbook, drop already-posted rows and
        write the reconciled workbook.

        Returns the :class:`StockReconcileOutcome` describing how many rows were
        kept for import and how many were gracefully skipped as already present.
        """
        source = self._canonical_path()
        if not source.is_file():
            message = (
                f"Opening Stock reconciliation skipped: canonical workbook not "
                f"found at {source}."
            )
            self._logger.warning(message)
            return StockReconcileOutcome(source_path=str(source), notes=[message])

        rows = self._read_rows(source)
        existing = self._existing_pairs()

        if not rows:
            message = (
                "Opening Stock reconciliation skipped: canonical workbook has "
                "no data rows."
            )
            self._logger.warning(message)
            self._emit_kept_rows([])
            return StockReconcileOutcome(
                source_path=str(source),
                reconciled_path=str(self._reconciled_path()),
                notes=[message],
            )

        kept_rows = [
            row
            for row in rows
            if (
                self._clean(row.get("item_code")),
                self._resolve_warehouse(self._clean(row.get("warehouse"))),
            )
            not in existing
        ]
        skipped = len(rows) - len(kept_rows)

        kept = self._emit_kept_rows(kept_rows)
        self._logger.info(
            "Opening Stock reconciliation: %d kept for import, %d skipped as "
            "already present.",
            kept,
            skipped,
        )
        return StockReconcileOutcome(
            source_path=str(source),
            reconciled_path=str(self._reconciled_path()),
            kept=kept,
            skipped=skipped,
        )

    # ------------------------------------------------------------------ #
    # Existing pairs
    # ------------------------------------------------------------------ #

    def _existing_pairs(self) -> set[tuple[str, str]]:
        """
        Return the ``(Item Code, Warehouse)`` pairs already stocked in ERPNext.

        Uses the injectable provider when given; otherwise reads an active
        ``frappe.db`` (read-only). When no site database is available the
        lookup degrades to an empty result so nothing is skipped and the import
        proceeds - the executor will report a clean not-imported outcome if
        Frappe is genuinely unavailable.
        """
        if self._existing_pairs_provider is not None:
            return self._existing_pairs_provider()
        return self._query_existing_pairs()

    def _resolve_warehouse(self, configured: str) -> str:
        """
        Resolve a configured warehouse name to its ERPNext Warehouse name.

        Uses the injectable resolver when given; otherwise resolves against an
        active ``frappe.db`` (read-only). Unresolvable names are returned
        unchanged so a genuine mismatch is still reported rather than skipped.
        """
        if not configured:
            return configured
        if self._warehouse_resolver is not None:
            return self._warehouse_resolver(configured)
        return self._query_resolved_warehouse(configured)

    def _query_resolved_warehouse(self, configured: str) -> str:
        """Resolve ``configured`` to an existing ERPNext Warehouse via frappe.db."""
        try:
            import frappe

            if not getattr(frappe, "db", None):
                return configured
            if frappe.db.exists("Warehouse", configured):
                return configured
            hit = frappe.get_all(
                "Warehouse",
                filters={"warehouse_name": configured},
                fields=["name"],
                limit_page_length=1,
            )
            if hit:
                return hit[0]["name"]
        except Exception as exc:
            self._logger.warning(
                "Could not resolve warehouse %r (%s); using it unchanged.",
                configured,
                exc,
            )
        return configured

    def _query_existing_pairs(self) -> set[tuple[str, str]]:
        """Read the existing ``(Item Code, Warehouse)`` pairs via ``frappe.db``."""
        try:
            import frappe

            if not getattr(frappe, "db", None):
                return set()
            pairs = frappe.get_all(
                "Stock Ledger Entry",
                fields=["item_code", "warehouse"],
                distinct=True,
            )
        except Exception as exc:
            self._logger.warning(
                "Could not read existing Opening Stock; treating none as "
                "existing (%s).",
                exc,
            )
            return set()
        return {
            (self._clean(row.get("item_code")), self._clean(row.get("warehouse")))
            for row in pairs
            if row.get("item_code") and row.get("warehouse")
        }

    # ------------------------------------------------------------------ #
    # Workbook handling
    # ------------------------------------------------------------------ #

    def _canonical_path(self) -> Path:
        return (
            self._export_config.output_dir
            / self._reconciliation.source_subdirectory
            / self._reconciliation.canonical_filename
        )

    def _reconciled_path(self) -> Path:
        return (
            self._export_config.output_dir
            / self._reconciliation.source_subdirectory
            / self._reconciliation.reconciled_filename
        )

    def _read_rows(self, path: Path) -> list[dict[str, object]]:
        """
        Read the canonical workbook into rows keyed by canonical header.

        Values keep their spreadsheet types (e.g. the quantity stays numeric) so
        the kept rows can be re-exported without loss.
        """
        rows: list[dict[str, object]] = []
        try:
            workbook = load_workbook(path, read_only=True)
            sheet = workbook.active
            iterator = sheet.iter_rows(values_only=True)
            header_row = next(iterator, None)
            if header_row is None:
                workbook.close()
                return rows
            headers = [str(value) if value is not None else "" for value in header_row]
            for values in iterator:
                if values is None:
                    continue
                cells = list(values)
                row: dict[str, object] = {}
                for index, header in enumerate(headers):
                    if not header.strip():
                        continue
                    value = cells[index] if index < len(cells) else None
                    row[self._canonical_key(header.strip())] = value
                if row:
                    rows.append(row)
            workbook.close()
        except Exception as exc:
            self._logger.error("Unreadable Opening Stock workbook %s: %s", path, exc)
        return rows

    @staticmethod
    def _canonical_key(header: str) -> str:
        """Map a spreadsheet header to its canonical model attribute, if known."""
        mapping = {
            "Item Code": "item_code",
            "Warehouse": "warehouse",
            "Opening Quantity": "opening_quantity",
            "Valuation Rate": "valuation_rate",
        }
        return mapping.get(header, header)

    def _emit_kept_rows(self, kept_rows: list[dict[str, object]]) -> int:
        """
        Export the kept rows to the reconciled workbook and return the count.

        The reconciled workbook is produced by the shared record exporter using
        the centralized Opening Stock export spec but the reconciled file name.
        """
        stock_spec = self._config.stock_export
        reconciled_spec = replace(
            stock_spec, filename=self._reconciliation.reconciled_filename
        )
        records = [
            OpeningStock(
                item_code=self._clean(row.get("item_code")),
                warehouse=self._clean(row.get("warehouse")),
                opening_quantity=self._quantity(row.get("opening_quantity")),
                valuation_rate=self._rate(row.get("valuation_rate")),
            )
            for row in kept_rows
        ]
        export_records(
            records=records,
            spec=reconciled_spec,
            config=self._export_config,
            logger=self._logger,
        )
        return len(records)

    @staticmethod
    def _quantity(value: object) -> int:
        if value is None:
            return 0
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _rate(value: object) -> float:
        if value is None:
            return 0.0
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _clean(value: object) -> str:
        return str(value).strip() if value is not None else ""


def build_stock_reconciler(
    config: MasterDataConfig,
    logger: logging.Logger | None = None,
) -> StockReconciler:
    """
    Build a default Opening Stock reconciler from a master data configuration.
    """
    return StockReconciler(config=config, logger=logger)
