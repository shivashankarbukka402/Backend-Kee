"""
Item Price Importer

Imports the generated Item Price Excel file (``output/prices``) into the
ERPNext ``Item Price`` DocType through the standard Frappe Data Import API.
"""

from __future__ import annotations

import logging

from ..config import ImporterConfig
from .base_importer import BaseImporter, ImportExecutor


class PriceImporter(BaseImporter):
    """
    Imports the generated Item Price workbook into the ERPNext ``Item Price``
    DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="price",
        name="Item Price",
        doctype="Item Price",
        import_type="insert",
        subdirectory="prices",
        filenames=("Item_Prices.xlsx",),
        required_columns=("Item Code", "Price List", "Rate"),
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
