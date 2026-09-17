"""
Master Data File Readers

Reading strategies that parse raw master source files (CSV and Excel) into the
format-agnostic :class:`~master_data.models.FileContent` structure.

The ``FileReader`` protocol together with :class:`ReaderRegistry` implement the
Strategy and Registry patterns: the :class:`~master_data.master_loader.MasterDataLoader`
only knows about the registry, so supporting a new file format only requires
implementing a reader and registering it.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

import openpyxl

from .config import FileType
from .models import FileContent


class FileReader(Protocol):
    """
    Protocol describing a reader capable of parsing a master source file.
    """

    def read(self, path: Path) -> FileContent:
        """
        Parse the file at ``path`` and return a format-agnostic FileContent.
        """
        ...


def _clean_headers(headers: list[str]) -> list[str]:
    """
    Normalize a list of raw header strings by trimming whitespace.
    """

    return [str(header).strip() for header in headers]


class CSVReader:
    """
    Reads master data from a CSV file with a header row.
    """

    def read(self, path: Path) -> FileContent:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            raw_headers = list(reader.fieldnames or [])
            headers = _clean_headers(raw_headers)
            rows = [
                {str(key).strip(): _to_string(value) for key, value in row.items()}
                for row in reader
            ]
        return FileContent(path=path, headers=headers, rows=rows)


class ExcelReader:
    """
    Reads master data from an Excel workbook, using the first row as headers.
    """

    def read(self, path: Path) -> FileContent:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            rows_iterator = sheet.iter_rows(values_only=True)

            headers: list[str] = []
            data_rows: list[dict[str, str]] = []

            for index, values in enumerate(rows_iterator):
                if index == 0:
                    headers = _clean_headers([_to_string(value) for value in values])
                    continue
                record: dict[str, str] = {}
                for header, value in zip(headers, values, strict=False):
                    record[header] = _to_string(value)
                data_rows.append(record)

            return FileContent(path=path, headers=headers, rows=data_rows)
        finally:
            workbook.close()


def _to_string(value: object) -> str:
    """
    Coerce an arbitrary cell value to a trimmed string (blanks become ``""``).
    """

    if value is None:
        return ""
    return str(value).strip()


class ReaderRegistry:
    """
    Registry that maps a :class:`~master_data.config.FileType` to a reader.

    Allows new file formats to be supported by registering a reader here
    without requiring changes to the loader.
    """

    def __init__(self) -> None:
        self._readers: dict[FileType, FileReader] = {}

    def register(self, file_type: FileType, reader: FileReader) -> None:
        """Associate a reader with a file type."""

        self._readers[file_type] = reader

    def get(self, file_type: FileType) -> FileReader:
        """
        Return the reader for ``file_type``.

        Raises
        ------
        KeyError
            If no reader has been registered for the file type.
        """

        return self._readers[file_type]


def build_default_registry() -> ReaderRegistry:
    """
    Return a registry pre-populated with the CSV and Excel readers.
    """

    registry = ReaderRegistry()
    registry.register(FileType.CSV, CSVReader())
    registry.register(FileType.EXCEL, ExcelReader())
    return registry
