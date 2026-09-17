"""
Auth Service

Business logic for user registration, login, logout and profile retrieval.
Orchestrates ERPNext User and Customer documents through the standard Frappe
authentication lifecycle.

Design notes
------------
- Registration creates both a User and a Customer (linked via portal_users).
- Login uses ERPNext's standard LoginManager (no JWT, no custom tokens).
- Logout destroys the current ERPNext session.
- All ERPNext reads/writes happen here; controllers only delegate and serialize.
"""

from __future__ import annotations

import logging

import frappe
from frappe import _

from keemeds_commerce.domain.user import UserProfile
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.auth")


class AuthService:
    """
    Handles user registration, login, logout and profile operations.
    """

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        full_name: str,
        mobile_no: str,
        password: str,
    ) -> UserProfile:
        """
        Create a new ERPNext User and link a Customer to it.

        Returns the newly created user's profile.
        """
        self._check_duplicate_email(email)
        self._check_duplicate_phone(mobile_no)

        user = self._create_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            mobile_no=mobile_no,
            password=password,
        )

        self._assign_portal_roles(user)

        customer = self._create_customer(
            customer_name=full_name,
            user_email=email,
            first_name=first_name,
            last_name=last_name,
            mobile_no=mobile_no,
        )

        frappe.db.commit()

        logger.info("User registered: %s (Customer: %s)", email, customer.name)
        return self._build_profile(user, customer)

    # ------------------------------------------------------------------ #
    # Login
    # ------------------------------------------------------------------ #

    def login(self, *, email: str, password: str) -> None:
        """
        Authenticate using ERPNext's standard LoginManager.

        Sets session cookies on success. Raises on failure.
        """
        from frappe.auth import CookieManager, LoginManager
        from frappe.utils import set_request
        from frappe.utils.password import check_password

        check_password(email, password)

        try:
            frappe.local.request
        except AttributeError:
            set_request(path="/")
        if not getattr(frappe.local, "cookie_manager", None):
            frappe.local.cookie_manager = CookieManager()
        if not getattr(frappe.local, "login_manager", None):
            frappe.local.login_manager = LoginManager()

        frappe.local.login_manager.login_as(email)

    # ------------------------------------------------------------------ #
    # Logout
    # ------------------------------------------------------------------ #

    def logout(self) -> None:
        """
        Destroy the current ERPNext session.
        """
        login_manager = getattr(frappe.local, "login_manager", None)
        if login_manager is not None:
            login_manager.logout()
        else:
            frappe.session.user = "Guest"
        frappe.db.commit()

    # ------------------------------------------------------------------ #
    # Profile
    # ------------------------------------------------------------------ #

    def get_profile(self) -> UserProfile:
        """
        Return the currently logged-in user's profile and linked Customer.
        """
        user_name = frappe.session.user
        if not user_name or user_name == "Guest":
            raise_validation_error(_("You must be logged in to view your profile."))

        user = self._get_user(user_name)
        customer = self._get_linked_customer(user_name)

        return self._build_profile(user, customer)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _check_duplicate_email(self, email: str) -> None:
        if frappe.db.exists("User", email):
            raise_validation_error(
                _("An account with email '{}' already exists.").format(email)
            )

    def _check_duplicate_phone(self, mobile_no: str) -> None:
        existing = frappe.db.get_value(
            "User", {"mobile_no": mobile_no}, ["name", "email"]
        )
        if existing:
            raise_validation_error(
                _("An account with phone '{}' already exists.").format(mobile_no)
            )

    def _create_user(
        self,
        *,
        email: str,
        first_name: str,
        last_name: str,
        mobile_no: str,
        password: str,
    ):
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "mobile_no": mobile_no,
                "enabled": 1,
                "user_type": "Website User",
                "new_password": password,
            }
        )
        user.flags.ignore_permissions = True
        user.flags.ignore_password_policy = True
        user.insert()
        return user

    def _create_customer(
        self,
        *,
        customer_name: str,
        user_email: str,
        first_name: str = "",
        last_name: str = "",
        mobile_no: str = "",
    ):
        customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")
        if not customer_group:
            customer_group = frappe.db.get_value(
                "Customer Group", {"is_group": 0, "parent_customer_group": "All Customer Groups"}, "name"
            )
        if not customer_group:
            customer_group = "All Customer Groups"

        territory = frappe.db.get_single_value("Selling Settings", "territory")
        if not territory:
            territory = "All Territories"

        customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": customer_name,
                "customer_type": "Individual",
                "customer_group": customer_group,
                "territory": territory,
                "portal_users": [{"user": user_email}],
            }
        )
        customer.flags.ignore_permissions = True
        customer.insert()
        if not customer.get("customer_primary_contact"):
            self._create_primary_contact(
                customer=customer,
                first_name=first_name,
                last_name=last_name,
                email=user_email,
                mobile_no=mobile_no,
            )
        return customer

    def _create_primary_contact(
        self,
        *,
        customer,
        first_name: str,
        last_name: str,
        email: str,
        mobile_no: str,
    ) -> None:
        """
        Create the primary Contact for a Customer, mirroring ERPNext's
        standard portal signup (``create_party_contact``).

        The Contact is linked to the Customer and carries the user's email and
        mobile number, which is what ties portal web users to their Customer
        records.
        """
        values = {
            "doctype": "Contact",
            "first_name": first_name or customer.customer_name,
            "last_name": last_name,
            "email_id": email,
            "mobile_no": mobile_no,
            "is_primary_contact": 1,
            "email_ids": [{"email_id": email, "is_primary": 1}],
            "links": [{"link_doctype": "Customer", "link_name": customer.name}],
        }
        if mobile_no:
            values["phone_nos"] = [{"phone": mobile_no, "is_primary_mobile_no": 1}]

        contact = frappe.get_doc(values)
        contact.flags.ignore_mandatory = True
        contact.insert(ignore_permissions=True)
        customer.db_set("customer_primary_contact", contact.name)

    def _assign_portal_roles(self, user) -> None:
        """
        Ensure the newly created user holds the portal roles a standard
        ERPNext signup grants.

        Self-registration runs as Guest, where ERPNext's
        ``add_role_for_portal_user`` silently skips role assignment (it only
        acts when the caller holds System Manager). We therefore grant the
        ``Customer`` role explicitly, and also apply the configured
        ``Portal Settings.default_role`` to mirror frappe's ``sign_up``.

        Roles are assigned right after user creation and before any Customer
        or Contact exists, exactly like ``sign_up``. The in-memory document
        still carries ``ignore_permissions`` from creation, so the save
        succeeds even when the request runs as Guest.
        """
        roles = {r.role for r in user.get("roles") or []}
        to_add = {"Customer"}

        default_role = frappe.db.get_single_value("Portal Settings", "default_role")
        if default_role:
            to_add.add(default_role)

        for role in sorted(to_add):
            if role not in roles:
                user.add_roles(role)

    def _get_user(self, user_name: str):
        rows = frappe.get_all(
            "User",
            filters={"name": user_name},
            fields=["name", "email", "first_name", "last_name", "mobile_no", "user_type", "enabled"],
            limit=1,
        )
        if not rows:
            raise_not_found("User", user_name)
        return rows[0]

    def _get_linked_customer(self, user_name: str) -> dict | None:
        """Return the first Customer linked via portal_users, or None."""
        rows = frappe.db.sql(
            """
            SELECT c.name AS customer_id, c.customer_name
            FROM `tabCustomer` c
            INNER JOIN `tabPortal User` pu ON pu.parent = c.name
            WHERE pu.user = %s AND pu.parenttype = 'Customer'
            LIMIT 1
            """,
            (user_name,),
            as_dict=True,
        )
        return rows[0] if rows else None

    def _build_profile(self, user, customer) -> UserProfile:
        email = user.get("email") or user.get("name") or ""
        first_name = user.get("first_name") or ""
        last_name = user.get("last_name") or ""
        full_name = f"{first_name} {last_name}".strip() if last_name else first_name
        cust_id = ""
        cust_name = ""
        if customer:
            cust_id = customer.get("customer_id") or customer.get("name") or ""
            cust_name = customer.get("customer_name") or ""
        return UserProfile(
            email=email,
            full_name=full_name,
            mobile_no=user.get("mobile_no") or "",
            user_type=user.get("user_type") or "",
            customer_name=cust_name,
            customer_id=cust_id,
        )
