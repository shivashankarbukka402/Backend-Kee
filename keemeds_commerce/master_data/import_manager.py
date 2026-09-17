"""
Import Manager

The registry/factory for the Phase 6 ERPNext importers. The :class:`ImportManager`
holds the ordered, dependency-ordered collection of :class:`~master_data.importers.base_importer.BaseImporter`
instances and provides lookup and ordered iteration so the import pipeline stays
free of hardcoded importer logic.

Importers are registered through the :func:`build_importers` factory. A future
importer (OTC, Wellness, Personal Care, Baby Care, Devices, Surgical,
Accessories) requires only:

- a concrete :class:`BaseImporter` subclass with a ``DEFAULT_CONFIG``, and
- one line adding it here.

No orchestration change is needed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator

from .config import MasterDataConfig
from .importers import (
    BaseImporter,
    BrandImporter,
    FrappeImportExecutor,
    ImageImporter,
    ImportExecutor,
    ItemAttributeImporter,
    ItemAttributeValueImporter,
    ItemGroupImporter,
    ItemImporter,
    ManufacturerImporter,
    PriceImporter,
    StockEntryExecutor,
    StockImporter,
    UOMImporter,
)

#: The concrete importer classes, in ERPNext dependency order. The master
#: entities (UOM, Item Group, Brand, Manufacturer, Item Attribute, Item
#: Attribute Value) precede the Item Master, which precedes the enrichment
#: imports.
IMPORTER_CLASSES: tuple[type[BaseImporter], ...] = (
    UOMImporter,
    ItemGroupImporter,
    BrandImporter,
    ManufacturerImporter,
    ItemAttributeImporter,
    ItemAttributeValueImporter,
    ItemImporter,
    PriceImporter,
    StockImporter,
    ImageImporter,
)


class ImportManager:
    """
    Ordered registry of configured ERPNext importers.
    """

    def __init__(self, importers: Iterable[BaseImporter]) -> None:
        self._importers: dict[str, BaseImporter] = {
            importer.key: importer for importer in importers
        }

    def __iter__(self) -> Iterator[BaseImporter]:
        """Yield importers in registration (dependency) order."""
        yield from self._importers.values()

    @property
    def keys(self) -> tuple[str, ...]:
        """The importer keys in registration order."""
        return tuple(self._importers.keys())

    def get(self, key: str) -> BaseImporter | None:
        """Return the importer for ``key`` or ``None``."""
        return self._importers.get(key)

    @property
    def count(self) -> int:
        """Number of registered importers."""
        return len(self._importers)


def build_executor(
    logger: logging.Logger | None = None,
) -> ImportExecutor:
    """
    Build the default ERPNext import executor.

    In production this is the standard Frappe Data Import executor.
    """
    return FrappeImportExecutor(logger=logger)


def build_importers(
    config: MasterDataConfig,
    executor: ImportExecutor | None = None,
    logger: logging.Logger | None = None,
) -> ImportManager:
    """
    Build the registry of configured ERPNext importers.

    Each registered importer class resolves its ``ImporterConfig`` from the
    centralized configuration, falling back to its ``DEFAULT_CONFIG`` when not
    yet configured, so adding a new importer class only requires registration.
    """
    executor = executor or build_executor(logger=logger)
    stock_executor = StockEntryExecutor(config=config, logger=logger)
    importers: list[BaseImporter] = []
    for cls in IMPORTER_CLASSES:
        resolved = config.importer_config_for(cls.DEFAULT_CONFIG.key) or cls.DEFAULT_CONFIG
        # Opening Stock cannot be Data Imported (SLE is a ledger), so it gets a
        # dedicated executor that posts Stock Entry "Material Receipt" documents
        # through the standard ERPNext inventory workflow.
        cls_executor = (
            stock_executor if resolved.key == StockImporter.DEFAULT_CONFIG.key else executor
        )
        importers.append(
            cls(
                config=resolved,
                executor=cls_executor,
                export_config=config.export,
                import_config=config.import_config,
                logger=logger,
            )
        )
    return ImportManager(importers)
