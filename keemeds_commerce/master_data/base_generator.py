"""
Base Generator

Reusable base class for every master data generator (Medicines, OTC, Personal
Care, Wellness, Baby Care, Medical Devices, Surgical and Accessories).

It holds the injected dependencies every generator needs - the centralized
configuration and the normalized master data produced by the
:class:`~master_data.master_loader.MasterDataLoader` - and exposes convenience
accessors for the loaded master sets.

New generators subclass :class:`BaseGenerator` and only implement
:meth:`BaseGenerator.generate`, so the generation logic stays separated from
the shared data models and configuration.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .config import MasterDataConfig
from .item_models import GenerationResult, Item, ItemBatch
from .logging_setup import get_logger
from .models import MasterDataResult


class BaseGenerator(ABC):
    """
    Abstract base class for all master data generators.

    Attributes
    ----------
    key:
        Stable, machine-readable identifier (e.g. ``"medicine"``).
    name:
        Human-readable display name (e.g. ``"Medicines"``).
    """

    key: str = ""
    name: str = ""

    def __init__(
        self,
        config: MasterDataConfig,
        master_data: MasterDataResult,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._master_data = master_data
        self._logger = logger or get_logger(self.__class__.__name__)

    @property
    def brands(self) -> list[str]:
        """Loaded brand values."""
        return self._master_data.values_for("brand")

    @property
    def manufacturers(self) -> list[str]:
        """Loaded manufacturer values."""
        return self._master_data.values_for("manufacturer")

    @property
    def item_groups(self) -> list[str]:
        """Loaded item group values."""
        return self._master_data.values_for("item_group")

    @property
    def uoms(self) -> list[str]:
        """Loaded UOM values."""
        return self._master_data.values_for("uom")

    @abstractmethod
    def generate(self) -> GenerationResult:
        """
        Generate the master items and return them grouped into batches.
        """
        ...

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def split_into_batches(
        self,
        items: list[Item],
        batch_size: int,
    ) -> tuple[ItemBatch, ...]:
        """
        Split ``items`` into labelled batches of up to ``batch_size`` each.

        Batch labels follow the ``<Name> 001-100`` convention used by the
        generated item codes.
        """

        batches: list[ItemBatch] = []
        for start in range(0, len(items), batch_size):
            chunk = items[start : start + batch_size]
            label = f"{self.name} {start + 1:03d}-{start + len(chunk):03d}"
            batches.append(ItemBatch(label=label, items=tuple(chunk)))
        return tuple(batches)
