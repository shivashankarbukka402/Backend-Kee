"""
Base Service

Reusable base class for common ERPNext document operations.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.model.document import Document


class BaseService:
    """
    Base class for ERPNext document services.

    Child classes must define:
        doctype = "<ERPNext DocType>"
    """

    doctype: str = ""

    @classmethod
    def exists(cls, name: str) -> bool:
        """Check whether a document exists."""
        return bool(frappe.db.exists(cls.doctype, name))

    @classmethod
    def get(cls, name: str) -> Document:
        """Fetch a document."""
        return frappe.get_doc(cls.doctype, name)

    @classmethod
    def list(
        cls,
        *,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        order_by: str = "modified desc",
        limit_start: int = 0,
        limit_page_length: int = 20,
    ) -> list[dict[str, Any]]:
        """List documents."""

        return frappe.get_all(
            cls.doctype,
            filters=filters or {},
            fields=fields or ["name"],
            order_by=order_by,
            limit_start=limit_start,
            limit_page_length=limit_page_length,
        )

    @classmethod
    def count(
        cls,
        *,
        filters: dict[str, Any] | None = None,
    ) -> int:
        """Count documents."""

        return frappe.db.count(
            cls.doctype,
            filters=filters or {},
        )

    @classmethod
    def create(cls, document_data: dict[str, Any]) -> Document:
        """Create a new document."""

        document = frappe.get_doc(document_data)
        document.insert()

        return document

    @classmethod
    def save(cls, document: Document) -> Document:
        """Save an existing document."""

        document.save()

        return document

    @classmethod
    def submit(cls, document: Document) -> Document:
        """Submit a document."""

        document.submit()

        return document

    @classmethod
    def cancel(cls, document: Document) -> Document:
        """Cancel a submitted document."""

        document.cancel()

        return document

    @classmethod
    def delete(cls, name: str) -> None:
        """Delete a document."""

        frappe.delete_doc(cls.doctype, name)

    @classmethod
    def update(
        cls,
        document: Document,
        values: dict[str, Any],
    ) -> Document:
        """Update a document with the given values and save."""

        document.update(values)

        document.save()

        return document
