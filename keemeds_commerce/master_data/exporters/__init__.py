"""
Master Data Exporters

Contains exporters that turn generated master data models into standardized
output files.

The package provides two exporter flavors:

- :class:`~master_data.exporters.excel_exporter.ExcelExporter`, the ERPNext Item
  Import exporter for :class:`~master_data.item_models.GenerationResult` batches
  (one file per batch).
- :class:`~master_data.exporters.record_exporter.RecordExcelExporter`, the
  generic single-file exporter for catalog-enrichment records (item prices,
  opening stock, image mappings) and any future generator record.

Both are intentionally generic: any generator's records can be exported without
modifying the exporter itself.
"""

from __future__ import annotations

import logging

from ..config import EnrichmentExportConfig, ExportConfig
from ..item_models import GenerationResult
from .excel_exporter import (
    ExcelExporter,
    ExportReport,
    ValidationError,
    build_exporter,
)
from .record_exporter import RecordExcelExporter, build_record_exporter

__all__ = [
    "EnrichmentExportConfig",
    "ExcelExporter",
    "ExportConfig",
    "ExportReport",
    "GenerationResult",
    "RecordExcelExporter",
    "ValidationError",
    "build_exporter",
    "build_record_exporter",
    "export_generation_result",
    "export_records",
]


def export_generation_result(
    result: GenerationResult,
    config: ExportConfig,
    logger: logging.Logger | None = None,
) -> ExportReport:
    """
    Export every batch of a generation result as ERPNext Item Import files.

    Convenience wrapper that builds an exporter and exports the given result's
    batches, writing them under the configured output directory.
    """
    exporter = build_exporter(config=config, logger=logger)
    return exporter.export(result.batches)


def export_records(
    records: list[object] | tuple[object, ...],
    spec: EnrichmentExportConfig,
    config: ExportConfig,
    logger: logging.Logger | None = None,
) -> ExportReport:
    """
    Export catalog-enrichment records into a single ERPNext import workbook.

    Convenience wrapper that builds a generic record exporter and writes the
    records according to the given centralized export specification.
    """
    exporter = build_record_exporter(config=config, logger=logger)
    return exporter.export(records, spec)
