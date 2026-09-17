"""
Phase 15 — ERPNext Session Authentication & CSRF Parity

Verifies that the custom register/login/logout endpoints follow ERPNext's
standard session authentication flow WITHOUT disabling CSRF protection:

1. ``register`` (``allow_guest`` POST) works with no CSRF token — a brand-new
   guest session holds no stored ``csrf_token``, so Frappe's
   ``HTTPRequest.validate_csrf_token`` skips enforcement. No separate
   ``get_csrf_token()`` call is required for fresh guests.
2. ``login`` (``allow_guest`` POST) works with no CSRF token and returns the
   standard login payload (``home_page``), establishing an ERPNext session.
3. A logged-in session with no stored token may POST without a token.
4. Once a session HAS a ``csrf_token`` (e.g. a Desk boot in the same browser),
   Frappe enforces it on every POST — including ``login`` — because the custom
   login runs after request init on the existing session: a POST without the
   token fails with ``CSRFTokenError`` and one carrying ``X-Frappe-CSRF-Token``
   succeeds. (Standard ``/api/method/login`` sidesteps this only because it logs
   in during request init, before validation, leaving a token-less session.)
5. ``GET`` ``csrf_token`` returns a valid token pre-login so a client can send
   it with the following POST — the required flow, never disabled.
"""

from __future__ import annotations

import frappe
from frappe.auth import CookieManager, LoginManager
from frappe.utils import get_test_client, set_request

_SITE = "keemeds-commerce.local"
_BASE = f"http://{_SITE}"
_HDR_SITE = {"X-Frappe-Site-Name": _SITE}

_EMAIL = "test.phase15@example.com"
_PHONE = "+915551515151"
_PASSWORD = "TestPass123!"
_FULL_NAME = "Phase Fifteen User"

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


def _cleanup():
    # Retry once on MariaDB 1020 (spurious under long-lived test sessions).
    for _ in range(2):
        try:
            _purge_all()
            return
        except frappe.exceptions.QueryDeadlockError:
            frappe.db.rollback()
            frappe.db.commit()
    _purge_all()


def _purge_all():
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
    frappe.db.commit()


def _register_payload() -> dict:
    return {
        "email": _EMAIL,
        "first_name": "Phase",
        "last_name": "Fifteen User",
        "full_name": _FULL_NAME,
        "mobile_no": _PHONE,
        "password": _PASSWORD,
    }


def _register(client) -> None:
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.register",
        json=_register_payload(),
        headers=_HDR_SITE,
    )
    body = r.get_json() or {}
    assert r.status_code == 200, f"register failed: {body}"
    assert body["message"]["success"] is True
    assert body["message"]["data"]["email"] == _EMAIL


def test_register_needs_no_csrf():
    client = get_test_client(use_cookies=True)
    _register(client)
    _restore()


def test_login_needs_no_csrf():
    client = get_test_client(use_cookies=True)
    _register(client)
    _restore()
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.login",
        json={"email": _EMAIL, "password": _PASSWORD},
        headers=_HDR_SITE,
    )
    body = r.get_json() or {}
    assert r.status_code == 200, f"login failed: {body}"
    assert body["message"]["success"] is True
    assert body.get("home_page") == "/portal", "login must follow standard ERPNext semantics"
    _restore()


def test_logged_in_session_without_token_can_post():
    client = get_test_client(use_cookies=True)
    _register(client)
    _restore()
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.login",
        json={"email": _EMAIL, "password": _PASSWORD},
        headers=_HDR_SITE,
    )
    assert r.status_code == 200, "login failed"
    _restore()
    r = client.post(_BASE + "/api/method/keemeds_commerce.api.auth.logout", json={}, headers=_HDR_SITE)
    body = r.get_json() or {}
    assert r.status_code == 200 and body["message"]["success"] is True, f"logout failed: {body}"
    _restore()


