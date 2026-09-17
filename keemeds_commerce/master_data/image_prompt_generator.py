"""
AI Image Prompt Generator

Phase 9.5: emits one deterministic AI image prompt per medicine item.

The prompt text is assembled entirely from the item's own generated metadata
(medicine name, brand, manufacturer, dosage form, strength, category and
packaging style) together with a fixed set of professional packaging and
commercial e-commerce photography instructions. No external service is
consulted; the prompt file is a self-contained input artifact for the
Phase 9.5 image renderer, stored at ``output/image_prompts/MED-001.txt``.

The dosage form / strength / ingredient data comes from the same deterministic
medicine catalog the items were generated from (keyed by item-code index), so
the prompt is an accurate, lossless description of the medicine.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import AIImageConfig
from .generators.medicine_generator import MEDICINE_CATALOG
from .item_models import Item
from .logging_setup import get_logger

#: Generic, fixed instructions applied to every medicine prompt (professional
#: pharma packaging, commercial e-commerce photography, clean presentation).
_INSTRUCTIONS = (
    "Professional pharmaceutical packaging, commercial e-commerce product "
    "photography, front-facing package, pure white background, soft natural "
    "shadow, studio lighting, ultra realistic, high resolution, no watermark, "
    "no unrelated branding."
)

#: Deterministic packaging-style descriptions keyed by a keyword found in the
#: dosage form, so a tablet form yields a "blister pack" style and an inhaler
#: yields its cartridge, without redesigning the medicine catalog.
_PACKAGING_STYLES = {
    "tablet": "blister pack of tablets",
    "capsule": "blister pack of capsules",
    "syrup": "amber glass syrup bottle",
    "suspension": "plastic suspension bottle",
    "ointment": "collapsible ointment tube",
    "cream": "cream jar",
    "gel": "gel tube",
    "injection": "single-dose glass vial with flip-off cap",
    "drops": "dropper bottle",
    "spray": "spray pump bottle",
    "inhaler": "metered-dose inhaler",
    "powder": "sealed sachet of powder",
    "sachet": "sealed sachet",
    "ampoule": "glass ampoule",
    "lozenge": "blister pack of lozenges",
    "suppository": "blister pack of suppositories",
}


@dataclass
class ImagePromptOutcome:
    """
    The result of an AI image prompt generation run.
    """

    generated: int = 0
    prompts_dir: Path | None = None
    files: list[str] = field(default_factory=list)


class MedicinePromptBuilder:
    """
    Assembles a single deterministic AI image prompt from an item's metadata.
    """

    def build(self, item: Item, variant) -> str:
        """Return the complete prompt text for ``item``."""
        lines = [
            f"Medicine Name: {item.item_name}",
            f"Brand: {item.brand}",
            f"Manufacturer: {item.default_item_manufacturer}",
            f"Dosage Form: {variant.form}",
            f"Strength: {variant.strength}",
            f"Category: {item.item_group}",
            f"Packaging Style: {_packaging_for(variant.form)}",
            "",
            _INSTRUCTIONS,
            "",
        ]
        return "\n".join(lines)

    def variant_for(self, item: Item):
        """Resolve the medicine variant used to build the prompt."""
        index = _item_index(item.item_code)
        return MEDICINE_CATALOG[index]


class _ItemSource(Protocol):
    """Minimal contract satisfied by the items handed to the prompt generator."""

    item_code: str
    item_name: str
    brand: str
    default_item_manufacturer: str
    item_group: str


class ImagePromptGenerator:
    """
    Writes one prompt file per medicine item into the prompts output directory.
    """

    def __init__(
        self,
        config: AIImageConfig,
        builder: MedicinePromptBuilder | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._builder = builder or MedicinePromptBuilder()
        self._logger = logger or get_logger(self.__class__.__name__)

    def run(self, items: Sequence[_ItemSource]) -> ImagePromptOutcome:
        """Generate the per-medicine prompt files and return the outcome."""
        prompts_dir = self._config.prompts_output_dir
        prompts_dir.mkdir(parents=True, exist_ok=True)
        outcome = ImagePromptOutcome(prompts_dir=prompts_dir)

        for item in items:
            try:
                variant = self._builder.variant_for(item)
            except (ValueError, IndexError, KeyError):
                self._logger.warning(
                    "Could not resolve variant for %s; skipping prompt.",
                    item.item_code,
                )
                continue
            text = self._builder.build(item, variant)
            path = prompts_dir / f"{item.item_code}{self._config.prompt_extension}"
            if path.exists():
                self._logger.info("Prompt already present; skipping %s", path.name)
                continue
            path.write_text(text, encoding="utf-8")
            outcome.generated += 1
            outcome.files.append(path.name)
            self._logger.info("Wrote AI prompt: %s", path)

        return outcome


def _item_index(item_code: str) -> int:
    """Return the 0-based medicine-catalog index for an item code like ``MED-001``."""
    suffix = item_code.rsplit("-", 1)[-1]
    return int(suffix) - 1


def _packaging_for(form: str) -> str:
    """Return a deterministic packaging-style description for a dosage form."""
    tokens = form.casefold().replace("-", " ").split()
    for token in tokens:
        for keyword, style in _PACKAGING_STYLES.items():
            if keyword in token:
                return style
    return "commercial medicine package"


def build_image_prompt_generator(
    config: AIImageConfig,
    logger: logging.Logger | None = None,
) -> ImagePromptGenerator:
    """Build a default AI image prompt generator."""
    return ImagePromptGenerator(config=config, logger=logger)
