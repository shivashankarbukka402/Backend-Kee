"""
Image Mapping Importer

Imports the generated Image Mapping Excel file (``output/images``) into the
ERPNext ``Item`` DocType through the standard Frappe Data Import API, updating
each existing Item's image from its Item Code.
"""

from __future__ import annotations

import logging

from ..config import ImporterConfig
from .base_importer import BaseImporter, ImportExecutor


class ImageImporter(BaseImporter):
    """
    Imports the generated Image Mapping workbook by updating the ERPNext
    ``Item`` DocType (keyed on Item Code).
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="image",
        name="Image Mapping",
        doctype="Item",
        import_type="update",
        subdirectory="images",
        filenames=("Item_Image_Mapping.xlsx",),
        required_columns=("Item Code", "Image Path"),
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