def test_csrf_enforced_when_session_has_token():
    client = get_test_client(use_cookies=True)
    _register(client)
    _restore()
    frappe.local.login_manager.login_as(_EMAIL)
    frappe.sessions.generate_csrf_token()
    frappe.db.commit()
    sid = frappe.session.sid
    token = frappe.session.data.csrf_token
    client = get_test_client(use_cookies=False)

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.logout",
        json={},
        headers={**_HDR_SITE, "Cookie": f"sid={sid}"},
    )
    assert (r.get_json() or {}).get("exc_type") == "CSRFTokenError", (
        "CSRF must be enforced once session data has a token"
    )

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.logout",
        json={},
        headers={**_HDR_SITE, "Cookie": f"sid={sid}", "X-Frappe-CSRF-Token": token},
    )
    assert r.status_code == 200, "matching X-Frappe-CSRF-Token must satisfy enforcement"
    _restore()


def _csrf_token_get_endpoint() -> str:
    return _BASE + "/api/method/keemeds_commerce.api.auth.csrf_token"


def test_csrf_token_endpoint_returns_token():
    client = get_test_client(use_cookies=True)
    r = client.get(_csrf_token_get_endpoint(), headers=_HDR_SITE)
    body = r.get_json() or {}
    token = (body.get("message") or {}).get("csrf_token")
    assert r.status_code == 200, f"csrf_token endpoint failed: {body}"
    assert isinstance(token, str) and token, (
        "endpoint must return a non-empty csrf_token"
    )
    _restore()


def _register_url() -> str:
    return _BASE + "/api/method/keemeds_commerce.api.auth.register"


def _register_as_guest_via_wsgi(email: str, full: str, phone: str) -> None:
    """Create an account exactly like the frontend: withCredentials, JSON body,
    no CSRF token, no Origin header (fresh anonymous browser)."""
    client = get_test_client(use_cookies=True)
    r = client.post(
        _register_url(),
        json={"email": email, "full_name": full, "mobile_no": phone, "password": _PASSWORD},
        headers=_HDR_SITE,
    )
    body = r.get_json() or {}
    assert r.status_code == 200, f"guest register failed: {body}"
    _restore()


def _purge_specific(email: str, full: str) -> None:
    """Remove one test account (user, customer, contact and child rows)."""
    for c in frappe.get_all("Contact", filters={"email_id": email}, pluck="name"):
        for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
        frappe.db.sql("DELETE FROM `tabContact` WHERE name = %s", (c,))
    for pu in frappe.get_all("Portal User", filters={"user": email}, pluck="name"):
        frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
    for dl in frappe.get_all("Dynamic Link", filters={"link_name": full}, pluck="name"):
        frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
    for c in frappe.get_all("Customer", filters={"customer_name": full}, pluck="name"):
        frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (c,))
    if frappe.db.exists("User", email):
        frappe.db.sql("DELETE FROM `tabUser` WHERE name = %s", (email,))
    frappe.db.sql("DELETE FROM `tabSessions` WHERE user = %s", (email,))
    frappe.db.commit()


def test_register_from_token_session_origin_whitelist():
    """Production regression: the SPA posts register with credentials and NO
    csrf token. A session that already holds a token (Desk in the same browser)
    must not reject the register POST when the Origin is the trusted SPA origin,
    while foreign origins and bare token-less POSTs stay rejected (CSRF on)."""
    owner_email = "p15.owner@example.com"
    owner_full = "Phase Fifteen Owner"
    new_email = "p15.newreg@example.com"
    new_full = "Phase Fifteen New Reg"
    _purge_specific(owner_email, owner_full)
    _purge_specific(new_email, new_full)

    _register_as_guest_via_wsgi(owner_email, owner_full, "+915552222222")
    frappe.local.login_manager.login_as(owner_email)
    frappe.sessions.generate_csrf_token()
    frappe.db.commit()
    sid = frappe.session.sid
    _restore()

    client = get_test_client(use_cookies=False)
    cookie = {**_HDR_SITE, "Cookie": f"sid={sid}"}
    payload = {
        "email": new_email,
        "full_name": new_full,
        "mobile_no": "+915553333333",
        "password": _PASSWORD,
    }

    r = client.post(_register_url(), json=payload, headers=cookie)
    assert (r.get_json() or {}).get("exc_type") == "CSRFTokenError", (
        "bare token-less POST from a tokened session must stay rejected"
    )
    _restore()

    r = client.post(
        _register_url(),
        json=payload,
        headers={**cookie, "Origin": f"http://{_SITE}"},
    )
    body = r.get_json() or {}
    assert r.status_code == 200 and body["message"]["success"] is True, (
        f"trusted SPA origin must pass without a token: {body}"
    )
    _restore()

    r = client.post(
        _register_url(),
        json=payload,
        headers={**cookie, "Origin": "http://attacker.example.net"},
    )
    assert (r.get_json() or {}).get("exc_type") == "CSRFTokenError", (
        "foreign origin must stay rejected — CSRF remains enforced"
    )
    _restore()

    _purge_specific(owner_email, owner_full)
    _purge_specific(new_email, new_full)


