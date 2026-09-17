"""
Master Data Loader

The ``MasterDataLoader`` is the core orchestrator of the master data
foundation. It coordinates the centralized :class:`~master_data.config.MasterDataConfig`,
the registered :class:`~master_data.readers.FileReader` strategies and the pure
validation helpers to load, validate and normalize master data (Brands,
Manufacturers, Item Groups and UOMs).

Dependencies are injected through the constructor, making the loader easy to
test in isolation and leaving business logic outside the CLI layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import validators
from .config import MasterDataConfig
from .logging_setup import get_logger
from .models import FileContent, LoadedEntity, MasterDataResult
from .readers import ReaderRegistry
from .validators import IssueLevel, ValidationReport


@dataclass(frozen=True)
class NormalizationResult:
    """
    The output of normalizing a single column of raw values.
    """

    values: list[str]
    source_count: int
    duplicate_count: int
    blank_count: int
    raw_values: list[str]


class MasterDataLoader:
    """
    Loads, validates and normalizes master data source files.
    """

    def __init__(
        self,
        config: MasterDataConfig,
        readers: ReaderRegistry,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._readers = readers
        self._logger = logger or get_logger(self.__class__.__name__)

    def validate(self) -> ValidationReport:
        """
        Validate every configured source file without normalizing.

        Returns
        -------
        ValidationReport
            Aggregated findings across all entities.
        """

        report = ValidationReport()
        for entity in self._config.entities:
            path = self._config.source_file_for(entity)
            validators.validate_file_exists(report, entity, path)
            if not path.is_file():
                continue

            content = self._read_file(entity, path)
            if content is None:
                report.add(
                    validators.issue(
                        entity.key,
                        IssueLevel.ERROR,
                        f"Unable to read source file: {path}",
                    )
                )
                continue

            validators.validate_not_empty(report, entity, content)
            validators.validate_required_columns(report, entity, content)

            if content.rows:
                raw_values = self._extract_values(entity, content)
                validators.validate_duplicates(report, entity, raw_values)

        return report

    def load(self) -> MasterDataResult:
        """
        Run validation and return the normalized master data.

        Entities that fail validation with errors are excluded from the result
        and surfaced in :attr:`~master_data.models.MasterDataResult.errored`.
        """

        report = self.validate()
        result = MasterDataResult()

        for entity in self._config.entities:
            path = self._config.source_file_for(entity)
            if not path.is_file():
                result.errored.append(entity.key)
                self._logger.error("Skipping %s: file not found (%s)", entity.name, path)
                continue

            content = self._read_file(entity, path)
            if content is None or not content.rows:
                result.errored.append(entity.key)
                self._logger.error("Skipping %s: unreadable or empty file", entity.name)
                continue

            missing = self._missing_required_columns(entity, content)
            if missing:
                result.errored.append(entity.key)
                self._logger.error(
                    "Skipping %s: missing required columns %s", entity.name, missing
                )
                continue

            loaded = self._normalize_entity(entity, content, path)
            result.entities[entity.key] = loaded
            self._logger.info(
                "Loaded %s: %d value(s)",
                entity.name,
                len(loaded.values),
            )

        self._log_report(report)
        return result

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _read_file(self, entity, path: Path) -> FileContent | None:
        """
        Parse an entity's file using the reader registered for its format.
        """

        reader = self._readers.get(entity.file_type)
        try:
            return reader.read(path)
        except Exception as exc:
            self._logger.error("Failed to read %s: %s", path, exc)
            return None

    def _extract_values(self, entity, content: FileContent) -> list[str]:
        """
        Pull the raw string values for the entity's value column.
        """

        return [
            row.get(entity.value_column, "")
            for row in content.rows
            if entity.value_column in row
        ]

    def _missing_required_columns(self, entity, content: FileContent) -> list[str]:
        present = {header for header in content.headers}
        return [column for column in entity.required_columns if column not in present]

    def _normalize_entity(
        self,
        entity,
        content: FileContent,
        path: Path,
    ) -> LoadedEntity:
        """
        Extract and normalize (trim, de-duplicate, sort) an entity's values.
        """

        raw_values = self._extract_values(entity, content)
        normalized = normalize_values(raw_values)

        loaded = LoadedEntity(
            key=entity.key,
            name=entity.name,
            source_file=path,
            values=normalized.values,
            rows=[dict(row) for row in content.rows],
            source_count=normalized.source_count,
            duplicate_count=normalized.duplicate_count,
            blank_count=normalized.blank_count,
        )

        if normalized.blank_count:
            loaded.warnings.append(
                f"{normalized.blank_count} blank value(s) discarded from {entity.name}"
            )
        if normalized.duplicate_count:
            loaded.warnings.append(
                f"{normalized.duplicate_count} duplicate value(s) removed from {entity.name}"
            )
        return loaded

    def _log_report(self, report: ValidationReport) -> None:
        for finding in report.issues:
            text = f"[{finding.entity_key}] {finding.message}"
            if finding.level is IssueLevel.ERROR:
                self._logger.error(text)
            else:
                self._logger.warning(text)


def normalize_values(raw_values: list[str]) -> NormalizationResult:
    """
    Normalize a list of raw values.

    Steps
    -----
    1. Trim surrounding whitespace from every value.
    2. Discard blank values.
    3. Remove duplicates (case-sensitive) while preserving first occurrence.
    4. Sort the remaining values case-insensitively.
    """

    trimmed = [value.strip() for value in raw_values]
    non_blank = [value for value in trimmed if value]

    seen: set[str] = set()
    unique: list[str] = []
    duplicate_count = 0
    for value in non_blank:
        if value in seen:
            duplicate_count += 1
            continue
        seen.add(value)
        unique.append(value)

    return NormalizationResult(
        values=sorted(unique, key=str.casefold),
        source_count=len(non_blank),
        duplicate_count=duplicate_count,
        blank_count=len(raw_values) - len(non_blank),
        raw_values=list(raw_values),
    )
