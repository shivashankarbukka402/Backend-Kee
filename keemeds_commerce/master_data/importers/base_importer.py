"""
Base Importer

The reusable base class for every Phase 6 ERPNext importer. Each importer wraps
the standard Frappe Data Import API so generated catalog files can be imported
into ERPNext without bypassing ERPNext validation.

Responsibilities
----------------
- Resolve the generated source file(s) from the centralized configuration.
- Pre-flight validation: detect missing files and validate required columns
  *before* any import is attempted.
- Delegate the actual import to an injectable :class:`ImportExecutor` that in
  production wraps ``frappe.core.doctype.data_import.data_import.import_file``
  (the standard Frappe Data Import API).
- Aggregate the per-file outcome into a
  :class:`~master_data.import_report.FileImportReport`.

The base class is intentionally Frappe-free: it only talks to the injected
executor, so the import logic stays testable and the orchestration layers never
import Frappe directly.

Future importers (OTC, Wellness, Personal Care, Baby Care, Devices, Surgical,
Accessories) subclass this class and only declare their target DocType, import
type and source files; they need no orchestration change.
"""

from __future__ import annotations

import glob
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from ..config import ExportConfig, ImportConfig, ImporterConfig
from ..import_report import FileImportReport


@dataclass(frozen=True)
class ImportOutcome:
    """
    The raw result of importing a single file through the executor.

    Attributes
    ----------
    imported:
        Number of new records created.
    updated:
        Number of existing records updated.
    skipped:
        Number of rows accepted but not imported.
    failed:
        Number of rows rejected by ERPNext validation.
    errors:
        Human-readable validation error messages, if any.
    """

    imported: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: tuple[str, ...] = ()


class ImportExecutor(ABC):
    """
    Injectable backend that performs the actual ERPNext import.

    In production :class:`FrappeImportExecutor` delegates to the standard
    Frappe Data Import API. Injecting a stub enables testing the base importer
    and the pipeline outside a live ERPNext site.
    """

    @abstractmethod
    def import_file(
        self,
        file_path: Path,
        *,
        doctype: str,
        import_type: str,
        submit_after_import: bool = False,
    ) -> ImportOutcome:
        """
        Import a single source file and return its outcome.
        """
        raise NotImplementedError


