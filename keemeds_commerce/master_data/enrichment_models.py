"""
Catalog Enrichment Models

Dataclasses that represent the catalog-enrichment records produced by Phase 4
generators (item prices, opening stock and item image mappings). Like the
:mod:`~master_data.item_models`, they are deliberately independent of any
ERPNext DocType and of the generation logic that produces them.

These models are shared by every enrichment generator (and reusable by any
future OTC, Personal Care, Wellness, Baby Care, Medical Devices, Surgical and
Accessories generator) so the same record shapes are reused across the
application.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ItemPrice:
    """
    A single generated item price record.

    Attributes
    ----------
    item_code:
        The unique code of the medicine item this price belongs to.
    price_list:
        The price list the rate applies to.
    rate:
        The rate of the item in the given price list.
    currency:
        Currency of the rate.
    """

    item_code: str
    price_list: str
    rate: float
    currency: str


@dataclass(frozen=True)
class OpeningStock:
    """
    A single generated opening stock record.

    Attributes
    ----------
    item_code:
        The unique code of the medicine item this stock belongs to.
    warehouse:
        The warehouse holding the opening stock.
    opening_quantity:
        The opening quantity on hand.
    valuation_rate:
        The valuation rate used to value the opening quantity.
    """

    item_code: str
    warehouse: str
    opening_quantity: int
    valuation_rate: float


@dataclass(frozen=True)
class ImageMapping:
    """
    A single generated item image mapping record.

    Attributes
    ----------
    item_code:
        The unique code of the medicine item this image belongs to.
    image_path:
        The web path of the item image under the configured image URL root.
    """

    item_code: str
    image_path: str
