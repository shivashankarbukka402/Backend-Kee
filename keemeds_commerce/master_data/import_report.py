"""
Import Reporting

Models and writers for the Phase 6 ERPNext import framework.

The :class:`ImportReport` aggregates everything that happened during an import
run - timing, the number of imported/updated/skipped/failed rows, the files
that were processed and any validation findings - so the pipeline can report a
complete picture even when individual importers succeed or fail.

The report is deliberately free of Frappe or ERPNext dependencies, depending
only on the centralized :class:`~master_data.config.ReportConfig` /
:class:`~master_data.config.ImportConfig`. The writers emit a machine-readable
JSON summary and a human-readable log under ``master_data/output/reports/``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ExportConfig, ImportConfig, MasterDataConfig, ReportConfig

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
_DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class FileImportReport:
    """
    The outcome of importing a single source file through ERPNext.

    Attributes
    ----------
    path:
        Absolute path of the imported file.
    doctype:
        The ERPNext DocType the file was imported into.
    import_type:
        The import type used (``"insert"`` or ``"update"``).
    imported:
        Number of new records created.
    updated:
        Number of existing records updated.
    skipped:
        Number of rows accepted by the importer but not imported.
    failed:
        Number of rows rejected by ERPNext validation.
    errors:
        Human-readable validation messages for failed/skipped rows.
    """

    path: str
    doctype: str
    import_type: str
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ImportReport:
    """
    Mutable aggregator for an entire import pipeline run.

    Attributes
    ----------
    started_at:
        Start timestamp of the run.
    finished_at:
        End timestamp of the run (set on completion).
    files:
        Ordered per-file outcomes for every processed file.
    missing_files:
        Required source files that could not be found before execution.
    fatal_errors:
        Fatal failures that stopped the run (e.g. missing required file).
    notes:
        Free-text notes about recoverable events.
    """

    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    files: list[FileImportReport] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """
        Duration of the run in seconds, or ``0`` before completion.
        """
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def imported(self) -> int:
        """Total new records imported across all files."""
        return sum(file_report.imported for file_report in self.files)

    @property
    def updated(self) -> int:
        """Total existing records updated across all files."""
        return sum(file_report.updated for file_report in self.files)

    @property
    def skipped(self) -> int:
        """Total rows skipped across all files."""
        return sum(file_report.skipped for file_report in self.files)

    @property
    def failed(self) -> int:
        """Total rows failed across all files."""
        return sum(file_report.failed for file_report in self.files)

    @property
    def ok(self) -> bool:
        """
        ``True`` when nothing is missing, nothing is fatal and no row failed.

        Warnings and skipped rows alone do not make a run unsuccessful, but a
        missing required file or any fatal error does.
        """
        return not self.missing_files and not self.fatal_errors and self.failed == 0

    def record_file(self, file_report: FileImportReport) -> None:
        """Append a per-file outcome."""
        self.files.append(file_report)

    def add_missing_file(self, path: str) -> None:
        """Record a required file that could not be found before execution."""
        if path not in self.missing_files:
            self.missing_files.append(path)

    def add_fatal(self, message: str) -> None:
        """Record a fatal pipeline failure."""
        self.fatal_errors.append(message)

    def add_note(self, message: str) -> None:
        """Record a free-text note about a recoverable event."""
        self.notes.append(message)


class ImportReportWriter:
    """
    Writes an :class:`ImportReport` to a JSON summary and a log file.
    """

    def __init__(
        self,
        report_config: ReportConfig,
        import_config: ImportConfig,
        export_config: ExportConfig,
        logger: logging.Logger | None = None,
    ) -> None:
        self._report_config = report_config
        self._import_config = import_config
        self._export_config = export_config
        self._logger = logger or logging.getLogger("keemeds.master_data.import_report")

    def resolve_dir(self) -> Path:
        """Resolve the directory that receives the import report files."""
        target = self._export_config.output_dir / self._report_config.subdirectory
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write(self, report: ImportReport) -> tuple[Path, Path]:
        """
        Write the JSON summary and the log file.

        Returns the (json_path, log_path) pair.
        """
        target = self.resolve_dir()
        json_path = target / self._import_config.import_report_filename
        log_path = target / self._import_config.import_log_filename
        self._write_json(report, json_path)
        self._write_log(report, log_path)
        return json_path, log_path

    def _write_json(self, report: ImportReport, path: Path) -> None:
        payload = {
            "started_at": report.started_at.strftime(_TIMESTAMP_FORMAT),
            "finished_at": (
                report.finished_at.strftime(_TIMESTAMP_FORMAT)
                if report.finished_at
                else None
            ),
            "duration_seconds": round(report.duration_seconds, 3),
            "imported_records": report.imported,
            "updated_records": report.updated,
            "skipped_records": report.skipped,
            "failed_records": report.failed,
            "ok": report.ok,
            "missing_files": list(report.missing_files),
            "fatal_errors": list(report.fatal_errors),
            "notes": list(report.notes),
            "files": [
                {
                    "path": file_report.path,
                    "doctype": file_report.doctype,
                    "import_type": file_report.import_type,
                    "imported": file_report.imported,
                    "updated": file_report.updated,
                    "skipped": file_report.skipped,
                    "failed": file_report.failed,
                    "errors": list(file_report.errors),
                }
                for file_report in report.files
            ],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _write_log(self, report: ImportReport, path: Path) -> None:
        lines: list[str] = []
        lines.append("KeeMeds ERPNext Import Report")
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
        lines.append(f"Imported records: {report.imported}")
        lines.append(f"Updated records:  {report.updated}")
        lines.append(f"Skipped records:  {report.skipped}")
        lines.append(f"Failed records:   {report.failed}")
        lines.append("")
        if report.missing_files:
            lines.append("Missing files (fatal):")
            for missing in report.missing_files:
                lines.append(f"  - {missing}")
            lines.append("")
        if report.fatal_errors:
            lines.append("Fatal errors:")
            for error in report.fatal_errors:
                lines.append(f"  - {error}")
            lines.append("")
        lines.append("Imported files:")
        if report.files:
            for file_report in report.files:
                lines.append(
                    f"  - {file_report.path} "
                    f"[{file_report.import_type}] "
                    f"imported={file_report.imported} "
                    f"updated={file_report.updated} "
                    f"skipped={file_report.skipped} "
                    f"failed={file_report.failed}"
                )
        else:
            lines.append("  (none)")
        if report.notes:
            lines.append("")
            lines.append("Execution notes:")
            for note in report.notes:
                lines.append(f"  - {note}")
        lines.append("")
        lines.append("Report generated automatically by the Master Data import pipeline.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_import_report_writer(
    config: MasterDataConfig,
    logger: logging.Logger | None = None,
) -> ImportReportWriter:
    """
    Build a default import report writer from a master data configuration.
    """
    return ImportReportWriter(
        report_config=config.reports,
        import_config=config.import_config,
        export_config=config.export,
        logger=logger,
    )