class FrappeImportExecutor(ImportExecutor):
    """
    Imports files through the standard Frappe Data Import API.

    In production this walks the documented Data Import flow - create and save
    a ``Data Import`` document, then run its standard ``Importer`` - and reads
    the resulting row outcomes back from the persisted ``Data Import Log``, so
    ERPNext validation is never bypassed and the returned counts are real.

    When Frappe is not available (no active site/database, or the module is not
    importable) the file is reported as validated-but-not-imported: zero counts
    plus a clear note. No success is ever fabricated.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("keemeds.master_data.import")

    def import_file(
        self,
        file_path: Path,
        *,
        doctype: str,
        import_type: str,
        submit_after_import: bool = False,
    ) -> ImportOutcome:
        try:
            frappe = self._import_frappe()
        except _FrappeUnavailableError as exc:
            self._logger.warning("Frappe unavailable; not importing %s", file_path)
            return ImportOutcome(errors=(str(exc),))

        return self._run_standard_import(
            frappe=frappe,
            file_path=file_path,
            doctype=doctype,
            import_type=import_type,
            submit_after_import=submit_after_import,
        )

    def _import_frappe(self):
        """Import the ``frappe`` module, or raise a clear unavailable error."""
        try:
            import frappe
        except ImportError as exc:
            raise _FrappeUnavailableError(
                f"Frappe is not available in this environment: {exc}"
            ) from exc
        if not getattr(frappe, "db", None):
            raise _FrappeUnavailableError("No active Frappe site/database connection.")
        return frappe

    def _run_standard_import(
        self,
        *,
        frappe,
        file_path: Path,
        doctype: str,
        import_type: str,
        submit_after_import: bool,
    ) -> ImportOutcome:
        """Run the standard Data Import flow and read back real row counts."""
        import_type = _normalize_import_type(import_type)

        # A header-only template (no data rows) cannot be imported - ERPNext's
        # Data Import validate() rejects it with "Header and atleast one row".
        # This is the normal, graceful state on an idempotent re-run after
        # reconciliation has already kept every existing row. Short-circuit as a
        # clean no-op (imported=0, failed=0) instead of raising and flagging the
        # importer as failed, so the report records an explicit "nothing to
        # import" file entry rather than an error.
        if _count_data_rows(file_path) == 0:
            return ImportOutcome(imported=0, updated=0, skipped=0, failed=0)

        # Upload the generated workbook into Frappe's File manager (the same
        # step the ERPNext Data Import UI performs) so the Importer can read it.
        # A local path cannot be consumed directly by the Importer because it
        # reads templates through the File DocType.
        try:
            file_url = _upload_to_file_doc(frappe, file_path)
        except _FrappeUnavailableError as exc:
            return ImportOutcome(errors=(str(exc),))

        data_import = frappe.new_doc("Data Import")
        data_import.reference_doctype = doctype
        data_import.import_file = file_url
        data_import.import_type = import_type
        data_import.submit_after_import = submit_after_import
        data_import.insert(ignore_permissions=True)
        frappe.db.commit()

        try:
            from frappe.core.doctype.data_import.data_import import DataImport
            from frappe.core.doctype.data_import.importer import Importer

            data_import = frappe.get_doc("Data Import", data_import.name)
            # The Importer reads the uploaded file through the File DocType and
            # applies full ERPNext validation; console is off so template
            # warnings are persisted on the Data Import document.
            data_import.set_payload_count(Importer(doctype, data_import=data_import))
            importer = Importer(doctype, data_import=data_import)
            importer.import_data()
        except Exception as exc:
            frappe.db.rollback()
            return ImportOutcome(
                failed=0,
                errors=(f"Data Import raised: {exc}",),
            )

        logs = frappe.get_all(
            "Data Import Log",
            filters={"data_import": data_import.name},
            fields=["success", "row_indexes"],
        )
        imported = updated = failed = 0
        for log in logs:
            if not log.get("success"):
                failed += 1
            elif import_type.startswith("Update"):
                updated += 1
            else:
                imported += 1
        return ImportOutcome(
            imported=imported,
            updated=updated,
            skipped=0,
            failed=failed,
        )


class _FrappeUnavailableError(RuntimeError):
    """Raised when the import cannot run because Frappe is unavailable."""


def _normalize_import_type(import_type: str) -> str:
    """Normalize ``"insert"``/``"update"`` to ERPNext's Data Import wording."""
    lowered = import_type.lower()
    if lowered == "insert":
        return "Insert New Records"
    if lowered == "update":
        return "Update Existing Records"
    return import_type


def _count_data_rows(file_path: Path) -> int:
    """Return the number of data rows (excluding the header) in a workbook."""
    workbook = load_workbook(file_path, read_only=True)
    try:
        sheet = workbook.active
        count = 0
        for i, _row in enumerate(sheet.iter_rows(values_only=True)):
            if i == 0:
                continue
            count += 1
            if count > 0:
                break
        return count
    finally:
        workbook.close()


def _upload_to_file_doc(frappe, file_path: Path) -> str:
    """
    Upload a local template file into Frappe's File manager.

    This mirrors the standard ERPNext Data Import UI: the template is first
    attached as a File document, then the Data Import references its URL. The
    Importer reads the file through the File DocType, so validation is applied
    exactly as it is in the UI.

    Returns the public file URL to assign to ``Data Import.import_file``.

    Raises
    ------
    _FrappeUnavailableError:
        When the file cannot be read or uploaded.
    """
    from datetime import datetime

    try:
        content = file_path.read_bytes()
    except OSError as exc:
        raise _FrappeUnavailableError(
            f"Could not read import file {file_path}: {exc}"
        ) from exc

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_doc = frappe.new_doc("File")
    file_doc.file_name = f"{file_path.stem}_{stamp}{file_path.suffix}"
    file_doc.is_private = 0
    file_doc.content = content
    file_doc.save(ignore_permissions=True)
    return file_doc.file_url


