"""
Opening Stock Importer

Imports the generated Opening Stock Excel file (``output/stock``) into ERPNext.
Opening Stock cannot be imported as a ``Stock Ledger Entry`` through the Data
Import API, so this importer delegates to a Stock Entry backend that posts each
row as a standard Stock Entry of type "Material Receipt" through the normal
ERPNext inventory workflow. The generated rows describe the opening quantity
and valuation rate per item per warehouse.
"""

from __future__ import annotations

import logging

from ..config import STOCK_RECONCILED_FILENAME, ImporterConfig
from .base_importer import BaseImporter, ImportExecutor


class StockImporter(BaseImporter):
    """
    Imports the reconciled Opening Stock workbook as Stock Entry documents.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="stock",
        name="Opening Stock",
        doctype="Stock Entry",
        import_type="insert",
        subdirectory="stock",
        filenames=(STOCK_RECONCILED_FILENAME,),
        required_columns=("Item Code", "Warehouse", "Opening Quantity"),
    )

    def __init__(
        self,
        config: ImporterConfig,
        executor: ImportExecutor,
        *,
        export_config=None,
        import_config=None,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(
            config=config,
            export_config=export_config,
            import_config=import_config,
            executor=executor,
            logger=logger,
        )
