"""
Phase 16 Validation — Registered Account Visible Under ERPNext Desk User

Regression guard for the reported symptom: "registration returns success and
login works, but the newly registered account is not visible in ERPNext Desk
under User".

That symptom is only reproducible when the frontend runs the STATIC in-memory
auth mock (no ERPNext records are created at all). This test locks in the real
backend behaviour through the exact frontend request pattern (WSGI client,
Guest session, JSON body, no CSRF token on a fresh session) and then asserts,
through the same mechanism ERPNext Desk uses to list Users
(``frappe.get_list`` as Administrator), that:

- The User document exists, is enabled and is a Website User.
- The Customer document exists.
- The User and Customer are linked via ``tabPortal User``.
- The User can log in immediately with the supplied password (standard
  ERPNext session, ``home_page == "/portal"``).
- The User is visible in the Desk User list query.

Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase16_register_desk.run
"""

from __future__ import annotations

import frappe
from frappe.auth import CookieManager, LoginManager
from frappe.utils import get_test_client, set_request

_SITE = "keemeds-commerce.local"
_BASE = f"http://{_SITE}"
_HDR_SITE = {"X-Frappe-Site-Name": _SITE}

_EMAIL = "test.phase16@example.com"
_PHONE = "+916556565656"
_PASSWORD = "TestPass123!"
_FULL_NAME = "Phase Sixteen User"
_FIRST = "Phase"
_LAST = "Sixteen User"

_results: dict = {"passed": 0, "failed": 0, "errors": []}


def _test(name: str, fn) -> None:
    try:
        fn()
        _results["passed"] += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _results["failed"] += 1
        _results["errors"].append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def _restore():
    """Re-initialise request-local state after a WSGI sub-request (which calls
    ``frappe.destroy`` at the end of every request)."""
    set_request(path="/")
    if not getattr(frappe.local, "cookie_manager", None):
        frappe.local.cookie_manager = CookieManager()
    if not getattr(frappe.local, "login_manager", None):
        frappe.local.login_manager = LoginManager()
    frappe.local.form_dict = frappe._dict()
    frappe.db.commit()


def _as_admin():
    _restore()
    frappe.local.user_perms = None
    frappe.local.login_manager.login_as("Administrator")
    frappe.local.roles = frappe.permissions.get_roles("Administrator")
    frappe.db.commit()


def _purge():
    # Retry once on MariaDB 1020 (spurious under long-lived test sessions).
    for _ in range(2):
        try:
            _purge_once()
            return
        except frappe.exceptions.QueryDeadlockError:
            frappe.db.rollback()
            frappe.db.commit()
    _purge_once()


def _purge_once():
    frappe.db.commit()
    for c in frappe.get_all("Contact", filters={"email_id": _EMAIL}, pluck="name"):
        for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
        frappe.db.sql("DELETE FROM `tabContact` WHERE name = %s", (c,))
    for pu in frappe.get_all("Portal User", filters={"user": _EMAIL}, pluck="name"):
        frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
    for dl in frappe.get_all("Dynamic Link", filters={"link_name": _FULL_NAME}, pluck="name"):
        frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
    for c in frappe.get_all("Customer", filters={"customer_name": _FULL_NAME}, pluck="name"):
        frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (c,))
    if frappe.db.exists("User", _EMAIL):
        frappe.db.sql("DELETE FROM `tabUser` WHERE name = %s", (_EMAIL,))
    frappe.db.sql("DELETE FROM `tabSessions` WHERE user = %s", (_EMAIL,))
    frappe.db.commit()


def _register(client) -> dict:
    """The exact frontend pattern: WSGI Guest POST, JSON body, no CSRF token."""
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.register",
        json={
            "email": _EMAIL,
            "first_name": _FIRST,
            "last_name": _LAST,
            "full_name": _FULL_NAME,
            "mobile_no": _PHONE,
            "password": _PASSWORD,
        },
        headers=_HDR_SITE,
    )
    body = r.get_json() or {}
    assert r.status_code == 200, f"register failed: {body}"
    assert body["message"]["success"] is True, f"register not successful: {body}"
    return body["message"]["data"] or {}

