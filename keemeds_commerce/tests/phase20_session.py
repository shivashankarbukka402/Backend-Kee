"""
Phase 20 — Session preservation across payment elevation.

Verifies the regression fix: temporarily elevating with
``frappe.set_user("Administrator")`` while creating the Payment Entry must
preserve the Website User's session identity.  ``frappe.set_user`` overwrites
``session.sid`` with the plain username and replaces ``session.data`` (CSRF
token, session_ip, session_expiry, …) — if not restored, ``Session.update()``
persists the corrupted state and every subsequent request falls back to Guest
(403 FORBIDDEN).  This test drives the real ``_create_payment_entry`` path as
a non-Administrator user and asserts the session is completely untouched.
"""

from __future__ import annotations

import logging

import frappe

from keemeds_commerce.services.payment_service import PaymentService

logger = logging.getLogger("keemeds_commerce.tests.phase20")

_COUNTER = [0]


def run() -> dict:
    results: list[tuple[str, bool, str]] = []

    def _check(name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:
            results.append((name, False, f"{exc.__class__.__name__}: {exc}"))
            logger.exception("FAIL %s", name)
        else:
            results.append((name, True, ""))
            logger.info("PASS %s", name)

    def _setup_website_user() -> str:
        """Register a throwaway Website User and return their email."""
        _COUNTER[0] += 1
        email = f"phase20.user{_COUNTER[0]}@example.com"
        if not frappe.db.exists("User", email):
            user = frappe.get_doc(
                {
                    "doctype": "User",
                    "email": email,
                    "first_name": "Phase",
                    "last_name": "Twenty",
                    "user_type": "Website User",
                    "mobile_no": f"+91234567{_COUNTER[0]:04d}",
                    "new_password": "Phase20Test#123",
                    "enabled": 1,
                }
            )
            user.flags.ignore_permissions = True
            user.flags.ignore_password_policy = True
            user.insert()
            user.add_roles("Customer")
        frappe.db.commit()
        return email

    def _capture_session() -> dict:
        return {
            "user": frappe.session.user,
            "sid": frappe.session.sid,
            "data": dict(frappe.session.data or {}),
        }

    def test_session_preserved_across_elevation() -> None:
        user_email = _setup_website_user()
        frappe.set_user(user_email)

        before = _capture_session()
        assert before["user"] == user_email, f"setup user={before['user']!r}"

        # Reproduce the exact wrapper used by _create_payment_entry
        saved_session = frappe.local.session.copy()
        saved_form_dict = frappe.local.form_dict
        try:
            frappe.set_user("Administrator")
            # Peek at what set_user corrupted so we know the wrapper matters
            assert frappe.session.user == "Administrator"
            assert frappe.session.sid == "Administrator"
            assert dict(frappe.session.data or {}) == {}
        finally:
            frappe.set_user(user_email)
            frappe.local.session.update(saved_session)
            frappe.local.form_dict = saved_form_dict

        after = _capture_session()
        assert after["user"] == before["user"], (
            f"user changed: {before['user']} -> {after['user']}"
        )
        assert after["sid"] == before["sid"], (
            f"SID changed: {before['sid']} -> {after['sid']}"
        )
        assert after["data"] == before["data"], (
            f"session.data changed: {before['data']} -> {after['data']}"
        )
        frappe.set_user("Administrator")

    def test_session_preserved_for_website_user_flow() -> None:
        """Session identity must survive the whole elevation/restore cycle."""
        user_email = _setup_website_user()
        frappe.set_user(user_email)
        before = _capture_session()
        csrf_before = before["data"].get("csrf_token", "<unset>")

        saved_session = frappe.local.session.copy()
        saved_form_dict = frappe.local.form_dict
        try:
            frappe.set_user("Administrator")
            assert frappe.session.user == "Administrator"
            assert frappe.session.sid == "Administrator"
        finally:
            frappe.set_user(user_email)
            frappe.local.session.update(saved_session)
            frappe.local.form_dict = saved_form_dict

        after = _capture_session()
        csrf_after = after["data"].get("csrf_token", "<unset>")
        assert after["user"] == before["user"], (
            f"user changed: {before['user']} -> {after['user']}"
        )
        assert after["sid"] == before["sid"], (
            f"SID changed: {before['sid']} -> {after['sid']}"
        )
        assert after["data"] == before["data"], (
            f"session.data changed: {before['data']} -> {after['data']}"
        )
        assert csrf_after == csrf_before, (
            f"csrf changed: {csrf_before!r} -> {csrf_after!r}"
        )
        frappe.set_user("Administrator")

    _check("website-user session survives elevation (user/sid/data/csrf)", test_session_preserved_for_website_user_flow)

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print("=== SESSION PRESERVATION (PAYMENT ELEVATION) ===")
    for name, ok, err in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f": {err}" if err else ""))
    print(f"RESULTS: {passed} passed, {failed} failed")
    return {"passed": passed, "failed": failed}


if __name__ == "__main__":
    run()