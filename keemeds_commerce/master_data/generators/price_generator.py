"""
Item Price Generator

Generates ERPNext Item Price records (Phase 4 catalog enrichment) for the
already-generated medicine items. For every item it produces one deterministic
price record per configured price list (e.g. ``Standard Selling`` and
``Standard Buying``), referencing the item's actual item code.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import PriceConfig
from ..enrichment_generator import BaseEnrichmentGenerator
from ..enrichment_models import ItemPrice
from ..item_models import Item


class PriceGenerator(BaseEnrichmentGenerator):
    """
    Generate one :class:`~master_data.enrichment_models.ItemPrice` per item per
    configured price list, deterministically derived from the item index.
    """

    key: str = "price"
    name: str = "Item Prices"

    def __init__(
        self,
        config,
        items: Sequence[Item],
        logger=None,
    ) -> None:
        super().__init__(config=config, items=items, logger=logger)
        self._price_config: PriceConfig = config.price

    def generate(self) -> list[ItemPrice]:
        """
        Generate the item price records.

        Returns
        -------
        list[ItemPrice]
            Ordered price records, one per item per configured price list.
        """
        price_config = self._price_config
        records: list[ItemPrice] = []

        for index, item in enumerate(self.items):
            selling_rate = self._selling_rate(index)
            for price_list in price_config.price_lists:
                if price_list == "Standard Buying":
                    rate = self._buying_rate(selling_rate)
                else:
                    rate = selling_rate
                records.append(
                    ItemPrice(
                        item_code=item.item_code,
                        price_list=price_list,
                        rate=rate,
                        currency=price_config.currency,
                    )
                )

        return records

    def _selling_rate(self, index: int) -> float:
        """Deterministic selling rate for the item at ``index``."""
        price_config = self._price_config
        return round(price_config.base_rate + index * price_config.rate_step, 2)

    def _buying_rate(self, selling_rate: float) -> float:
        """Deterministic buying rate derived from a selling rate."""
        price_config = self._price_config
        return round(selling_rate * price_config.buying_factor, 2)
