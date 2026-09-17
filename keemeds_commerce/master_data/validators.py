"""
Master Data Validation

Pure validation helpers used by :class:`~master_data.master_loader.MasterDataLoader`.

The validators here are free of I/O and side effects, operating only on the
already-parsed :class:`~master_data.models.FileContent`. This keeps them easy to
reason about and test independently.

Validation performed
-------------------
- Required source files exist
- Files are not empty (contain at least one data row)
- Required columns are present in the file header
- Values contain no duplicates
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .config import EntityConfig
from .models import FileContent


class IssueLevel(StrEnum):
    """
    Severity of a validation finding.
    """

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    """
    A single validation finding for one entity.
    """

    entity_key: str
    level: IssueLevel
    message: str


@dataclass
class ValidationReport:
    """
    Aggregate of all validation findings across every configured entity.
    """

    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, issue: ValidationIssue) -> None:
        """Record a finding."""

        self.issues.append(issue)

    @property
    def is_valid(self) -> bool:
        """
        ``True`` when there are no error-level findings.
        """

        return not any(
            issue.level is IssueLevel.ERROR for issue in self.issues
        )

    def errors_for(self, key: str) -> list[ValidationIssue]:
        """
        Return all error-level findings for a given entity key.
        """

        return [
            issue
            for issue in self.issues
            if issue.entity_key == key and issue.level is IssueLevel.ERROR
        ]


def issue(
    entity_key: str,
    level: IssueLevel,
    message: str,
) -> ValidationIssue:
    """Convenience factory for a validation finding."""

    return ValidationIssue(entity_key=entity_key, level=level, message=message)


def validate_file_exists(
    report: ValidationReport,
    entity: EntityConfig,
    path: Path,
) -> None:
    """
    Record an error if the entity's source file does not exist.
    """

    if not path.is_file():
        report.add(
            issue(
                entity.key,
                IssueLevel.ERROR,
                f"Required source file does not exist: {path}",
            )
        )


def validate_not_empty(
    report: ValidationReport,
    entity: EntityConfig,
    content: FileContent,
) -> None:
    """
    Record an error if the file has no data rows.
    """

    if not content.rows:
        report.add(
            issue(
                entity.key,
                IssueLevel.ERROR,
                f"Source file is empty (no data rows): {content.path}",
            )
        )


def validate_required_columns(
    report: ValidationReport,
    entity: EntityConfig,
    content: FileContent,
) -> None:
    """
    Record an error for each required column missing from the header.
    """

    present = {header.strip() for header in content.headers}
    missing = [column for column in entity.required_columns if column not in present]
    if missing:
        report.add(
            issue(
                entity.key,
                IssueLevel.ERROR,
                f"Missing required column(s) {missing} in {content.path}",
            )
        )


def validate_duplicates(
    report: ValidationReport,
    entity: EntityConfig,
    values: list[str],
) -> None:
    """
    Record warnings describing duplicated (non-normalized) values.
    """

    duplicates = _find_duplicates(values)
    for value in duplicates:
        report.add(
            issue(
                entity.key,
                IssueLevel.WARNING,
                f"Duplicate value in {entity.name}: {value!r}",
            )
        )


def _find_duplicates(values: list[str]) -> list[str]:
    """
    Return the distinct values that occur more than once, preserving order.
    """

    seen: set[str] = set()
    duplicates: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.add(value)
        seen.add(value)
    for value in values:
        if value in duplicates and value not in ordered:
            ordered.append(value)
    return ordered
