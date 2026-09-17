"""
Opening Stock Generator

Generates ERPNext Opening Stock records (Phase 4 catalog enrichment) for the
already-generated medicine items. For every item it produces one deterministic
opening stock record referencing the item's actual item code.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import StockConfig
from ..enrichment_generator import BaseEnrichmentGenerator
from ..enrichment_models import OpeningStock
from ..item_models import Item


class StockGenerator(BaseEnrichmentGenerator):
    """
    Generate one :class:`~master_data.enrichment_models.OpeningStock` record
    per item, deterministically derived from the item index.
    """

    key: str = "stock"
    name: str = "Opening Stock"

    def __init__(self, config, items: Sequence[Item], logger=None) -> None:
        super().__init__(config=config, items=items, logger=logger)
        self._stock_config: StockConfig = config.stock

    def generate(self) -> list[OpeningStock]:
        """
        Generate the opening stock records.

        Returns
        -------
        list[OpeningStock]
            Ordered opening stock records, one per item.
        """
        stock_config = self._stock_config
        records: list[OpeningStock] = []

        for index, item in enumerate(self.items):
            quantity = self.deterministic_quantity(
                index,
                stock_config.quantity_min,
                stock_config.quantity_max,
            )
            records.append(
                OpeningStock(
                    item_code=item.item_code,
                    warehouse=self.cycle_at(stock_config.warehouses, index),
                    opening_quantity=quantity,
                    valuation_rate=self._valuation_rate(index),
                )
            )

        return records

    def _valuation_rate(self, index: int) -> float:
        """Deterministic valuation rate for the item at ``index``."""
        return round(18.0 + index * 4.25, 2)
