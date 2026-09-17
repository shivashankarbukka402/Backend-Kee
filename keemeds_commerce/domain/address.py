"""
Address Domain DTOs

Plain data holders for the Customer Profile & Address API. They carry no
logic beyond serialization and never touch the database. They decouple the
customer service and API controllers from ERPNext Address and Customer
documents so:

- Services return DTOs (not ERPNext documents).
- Controllers serialize DTOs to JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class AddressDTO:
    """
    Public representation of a single ERPNext Address.

    Only exposes fields the storefront needs; internal ERPNext metadata is
    never serialized.
    """

    name: str = ""
    address_type: str = ""
    address_title: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    pincode: str = ""
    phone: str = ""
    email_id: str = ""
    is_primary_address: bool = False
    is_shipping_address: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AddressListDTO:
    """
    Paginated list of addresses for a customer.
    """

    addresses: list[AddressDTO] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict:
        return {
            "addresses": [a.to_dict() for a in self.addresses],
            "total": self.total,
        }


@dataclass(frozen=True)
class CustomerProfileDTO:
    """
    Extended customer profile for the Profile page.

    Superset of UserProfile with customer-specific fields that are not needed
    by the lightweight ``/me`` endpoint.
    """

    email: str = ""
    full_name: str = ""
    mobile_no: str = ""
    customer_name: str = ""
    customer_id: str = ""
    gender: str = ""
    customer_group: str = ""
    territory: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