class BaseImporter:
    """
    Reusable base class for all ERPNext importers.

    Subclasses declare their target DocType, import type, and source file(s)
    through an :class:`~master_data.config.ImporterConfig`. The base class
    resolves the files, validates them and delegates import to the injected
    executor.
    """

    def __init__(
        self,
        config: ImporterConfig,
        export_config: ExportConfig,
        import_config: ImportConfig,
        executor: ImportExecutor,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._export_config = export_config
        self._import_config = import_config
        self._executor = executor
        self._logger = logger or logging.getLogger(
            f"keemeds.master_data.import.{config.key}"
        )

    @property
    def key(self) -> str:
        """The stable importer identifier."""
        return self._config.key

    @property
    def name(self) -> str:
        """The human-readable importer name."""
        return self._config.name

    @property
    def doctype(self) -> str:
        """The ERPNext DocType imported into."""
        return self._config.doctype

    def resolve_files(self) -> list[Path]:
        """
        Resolve the generated source file(s) from configuration.

        A non-glob name matches a single file; a glob pattern matches every
        matching file under the configured subdirectory, sorted for a
        deterministic order.
        """
        base_dir = self._export_config.output_dir / self._config.subdirectory
        files: list[Path] = []
        for name in self._config.filenames:
            matches = glob.glob(str(base_dir / name))
            files.extend(sorted(Path(match) for match in matches))
        return files

    def missing_files(self) -> list[Path]:
        """
        Return the configured source files that are absent/cannot be found.

        A non-glob filename is missing when its target file does not exist. A
        glob pattern is missing when it matches no file at all. Missing entries
        point at the configured subdirectory-scoped reference so they are
        stable regardless of resolved batch names.
        """
        missing: list[Path] = []
        base_dir = self._export_config.output_dir / self._config.subdirectory
        for name in self._config.filenames:
            matches = glob.glob(str(base_dir / name))
            if matches:
                missing.extend(Path(m) for m in matches if not Path(m).is_file())
            else:
                missing.append(base_dir / name)
        return missing

    def validate_files(self) -> list[str]:
        """
        Validate the resolved source file(s); return error messages.

        A file that cannot be found, or that is missing a required column, is
        reported as a validation error.
        """
        errors: list[str] = []
        files = self.resolve_files()
        if not files:
            errors.append(f"{self.name}: no source files found for {self._config.subdirectory}")
            return errors
        for path in files:
            errors.extend(self._validate_file(path))
        return errors

    def _validate_file(self, path: Path) -> list[str]:
        """Validate a single file's presence and required column headers."""
        errors: list[str] = []
        if not path.is_file():
            errors.append(f"{self.name}: required file does not exist: {path}")
            return errors
        missing_columns = self._missing_columns(path)
        if missing_columns:
            errors.append(
                f"{self.name}: missing required column(s) {missing_columns} in {path}"
            )
        return errors

    def _missing_columns(self, path: Path) -> list[str]:
        """Return the required columns that are absent from a file's header."""
        if path.suffix.lower() not in (".xlsx", ".xls"):
            return list(self._config.required_columns)
        try:
            workbook = load_workbook(path, read_only=True)
        except Exception as exc:
            return [f"<unreadable: {exc}>"]
        sheet = workbook.active
        headers = []
        for row in sheet.iter_rows(max_row=1, values_only=True):
            headers = [str(cell).strip() if cell is not None else "" for cell in row]
            break
        workbook.close()
        present = {header for header in headers}
        return [column for column in self._config.required_columns if column not in present]

    def import_all(self, report) -> None:
        """
        Import every resolved source file, aggregating outcomes into ``report``.

        Missing files and validation errors are recorded rather than raised so
        the pipeline can decide whether to continue.
        """
        validation_errors = self.validate_files()
        if validation_errors:
            for error in validation_errors:
                report.add_note(error)
            self._logger.warning("%s: %s", self.name, "; ".join(validation_errors))
            for path in self.missing_files():
                report.add_missing_file(str(path))
            return

        for path in self.resolve_files():
            file_report = self._import_file(path)
            report.record_file(file_report)
            self._logger.info(
                "%s -> %s: imported=%d updated=%d skipped=%d failed=%d",
                path,
                self.doctype,
                file_report.imported,
                file_report.updated,
                file_report.skipped,
                file_report.failed,
            )

    def _import_file(self, path: Path) -> FileImportReport:
        """Import one file and return its per-file outcome."""
        outcome = self._executor.import_file(
            path,
            doctype=self.doctype,
            import_type=self._config.import_type,
            submit_after_import=self._import_config.submit_after_import,
        )
        if outcome.errors and not (outcome.imported or outcome.updated or outcome.skipped or outcome.failed):
            self._logger.info(
                "Not imported (reported): %s -> %s: %s",
                path,
                self.doctype,
                "; ".join(outcome.errors),
            )
        return FileImportReport(
            path=str(path),
            doctype=self.doctype,
            import_type=self._config.import_type,
            imported=outcome.imported,
            updated=outcome.updated,
            skipped=outcome.skipped,
            failed=outcome.failed,
            errors=outcome.errors,
        )
