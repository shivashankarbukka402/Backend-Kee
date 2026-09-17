"""
Execution Reporting

Collects and writes the Phase 5 execution reports for the one-command master
data pipeline. The pipeline feeds this module an aggregate of everything that
happened during a run - timing, records generated/exported/skipped, the files
that were produced and the validation findings - and the module turns those
into three outputs under ``master_data/output/reports/``:

- ``generation_summary.json``  - machine-readable summary (JSON)
- ``generation_summary.xlsx``  - spreadsheet summary (openpyxl)
- ``generation_report.log``    - human-readable execution log

The report model is deliberately free of Frappe or ERPNext dependencies and
depends only on the centralized :class:`~master_data.config.ReportConfig`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .config import ExportConfig, MasterDataConfig, ReportConfig
from .exporters.excel_exporter import ExportReport
from .logging_setup import get_logger
from .validators import IssueLevel, ValidationReport

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
_DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class GenerationReport:
    """
    Mutable aggregator that records everything about a pipeline run.

    Attributes
    ----------
    started_at:
        Start timestamp of the run.
    finished_at:
        End timestamp of the run (set when the run completes).
    records_generated:
        Total generated records across every source (items + enrichment).
    records_exported:
        Total records actually written by the exporters.
    records_skipped:
        Total records rejected during export validation.
    files_generated:
        Paths of every file produced during the run.
    validation_warnings:
        Warning-level validation findings.
    validation_errors:
        Error-level validation findings.
    notes:
        Free-text execution notes (recoverable errors and warnings).
    """

    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    records_generated: int = 0
    records_exported: int = 0
    records_skipped: int = 0
    files_generated: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ai_prompts_generated: int = 0
    ai_images_generated: int = 0
    ai_images_optimized: int = 0
    ai_images_skipped: int = 0
    ai_image_failures: list[str] = field(default_factory=list)
    ai_manifest_entries: int = 0

    @property
    def duration_seconds(self) -> float:
        """
        Duration of the run in seconds, or ``0`` before completion.
        """
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    def record_export(self, report: ExportReport) -> None:
        """
        Fold one export report into the aggregate counts.
        """
        self.records_generated += report.total_generated
        self.records_exported += report.exported
        self.records_skipped += report.skipped
        self.files_generated.extend(str(path) for path in report.files_generated)

    def add_note(self, message: str) -> None:
        """Record a free-text note about a recoverable event."""
        self.notes.append(message)


class ReportWriter:
    """
    Writes a :class:`GenerationReport` to JSON, a spreadsheet and a log file.
    """

    def __init__(
        self,
        config: ReportConfig,
        export_config: ExportConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._export_config = export_config
        self._logger = logger or get_logger(self.__class__.__name__)

    def resolve_dir(self, output_dir: Path | None = None) -> Path:
        """
        Resolve the directory that receives the report files.
        """
        base = output_dir or self._export_config.output_dir
        target = base / self._config.subdirectory
        target.mkdir(parents=True, exist_ok=True)
        return target

    def file_paths(self, output_dir: Path | None = None) -> tuple[Path, Path, Path]:
        """
        Resolve (without writing) the JSON, spreadsheet and log file paths.

        The three paths are returned in that order.
        """
        target = self.resolve_dir(output_dir)
        return (
            target / self._config.summary_json_filename,
            target / self._config.summary_xlsx_filename,
            target / self._config.report_log_filename,
        )

    def write_all(
        self,
        report: GenerationReport,
        output_dir: Path | None = None,
    ) -> tuple[Path, Path, Path]:
        """
        Write the JSON, spreadsheet and log report files.

        Returns the paths of the three generated files in that order.
        """
        json_path, xlsx_path, log_path = self.file_paths(output_dir)
        self._write_json(report, json_path)
        self._write_xlsx(report, xlsx_path)
        self._write_log(report, log_path)
        return json_path, xlsx_path, log_path

    # ------------------------------------------------------------------ #
    # JSON
    # ------------------------------------------------------------------ #

    def _write_json(self, report: GenerationReport, path: Path) -> None:
        payload = {
            "started_at": report.started_at.strftime(_TIMESTAMP_FORMAT),
            "finished_at": (
                report.finished_at.strftime(_TIMESTAMP_FORMAT)
                if report.finished_at
                else None
            ),
            "duration_seconds": round(report.duration_seconds, 3),
            "generated_records": report.records_generated,
            "exported_records": report.records_exported,
            "skipped_records": report.records_skipped,
            "generated_files": list(report.files_generated),
            "validation_warnings": list(report.validation_warnings),
            "validation_errors": list(report.validation_errors),
            "notes": list(report.notes),
            "ai_prompts_generated": report.ai_prompts_generated,
            "ai_images_generated": report.ai_images_generated,
            "ai_images_optimized": report.ai_images_optimized,
            "ai_images_skipped": report.ai_images_skipped,
            "ai_image_failures": list(report.ai_image_failures),
            "ai_manifest_entries": report.ai_manifest_entries,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Spreadsheet
    # ------------------------------------------------------------------ #

    def _write_xlsx(self, report: GenerationReport, path: Path) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Generation Summary"
        rows = [
            ("Started At", (report.started_at.strftime(_DISPLAY_FORMAT),)),
            ("Finished At", (
                (report.finished_at.strftime(_DISPLAY_FORMAT) if report.finished_at else ""),
            )),
            ("Duration (s)", (str(round(report.duration_seconds, 3)),)),
            ("Generated Records", (str(report.records_generated),)),
            ("Exported Records", (str(report.records_exported),)),
            ("Skipped Records", (str(report.records_skipped),)),
        ]
        for row_index, (label, (value,)) in enumerate(rows, start=1):
            key_cell = sheet.cell(row=row_index, column=1, value=label)
            key_cell.font = Font(bold=True)
            sheet.cell(row=row_index, column=2, value=value)

        files_header = len(rows) + 2
        sheet.cell(row=files_header, column=1, value="Generated Files").font = Font(bold=True)
        for offset, file_path in enumerate(report.files_generated, start=1):
            sheet.cell(row=files_header + offset, column=1, value=file_path)

        self._autofit(sheet, [1, 2])
        workbook.save(path)
        workbook.close()

    def _autofit(self, sheet, columns: list[int]) -> None:
        """Size each given column to its widest cell, capped at a maximum."""
        for column in columns:
            letter = get_column_letter(column)
            widths = [0]
            for cell in sheet[letter]:
                if cell.value is not None:
                    widths.append(len(str(cell.value)))
            sheet.column_dimensions[letter].width = min(max(widths) + 2, 100)

    # ------------------------------------------------------------------ #
    # Human-readable log
    # ------------------------------------------------------------------ #

    def _write_log(self, report: GenerationReport, path: Path) -> None:
        lines: list[str] = []
        lines.append("KeeMeds Master Data Generation Report")
        lines.append("=" * 40)
        lines.append(f"Started at:   {report.started_at.strftime(_DISPLAY_FORMAT)}")
        finished = (
            report.finished_at.strftime(_DISPLAY_FORMAT)
            if report.finished_at
            else "not finished"
        )
        lines.append(f"Finished at:  {finished}")
        lines.append(f"Duration (s): {report.duration_seconds:.3f}")
        lines.append("")
        lines.append(f"Generated records: {report.records_generated}")
        lines.append(f"Exported records:  {report.records_exported}")
        lines.append(f"Skipped records:   {report.records_skipped}")
        lines.append("")
        lines.append("Generated files:")
        for file_path in report.files_generated:
            lines.append(f"  - {file_path}")
        lines.append("")
        lines.append("Validation warnings:")
        for warning in report.validation_warnings or ["  (none)"]:
            lines.append(f"  - {warning}")
        lines.append("")
        lines.append("Validation errors:")
        for error in report.validation_errors or ["  (none)"]:
            lines.append(f"  - {error}")
        lines.append("")
        lines.append("Phase 9.5 AI product images:")
        lines.append(f"  Prompts generated:  {report.ai_prompts_generated}")
        lines.append(f"  Images generated:   {report.ai_images_generated}")
        lines.append(f"  Images optimized:   {report.ai_images_optimized}")
        lines.append(f"  Images skipped:     {report.ai_images_skipped}")
        lines.append(f"  Manifest entries:   {report.ai_manifest_entries}")
        if report.ai_image_failures:
            lines.append("  Failures:")
            for failure in report.ai_image_failures:
                lines.append(f"    - {failure}")
        lines.append("")
        lines.append("Execution notes:")
        for note in report.notes or ["  (none)"]:
            lines.append(f"  - {note}")
        lines.append("")
        lines.append("Report generated automatically by the Master Data pipeline.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def collect_validation_issues(
        report: GenerationReport,
        validation: ValidationReport,
    ) -> None:
        """
        Populate a report with the warning/error findings from validation.
        """
        for issue in validation.issues:
            text = f"{issue.entity_key}: {issue.message}"
            if issue.level is IssueLevel.ERROR:
                report.validation_errors.append(text)
            else:
                report.validation_warnings.append(text)


def build_report_writer(
    config: MasterDataConfig,
    logger: logging.Logger | None = None,
) -> ReportWriter:
    """
    Build a default report writer from a master data configuration.
    """
    return ReportWriter(
        config=config.reports,
        export_config=config.export,
        logger=logger,
    )
