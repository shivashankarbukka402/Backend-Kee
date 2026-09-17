"""
Placeholder Image Generator

Phase 7: generates one professional placeholder medicine image and then
produces every item image file required by the current catalog, without
consulting any external AI service or downloading anything.

Approach
--------
1. A single vector placeholder (``medicine-placeholder.svg``) is authored
   locally and written into the output directory.
2. When Pillow is available the placeholder is also rasterized into
   ``medicine-placeholder.webp`` and that becomes the source for the item
   files. When Pillow is unavailable the SVG remains the source.
3. The expected item image file names (``med-001.webp`` ... ``med-300.webp``)
   are read from the generated Item Image Mapping workbook, never hard-coded.
4. For each expected file the placeholder source is materialised as a soft link
   first and as a byte-for-byte copy only when symlinks are unsupported.
5. Files that already exist (real product images) are never touched.

The generator is registration-driven: the catalog workbook and the target
directory both come from centralized configuration, so a future OTC, Wellness,
Devices, Baby Care, Surgical or Accessories catalog requires no change here.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from .config import ExportConfig, ImageGeneratorConfig
from .logging_setup import get_logger

#: The authored, self-contained vector placeholder for the medicine catalog.
_MEDICINE_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#e8f6ef"/>
      <stop offset="1" stop-color="#cfe8fb"/>
    </linearGradient>
    <linearGradient id="cap" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2e8b6e"/>
      <stop offset="1" stop-color="#256b53"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="1024" height="1024" rx="120" fill="url(#bg)"/>
  <circle cx="512" cy="512" r="300" fill="#ffffff" fill-opacity="0.85"/>
  <!-- pill body -->
  <rect x="312" y="462" width="400" height="160" rx="80" fill="#ffffff" stroke="#c5d6e8" stroke-width="8"/>
  <!-- pill mid line -->
  <line x1="512" y1="462" x2="512" y2="622" stroke="#dbe6f2" stroke-width="8"/>
  <!-- capsule end cap -->
  <rect x="312" y="462" width="180" height="160" rx="80" fill="url(#cap)"/>
  <text x="512" y="760" text-anchor="middle" font-family="Arial, Helvetica, sans-serif"
        font-size="72" font-weight="bold" fill="#256b53">KeeMeds</text>
</svg>
"""


