"""
Master Data Export Models

Dataclasses that represent the ERPNext master records produced by the Phase 7
master generator (Brand, Manufacturer, Item Group, UOM, Item Attribute and
Item Attribute Value).

They are deliberately independent of any ERPNext DocType and of the generation
logic, mirroring :mod:`~master_data.item_models` and
:mod:`~master_data.enrichment_models`. The attribute names on each record match
the ``attribute`` of the centralized :class:`~master_data.config.ExportColumn`
definitions, so the reusable :class:`~master_data.exporters.record_exporter.RecordExcelExporter`
can serialize them without modification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    """
    A single ERPNext ``Brand`` record.

    Attributes
    ----------
    name:
        The brand name (the ``Brand Name`` field, which also names the record).
    """

    name: str


@dataclass(frozen=True)
class Manufacturer:
    """
    A single ERPNext ``Manufacturer`` record.

    Attributes
    ----------
    short_name:
        The manufacturer short name (the record name / autoname field).
    full_name:
        The manufacturer full/legal name.
    """

    short_name: str
    full_name: str


@dataclass(frozen=True)
class ItemGroup:
    """
    A single ERPNext ``Item Group`` record.

    Attributes
    ----------
    name:
        The item group name (the record name / autoname field).
    parent_item_group:
        The parent item group this group nests under.
    is_group:
        Whether this group can contain child groups.
    """

    name: str
    parent_item_group: str
    is_group: str


@dataclass(frozen=True)
class UOM:
    """
    A single ERPNext ``UOM`` record.

    Attributes
    ----------
    name:
        The unit of measure name (the record name / autoname field).
    """

    name: str


@dataclass(frozen=True)
class ItemAttribute:
    """
    A single ERPNext ``Item Attribute`` record (parent only).

    Attributes
    ----------
    name:
        The attribute name (the record name / autoname field).
    """

    name: str


@dataclass(frozen=True)
class ItemAttributeValue:
    """
    A single Item Attribute Value row, nested under a parent Item Attribute.

    Attributes
    ----------
    attribute:
        The parent attribute the value belongs to.
    attribute_value:
        The attribute value (``Attribute Value`` child field).
    abbr:
        The value abbreviation (``Abbreviation`` child field).
    """

    attribute: str
    attribute_value: str
    abbr: str
