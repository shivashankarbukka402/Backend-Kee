"""
Item Master Importer

Imports the generated Item Master Excel files (under ``output/items``) into the
ERPNext ``Item`` DocType through the standard Frappe Data Import API.
"""

from __future__ import annotations

import logging

from ..config import ImporterConfig
from .base_importer import BaseImporter, ImportExecutor


class ItemImporter(BaseImporter):
    """
    Imports generated Item Master workbooks into the ERPNext ``Item`` DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="item",
        name="Item Master",
        doctype="Item",
        import_type="insert",
        subdirectory="items",
        filenames=("*Item_Master*.xlsx",),
        required_columns=("Item Code",),
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
        # Defaults are resolved by the import manager; concrete importers are
        # constructed through the registry which supplies all collaborators.
        super().__init__(
            config=config,
            export_config=export_config,
            import_config=import_config,
            executor=executor,
            logger=logger,
        )
