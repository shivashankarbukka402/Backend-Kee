"""
Master Data Models

Dataclasses that represent the parsed and normalized outputs of the master
data foundation. They are deliberately free of any Frappe or ERPNext
dependency so the foundation stays a pure, testable Python layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileContent:
    """
    Parsed contents of a single master data source file.

    Attributes
    ----------
    path:
        Absolute path of the file that was parsed.
    headers:
        The column headers read from the first row.
    rows:
        Data rows, keyed by header name, with string values.
    """

    path: Path
    headers: list[str]
    rows: list[dict[str, str]]


@dataclass
class LoadedEntity:
    """
    The normalized result of loading a single master data entity.

    Attributes
    ----------
    key:
        Stable identifier of the entity (e.g. ``"brand"``).
    name:
        Human-readable entity name (e.g. ``"Brands"``).
    source_file:
        Absolute path of the source file it was loaded from.
    values:
        Normalized values: trimmed, de-duplicated and sorted.
    rows:
        The raw parsed data rows (keyed by header) read from the source file,
        before normalization. Useful for entities whose records carry more than
        a single value column (e.g. Item Attribute Values).
    source_count:
        Number of non-empty values read from the source before de-duplication.
    duplicate_count:
        Number of duplicate values that were removed during normalization.
    blank_count:
        Number of blank values that were discarded during normalization.
    warnings:
        Non-fatal messages raised while loading this entity.
    """

    key: str
    name: str
    source_file: Path
    values: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    source_count: int = 0
    duplicate_count: int = 0
    blank_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class MasterDataResult:
    """
    The normalized master data across all configured entities.

    Entities that could not be loaded successfully (missing file, empty file,
    missing required columns) are listed in :attr:`errored` and are absent
    from :attr:`entities`.
    """

    entities: dict[str, LoadedEntity] = field(default_factory=dict)
    errored: list[str] = field(default_factory=list)

    def values_for(self, key: str) -> list[str]:
        """
        Return the normalized values for an entity key (empty if absent).
        """

        entity = self.entities.get(key)
        return list(entity.values) if entity else []

    def rows_for(self, key: str) -> list[dict[str, str]]:
        """
        Return the raw parsed rows for an entity key (empty if absent).
        """

        entity = self.entities.get(key)
        return [dict(row) for row in entity.rows] if entity else []
