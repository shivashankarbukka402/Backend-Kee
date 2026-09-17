"""
AI Product Image Generator

Phase 9.5: renders a small product gallery for every medicine item.

For each medicine the generator produces ``images_per_item`` (default three)
optimized WebP product images and stores them under the canonical
``output/item_images`` directory as ``MED-001-1.webp``, ``MED-001-2.webp`` and
``MED-001-3.webp`` (front packshot, 45-degree perspective and side/back view).
The first image is the primary image assigned to the ERPNext Item through the
existing image-mapping pipeline.

Rendering is behind an injected :class:`MedicineImageRenderer` seam. The default
:class:`PillowMedicineImageRenderer` is fully self-contained and deterministic
(it derives each package's visual from the medicine's own metadata and catalog
index, so different medicines and different views produce distinct images)
matching the Phase 7 approach of producing real files without an external AI
service. A future cloud AI renderer can be supplied through the same seam.

Optimization (WebP conversion, resize to the configured dimensions, lossy
compression and metadata stripping) is centralized in :class:`ImageOptimizer`
using the values from :class:`~master_data.config.AIImageConfig`, so a catalog
or size change requires config-only edits. Optimized copies are additionally
mirrored into the ERPNext site's public ``files/item_images`` directory so the
existing ``/files/item_images`` image URLs resolve at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .config import AIImageConfig
from .image_prompt_generator import MedicinePromptBuilder
from .item_models import Item
from .logging_setup import get_logger

if TYPE_CHECKING:
    from PIL import Image, ImageDraw

#: Deterministic foreground/background color pairs cycled per item index so the
#: generated packages are visually distinct across the catalog.
_CATEGORY_COLORS = (
    ((46, 139, 110), (232, 246, 239)),  # herbal green
    ((37, 99, 168), (224, 238, 250)),   # cool blue
    ((181, 101, 29), (252, 235, 214)),  # warm amber
    ((126, 34, 152), (246, 230, 252)),  # violet
    ((196, 30, 58), (252, 228, 232)),   # crimson
    ((13, 148, 136), (222, 245, 241)),  # teal
    ((120, 113, 108), (238, 235, 233)), # neutral
    ((30, 64, 175), (225, 232, 250)),   # indigo
)


@dataclass
class AIMageOutcome:
    """
    The result of an AI product image generation run.
    """

    images_generated: int = 0
    images_optimized: int = 0
    images_skipped: int = 0
    failed: list[str] = field(default_factory=list)
    items: int = 0
    prompts_generated: int = 0
    manifest_entries: int = 0


class MedicineImageRenderer(Protocol):
    """Renders a single per-item product image as a Pillow ``RGB`` image."""

    def render(
        self,
        item: Item,
        variant,
        profile_index: int,
        size: tuple[int, int],
    ) -> Image.Image:
        """Return a newly allocated ``RGB`` image for the item/view."""
        raise NotImplementedError


class PillowMedicineImageRenderer:
    """
    Deterministic, self-contained product-image renderer.

    The generated package art encodes the medicine name, brand, dosage form
    and strength, and its coloring is derived from the item's catalog index so
    each item is visually unique. The profile index selects the view:
    ``0`` front packshot, ``1`` 45-degree perspective, ``2`` side/back view.
    """

    def __init__(self, config: AIImageConfig) -> None:
        self._config = config

    def render(
        self,
        item: Item,
        variant,
        profile_index: int,
        size: tuple[int, int],
    ) -> Image.Image:
        from PIL import Image, ImageDraw

        index = _item_index(item.item_code)
        fg, bg = _CATEGORY_COLORS[index % len(_CATEGORY_COLORS)]
        width, height = size

        image = Image.new("RGB", size, bg)
        draw = ImageDraw.Draw(image)
        self._draw_shadow(draw, size)
        self._draw_package(draw, width, height, fg, profile_index)
        self._draw_label(draw, width, height, item, variant, fg, profile_index)
        return image

    @staticmethod
    def _draw_shadow(draw, size: tuple[int, int]) -> None:
        width, height = size
        # Soft natural ground shadow beneath the package.
        draw.ellipse(
            [
                int(width * 0.20),
                int(height * 0.82),
                int(width * 0.80),
                int(height * 0.90),
            ],
            fill=(0, 0, 0),
        )

    def _draw_package(
        self,
        draw,
        width: int,
        height: int,
        fg,
        profile_index: int,
    ) -> None:
        cx, cy = width // 2, int(height * 0.44)
        pw = int(width * 0.44)
        ph = int(height * 0.52)
        left, top = cx - pw // 2, cy - ph // 2

        if profile_index == 2:  # side / back view: narrow depth
            left += int(pw * 0.30)
            pw = int(pw * 0.40)
        try:
            draw.rounded_rectangle(
                [(left, top), (left + pw, top + ph)],
                radius=int(width * 0.025),
                fill=(255, 255, 255),
                outline=fg,
                width=int(width * 0.006),
            )
        except TypeError:
            draw.rectangle(
                [(left, top), (left + pw, top + ph)],
                fill=(255, 255, 255),
                outline=fg,
            )
        if profile_index == 1:  # 45-degree: hint the front face with a band
            band_y = top + int(ph * 0.18)
            try:
                draw.rounded_rectangle(
                    [(left, band_y), (left + pw, band_y + int(ph * 0.10))],
                    radius=0,
                    fill=fg,
                )
            except TypeError:
                draw.rectangle(
                    [(left, band_y), (left + pw, band_y + int(ph * 0.10))],
                    fill=fg,
                )

    def _draw_label(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        item: Item,
        variant,
        fg,
        profile_index: int,
    ) -> None:
        cx = width // 2
        name = item.item_name.replace(" - ", " ").replace("-", " ")
        if profile_index == 2:
            return  # side/back view keeps the label minimal
        font_name = _font(int(width * 0.040))
        font_strength = _font(int(width * 0.052))
        draw.text((cx, int(height * 0.30)), name, fill=fg, anchor="mm", font=font_name)
        draw.text(
            (cx, int(height * 0.42)),
            f"{variant.strength}",
            fill=fg,
            anchor="mm",
            font=font_strength,
        )
        draw.text(
            (cx, int(height * 0.55)),
            item.brand,
            fill=(90, 90, 90),
            anchor="mm",
            font=_font(int(width * 0.032)),
        )


class ImageOptimizer:
    """
    Centralized WebP optimization for the phase 9.5 product images.

    Re-encodes an in-memory ``RGB`` image to a lossy WebP at the configured
    dimensions and quality. Because a fresh ``RGB`` image is always encoded,
    any source metadata is automatically stripped.
    """

    def __init__(self, config: AIImageConfig) -> None:
        self._config = config

    def optimize(self, image, target: Path) -> None:
        """Resize, re-encode to optimized WebP and write to ``target``."""
        from PIL import Image

        cfg = self._config
        resized = image.resize(
            (cfg.width, cfg.height),
            Image.Resampling.LANCZOS,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        resized.save(
            target,
            format="WEBP",
            quality=cfg.webp_quality,
            method=cfg.webp_method,
            lossless=False,
        )


class AIImageGenerator:
    """
    Generates, optimizes and mirrors the Phase 9.5 product-image gallery.

    Dependencies (prompt builder, image renderer and optimizer) are injected so
    the generator is testable in isolation and a real AI renderer can be
    substituted later.
    """

    def __init__(
        self,
        config: AIImageConfig,
        renderer: MedicineImageRenderer | None = None,
        optimizer: ImageOptimizer | None = None,
        prompt_builder: MedicinePromptBuilder | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._renderer = renderer or PillowMedicineImageRenderer(config)
        self._optimizer = optimizer or ImageOptimizer(config)
        self._prompt_builder = prompt_builder or MedicinePromptBuilder()
        self._logger = logger or get_logger(self.__class__.__name__)

    def run(self, items: Sequence[Item]) -> AIMageOutcome:
        """Generate the product gallery for every item and mirror the result."""
        outcome = AIMageOutcome()
        for item in items:
            try:
                variant = self._prompt_builder.variant_for(item)
            except (ValueError, IndexError, KeyError):
                self._logger.warning(
                    "Could not resolve variant for %s; skipping.", item.item_code
                )
                continue
            outcome.items += 1
            try:
                self._generate_item(item, variant, outcome)
            except Exception as exc:
                outcome.failed.append(f"{item.item_code}: {exc}")
                self._logger.error("Failed to generate images for %s: %s", item.item_code, exc)
        return outcome

    def _generate_item(self, item: Item, variant, outcome: AIMageOutcome) -> None:
        cfg = self._config
        size = (cfg.width, cfg.height)
        for profile in range(cfg.images_per_item):
            filename = f"{item.item_code}-{profile + 1}{cfg.extension}"
            canonical = cfg.item_images_output_dir / filename
            if canonical.is_file():
                outcome.images_skipped += 1
                self._logger.info("Skipped %s (already present)", filename)
                continue
            image = self._renderer.render(item, variant, profile, size)
            self._optimizer.optimize(image, canonical)
            outcome.images_generated += 1
            # Mirror the optimized copy into the ERPNext-served public directory.
            canonical_dir = cfg.item_images_output_dir.resolve()
            optimize_dir = cfg.optimize_output_dir.resolve() if isinstance(
                cfg.optimize_output_dir, Path
            ) else cfg.optimize_output_dir
            if optimize_dir != canonical_dir:
                mirror = cfg.optimize_output_dir / filename
                mirror.parent.mkdir(parents=True, exist_ok=True)
                mirror.write_bytes(canonical.read_bytes())
            outcome.images_optimized += 1
            self._logger.info("Generated %s", canonical)


def _item_index(item_code: str) -> int:
    """Return the 0-based medicine-catalog index for an item code like ``MED-001``."""
    suffix = item_code.rsplit("-", 1)[-1]
    return int(suffix) - 1


def _font(size: int):
    """Return a best-effort TrueType default font scaled to ``size``."""
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow without size support
        return ImageFont.load_default()


def build_ai_image_generator(
    config: AIImageConfig,
    logger: logging.Logger | None = None,
) -> AIImageGenerator:
    """Build a default Phase 9.5 product-image generator."""
    return AIImageGenerator(config=config, logger=logger)
