"""
Common Validators

Reusable validation helpers shared across services.

Responsibilities
----------------
- Validate required values
- Validate company
- Validate supplier
- Validate customer
- Validate item collections
- Validate dates

These validators contain generic validation only.
Business-specific validation belongs in domain services.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from keemeds_commerce.utils.exceptions import require


def validate_required(value: Any, field_name: str) -> None:
    """
    Validate that a required value is provided.
    """
    require(value, f"{field_name} is required.")


def validate_company(company: str) -> None:
    """
    Validate company.
    """
    require(company, "Company is required.")


def validate_supplier(supplier: str) -> None:
    """
    Validate supplier.
    """
    require(supplier, "Supplier is required.")


def validate_customer(customer: str) -> None:
    """
    Validate customer.
    """
    require(customer, "Customer is required.")


def validate_items(items: Sequence[Any]) -> None:
    """
    Validate that at least one item exists.
    """
    require(items, "At least one item is required.")


def validate_posting_date(posting_date: str) -> None:
    """
    Validate posting date.
    """
    require(posting_date, "Posting Date is required.")


def validate_schedule_date(schedule_date: str) -> None:
    """
    Validate schedule date.
    """
    require(schedule_date, "Schedule Date is required.")


def validate_transaction_date(transaction_date: str) -> None:
    """
    Validate transaction date.
    """
    require(transaction_date, "Transaction Date is required.")


def validate_currency(value: float, field_name: str) -> None:
    """
    Validate currency value.
    """
    require(value is not None, f"{field_name} is required.")


def validate_delivery_date(delivery_date: str) -> None:
    """
    Validate delivery date.
    """
    require(delivery_date, "Delivery Date is required.")
