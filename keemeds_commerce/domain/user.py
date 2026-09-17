"""
Auth Domain DTOs

Plain data holders for the Authentication API. They carry no logic beyond
serialization and never touch the database. They decouple the auth service
and API controllers from ERPNext User/Customer documents so:

- Services return DTOs (not ERPNext documents).
- Controllers serialize DTOs to JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class UserProfile:
    """
    Public profile of the currently authenticated user.

    Attributes
    ----------
    email:
        User email (also the ERPNext User name).
    full_name:
        Concatenated first + last name.
    mobile_no:
        Mobile phone number.
    user_type:
        ERPNext user type (``"Website User"`` or ``"System User"``).
    customer_name:
        Linked Customer name, if any.
    customer_id:
        Linked Customer ID, if any.
    """

    email: str = ""
    full_name: str = ""
    mobile_no: str = ""
    user_type: str = ""
    customer_name: str = ""
    customer_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
