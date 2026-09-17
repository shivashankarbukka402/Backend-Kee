"""
Generated Item Models

Dataclasses that represent the output of a master data generator. They are
deliberately independent of any ERPNext DocType and of the generation logic
that produces them, keeping the "data models" separate from the "generation
logic" as the Phase 2 architecture requires.

These models are shared by every future generator (OTC, Personal Care,
Wellness, Baby Care, Medical Devices, Surgical and Accessories) so a single
``Item`` shape is reused across the application.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    """
    A single generated master data item.

    Attributes
    ----------
    item_code:
        Unique, deterministic code for the item.
    item_name:
        Unique, human-readable name of the item.
    item_group:
        ERPNext Item Group referenced from loaded master data.
    default_uom:
        Default Unit of Measure referenced from loaded master data.
    brand:
        Brand referenced from loaded master data.
    description:
        Free-text description of the item.
    maintain_stock:
        Whether the item is stock-maintained.
    allow_sales:
        Whether the item can be sold.
    allow_purchase:
        Whether the item can be purchased.
    has_batch_no:
        Whether the item is tracked by batch number.
    has_expiry_date:
        Whether the item has an expiry date.
    shelf_life_in_days:
        Shelf life of the item in days.
    country_of_origin:
        Country where the item originates.
    default_item_manufacturer:
        Default manufacturer referenced from loaded master data.
    has_variants:
        Whether the item has variants.
    """

    item_code: str
    item_name: str
    item_group: str
    default_uom: str
    brand: str
    description: str
    shelf_life_in_days: int
    country_of_origin: str
    default_item_manufacturer: str
    maintain_stock: bool = True
    allow_sales: bool = True
    allow_purchase: bool = True
    has_batch_no: bool = True
    has_expiry_date: bool = True
    has_variants: bool = False


@dataclass(frozen=True)
class ItemBatch:
    """
    A contiguous slice of generated items with a human-readable label.
    """

    label: str
    items: tuple[Item, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GenerationResult:
    """
    The output of a generator: a labelled sequence of batches.

    Attributes
    ----------
    batch_size:
        Number of items per batch used when splitting the result.
    batches:
        Ordered tuple of batches; each holds a slice of the items.
    """

    batch_size: int
    batches: tuple[ItemBatch, ...] = field(default_factory=tuple)

    @property
    def items(self) -> tuple[Item, ...]:
        """
        Flatten all batches into a single ordered tuple of items.
        """

        return tuple(item for batch in self.batches for item in batch.items)

    @property
    def count(self) -> int:
        """
        Total number of generated items.
        """

        return len(self.items)
