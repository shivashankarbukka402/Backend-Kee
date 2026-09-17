"""
Image Manifest Writer

Phase 9.5: writes the reusable product-gallery manifest at
``output/image_manifest.json``.

For every medicine item the manifest records the primary image (the first
gallery file, also assigned to the ERPNext Item through the existing
image-mapping pipeline) and the full ordered gallery of optimized product
images, so the React gallery and any future API integration can discover an
item's images without scanning the filesystem.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .config import IMAGE_MANIFEST_FILENAME, AIImageConfig
from .item_models import Item
from .logging_setup import get_logger


@dataclass
class ImageManifest:
    """
    In-memory representation of the product-gallery manifest.
    """

    images: list[dict] = field(default_factory=list)


@dataclass
class ManifestOutcome:
    """
    The result of a manifest write run.
    """

    entries: int = 0
    path: Path | None = None


class ImageManifestWriter:
    """
    Writes the product-gallery manifest for the supplied items.
    """

    def __init__(
        self,
        config: AIImageConfig,
        output_dir: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._output_dir = output_dir
        self._logger = logger or get_logger(self.__class__.__name__)

    def write(self, items: Sequence[Item], manifest: ImageManifest | None = None):
        """
        Write the manifest JSON and return the outcome.

        Existing manifest files are overwritten (the gallery is fully
        re-derivable and eagerly rewritten to stay in sync with the catalog).
        """
        manifest = manifest or ImageManifest()
        output: list[dict] = []
        per_item = self._config.images_per_item
        for item in items:
            gallery = [
                f"{item.item_code}-{index + 1}{self._config.extension}"
                for index in range(per_item)
            ]
            output.append(
                {
                    "item_code": item.item_code,
                    "primary_image": gallery[0],
                    "gallery": gallery,
                }
            )
        manifest.images = output

        path = self._output_dir / IMAGE_MANIFEST_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"images": manifest.images}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._logger.info("Wrote image manifest: %s (%d entries)", path, len(output))
        return ManifestOutcome(entries=len(output), path=path)


def build_image_manifest_writer(
    config: AIImageConfig,
    output_dir: Path,
    logger: logging.Logger | None = None,
) -> ImageManifestWriter:
    """Build a default image manifest writer."""
    return ImageManifestWriter(config=config, output_dir=output_dir, logger=logger)
