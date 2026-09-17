"""
Phase 14 Validation — Portal User Parity for the Register API

The custom ``register`` endpoint must create a fully functional ERPNext
Website User exactly like ERPNext's standard portal signup:

- Can log in immediately with the supplied password.
- Customer document auto-created and linked via ``Portal User``.
- Primary Contact auto-created and linked to the Customer.
- ``Customer`` role (and ``Portal Settings.default_role``) granted even when
  the request runs as Guest (no Desk permission is required afterwards).

Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase14_user_parity.run
"""

from __future__ import annotations

import json

import frappe
from frappe.auth import CookieManager, LoginManager
from frappe.utils import set_request

from keemeds_commerce.services.auth_service import AuthService

_TEST_EMAIL = "test.phase14@example.com"
_TEST_PHONE = "+915555555555"
_TEST_PASSWORD = "TestPass123!"
_TEST_FULL_NAME = "Phase Fourteen User"
_TEST_FIRST = "Phase"
_TEST_LAST = "Fourteen User"

_EMAIL2 = "test.phase14b@example.com"
_PHONE2 = "+914444444444"
_FULL2 = "Phase Fourteen Second User"

svc: AuthService | None = None


def _get_svc() -> AuthService:
    global svc
    if svc is None:
        svc = AuthService()
    return svc


def _setup():
    set_request(path="/")
    if not getattr(frappe.local, "cookie_manager", None):
        frappe.local.cookie_manager = CookieManager()
    if not getattr(frappe.local, "login_manager", None):
        frappe.local.login_manager = LoginManager()
    frappe.local.form_dict = frappe._dict()


def _commit():
    frappe.db.commit()


def _as_admin():
    _commit()
    frappe.local.user_perms = None
    frappe.local.login_manager.login_as("Administrator")
    _commit()


def _as_guest():
    frappe.session.user = "Guest"
    _commit()


def _as_pristine_guest():
    """A Guest session with no inherited role cache (real HTTP request)."""
    _commit()
    frappe.session.user = "Guest"
    frappe.local.roles = frappe.permissions.get_roles("Guest")
    _commit()


def _cleanup_all():
    emails = [_TEST_EMAIL, _EMAIL2]
    names = [_TEST_FULL_NAME, _FULL2]
    # Retry once on MariaDB 1020 (spurious under long-lived test sessions).
    for _ in range(2):
        try:
            _purge_once(emails, names)
            return
        except frappe.exceptions.QueryDeadlockError:
            frappe.db.rollback()
            _commit()
    _purge_once(emails, names)