# --------------------------------------------------------------------- #
# Test regime: register once through the WSGI client, then verify.
# --------------------------------------------------------------------- #

def test_register_creates_committed_account():
    """A Guest WSGI register (exactly what AuthService does in production)
    must create an enabled Website User and a linked Customer, committed to
    this site's database before the response — checked as Administrator,
    which is the identity ERPNext Desk uses to list Users."""
    client = get_test_client(use_cookies=True)
    data = _register(client)
    _restore()

    assert data.get("email") == _EMAIL
    assert data.get("customer_id") != ""
    _as_admin()

    assert frappe.db.exists("User", _EMAIL), "User document missing"
    user = frappe.db.get_value("User", _EMAIL, ["enabled", "user_type"], as_dict=True)
    assert user["enabled"] == 1, f"User must be enabled: {user}"
    assert user["user_type"] == "Website User", f"user_type={user['user_type']}"

    customer = frappe.db.sql(
        """
        SELECT c.name, c.customer_name
        FROM `tabCustomer` c
        INNER JOIN `tabPortal User` pu ON pu.parent = c.name
        WHERE pu.user = %s AND pu.parenttype = 'Customer'
        LIMIT 1
        """,
        (_EMAIL,),
        as_dict=True,
    )
    assert customer, "No Customer linked to the User via Portal User"
    assert customer[0]["customer_name"] == _FULL_NAME
    assert frappe.db.exists("Customer", customer[0]["name"]), "Customer document missing"
    count = len(frappe.db.get_all("Portal User", filters={"user": _EMAIL}))
    assert count == 1, f"expected exactly one Portal User link, got {count}"


def test_user_visible_in_desk_user_list():
    """The exact mechanism ERPNext Desk uses to render the User list
    (``frappe.get_list('User', ...)``) must include the new account, running
    as Administrator (the Desk session identity)."""
    _as_admin()
    users = frappe.get_list(
        "User",
        filters={"name": _EMAIL},
        fields=["name", "enabled", "user_type"],
        limit_page_length=10,
    )
    assert users, "New account must appear in the Desk User list query"
    assert len(users) == 1 and users[0]["name"] == _EMAIL
    assert users[0]["enabled"] == 1


def test_immediate_standard_login():
    """A fresh Guest session must be able to log in immediately with the
    supplied password, establishing a standard ERPNext session."""
    client = get_test_client(use_cookies=True)
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.login",
        json={"email": _EMAIL, "password": _PASSWORD},
        headers=_HDR_SITE,
    )
    body = r.get_json() or {}
    assert r.status_code == 200, f"login failed: {body}"
    assert body["message"]["success"] is True, f"login not successful: {body}"
    assert body.get("home_page") == "/portal", "standard ERPNext login semantics required"
    _restore()
    _as_admin()


def test_check_password_standard():
    """ERPNext's standard password store accepts the supplied password
    (frappe.utils.password.check_password — the LoginManager primitive)."""
    from frappe.utils.password import check_password

    _as_admin()
    assert check_password(_EMAIL, _PASSWORD), "password must be valid against the standard store"


def run():
    _results.update({"passed": 0, "failed": 0, "errors": []})
    _restore()
    _as_admin()
    _purge()
    _restore()

    print("=== REGISTERED ACCOUNT VISIBLE UNDER DESK USER ===")
    _test("register creates committed enabled Website User + linked Customer", test_register_creates_committed_account)
    _test("account is visible in the Desk User list query", test_user_visible_in_desk_user_list)
    _test("fresh session can log in immediately (standard ERPNext)", test_immediate_standard_login)
    _test("password valid against standard ERPNext password store", test_check_password_standard)

    _as_admin()
    _purge()

    print(f"RESULTS: {_results['passed']} passed, {_results['failed']} failed")
    for name, err in _results["errors"]:
        print(f"  - {name}: {err}")
    if _results["failed"]:
        raise SystemExit(1)
    return _results