"""
Item Price Reconciler

The Phase 8 idempotency seam for the ERPNext Item Price import.

Why this exists
---------------
``Item Price`` uses a ``hash`` autoname (not ``field:...``), so the standard
Data Import "update existing records" path cannot locate an existing record
without a ``name``/ID column in the workbook. Inserting the full catalog on a
re-run would therefore fail every already-existing price row and turn the import
non-zero, which is not idempotent.

Phase 8 achieves idempotency *before* import instead, without changing the
existing :class:`~master_data.importers.price_importer.PriceImporter` or the
standard Data Import API:

1. The reconciler reads the canonical Item Price workbook
   (``output/prices/Item_Prices.xlsx``).
2. It queries the ``(Item Code, Price List)`` pairs that already exist in
   ERPNext (a read-only check via ``frappe.db`` - no direct SQL, no record
   creation).
3. It keeps only the rows whose pairing is not yet present and writes them to a
   reconciled workbook pointed at by the centralized price importer
   configuration.
4. The existing :class:`PriceImporter` imports only the reconciled rows.

The canonical workbook is left untouched, so ``--verify`` and ``--all`` still
see the full catalog. When ``frappe`` / a site database is not available the
reconciler degrades gracefully: it copies the canonical workbook to the
reconciled path without filtering (an import cannot actually run without a
site anyway), so pre-flight file detection keeps working.

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

from .config import ExportConfig, MasterDataConfig, PriceReconciliationConfig
from .enrichment_models import ItemPrice
from .exporters import export_records
from .logging_setup import get_logger

#: Injectable provider of the existing ``(Item Code, Price List)`` pairs.
ExistingPairsProvider = Callable[[], set[tuple[str, str]]]


@dataclass(frozen=True)
class PriceReconcileOutcome:
    """
    The result of reconciling the Item Price workbook before import.

    Attributes
    ----------
    source_path:
        Path of the canonical (full) Item Price workbook that was read.
    reconciled_path:
        Path of the reconciled (to-import) workbook that was written, or
        ``None`` when nothing could be produced.
    kept:
        Number of price rows retained for import (not yet present in ERPNext).
    skipped:
        Number of price rows skipped because their ``(Item Code, Price List)``
        pairing already exists in ERPNext.
    notes:
        Free-text notes about the reconciliation run.
    """

    source_path: str = ""
    reconciled_path: str | None = None
    kept: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)


class PriceReconciler:
    """
    Produces an idempotent Item Price workbook from the canonical one.

    The reconciler reuses the generic record exporter
    (:func:`~master_data.exporters.export_records`) so no export logic is
    duplicated, and it never creates records directly - the actual insertion is
    left to the existing :class:`PriceImporter` via the standard Data Import API.
    """

    def __init__(
        self,
        config: MasterDataConfig,
        *,
        existing_pairs_provider: ExistingPairsProvider | None = None,
        export_config: ExportConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._reconciliation: PriceReconciliationConfig = config.price_reconciliation
        self._export_config = export_config or config.export
        self._existing_pairs_provider = existing_pairs_provider
        self._logger = logger or get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def reconcile(self) -> PriceReconcileOutcome:
        """
        Read the canonical price workbook, drop already-existing prices and
        write the reconciled workbook.

        Returns the :class:`PriceReconcileOutcome` describing how many rows were
        kept for import and how many were gracefully skipped as already present.
        """
        source = self._canonical_path()
        if not source.is_file():
            message = (
                f"Item Price reconciliation skipped: canonical workbook not "
                f"found at {source}."
            )
            self._logger.warning(message)
            return PriceReconcileOutcome(source_path=str(source), notes=[message])

        rows = self._read_rows(source)
        existing = self._existing_pairs()

        if not rows:
            message = "Item Price reconciliation skipped: canonical workbook has no data rows."
            self._logger.warning(message)
            self._emit_kept_rows([])
            return PriceReconcileOutcome(
                source_path=str(source),
                reconciled_path=str(self._reconciled_path()),
                notes=[message],
            )

        kept_rows = [
            row
            for row in rows
            if (self._clean(row.get("item_code")), self._clean(row.get("price_list")))
            not in existing
        ]
        skipped = len(rows) - len(kept_rows)

        kept = self._emit_kept_rows(kept_rows)
        self._logger.info(
            "Item Price reconciliation: %d kept for import, %d skipped as "
            "already present.",
            kept,
            skipped,
        )
        return PriceReconcileOutcome(
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
        Return the ``(Item Code, Price List)`` pairs already present in ERPNext.

        Uses the injectable provider when given; otherwise reads an active
        ``frappe.db`` (read-only). When no site database is available the
        lookup degrades to an empty result so nothing is skipped and the import
        proceeds - the executor will report a clean not-imported outcome if
        Frappe is genuinely unavailable.
        """
        if self._existing_pairs_provider is not None:
            return self._existing_pairs_provider()
        return self._query_existing_pairs()

    def _query_existing_pairs(self) -> set[tuple[str, str]]:
        """Read the existing ``(Item Code, Price List)`` pairs via ``frappe.db``."""
        try:
            import frappe

            if not getattr(frappe, "db", None):
                return set()
            pairs = frappe.get_all(
                "Item Price",
                fields=["item_code", "price_list"],
                distinct=True,
            )
        except Exception as exc:
            self._logger.warning(
                "Could not read existing Item Prices; treating none as existing "
                "(%s).",
                exc,
            )
            return set()
        return {
            (self._clean(row.get("item_code")), self._clean(row.get("price_list")))
            for row in pairs
            if row.get("item_code") and row.get("price_list")
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

        Values keep their spreadsheet types (e.g. the rate stays numeric) so the
        kept rows can be re-exported without loss.
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
            self._logger.error("Unreadable Item Price workbook %s: %s", path, exc)
        return rows

    @staticmethod
    def _canonical_key(header: str) -> str:
        """Map a spreadsheet header to its canonical model attribute, if known."""
        mapping = {
            "Item Code": "item_code",
            "Price List": "price_list",
            "Price List Rate": "rate",
            "Rate": "rate",
            "Currency": "currency",
        }
        return mapping.get(header, header)

    def _emit_kept_rows(self, kept_rows: list[dict[str, object]]) -> int:
        """
        Export the kept rows to the reconciled workbook and return the count.

        The reconciled workbook is produced by the shared record exporter using
        the centralized price export spec but the reconciled file name.
        """
        price_spec = self._config.price_export
        reconciled_spec = replace(
            price_spec, filename=self._reconciliation.reconciled_filename
        )
        records = [
            ItemPrice(
                item_code=self._clean(row.get("item_code")),
                price_list=self._clean(row.get("price_list")),
                rate=self._rate(row.get("rate")),
                currency=self._clean(row.get("currency")) or self._config.price.currency,
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


def build_price_reconciler(
    config: MasterDataConfig,
    logger: logging.Logger | None = None,
) -> PriceReconciler:
    """
    Build a default Item Price reconciler from a master data configuration.
    """
    return PriceReconciler(config=config, logger=logger)
