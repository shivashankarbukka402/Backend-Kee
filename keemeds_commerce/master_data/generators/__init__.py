"""
Master Data Generators

Contains concrete generators that produce master data items. Every item generator
subclasses :class:`~master_data.base_generator.BaseGenerator` and receives its
configuration and normalized master data through dependency injection. Every
catalog-enrichment generator subclasses
:class:`~master_data.enrichment_generator.BaseEnrichmentGenerator` and receives
the already-generated items.

Future generators (OTC, Personal Care, Wellness, Baby Care, Medical Devices,
Surgical and Accessories) register themselves here using the same pattern as
:class:`~master_data.generators.medicine_generator.MedicineGenerator`.
"""

from __future__ import annotations

from ..base_generator import BaseGenerator
from ..config import MasterDataConfig
from ..enrichment_generator import BaseEnrichmentGenerator
from ..item_models import GenerationResult
from ..models import MasterDataResult
from .image_mapping_generator import ImageMappingGenerator
from .medicine_generator import MedicineGenerator
from .price_generator import PriceGenerator
from .stock_generator import StockGenerator

__all__ = [
    "BaseEnrichmentGenerator",
    "BaseGenerator",
    "GenerationResult",
    "ImageMappingGenerator",
    "MasterDataConfig",
    "MasterDataResult",
    "MedicineGenerator",
    "PriceGenerator",
    "StockGenerator",
]


def build_generators(
    config: MasterDataConfig,
    master_data: MasterDataResult,
) -> dict[str, BaseGenerator]:
    """
    Build a registry of enabled generators keyed by their ``key`` attribute.

    The registry is used by the CLI and future integration points; adding a new
    generator only requires appending it here without touching existing code.
    """

    registry: dict[str, BaseGenerator] = {
        MedicineGenerator.key: MedicineGenerator(
            config=config, master_data=master_data
        ),
    }
    return registry


def build_enrichment_generators(
    config: MasterDataConfig,
    items: list[object] | tuple[object, ...],
) -> dict[str, BaseEnrichmentGenerator]:
    """
    Build a registry of catalog-enrichment generators keyed by ``key``.

    Each enrichment generator consumes the already-generated medicine items so
    the prices, opening stock and image mappings all reference real item codes.
    """

    registry: dict[str, BaseEnrichmentGenerator] = {
        PriceGenerator.key: PriceGenerator(config=config, items=items),
        StockGenerator.key: StockGenerator(config=config, items=items),
        ImageMappingGenerator.key: ImageMappingGenerator(config=config, items=items),
    }
    return registry
