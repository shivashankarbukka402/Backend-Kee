"""
Master Data Importers

Concrete ERPNext importers that feed the generated catalog files into ERPNext
through the standard Frappe Data Import API. Every importer subclasses
:class:`~master_data.importers.base_importer.BaseImporter` and only declares the
target DocType, import type and source files; all orchestration lives in the
base class and the import pipeline.

Future importers (OTC, Wellness, Personal Care, Baby Care, Devices, Surgical,
Accessories) register a new class here and in the import manager registry; no
orchestration change is needed.
"""

from __future__ import annotations

from ..config import ImporterConfig
from .base_importer import (
    BaseImporter,
    FrappeImportExecutor,
    ImportExecutor,
    ImportOutcome,
)
from .image_importer import ImageImporter
from .item_importer import ItemImporter
from .master_importers import (
    BrandImporter,
    ItemAttributeImporter,
    ItemAttributeValueImporter,
    ItemGroupImporter,
    ManufacturerImporter,
    UOMImporter,
)
from .price_importer import PriceImporter
from .stock_entry_executor import StockEntryExecutor
from .stock_importer import StockImporter

__all__ = [
    "BaseImporter",
    "BrandImporter",
    "FrappeImportExecutor",
    "ImageImporter",
    "ImportExecutor",
    "ImportOutcome",
    "ImporterConfig",
    "ItemAttributeImporter",
    "ItemAttributeValueImporter",
    "ItemGroupImporter",
    "ItemImporter",
    "ManufacturerImporter",
    "PriceImporter",
    "StockEntryExecutor",
    "StockImporter",
    "UOMImporter",
]
