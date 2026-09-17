"""
Customer Service

Business logic for customer profile management and address CRUD.
Orchestrates ERPNext Customer, User, and Address documents.

Design notes
------------
- All operations are scoped to the currently authenticated user's linked
  Customer (looked up via ``tabPortal User``).
- Address ownership is verified via the ``tabDynamic Link`` child table on
  every read/write/delete.
- The service never exposes raw ERPNext documents; it returns domain DTOs.
"""

from __future__ import annotations

import logging

import frappe
from frappe import _

from keemeds_commerce.domain.address import AddressDTO, AddressListDTO, CustomerProfileDTO
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_permission_error,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.customer")


class CustomerService:
    """
    Customer profile and address operations.
    """

    # ------------------------------------------------------------------ #
    # Profile
    # ------------------------------------------------------------------ #

    def get_profile(self) -> CustomerProfileDTO:
        """
        Return the extended profile for the currently authenticated customer.
        """
        customer_name = self._get_customer_name()
        user = self._get_user(frappe.session.user)
        customer = self._get_customer(customer_name)

        first_name = user.get("first_name") or ""
        last_name = user.get("last_name") or ""
        full_name = f"{first_name} {last_name}".strip() if last_name else first_name

        return CustomerProfileDTO(
            email=user.get("email") or user.get("name") or "",
            full_name=full_name or customer.get("customer_name") or "",
            mobile_no=user.get("mobile_no") or "",
            customer_name=customer.get("customer_name") or "",
            customer_id=customer.get("customer_id") or customer.get("name") or "",
            gender=customer.get("gender") or "",
            customer_group=customer.get("customer_group") or "",
            territory=customer.get("territory") or "",
        )

    def update_profile(
        self,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        full_name: str | None = None,
        mobile_no: str | None = None,
        gender: str | None = None,
    ) -> CustomerProfileDTO:
        """
        Partially update the authenticated user's profile.

        Only non-None parameters are persisted.
        """
        customer_name = self._get_customer_name()

        if full_name is not None or mobile_no is not None:
            user_doc = frappe.get_doc("User", frappe.session.user)
            if full_name is not None:
                if first_name is None and last_name is None:
                    parts = full_name.strip().split(None, 1)
                    first_name = parts[0]
                    last_name = parts[1] if len(parts) > 1 else ""
                user_doc.first_name = first_name or user_doc.first_name or "User"
                user_doc.last_name = last_name if last_name is not None else (user_doc.last_name or "")
            if mobile_no is not None:
                user_doc.mobile_no = mobile_no
            user_doc.flags.ignore_permissions = True
            user_doc.save()

        if gender is not None:
            frappe.db.set_value("Customer", customer_name, "gender", gender)

        frappe.db.commit()
        logger.info("Profile updated for %s", frappe.session.user)
        return self.get_profile()

    # ------------------------------------------------------------------ #
    # Address — List
    # ------------------------------------------------------------------ #

    def list_addresses(self) -> AddressListDTO:
        """
        Return all non-disabled addresses linked to the current customer.
        """
        customer_name = self._get_customer_name()
        rows = frappe.get_all(
            "Address",
            filters=[
                ["Dynamic Link", "link_doctype", "=", "Customer"],
                ["Dynamic Link", "link_name", "=", customer_name],
                ["Dynamic Link", "parenttype", "=", "Address"],
                ["disabled", "=", 0],
            ],
            fields=[
                "name",
                "address_type",
                "address_title",
                "address_line1",
                "address_line2",
                "city",
                "state",
                "country",
                "pincode",
                "phone",
                "email_id",
                "is_primary_address",
                "is_shipping_address",
            ],
            order_by="is_primary_address DESC, creation ASC",
        )
        addresses = [self._row_to_dto(r) for r in rows]
        return AddressListDTO(addresses=addresses, total=len(addresses))

    # ------------------------------------------------------------------ #
    # Address — Get
    # ------------------------------------------------------------------ #

    def get_address(self, address_name: str) -> AddressDTO:
        """
        Return a single address, verifying it belongs to the current customer.
        """
        customer_name = self._get_customer_name()
        addr = self._get_address_doc(address_name)
        self._verify_ownership(addr, customer_name)
        return self._doc_to_dto(addr)

    # ------------------------------------------------------------------ #
    # Address — Create
    # ------------------------------------------------------------------ #

    def create_address(self, **kwargs) -> AddressDTO:
        """
        Create a new address linked to the current customer.
        """
        customer_name = self._get_customer_name()

        has_addresses = self._has_any_address(customer_name)
        is_first = not has_addresses

        addr = frappe.get_doc(
            {
                "doctype": "Address",
                "address_title": kwargs.get("address_title") or customer_name,
                "address_type": kwargs["address_type"],
                "address_line1": kwargs["address_line1"],
                "address_line2": kwargs.get("address_line2", ""),
                "city": kwargs["city"],
                "state": kwargs.get("state", ""),
                "country": kwargs["country"],
                "pincode": kwargs.get("pincode", ""),
                "phone": kwargs.get("phone", ""),
                "email_id": kwargs.get("email_id", ""),
                "is_primary_address": 1 if is_first else 0,
                "is_shipping_address": 1 if is_first else 0,
                "links": [{"link_doctype": "Customer", "link_name": customer_name}],
            }
        )
        addr.flags.ignore_permissions = True
        addr.insert()
        frappe.db.commit()

        logger.info("Address created: %s for %s", addr.name, customer_name)
        return self._doc_to_dto(addr)

    # ------------------------------------------------------------------ #
    # Address — Update
    # ------------------------------------------------------------------ #

    def update_address(self, address_name: str, **fields) -> AddressDTO:
        """
        Update an existing address, verifying ownership.
        """
        customer_name = self._get_customer_name()
        addr = self._get_address_doc(address_name)
        self._verify_ownership(addr, customer_name)

        for key, value in fields.items():
            if hasattr(addr, key):
                setattr(addr, key, value)

        addr.flags.ignore_permissions = True
        addr.save()
        frappe.db.commit()

        logger.info("Address updated: %s", address_name)
        return self._doc_to_dto(addr)

    # ------------------------------------------------------------------ #
    # Address — Delete
    # ------------------------------------------------------------------ #

    def delete_address(self, address_name: str) -> None:
        """
        Delete an address, verifying ownership.
        """
        customer_name = self._get_customer_name()
        addr = self._get_address_doc(address_name)
        self._verify_ownership(addr, customer_name)

        if addr.is_primary_address or addr.is_shipping_address:
            other_count = self._count_addresses_excluding(customer_name, address_name)
            if other_count == 0:
                raise_validation_error(
                    _("Cannot delete the last address. Create another address first.")
                )

        frappe.delete_doc("Address", address_name, force=True, ignore_permissions=True)
        frappe.db.commit()

        logger.info("Address deleted: %s", address_name)

    # ------------------------------------------------------------------ #
    # Address — Default Shipping
    # ------------------------------------------------------------------ #

    def set_default_shipping(self, address_name: str) -> AddressDTO:
        """
        Mark an address as the preferred shipping address.
        """
        customer_name = self._get_customer_name()
        addr = self._get_address_doc(address_name)
        self._verify_ownership(addr, customer_name)

        self._clear_preferred(customer_name, "is_shipping_address")
        addr.is_shipping_address = 1
        addr.flags.ignore_permissions = True
        addr.save()
        frappe.db.commit()

        logger.info("Default shipping set: %s", address_name)
        return self._doc_to_dto(addr)

    # ------------------------------------------------------------------ #
    # Address — Default Billing
    # ------------------------------------------------------------------ #

    def set_default_billing(self, address_name: str) -> AddressDTO:
        """
        Mark an address as the preferred billing address.
        """
        customer_name = self._get_customer_name()
        addr = self._get_address_doc(address_name)
        self._verify_ownership(addr, customer_name)

        self._clear_preferred(customer_name, "is_primary_address")
        addr.is_primary_address = 1
        addr.flags.ignore_permissions = True
        addr.save()
        frappe.db.commit()

        logger.info("Default billing set: %s", address_name)
        return self._doc_to_dto(addr)

    # ------------------------------------------------------------------ #
    # Internals — User / Customer
    # ------------------------------------------------------------------ #

    def _get_customer_name(self) -> str:
        """
        Return the Customer name linked to the current session user.

        Raises ValidationError if no session or no linked Customer.
        """
        user_name = frappe.session.user
        if not user_name or user_name == "Guest":
            raise_validation_error(_("You must be logged in to perform this action."))

        rows = frappe.db.sql(
            """
            SELECT c.name
            FROM `tabCustomer` c
            INNER JOIN `tabPortal User` pu ON pu.parent = c.name
            WHERE pu.user = %s AND pu.parenttype = 'Customer'
            LIMIT 1
            """,
            (user_name,),
            as_dict=True,
        )
        if not rows:
            raise_validation_error(_("No customer account is linked to this user."))
        return rows[0]["name"]

    def _get_user(self, user_name: str) -> dict:
        rows = frappe.get_all(
            "User",
            filters={"name": user_name},
            fields=["name", "email", "first_name", "last_name", "mobile_no", "user_type"],
            limit=1,
        )
        if not rows:
            raise_not_found("User", user_name)
        return rows[0]

    def _get_customer(self, customer_name: str) -> dict:
        rows = frappe.get_all(
            "Customer",
            filters={"name": customer_name},
            fields=[
                "name",
                "customer_name",
                "customer_type",
                "customer_group",
                "territory",
                "gender",
                "first_name",
                "last_name",
            ],
            limit=1,
        )
        if not rows:
            raise_not_found("Customer", customer_name)
        row = rows[0]
        row["customer_id"] = row["name"]
        return row

    # ------------------------------------------------------------------ #
    # Internals — Address
    # ------------------------------------------------------------------ #

    def _get_address_doc(self, address_name: str):
        try:
            addr = frappe.get_doc("Address", address_name)
        except frappe.DoesNotExistError:
            raise_not_found("Address", address_name)
        return addr

    def _verify_ownership(self, addr, customer_name: str) -> None:
        """Ensure the address is linked to the given Customer."""
        for link in addr.links:
            if link.link_doctype == "Customer" and link.link_name == customer_name:
                return
        raise_permission_error(_("You do not have permission to access this address."))

    def _has_any_address(self, customer_name: str) -> bool:
        count = frappe.db.count(
            "Address",
            filters=[
                ["Dynamic Link", "link_doctype", "=", "Customer"],
                ["Dynamic Link", "link_name", "=", customer_name],
                ["Dynamic Link", "parenttype", "=", "Address"],
                ["disabled", "=", 0],
            ],
        )
        return count > 0

    def _count_addresses_excluding(self, customer_name: str, exclude_name: str) -> int:
        rows = frappe.db.sql(
            """
            SELECT COUNT(*) AS cnt
            FROM `tabAddress` a
            INNER JOIN `tabDynamic Link` dl
                ON dl.parent = a.name
                AND dl.parenttype = 'Address'
                AND dl.link_doctype = 'Customer'
                AND dl.link_name = %s
            WHERE a.disabled = 0 AND a.name != %s
            """,
            (customer_name, exclude_name),
            as_dict=True,
        )
        return rows[0]["cnt"] if rows else 0

    def _clear_preferred(self, customer_name: str, field: str) -> None:
        """Uncheck *field* on all addresses for this customer."""
        rows = frappe.db.sql(
            """
            SELECT a.name
            FROM `tabAddress` a
            INNER JOIN `tabDynamic Link` dl
                ON dl.parent = a.name
                AND dl.parenttype = 'Address'
                AND dl.link_doctype = 'Customer'
                AND dl.link_name = %s
            WHERE a.disabled = 0 AND a.{field} = 1
            """.format(field=field),
            (customer_name,),
            as_dict=True,
        )
        for row in rows:
            frappe.db.set_value("Address", row["name"], field, 0)

    # ------------------------------------------------------------------ #
    # Internals — DTO builders
    # ------------------------------------------------------------------ #

    def _row_to_dto(self, row: dict) -> AddressDTO:
        return AddressDTO(
            name=row.get("name") or "",
            address_type=row.get("address_type") or "",
            address_title=row.get("address_title") or "",
            address_line1=row.get("address_line1") or "",
            address_line2=row.get("address_line2") or "",
            city=row.get("city") or "",
            state=row.get("state") or "",
            country=row.get("country") or "",
            pincode=row.get("pincode") or "",
            phone=row.get("phone") or "",
            email_id=row.get("email_id") or "",
            is_primary_address=bool(row.get("is_primary_address")),
            is_shipping_address=bool(row.get("is_shipping_address")),
        )

    def _doc_to_dto(self, doc) -> AddressDTO:
        return AddressDTO(
            name=doc.name or "",
            address_type=doc.address_type or "",
            address_title=doc.address_title or "",
            address_line1=doc.address_line1 or "",
            address_line2=doc.address_line2 or "",
            city=doc.city or "",
            state=doc.state or "",
            country=doc.country or "",
            pincode=doc.pincode or "",
            phone=doc.phone or "",
            email_id=doc.email_id or "",
            is_primary_address=bool(doc.is_primary_address),
            is_shipping_address=bool(doc.is_shipping_address),
        )
