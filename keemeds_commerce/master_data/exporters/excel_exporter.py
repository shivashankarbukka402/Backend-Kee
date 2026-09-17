"""
ERPNext Item Import Excel Exporter

Exports generated :class:`~master_data.item_models.Item` records (from any
generator) into ``.xlsx`` files that match the ERPNext Item Import template.

The exporter is reusable across all generators: it consumes a
:class:`~master_data.item_models.GenerationResult` (batches of items) and writes
one file per batch, deriving the file names from each batch label. It performs
no ERPNext import and never touches the ERPNext database.

Configuration (column template, required fields, output path, styling) is
centralized in :class:`~master_data.config.ExportConfig` and injected through
the constructor.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from ..config import ExportConfig
from ..item_models import Item, ItemBatch
from ..logging_setup import get_logger

#: Matches the standardized batch label, e.g. ``Medicines 001-100``.
_BATCH_LABEL_RE = re.compile(r"^(?P<name>.+) (?P<start>\d{3})-(?P<end>\d{3})$")


@dataclass(frozen=True)
class ValidationError:
    """
    A single record rejected during export validation.

    Attributes
    ----------
    item_code:
        Code of the rejected item.
    reason:
        Why the item was skipped.
    """

    item_code: str
    reason: str


@dataclass(frozen=True)
class ExportReport:
    """
    The outcome of an export run.

    Attributes
    ----------
    total_generated:
        Total number of records supplied to the exporter.
    exported:
        Number of records actually written to the files.
    skipped:
        Number of records rejected by validation.
    files_generated:
        Paths of the files that were produced.
    errors:
        Validation errors for the skipped records.
    """

    total_generated: int = 0
    exported: int = 0
    skipped: int = 0
    files_generated: tuple[Path, ...] = field(default_factory=tuple)
    errors: tuple[ValidationError, ...] = field(default_factory=tuple)


class ExcelExporter:
    """
    Writes ERPNext Item Import ``.xlsx`` files from generated item batches.
    """

    def __init__(
        self,
        config: ExportConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._logger = logger or get_logger(self.__class__.__name__)

    def export(
        self,
        batches: tuple[ItemBatch, ...] | list[ItemBatch],
        *,
        output_dir: Path | None = None,
    ) -> ExportReport:
        """
        Export the given batches into one file per batch.

        Batches may come from a :class:`~master_data.item_models.GenerationResult`
        (its ``batches`` attribute) or be supplied directly.
        """

        target_dir = (output_dir or self._config.output_dir) / self._config.subdirectory
        target_dir.mkdir(parents=True, exist_ok=True)

        files: list[Path] = []
        errors: list[ValidationError] = []
        total = 0
        exported = 0

        for sequence, batch in enumerate(batches, start=1):
            valid_items = self._validated_items(batch.items, errors)
            total += len(batch.items)
            exported += len(valid_items)

            path = self._write_workbook(batch, sequence, valid_items, target_dir)
            files.append(path)
            self._logger.info("Exported %d item(s) to %s", len(valid_items), path)

        return ExportReport(
            total_generated=total,
            exported=exported,
            skipped=total - exported,
            files_generated=tuple(files),
            errors=tuple(errors),
        )

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def _validated_items(
        self,
        items: tuple[Item, ...],
        errors: list[ValidationError],
    ) -> list[Item]:
        """
        Keep only the items whose required attributes are all non-blank.
        """

        valid: list[Item] = []
        for item in items:
            missing = self._missing_required(item)
            if missing:
                reasons = ", ".join(missing)
                errors.append(
                    ValidationError(item_code=item.item_code, reason=reasons)
                )
                self._logger.warning(
                    "Skipping %s: missing required field(s) %s",
                    item.item_code,
                    reasons,
                )
                continue
            valid.append(item)
        return valid

    def _missing_required(self, item: Item) -> list[str]:
        """
        Return the required attribute names that are blank on the item.
        """

        return [
            attribute
            for attribute in self._config.required_attributes
            if not str(getattr(item, attribute, "")).strip()
        ]

    # ------------------------------------------------------------------ #
    # Workbook generation
    # ------------------------------------------------------------------ #

    def _write_workbook(
        self,
        batch: ItemBatch,
        sequence: int,
        items: list[Item],
        target_dir: Path,
    ) -> Path:
        """
        Write one workbook and return its path.
        """

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = self._config.sheet_title

        self._write_header(sheet)
        self._write_rows(sheet, items)

        if self._config.auto_size_columns:
            self._auto_size(sheet)

        if self._config.freeze_header_row:
            sheet.freeze_panes = "A2"

        path = target_dir / self._filename(batch, sequence)
        workbook.save(path)
        workbook.close()
        return path

    def _write_header(self, sheet) -> None:
        """
        Write the ERPNext column headers into the first row.
        """

        for column_index, column in enumerate(self._config.columns, start=1):
            cell = sheet.cell(row=1, column=column_index, value=column.name)
            if self._config.bold_header:
                cell.font = Font(bold=True)

    def _write_rows(self, sheet, items: list[Item]) -> None:
        """
        Write each item as one data row below the header.
        """

        for row_index, item in enumerate(items, start=2):
            for column_index, column in enumerate(self._config.columns, start=1):
                sheet.cell(
                    row=row_index,
                    column=column_index,
                    value=self._value(item, column.attribute),
                )

    def _value(self, item: Item, attribute: str) -> object:
        """
        Serialize an item attribute for the spreadsheet.

        Booleans become the ``"1"``/``"0"`` strings expected by ERPNext import,
        integers stay numeric, everything else is written as text.
        """

        value = getattr(item, attribute, None)
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return value
        return str(value) if value is not None else ""

    def _auto_size(self, sheet) -> None:
        """
        Resize each column to fit its widest cell, with a sensible maximum.
        """

        for column_index, column in enumerate(self._config.columns, start=1):
            widths = [len(column.name)]
            letter = get_column_letter(column_index)
            for cell in sheet[letter][1:]:
                if cell.value is not None:
                    widths.append(len(str(cell.value)))
            width = min(max(widths) + 2, 80)
            sheet.column_dimensions[letter].width = width

    def _filename(self, batch: ItemBatch, sequence: int) -> str:
        """
        Derive the ERPNext-compatible file name for a batch.

        A batch labelled ``Medicines 001-100`` yields
        ``01_Item_Master_Medicines_001_100.xlsx``.
        """

        name, start, end = self._parse_batch_label(batch)
        slug = "_".join(name.split())
        return (
            f"{sequence:02d}_{self._config.filename_prefix}_{slug}"
            f"_{start}_{end}{self._config.file_extension}"
        )

    def _parse_batch_label(self, batch: ItemBatch) -> tuple[str, str, str]:
        """
        Split a batch label into its display name and zero-padded range.
        """

        match = _BATCH_LABEL_RE.match(batch.label)
        if match:
            name = match.group("name").strip()
            start = match.group("start")
            end = match.group("end")
        else:
            name = batch.label or "Items"
            start = "000"
            end = f"{len(batch.items):03d}"
        return name, start, end


def build_exporter(config: ExportConfig, logger: logging.Logger | None = None) -> ExcelExporter:
    """
    Build a default Excel exporter from an export configuration.
    """

    return ExcelExporter(config=config, logger=logger)
