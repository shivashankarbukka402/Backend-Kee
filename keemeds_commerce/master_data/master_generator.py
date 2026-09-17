"""
Master Data Generator (Phase 7)

Generates the ERPNext master records (UOM, Item Group, Brand, Manufacturer,
Item Attribute and Item Attribute Value) from the normalized master data loaded
by :class:`~master_data.master_loader.MasterDataLoader`.

Design
------
- **Reuses the foundation** by consuming a
  :class:`~master_data.models.MasterDataResult` and the centralized
  :class:`~master_data.config.MasterDataConfig` (dependency injection).
- **Never invents reference data** - every record value is read from the
  loaded source (Brands, Manufacturers, Item Groups, UOMs, Item Attributes and
  Item Attribute Values).
- **Deterministic** - output depends only on the loaded master data.
- **DB-free** - it produces Python record models only; it never touches the
  ERPNext database, so idempotency/duplicate handling stays with the import
  pipeline.

The generated records are keyed by the same ``key`` used by the centralized
master export specifications and importers, so the reusable exporters and the
import pipeline can drive them without orchestration changes.
"""

from __future__ import annotations

import logging

from .config import MasterDataConfig
from .logging_setup import get_logger
from .master_models import (
    UOM,
    Brand,
    ItemAttribute,
    ItemAttributeValue,
    ItemGroup,
    Manufacturer,
)
from .models import MasterDataResult

#: The ERPNext root Item Group under which generated groups are nested.
DEFAULT_PARENT_ITEM_GROUP = "All Item Groups"


class MasterGenerator:
    """
    Produces the Phase 7 ERPNext master records from the loaded source data.
    """

    key = "master"
    name = "Master Entities"

    def __init__(
        self,
        config: MasterDataConfig,
        master_data: MasterDataResult,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._master_data = master_data
        self._logger = logger or get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #

    def generate(self) -> dict[str, list[object]]:
        """
        Generate every master record set, keyed by its export/import key.

        Returns a mapping like ``{"uom": [UOM, ...], "item_group": [...], ...}``
        following the ERPNext dependency order.
        """
        return {
            "uom": self._uoms(),
            "item_group": self._item_groups(),
            "brand": self._brands(),
            "manufacturer": self._manufacturers(),
            "item_attribute": self._item_attributes(),
            "item_attribute_value": self._item_attribute_values(),
        }

    def _uoms(self) -> list[UOM]:
        names = self._master_data.values_for("uom")
        self._logger.info("Master UOMs: %d record(s)", len(names))
        return [UOM(name=name) for name in names]

    def _item_groups(self) -> list[ItemGroup]:
        names = self._master_data.values_for("item_group")
        self._logger.info("Master Item Groups: %d record(s)", len(names))
        return [
            ItemGroup(
                name=name,
                parent_item_group=DEFAULT_PARENT_ITEM_GROUP,
                is_group="0",
            )
            for name in names
        ]

    def _brands(self) -> list[Brand]:
        brand_names = self._master_data.values_for("brand")
        self._logger.info("Master Brands: %d record(s)", len(brand_names))
        return [Brand(name=name) for name in brand_names]

    def _manufacturers(self) -> list[Manufacturer]:
        manufacturers = self._master_data.values_for("manufacturer")
        self._logger.info("Master Manufacturers: %d record(s)", len(manufacturers))
        # ERPNext keys the Manufacturer record by its Short Name; the source
        # carries a single name per manufacturer, so it is used for both the
        # Short Name and the Full Name so the Item import references resolve.
        return [
            Manufacturer(short_name=name, full_name=name) for name in manufacturers
        ]

    def _item_attributes(self) -> list[ItemAttribute]:
        attributes = self._master_data.values_for("item_attribute")
        self._logger.info("Master Item Attributes: %d record(s)", len(attributes))
        return [ItemAttribute(name=name) for name in attributes]

    def _item_attribute_values(self) -> list[ItemAttributeValue]:
        rows = self._master_data.rows_for("item_attribute_value")
        records = [
            ItemAttributeValue(
                attribute=self._clean(row.get("attribute")),
                attribute_value=self._clean(row.get("attribute_value")),
                abbr=self._clean(row.get("abbr")),
            )
            for row in rows
        ]
        self._logger.info(
            "Master Item Attribute Values: %d record(s)", len(records)
        )
        return records

    @staticmethod
    def _clean(value: object) -> str:
        return str(value).strip() if value is not None else ""