@dataclass
class ImageGenerationOutcome:
    """
    The result of a placeholder image generation run.
    """

    generated: int = 0
    skipped: int = 0
    copied: int = 0
    linked: int = 0
    placeholder: Path | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Total item image files produced (copied + linked)."""
        return self.copied + self.linked


class PlaceholderBuilder(ABC):
    """
    Produces the shared placeholder source file inside ``output_dir``.
    """

    @abstractmethod
    def build(self, output_dir: Path) -> Path:
        """
        Create the placeholder source and return its path.

        Returns the file (webp when rasterization is available, otherwise the
        SVG) that the per-item files will reference.
        """
        raise NotImplementedError


class MedicinePlaceholderBuilder(PlaceholderBuilder):
    """
    Writes the compiled-in SVG and, when Pillow is available, a rasterized webp.
    """

    def __init__(
        self,
        config: ImageGeneratorConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._logger = logger or get_logger(self.__class__.__name__)

    def build(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        svg_path = output_dir / self._config.svg_filename
        svg_path.write_text(_MEDICINE_SVG, encoding="utf-8")
        self._logger.info("Wrote placeholder SVG: %s", svg_path)

        webp = self._rasterize(output_dir)
        if webp is not None:
            return webp
        return svg_path

    def _rasterize(self, output_dir: Path) -> Path | None:
        """Rasterize the placeholder to webp via Pillow, or return ``None``."""
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self._logger.warning(
                "Pillow is unavailable; using the SVG as the image source."
            )
            return None

        config = self._config
        size = 1024
        image = Image.new("RGB", (size, size), (232, 246, 239))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [(0, 0), (size - 1, size - 1)], radius=120, fill=(207, 232, 251)
        )
        draw.rounded_rectangle(
            [(0, 0), (size - 1, size - 1)], radius=120, fill=None
        )
        draw.ellipse(
            [(412, 212), (612, 812)],
            fill=(255, 255, 255),
            outline=(197, 214, 232),
            width=8,
        )
        draw.rounded_rectangle(
            [(312, 462), (712, 622)], radius=80, fill=(255, 255, 255)
        )
        draw.rounded_rectangle(
            [(312, 462), (492, 622)], radius=80, fill=(46, 139, 110)
        )
        draw.text(
            (512, 760),
            "KeeMeds",
            fill=(37, 107, 83),
            anchor="mm",
            font=self._measure_font(draw, 72),
        )

        webp_path = output_dir / config.webp_filename
        image.save(webp_path, "WEBP")
        self._logger.info("Converted placeholder to webp: %s", webp_path)
        return webp_path

    @staticmethod
    def _measure_font(draw, size: int):
        """Return a best-effort default font scaled to ``size``."""
        try:
            from PIL import ImageFont

            return ImageFont.load_default(size=size)
        except TypeError:  # older Pillow without size support
            return ImageFont.load_default()


class ImageGenerator:
    """
    Creates the placeholder and every required item image file for the catalog.

    Dependencies (configuration and the placeholder builder) are injected so the
    generator is testable in isolation and reusable across future catalogs.
    """

    def __init__(
        self,
        config: ExportConfig,
        image_config: ImageGeneratorConfig,
        placeholder_builder: PlaceholderBuilder | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._image_config = image_config
        self._placeholder_builder = placeholder_builder or MedicinePlaceholderBuilder(
            image_config, logger=logger
        )
        self._logger = logger or get_logger(self.__class__.__name__)

    def run(
        self,
        output_dir: Path | None = None,
    ) -> ImageGenerationOutcome:
        """
        Generate the placeholder and all required item image files.
        """
        target = output_dir or self._image_config.output_dir
        target.mkdir(parents=True, exist_ok=True)

        source = self._placeholder_builder.build(target)
        expected = self._expected_filenames()
        outcome = ImageGenerationOutcome(placeholder=source)

        for filename in expected:
            path = target / filename
            if path.exists() or path.is_symlink():
                outcome.skipped += 1
                self._logger.info("Skipped %s (already present)", filename)
                continue
            created = self._materialise(source, path)
            if created == "link":
                outcome.linked += 1
            else:
                outcome.copied += 1
            self._logger.info("Created %s (%s)", filename, created)

        if source.suffix == self._image_config.extension:
            outcome.generated = 2  # SVG + rasterized webp placeholder
        else:
            outcome.generated = 1  # SVG placeholder only
        return outcome

    def _materialise(self, source: Path, target: Path) -> str:
        """Create ``target`` referencing ``source``; returns ``"link"`` or ``"copy"``."""
        try:
            target.symlink_to(source)
            return "link"
        except OSError:
            target.write_bytes(source.read_bytes())
            return "copy"

    def _expected_filenames(self) -> list[str]:
        """Read the expected item image file names from the catalog workbook."""
        workbook_path = (
            self._config.output_dir
            / self._image_config.catalog_subdirectory
            / self._image_config.catalog_filename
        )
        if not workbook_path.is_file():
            self._logger.warning(
                "Catalog workbook not found: %s; no item image files requested.",
                workbook_path,
            )
            return []

        names: list[str] = []
        try:
            workbook = load_workbook(workbook_path, read_only=True)
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            index = self._image_column_index(header)
            for values in rows:
                if values is None:
                    continue
                cells = list(values)
                if index is None or index >= len(cells):
                    continue
                image_path = cells[index]
                if not image_path:
                    continue
                names.append(str(image_path).rsplit("/", 1)[-1])
            workbook.close()
        except Exception as exc:
            self._logger.error("Failed to read catalog workbook %s: %s", workbook_path, exc)
            return []
        return self._dedupe(names)

    @staticmethod
    def _image_column_index(header) -> int | None:
        """Return the index of the ``Image Path`` column, or ``None``."""
        if not header:
            return None
        for index, value in enumerate(header):
            if value is not None and str(value).strip() == "Image Path":
                return index
        return None

    @staticmethod
    def _dedupe(names: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered


def build_image_generator(
    config: ExportConfig,
    image_config: ImageGeneratorConfig,
    logger: logging.Logger | None = None,
) -> ImageGenerator:
    """Build a default placeholder image generator."""
    return ImageGenerator(config=config, image_config=image_config, logger=logger)
