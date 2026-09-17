"""
Base Enrichment Generator

Reusable base class for the Phase 4 catalog-enrichment generators (Item Prices,
Opening Stock and Item Image Mappings). Unlike the item generators, every
enrichment generator consumes the already-generated medicine items rather than
the raw master reference sets, so it receives a flat tuple of
:class:`~master_data.item_models.Item` objects through dependency injection.

It holds the injected dependencies - the centralized configuration, the source
items and a logger - and provides deterministic helper methods that keep
generation logic separated from the shared data models and configuration.

Future OTC, Personal Care, Wellness, Baby Care, Medical Devices, Surgical and
Accessories enrichment generators can subclass this class and only implement
:meth:`BaseEnrichmentGenerator.generate`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from .config import MasterDataConfig
from .item_models import Item
from .logging_setup import get_logger


class BaseEnrichmentGenerator(ABC):
    """
    Abstract base class for all catalog-enrichment generators.

    Attributes
    ----------
    key:
        Stable, machine-readable identifier (e.g. ``"price"``).
    name:
        Human-readable display name (e.g. ``"Item Prices"``).
    """

    key: str = ""
    name: str = ""

    def __init__(
        self,
        config: MasterDataConfig,
        items: Sequence[Item],
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._items = tuple(items)
        self._logger = logger or get_logger(self.__class__.__name__)

    @property
    def items(self) -> tuple[Item, ...]:
        """The source medicine items this generator enriches."""
        return self._items

    @abstractmethod
    def generate(self) -> list[object]:
        """
        Enrich the source items and return the generated records.

        The concrete record type is defined by the subclass (e.g.
        :class:`~master_data.enrichment_models.ItemPrice`).
        """
        ...

    # ------------------------------------------------------------------ #
    # Shared deterministic helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def cycle_at(values: Sequence[str], index: int) -> str:
        """
        Return the value at ``index``, cycling through ``values``.

        ``values`` must be non-empty; the result is fully deterministic for a
        given ``index``.
        """
        return values[index % len(values)]

    @staticmethod
    def deterministic_quantity(
        index: int,
        minimum: int,
        maximum: int,
    ) -> int:
        """
        Return a deterministic opening quantity within ``[minimum, maximum]``.

        The value is derived only from ``index`` and the bounds, so repeated
        calls for the same inputs yield identical results.
        """
        span = maximum - minimum + 1
        return minimum + (index * 37) % span
