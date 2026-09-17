"""
Master Entity Importers

Imports the generated Phase 7 master workbooks (UOM, Item Group, Brand,
Manufacturer, Item Attribute and Item Attribute Value) into their ERPNext
DocTypes through the standard Frappe Data Import API.

Each importer subclasses :class:`~master_data.importers.base_importer.BaseImporter`
and only declares its target DocType, import type and source file. They run in
ERPNext dependency order (before the Item Master) because generated items
reference these masters.
"""

from __future__ import annotations

import logging

from ..config import ImporterConfig
from .base_importer import BaseImporter, ImportExecutor


class UOMImporter(BaseImporter):
    """
    Imports the generated UOM workbook into the ERPNext ``UOM`` DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="uom",
        name="UOM",
        doctype="UOM",
        import_type="insert",
        subdirectory="uoms",
        filenames=("UOMs.xlsx",),
        required_columns=("UOM Name",),
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


class ItemGroupImporter(BaseImporter):
    """
    Imports the generated Item Group workbook into the ERPNext ``Item Group``
    DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="item_group",
        name="Item Group",
        doctype="Item Group",
        import_type="insert",
        subdirectory="item_groups",
        filenames=("Item_Groups.xlsx",),
        required_columns=("Item Group Name",),
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


class BrandImporter(BaseImporter):
    """
    Imports the generated Brand workbook into the ERPNext ``Brand`` DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="brand",
        name="Brand",
        doctype="Brand",
        import_type="insert",
        subdirectory="brands",
        filenames=("Brands.xlsx",),
        required_columns=("Brand Name",),
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


class ManufacturerImporter(BaseImporter):
    """
    Imports the generated Manufacturer workbook into the ERPNext ``Manufacturer``
    DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="manufacturer",
        name="Manufacturer",
        doctype="Manufacturer",
        import_type="insert",
        subdirectory="manufacturers",
        filenames=("Manufacturers.xlsx",),
        required_columns=("Short Name",),
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


class ItemAttributeImporter(BaseImporter):
    """
    Imports the generated Item Attribute workbook into the ERPNext
    ``Item Attribute`` DocType.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="item_attribute",
        name="Item Attribute",
        doctype="Item Attribute",
        import_type="insert",
        subdirectory="item_attributes",
        filenames=("Item_Attributes.xlsx",),
        required_columns=("Attribute Name",),
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


class ItemAttributeValueImporter(BaseImporter):
    """
    Imports the generated Item Attribute Value workbook.

    Item Attribute Values live in a child table nested under the parent
    ``Item Attribute`` DocType, so this importer feeds the workbook into the
    ``Item Attribute`` DocType using the standard
    ``Label (Table Field Label)`` child columns.
    """

    DEFAULT_CONFIG = ImporterConfig(
        key="item_attribute_value",
        name="Item Attribute Value",
        doctype="Item Attribute",
        import_type="insert",
        subdirectory="item_attribute_values",
        filenames=("Item_Attribute_Values.xlsx",),
        required_columns=("Attribute Name",),
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
