"""
API Response Utilities

Provides a consistent JSON response format for all
whitelisted API endpoints.

Responsibilities
----------------
- Standard success responses
- Standard error responses
- Reusable across the application
"""

from __future__ import annotations

from typing import Any


def success_response(
    *,
    message: str = "",
    data: Any = None,
) -> dict[str, Any]:
    """
    Return a standard success response.
    """

    return {
        "success": True,
        "message": message,
        "data": data,
    }


def error_response(
    *,
    message: str,
    errors: Any = None,
) -> dict[str, Any]:
    """
    Return a standard error response.
    """

    return {
        "success": False,
        "message": message,
        "errors": errors,
    }
