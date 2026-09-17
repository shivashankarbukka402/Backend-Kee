"""
Product Parameter Validators

Normalize and validate the raw query arguments for the product API, returning a
:class:`~keemeds_commerce.domain.product.ProductQuery` DTO.

Validation is pure logic (no database access) and lives here so the API
controllers stay thin and consistent.
"""

from __future__ import annotations

from typing import Any

from frappe import _

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.product import ProductQuery
from keemeds_commerce.utils.exceptions import raise_validation_error


def build_product_query(
    args: dict[str, Any],
    config: CommerceConfig,
) -> ProductQuery:
    """
    Validate and normalize listing parameters into a :class:`ProductQuery`.

    Raises a validation error for invalid page/size/sort input.
    """
    page = _positive_int(args.get("page"), "page", default=1)
    page_size = _positive_int(args.get("page_size"), "page_size", default=config.default_page_size)
    page_size = _clamp_page_size(page_size, config)

    sort = _validated_sort(args.get("sort"), config)
    in_stock = _to_bool(args.get("in_stock"))

    search = _clean_text(args.get("search"))
    item_group = _clean_text(args.get("item_group"))
    brand = _clean_text(args.get("brand"))
    manufacturer = _clean_text(args.get("manufacturer"))

    return ProductQuery(
        page=page,
        page_size=page_size,
        search=search,
        item_group=item_group,
        brand=brand,
        manufacturer=manufacturer,
        in_stock=in_stock,
        sort=sort,
    )


def _positive_int(value: Any, field: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise_validation_error(_("Invalid {0} '{1}'.").format(field, value))
    if parsed < 1:
        raise_validation_error(_("{0} must be a positive integer.").format(field.title()))
    return parsed


def _clamp_page_size(page_size: int, config: CommerceConfig) -> int:
    return min(page_size, config.max_page_size)


def _validated_sort(value: Any, config: CommerceConfig) -> str:
    if value in (None, ""):
        return config.default_sort
    candidate = str(value).strip().lower()
    if candidate not in config.sort_options:
        raise_validation_error(
            _("Invalid sort '{0}'. Allowed: {1}.").format(
                value, ", ".join(config.sort_options)
            )
        )
    return candidate


def _to_bool(value: Any) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
