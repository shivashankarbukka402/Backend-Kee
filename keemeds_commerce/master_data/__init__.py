"""
KeeMeds Master Data Generator

Phase 1 - Foundation (Brands, Manufacturers, Item Groups, UOMs),
Phase 2 - Medicines, Phase 3 - Excel export, Phase 4 - Catalog enrichment
(item prices, opening stock, image mappings), Phase 5 - One-command pipeline
(validate, generate, export and report in a single run).

Provides the reusable foundation for generating master data for the KeeMeds
ERPNext application. The foundation loads raw master values from CSV or Excel
source files, validates them and normalizes the result, without depending on
the Frappe ORM or the ERPNext database.
"""

from __future__ import annotations

from .config import MasterDataConfig
from .enrichment_models import ImageMapping, ItemPrice, OpeningStock
from .master_loader import MasterDataLoader, normalize_values
from .models import MasterDataResult
from .pipeline import MasterDataPipeline
from .reporting import GenerationReport, ReportWriter, build_report_writer

__all__ = [
    "GenerationReport",
    "ImageMapping",
    "ItemPrice",
    "MasterDataConfig",
    "MasterDataLoader",
    "MasterDataPipeline",
    "MasterDataResult",
    "OpeningStock",
    "ReportWriter",
    "build_report_writer",
    "normalize_values",
]
