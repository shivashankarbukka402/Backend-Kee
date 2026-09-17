"""
Item Image Mapping Generator

Generates ERPNext Item Image Mapping records (Phase 4 catalog enrichment) for
the already-generated medicine items.

For every item it produces one image mapping whose ``Image Path`` always lives
under the configured ERPNext image URL root (``/files/item_images``). The
generator scans the local :mod:`master_data.item_images` directory for a
matching image file (matched by the item code slug). When no matching file is
present, it falls back to a deterministic placeholder path so the export stays
deterministic and complete.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from ..config import ImageMappingConfig
from ..enrichment_generator import BaseEnrichmentGenerator
from ..enrichment_models import ImageMapping
from ..item_models import Item

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class ImageMappingGenerator(BaseEnrichmentGenerator):
    """
    Generate one :class:`~master_data.enrichment_models.ImageMapping` record
    per item, referencing a deterministic image path under the image URL root.
    """

    key: str = "image_mapping"
    name: str = "Image Mapping"

    def __init__(
        self,
        config,
        items: Sequence[Item],
        logger=None,
    ) -> None:
        super().__init__(config=config, items=items, logger=logger)
        self._image_config: ImageMappingConfig = config.image_mapping
        self._available_files: set[str] = self._scan_image_files()

    def generate(self) -> list[ImageMapping]:
        """
        Generate the image mapping records.

        Returns
        -------
        list[ImageMapping]
            Ordered image mapping records, one per item.
        """
        image_config = self._image_config
        records: list[ImageMapping] = []

        for item in self.items:
            slug = self._slug(item.item_code)
            image_path = self._resolve_image_path(slug, image_config)
            records.append(ImageMapping(item_code=item.item_code, image_path=image_path))

        return records

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _scan_image_files(self) -> set[str]:
        """Scan the configured image directory for its available file names."""
        image_dir: Path = self._image_config.image_dir
        if not image_dir.is_dir():
            return set()
        extension = self._image_config.extension
        return {
            path.name
            for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() == extension
        }

    def _resolve_image_path(self, slug: str, config: ImageMappingConfig) -> str:
        """Resolve a deterministic image path for an item slug."""
        item_code = self._item_code_for(slug)
        primary = f"{item_code}-1{config.extension}"
        candidates = (primary, slug, slug + config.extension)
        filename = next(
            (name for name in candidates if name in self._available_files),
            slug + config.placeholder_extension,
        )
        return f"{config.image_url_root.rstrip('/')}/{filename}"

    @staticmethod
    def _item_code_for(slug: str) -> str:
        """Derive the item code from its URL slug (e.g. ``med-001`` -> ``MED-001``)."""
        parts = slug.split("-")
        if len(parts) == 2 and parts[1].isdigit():
            return f"{parts[0].upper()}-{parts[1]}"
        return slug

    @staticmethod
    def _slug(item_code: str) -> str:
        """
        Deterministic URL slug derived from an item code.

        Lower-cases the code, replaces any run of non-alphanumeric characters
        with a single hyphen and strips leading/trailing hyphens.
        """
        slug = _NON_ALNUM.sub("-", item_code.lower()).strip("-")
        return slug or "item"