def _purge_once(emails, names):
    _commit()
    for email in emails:
        if frappe.db.exists("User", email):
            frappe.db.sql("DELETE FROM `tabUser` WHERE name = %s", (email,))
        for pu in frappe.get_all("Portal User", filters={"user": email}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
    _commit()
    for name in names:
        for dl in frappe.get_all("Dynamic Link", filters={"link_name": name}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
        for c in frappe.get_all("Customer", filters={"customer_name": name}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (c,))
    _commit()
    for email in emails:
        for c in frappe.get_all("Contact", filters={"email_id": email}, pluck="name"):
            for ce in frappe.get_all("Contact Email", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabContact Email` WHERE name = %s", (ce,))
            for cp in frappe.get_all("Contact Phone", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabContact Phone` WHERE name = %s", (cp,))
            for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
            frappe.db.sql("DELETE FROM `tabContact` WHERE name = %s", (c,))
    _commit()


def _user_roles(email: str) -> set[str]:
    user = frappe.get_doc("User", email)
    return {r.role for r in user.roles}


def _linked_customer(email: str) -> dict | None:
    rows = frappe.db.sql(
        """
        SELECT c.name AS customer_id, c.customer_name
        FROM `tabCustomer` c
        INNER JOIN `tabPortal User` pu ON pu.parent = c.name
        WHERE pu.user = %s AND pu.parenttype = 'Customer'
        LIMIT 1
        """,
        (email,),
        as_dict=True,
    )
    return rows[0] if rows else None


def run():
    results: dict = {"passed": 0, "failed": 0, "errors": [], "samples": {}}

    def _test(name: str, fn) -> None:
        try:
            fn()
            results["passed"] += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            results["failed"] += 1
            results["errors"].append((name, str(e)))
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            results["failed"] += 1
            results["errors"].append((name, f"{type(e).__name__}: {e}"))
            print(f"  ERROR {name}: {type(e).__name__}: {e}")

    # ================================================================== #
    # Setup
    # ================================================================== #
    _setup()
    _as_admin()
    _cleanup_all()

    # ================================================================== #
    # USER PARITY (admin context)
    # ================================================================== #
    print("\n=== PORTAL USER PARITY ===")

    def test_register_creates_enabled_website_user():
        _as_admin()
        profile = _get_svc().register(
            email=_TEST_EMAIL,
            first_name=_TEST_FIRST,
            last_name=_TEST_LAST,
            full_name=_TEST_FULL_NAME,
            mobile_no=_TEST_PHONE,
            password=_TEST_PASSWORD,
        )
        assert profile.email == _TEST_EMAIL
        user = frappe.get_doc("User", _TEST_EMAIL)
        assert user.enabled == 1, f"enabled={user.enabled}"
        assert user.user_type == "Website User", f"user_type={user.user_type}"
        assert str(user.full_name) == _TEST_FULL_NAME
        results["samples"]["register"] = profile.to_dict()
    _test("User is enabled Website User with correct full name", test_register_creates_enabled_website_user)

    def test_immediate_login():
        _as_guest()
        _get_svc().login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        assert frappe.session.user == _TEST_EMAIL, f"session={frappe.session.user}"
    _test("User can log in immediately with supplied password", test_immediate_login)

    def test_customer_role_granted():
        _as_admin()
        roles = _user_roles(_TEST_EMAIL)
        assert "Customer" in roles, f"roles={sorted(roles)}"
    _test("Customer role granted after registration", test_customer_role_granted)

    def test_customer_auto_created_and_linked():
        _as_admin()
        linked = _linked_customer(_TEST_EMAIL)
        assert linked is not None, "No Customer linked via Portal User"
        assert linked["customer_name"] == _TEST_FULL_NAME
        count = len(frappe.db.get_all("Portal User", filters={"user": _TEST_EMAIL}))
        assert count == 1, f"expected 1 portal_user row, got {count}"
    _test("Customer auto-created with exactly one Portal User link", test_customer_auto_created_and_linked)

    def test_primary_contact_created():
        _as_admin()
        linked = _linked_customer(_TEST_EMAIL)
        customer = frappe.get_doc("Customer", linked["customer_id"])
        assert customer.customer_primary_contact, "No primary contact set on Customer"
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        links = [(l.link_doctype, l.link_name) for l in contact.links]
        assert ("Customer", customer.name) in links, f"links={links}"
        assert contact.email_id == _TEST_EMAIL, f"email={contact.email_id}"
        assert contact.mobile_no == _TEST_PHONE, f"mobile={contact.mobile_no}"
    _test("Primary Contact created and linked to Customer", test_primary_contact_created)

    def test_profile_returns_linked_customer():
        _as_guest()
        _get_svc().login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        profile = _get_svc().get_profile()
        assert profile.customer_id != ""
        assert profile.customer_name == _TEST_FULL_NAME
    _test("Profile resolves the linked Customer", test_profile_returns_linked_customer)

    # ================================================================== #
    # GUEST-CONTEXT REGISTRATION (real HTTP flow, no inherited roles)
    # ================================================================== #
    print("\n=== GUEST-CONTEXT REGISTRATION ===")

    def test_guest_register_grants_customer_role():
        _as_admin()
        _cleanup_all()
        _as_pristine_guest()
        _get_svc().register(
            email=_TEST_EMAIL,
            first_name=_TEST_FIRST,
            last_name=_TEST_LAST,
            full_name=_TEST_FULL_NAME,
            mobile_no=_TEST_PHONE,
            password=_TEST_PASSWORD,
        )
        roles = _user_roles(_TEST_EMAIL)
        assert "Customer" in roles, f"roles={sorted(roles)}"
    _test("Guest-context register still grants Customer role (no Desk needed)", test_guest_register_grants_customer_role)

    def test_guest_register_user_can_login():
        _as_pristine_guest()
        _get_svc().login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        assert frappe.session.user == _TEST_EMAIL
    _test("Guest-context registered user can log in immediately", test_guest_register_user_can_login)

    def test_guest_register_creates_contact():
        _as_admin()
        linked = _linked_customer(_TEST_EMAIL)
        customer = frappe.get_doc("Customer", linked["customer_id"])
        assert customer.customer_primary_contact, "No primary contact"
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        links = [(l.link_doctype, l.link_name) for l in contact.links]
        assert ("Customer", customer.name) in links
        assert contact.email_id == _TEST_EMAIL
        assert contact.mobile_no == _TEST_PHONE
    _test("Guest-context register creates linked Contact", test_guest_register_creates_contact)

    # ================================================================== #
    # PORTAL SETTINGS DEFAULT ROLE
    # ================================================================== #
    print("\n=== PORTAL SETTINGS DEFAULT ROLE ===")

    def test_default_role_honored():
        _original = frappe.db.get_single_value("Portal Settings", "default_role")
        try:
            frappe.db.set_single_value("Portal Settings", "default_role", "Accounts User")
            frappe.db.commit()
            _as_admin()
            _cleanup_all()
            _as_pristine_guest()
            _get_svc().register(
                email=_EMAIL2,
                first_name="Second",
                last_name="User",
                full_name=_FULL2,
                mobile_no=_PHONE2,
                password=_TEST_PASSWORD,
            )
            roles = _user_roles(_EMAIL2)
            assert "Accounts User" in roles, f"roles={sorted(roles)}"
            assert "Customer" in roles, f"roles={sorted(roles)}"
        finally:
            frappe.db.set_single_value("Portal Settings", "default_role", _original or "")
            frappe.db.commit()
    _test("Portal Settings default_role applied alongside Customer role", test_default_role_honored)

    # ================================================================== #
    # API CONTRACT PRESERVED
    # ================================================================== #
    print("\n=== API CONTRACT ===")

    def test_api_register_contract():
        _as_admin()
        _cleanup_all()
        frappe.local.form_dict = frappe._dict({
            "email": _TEST_EMAIL,
            "full_name": _TEST_FULL_NAME,
            "mobile_no": _TEST_PHONE,
            "password": _TEST_PASSWORD,
        })
        from keemeds_commerce.api.auth import register
        result = register()
        assert result["success"] is True
        assert result["message"] == "Account created successfully."
        data = result["data"]
        assert data["email"] == _TEST_EMAIL
        assert data["customer_name"] == _TEST_FULL_NAME
        assert data["customer_id"] != ""
        assert data["user_type"] == "Website User"
        results["samples"]["api_register"] = result
    _test("API: register response shape unchanged", test_api_register_contract)

    def test_api_register_still_rejects_duplicates():
        _as_admin()
        frappe.local.form_dict = frappe._dict({
            "email": _TEST_EMAIL,
            "full_name": "Duplicate User",
            "mobile_no": "+903333333333",
            "password": _TEST_PASSWORD,
        })
        from keemeds_commerce.api.auth import register
        try:
            register()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("API: duplicate registration still raises ValidationError", test_api_register_still_rejects_duplicates)

    # ================================================================== #
    # REGRESSION
    # ================================================================== #
    print("\n=== REGRESSION ===")

    def test_phase12_auth_still_works():
        _as_admin()
        _cleanup_all()
        frappe.local.form_dict = frappe._dict({
            "email": _TEST_EMAIL,
            "full_name": _TEST_FULL_NAME,
            "mobile_no": _TEST_PHONE,
            "password": _TEST_PASSWORD,
        })
        from keemeds_commerce.api.auth import login, me, register
        reg = register()
        assert reg["success"] is True
        _as_guest()
        frappe.local.form_dict = frappe._dict({"email": _TEST_EMAIL, "password": _TEST_PASSWORD})
        log = login()
        assert log["success"] is True
        assert frappe.session.user == _TEST_EMAIL
        frappe.local.form_dict = frappe._dict()
        prof = me()
        assert prof["data"]["email"] == _TEST_EMAIL
        assert prof["data"]["customer_name"] == _TEST_FULL_NAME
    _test("Regression: register->login->me flow intact", test_phase12_auth_still_works)

    def test_phase13_modules_importable():
        import importlib
        for m in [
            "keemeds_commerce.api.customer",
            "keemeds_commerce.services.customer_service",
            "keemeds_commerce.domain.address",
            "keemeds_commerce.validators.customer_params",
        ]:
            importlib.import_module(m)
    _test("Regression: customer modules importable", test_phase13_modules_importable)

    # ================================================================== #
    # Cleanup
    # ================================================================== #
    _as_admin()
    _cleanup_all()

    # ================================================================== #
    # SUMMARY
    # ================================================================== #
    print("\n" + "=" * 60)
    print(f"RESULTS: {results['passed']} passed, {results['failed']} failed")
    if results["errors"]:
        print("\nFailed tests:")
        for name, err in results["errors"]:
            print(f"  - {name}: {err}")
    print("=" * 60)

    if results["samples"]:
        print("\n--- Sample: Register Response ---")
        print(json.dumps(results["samples"].get("api_register", results["samples"].get("register", {})), indent=2, default=str)[:1500])

    return results