def test_login_enforced_then_accepted_with_session_token():
    """Reproduces the production CSRFTokenError: a session that holds a token
    (e.g. the browser is also logged into Desk) enforces CSRF on our custom
    login. Fetching the token via csrf_token and sending it must succeed."""
    client = get_test_client(use_cookies=True)
    _register(client)
    _restore()

    # Simulate the Desk-booted session (holds a csrf_token), as in production.
    frappe.local.login_manager.login_as(_EMAIL)
    frappe.sessions.generate_csrf_token()
    frappe.db.commit()
    sid = frappe.session.sid
    token = frappe.session.data.csrf_token

    token_client = get_test_client(use_cookies=False)
    cookie_hdr = {**_HDR_SITE, "Cookie": f"sid={sid}"}

    r = token_client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.login",
        json={"email": _EMAIL, "password": _PASSWORD},
        headers=cookie_hdr,
    )
    assert (r.get_json() or {}).get("exc_type") == "CSRFTokenError", (
        "CSRF must be enforced on login once the session holds a token"
    )
    _restore()

    # The csrf_token endpoint returns a valid pre-login token for that session.
    r = token_client.get(_csrf_token_get_endpoint(), headers=cookie_hdr)
    body = r.get_json() or {}
    returned_token = (body.get("message") or {}).get("csrf_token")
    assert r.status_code == 200 and returned_token == token, (
        f"csrf_token endpoint must return the session token: {body}"
    )
    _restore()

    r = token_client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.login",
        json={"email": _EMAIL, "password": _PASSWORD},
        headers={**cookie_hdr, "X-Frappe-CSRF-Token": token},
    )
    body = r.get_json() or {}
    assert r.status_code == 200 and body["message"]["success"] is True, f"login failed: {body}"
    _restore()


def run():
    _results.update({"passed": 0, "failed": 0, "errors": []})
    _restore()
    _cleanup()

    print("=== ERPNext SESSION AUTH & CSRF PARITY ===")
    _test("register works with no CSRF token (no get_csrf_token needed)", test_register_needs_no_csrf)
    _restore()
    _cleanup()
    _test("login works with no CSRF token, sets standard session", test_login_needs_no_csrf)
    _restore()
    _cleanup()
    _test("logged-in session without token can POST", test_logged_in_session_without_token_can_post)
    _restore()
    _cleanup()
    _test("CSRF enforced once session holds a token (never disabled)", test_csrf_enforced_when_session_has_token)
    _restore()
    _cleanup()
    _test("csrf_token endpoint returns a valid token", test_csrf_token_endpoint_returns_token)
    _restore()
    _cleanup()
    _test("login enforced then accepted with session token", test_login_enforced_then_accepted_with_session_token)
    _restore()
    _cleanup()
    _test("register from tokened session allowed for trusted origin only", test_register_from_token_session_origin_whitelist)
    _restore()
    _cleanup()

    print(f"RESULTS: {_results['passed']} passed, {_results['failed']} failed")
    for name, err in _results["errors"]:
        print(f"  - {name}: {err}")
    if _results["failed"]:
        raise SystemExit(1)
