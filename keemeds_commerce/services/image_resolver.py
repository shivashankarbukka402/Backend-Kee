"""
Image Resolver

Builds public product-image URLs (primary + gallery) from the Phase 9.5
generated gallery.

The gallery file names (``MED-001-1.webp`` ... ``MED-001-3.webp``) are
deterministic, so this service appends them to the configured public URL prefix
and returns absolute public URLs via ``frappe.utils.get_url``. It never exposes
filesystem paths.
"""

from __future__ import annotations

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.product import ProductImages


class ImageResolver:
    """
    Resolves public image URLs for an item code.
    """

    def __init__(self, config: CommerceConfig) -> None:
        self._config = config

    def resolve(self, item_code: str) -> ProductImages:
        filenames = self._config.gallery_filenames(item_code)
        urls = [self._public_url(name) for name in filenames]
        if not urls:
            return ProductImages()
        return ProductImages(primary_image=urls[0], gallery=urls)

    def _public_url(self, filename: str) -> str:
        import frappe

        prefix = self._config.item_image_url_prefix.rstrip("/")
        relative = f"{prefix}/{filename}"
        try:
            return frappe.utils.get_url(relative)
        except Exception:
            return relative
