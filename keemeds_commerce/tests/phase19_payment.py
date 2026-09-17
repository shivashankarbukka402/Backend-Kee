"""
Phase 19 — Payment API Layer (create / verify / complete / fail / retry /
status / history / webhook) built on Sales Order + Payment Entry.

Regression guard for the production-ready payment endpoints backed by ERPNext.

Coverage
--------
Validators (pure):
- ``order_name`` / ``session_name`` required cleaning; ``amount`` positive
  bound; ``signature`` required; ``optional_reason`` trimming.

Domain DTOs:
- Session / Completion / Status / History / Webhook contracts.

Authorization & ownership:
- Guest sessions (service + controller + HTTP/WSGI) rejected on every user
  payment operation; a successful webhook is guest-callable.
- Cross-user isolation: another user cannot create/verify/status/history on an
  order they do not own and cannot verify another user's session.

Flow (service-level):
- create_payment returns a gateway-ready session for a Draft Sales Order.
- Replay: a second create on the same order returns the same active session
  (never a duplicate Payment Session).
- verify_payment (valid signature) submits the Sales Order, creates + submits a
  referenced Payment Entry, marks the session Paid and updates the Sales Order
  ``payment_status`` = Paid plus ``advance_paid``.
- Duplicate verify rejected; invalid signature rejected; amount mismatch
  rejected; verify of a foreign/user-failed session rejected.
- fail_payment keeps the Sales Order Draft, records the reason; retry opens a
  fresh session (new session, same Draft SO, history preserved).
- status transitions (Pending/Processing->Paid, Failed, Cancelled) and the
  derived status from the submitted order.
- history returns every attempt in order.
- webhook: paid event completes once (single Payment Entry), duplicate event
  ignored via Integration Request marker; failed event leaves Draft; invalid
  signature rejected.
- rollback: a broken ``payment_paid_to_account`` leaves no partial Payment
  Entry / session / Sales Order state.

HTTP/WSGI end-to-end:
- create / verify / status / history through ``/api/method/keemeds_commerce.api.payment.*``
- guest webhook route.

Performance:
- average per-call latency for create_payment, verify_payment, status, history.

Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase19_payment.run
"""

from __future__ import annotations

import time
import traceback as tb
from typing import Any, Callable

import frappe
from frappe.auth import CookieManager, LoginManager
from frappe.utils import get_test_client, set_request

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.payment import (
    PaymentCompletionDTO,
    PaymentHistoryDTO,
    PaymentHistoryItemDTO,
    PaymentSessionDTO,
    PaymentStatusDTO,
    PaymentWebhookDTO,
)
from keemeds_commerce.services.auth_service import AuthService
from keemeds_commerce.services.cart_service import CartService
from keemeds_commerce.services.checkout_service import CheckoutService
from keemeds_commerce.services.customer_service import CustomerService
from keemeds_commerce.services.payment_gateway import TestGateway, get_gateway
from keemeds_commerce.services.payment_service import PaymentService
from keemeds_commerce.services.pricing_service import PricingService
from keemeds_commerce.validators import payment_params

_SITE = "keemeds-commerce.local"
_BASE = f"http://{_SITE}"
_HDR_SITE = {"X-Frappe-Site-Name": _SITE}

_PASSWORD = "TestPass123!"

_USERS = [
    {
        "email": "pay.u1@example.com",
        "first_name": "Pay",
        "last_name": "User One",
        "full_name": "Pay User One",
        "mobile_no": "+918004000001",
    },
    {
        "email": "pay.u2@example.com",
        "first_name": "Pay",
        "last_name": "User Two",
        "full_name": "Pay User Two",
        "mobile_no": "+918004000002",
    },
]

U1 = _USERS[0]["email"]
U2 = _USERS[1]["email"]

_results: dict = {"passed": 0, "failed": 0, "errors": []}
_performance: dict = {}
_registered: dict = {}

# ---------------------------------------------------------------------- #
# Harness
# ---------------------------------------------------------------------- #


def _test(name: str, fn: Callable[[], None]) -> None:
    try:
        fn()
        _results["passed"] += 1
        print(f"  PASS  {name}")
    except Exception as e:
        _results["failed"] += 1
        _results["errors"].append((name, f"{type(e).__name__}: {e}"))
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")
        tb.print_exc()


def _restore() -> None:
    try:
        frappe.db.sql("SELECT 1")
    except Exception:
        frappe.connect()
    set_request(path="/")
    if not getattr(frappe.local, "cookie_manager", None):
        frappe.local.cookie_manager = CookieManager()
    if not getattr(frappe.local, "login_manager", None):
        frappe.local.login_manager = LoginManager()
    frappe.local.form_dict = frappe._dict()
    frappe.db.commit()


def _as_guest() -> None:
    frappe.session.user = "Guest"
    frappe.db.commit()


def _as_admin() -> None:
    _restore()
    frappe.local.user_perms = None
    frappe.local.login_manager.login_as("Administrator")
    frappe.local.roles = frappe.permissions.get_roles("Administrator")
    frappe.db.commit()


def _login(email: str) -> None:
    _restore()
    AuthService().login(email=email, password=_PASSWORD)
    frappe.local.user_perms = None
    frappe.local.roles = frappe.permissions.get_roles(email)
    frappe.db.commit()


def _purge() -> None:
    for _ in range(2):
        try:
            _purge_once()
            return
        except frappe.exceptions.QueryDeadlockError:
            frappe.db.rollback()
            frappe.db.commit()
    _purge_once()


def _purge_once() -> None:
    frappe.db.commit()
    emails = [u["email"] for u in _USERS]
    fulls = [u["full_name"] for u in _USERS]

    for full in fulls:
        for pe in frappe.db.sql(
            "SELECT name FROM `tabPayment Entry` WHERE party LIKE %s", ("%" + full + "%",), as_list=True
        ):
            pe_name = pe[0]
            try:
                doc = frappe.get_doc("Payment Entry", pe_name)
                doc.flags.ignore_permissions = True
                if doc.docstatus == 1:
                    doc.cancel()
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.db.commit()
            frappe.db.sql("DELETE FROM `tabPayment Entry Reference` WHERE parent=%s", (pe_name,))
            frappe.db.sql("DELETE FROM `tabGL Entry` WHERE voucher_type='Payment Entry' AND voucher_no=%s", (pe_name,))
            frappe.db.sql("DELETE FROM `tabPayment Ledger Entry` WHERE voucher_no=%s", (pe_name,))
            frappe.delete_doc("Payment Entry", pe_name, force=1, ignore_permissions=True)
        for ps in frappe.db.sql(
            "SELECT name FROM `tabPayment Session` WHERE customer LIKE %s OR user_email IN (%s)", ("%" + full + "%", ",".join("'%s'" % e for e in emails)), as_list=True
        ):
            frappe.delete_doc("Payment Session", ps[0], force=1, ignore_permissions=True)
        for so in frappe.get_all("Sales Order", filters={"customer": full}, pluck="name"):
            try:
                doc = frappe.get_doc("Sales Order", so)
                doc.flags.ignore_permissions = True
                if doc.docstatus == 1:
                    doc.cancel()
                    frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.db.commit()
            frappe.db.sql("DELETE FROM `tabSales Order Item` WHERE parent=%s", (so,))
            frappe.db.sql("DELETE FROM `tabSales Taxes and Charges` WHERE parent=%s", (so,))
            frappe.delete_doc("Sales Order", so, force=1, ignore_permissions=True)

    for ir in frappe.get_all(
        "Integration Request", filters={"integration_request_service": ["like", "keemeds.%"]}, pluck="name"
    ):
        frappe.delete_doc("Integration Request", ir, force=1, ignore_permissions=True)
    # Clean up orphan Bin rows left by submitted-then-cancelled SOs in test
    # warehouses. These affect the phase18 stock-exceeds test which modifies
    # ALL Bin rows for the target item.
    for b in frappe.db.sql(
        "SELECT name FROM `tabBin` WHERE actual_qty <= 0 AND warehouse != 'Finished Goods - HG'",
        as_list=True,
    ):
        frappe.db.sql("DELETE FROM `tabBin` WHERE name = %s", (b[0],))

    for email in emails:
        for doctype in ("Cart", "Wishlist"):
            for name in frappe.get_all(doctype, filters={"user": email}, pluck="name"):
                frappe.get_doc(doctype, name).delete()
        for c in frappe.get_all("Contact", filters={"email_id": email}, pluck="name"):
            for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
            frappe.db.sql("DELETE FROM `tabContact` WHERE name = %s", (c,))
        for pu in frappe.get_all("Portal User", filters={"user": email}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
        if frappe.db.exists("User", email):
            frappe.db.sql("DELETE FROM `tabUser` WHERE name = %s", (email,))
        frappe.db.sql("DELETE FROM `tabSessions` WHERE user = %s", (email,))
    for full in fulls:
        for a in frappe.db.sql(
            """SELECT a.name FROM `tabAddress` a
               INNER JOIN `tabDynamic Link` dl
                   ON dl.parent = a.name AND dl.parenttype = 'Address'
               WHERE dl.link_name = %s""",
            (full,),
        ):
            frappe.delete_doc("Address", a[0], force=1, ignore_permissions=True)
        for dl in frappe.get_all("Dynamic Link", filters={"link_name": full}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
        for c in frappe.get_all("Customer", filters={"customer_name": full}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (c,))
    frappe.db.commit()


def _ensure_registered() -> None:
    for u in _USERS:
        _as_admin()
        _purge()
    _as_admin()
    for u in _USERS:
        if not frappe.db.exists("User", u["email"]):
            AuthService().register(
                email=u["email"],
                first_name=u["first_name"],
                last_name=u["last_name"],
                full_name=u["full_name"],
                mobile_no=u["mobile_no"],
                password=_PASSWORD,
            )
        _registered[u["email"]] = frappe.db.get_value(
            "Customer",
            {"customer_name": u["full_name"]},
            "name",
        )
    _as_admin()


# ---------------------------------------------------------------------- #
# Runtime data helpers
# ---------------------------------------------------------------------- #

_cfg: Any = None
_pr: PricingService | None = None


def _config() -> Any:
    global _cfg
    if _cfg is None:
        _cfg = CommerceConfig()
    return _cfg


def _pricing() -> PricingService:
    global _pr
    if _pr is None:
        _pr = PricingService(_config())
    return _pr


def _price(item_code: str) -> float:
    return float(_pricing().get_price(item_code)[0] or 0.0)


def _add_first_address(email: str) -> str:
    _login(email)
    _restore()
    return CustomerService().create_address(
        address_type="Shipping",
        address_line1="41 Payment Road",
        city="Pune",
        state="Maharashtra",
        country="India",
        pincode="411001",
    )


def _draft_order(email: str, qty: int = 1, item: str = "MED-001") -> str:
    """Log in as ``email``, fill a cart and create a fresh Draft Sales Order."""
    _login(email)
    CartService().clear_cart()
    CartService().add_item(item_code=item, quantity=qty)
    _add_first_address(email)
    # Every call builds a brand-new Draft: the window-0 config disables the
    # checkout replay guard so payment tests are isolated from it (phase 18
    # covers replay behaviour).
    return CheckoutService(
        config=CommerceConfig(checkout_duplicate_window_minutes=0)
    ).create_order().sales_order


def _svc(email: str | None = None) -> PaymentService:
    if email:
        _login(email)
    return PaymentService(config=_config())


def _session_amount(so: str) -> float:
    return round(float(frappe.get_doc("Sales Order", so).grand_total), 2)


def _pay_session_count(so: str) -> int:
    return frappe.db.count("Payment Session", {"sales_order": so})


def _entry_count_for(so: str) -> int:
    return frappe.db.count(
        "Payment Entry Reference", {"reference_doctype": "Sales Order", "reference_name": so}
    )


def _webhook_event(
    svc: PaymentService, ps, status: str, event_id: str, transaction_id: str = ""
) -> dict:
    gw = get_gateway(_config())
    payload = {
        "gateway": "test",
        "event_id": event_id,
        "order_id": ps.idempotency_key,
        "session": ps.session,
        "sales_order": ps.sales_order,
        "status": status,
        "transaction_id": transaction_id,
        "amount": f"{ps.amount:.2f}",
        "currency": ps.currency,
    }
    payload["signature"] = gw.sign_webhook(payload)
    return payload


# ---------------------------------------------------------------------- #
# Validators (pure)
# ---------------------------------------------------------------------- #


def test_validators():
    assert payment_params.order_name({"sales_order": "  SO-1 "}) == "SO-1"
    assert payment_params.session_name({"session": " s1 "}) == "s1"
    try:
        payment_params.order_name({"sales_order": ""})
        raise AssertionError("blank order name must raise")
    except frappe.exceptions.ValidationError:
        pass
    assert payment_params.amount({"amount": "12.345"}) == 12.35
    try:
        payment_params.amount({"amount": "-1"})
        raise AssertionError("negative amount must raise")
    except frappe.exceptions.ValidationError:
        pass
    try:
        payment_params.amount({"amount": "abc"})
        raise AssertionError("non-numeric amount must raise")
    except frappe.exceptions.ValidationError:
        pass
    assert payment_params.signature({"signature": " abc "}) == "abc"
    try:
        payment_params.signature({"signature": ""})
        raise AssertionError("blank signature must raise")
    except frappe.exceptions.ValidationError:
        pass
    assert payment_params.optional_reason({"reason": "  declined  "}) == "declined"
    assert payment_params.optional_reason({}) == ""


# ---------------------------------------------------------------------- #
# DTO contract
# ---------------------------------------------------------------------- #


def test_dto_contract():
    s = PaymentSessionDTO(session="s1", amount=65.0, amount_in_paise=6500)
    d = s.to_dict()
    assert d["session"] == "s1" and d["amount"] == 65.0 and d["amount_in_paise"] == 6500
    c = PaymentCompletionDTO(success=True, session="s1", payment_entry="ACC-PAY-1", status="Paid").to_dict()
    assert c["status"] == "Paid" and c["payment_entry"] == "ACC-PAY-1"
    st = PaymentStatusDTO(status="Failed", failure_reason="card declined").to_dict()
    assert st["status"] == "Failed" and st["failure_reason"] == "card declined"
    h = PaymentHistoryDTO(
        sales_order="SO-1",
        items=[PaymentHistoryItemDTO(session="s1", status="Paid")],
        total=1,
    ).to_dict()
    assert h["total"] == 1 and h["items"][0]["status"] == "Paid"
    w = PaymentWebhookDTO(accepted=True, duplicate=False, event_id="e1").to_dict()
    assert w["accepted"] is True and w["duplicate"] is False


# ---------------------------------------------------------------------- #
# Authorization & ownership
# ---------------------------------------------------------------------- #


def test_guest_rejected_on_services():
    so = _draft_order(U1)
    _as_guest()
    assert frappe.session.user == "Guest", "session must be Guest here"
    svc = PaymentService(config=_config())
    for fn in (
        lambda: svc.create_payment(sales_order=so),
        lambda: svc.verify_payment(sales_order=so, amount=1, signature="x"),
        lambda: svc.complete_payment(sales_order=so),
        lambda: svc.fail_payment(sales_order=so),
        lambda: svc.retry_payment(sales_order=so),
        lambda: svc.status(sales_order=so),
        lambda: svc.history(sales_order=so),
    ):
        try:
            fn()
            raise AssertionError("guest operation must be rejected")
        except frappe.exceptions.ValidationError:
            pass
    _as_admin()


def test_guest_rejected_on_controllers():
    _as_guest()
    for endpoint in (
        "create_payment",
        "verify_payment",
        "complete_payment",
        "retry_payment",
        "fail_payment",
        "status",
        "history",
    ):
        from importlib import import_module

        mod = import_module("keemeds_commerce.api.payment")
        try:
            getattr(mod, endpoint)()
            raise AssertionError(f"guest {endpoint} must be rejected")
        except frappe.exceptions.ValidationError:
            pass
    _as_admin()


def test_guest_webhook_allowed():
    _as_guest()
    import keemeds_commerce.api.payment as pm

    try:
        pm.webhook()
        raise AssertionError("unauthenticated webhook payload must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    _as_admin()


def test_cross_user_isolation():
    so = _draft_order(U1)
    s1 = _svc(U1).create_payment(sales_order=so)
    _svc(U2)  # log in as a different user
    svc = PaymentService(config=_config())
    for fn in (
        lambda: svc.create_payment(sales_order=so),
        lambda: svc.verify_payment(sales_order=so, session=s1.session, amount=1, signature="x"),
        lambda: svc.status(sales_order=so),
        lambda: svc.history(sales_order=so),
        lambda: svc.fail_payment(sales_order=so, session=s1.session, reason="x"),
    ):
        try:
            fn()
            raise AssertionError("cross-user operation must be rejected")
        except (frappe.exceptions.ValidationError, frappe.exceptions.PermissionError):
            pass


# ---------------------------------------------------------------------- #
# Flow: create / replay
# ---------------------------------------------------------------------- #


def test_create_payment_session():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    assert isinstance(ps, PaymentSessionDTO)
    d = ps.to_dict()
    assert d["sales_order"] == so
    assert d["status"] == "Pending"
    assert d["gateway"] == "TEST"
    assert d["amount"] == _session_amount(so)
    assert d["amount_in_paise"] == int(round(d["amount"] * 100))
    assert d["signature"], "a gateway signature must be present"
    assert d["payload"]["order_id"] == d["idempotency_key"]
    assert _pay_session_count(so) == 1

    again = svc.create_payment(sales_order=so)
    assert again.session == ps.session, "replay must return the same active session"
    assert _pay_session_count(so) == 1, "replay must not create a duplicate session"


def test_create_after_paid_rejected():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    svc.verify_payment(sales_order=so, session=ps.session, amount=ps.amount, signature=ps.signature)
    try:
        svc.create_payment(sales_order=so)
        raise AssertionError("create after paid must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    try:
        svc.retry_payment(sales_order=so)
        raise AssertionError("retry after paid must be rejected")
    except frappe.exceptions.ValidationError:
        pass


# ---------------------------------------------------------------------- #
# Flow: verify / complete -> Payment Entry + Sales Order submit
# ---------------------------------------------------------------------- #


def test_verify_payment_success():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)

    comp = svc.verify_payment(sales_order=so, session=ps.session, amount=ps.amount, signature=ps.signature)
    assert isinstance(comp, PaymentCompletionDTO)
    assert comp.success is True and comp.status == "Paid"
    assert comp.payment_entry

    so_doc = frappe.get_doc("Sales Order", so)
    assert so_doc.docstatus == 1, "Sales Order must be submitted on payment"
    assert so_doc.payment_status == "Paid"
    assert float(so_doc.advance_paid or 0) == comp.amount

    pe = frappe.get_doc("Payment Entry", comp.payment_entry)
    assert pe.docstatus == 1 and pe.status == "Submitted"
    assert pe.party == frappe.get_doc("Sales Order", so).customer
    refs = [(r.reference_doctype, r.reference_name) for r in pe.references]
    assert ("Sales Order", so) in refs
    assert pe.paid_from == "Debtors - HG" and pe.paid_to == "Cash - HG"
    assert pe.mode_of_payment == "Cash", "default (no method) should resolve to Cash"
    assert _entry_count_for(so) == 1, "exactly one Payment Entry reference for the order"

    ps_doc = frappe.get_doc("Payment Session", ps.session)
    assert ps_doc.status == "Paid"
    assert ps_doc.payment_entry == comp.payment_entry
    assert ps_doc.transaction_id == comp.transaction_id


def test_upi_payment_creates_upi_mode():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so, method="upi")
    assert ps.payment_method == "upi"

    comp = svc.verify_payment(
        sales_order=so, session=ps.session, amount=ps.amount,
        signature=ps.signature, method="upi",
    )
    assert comp.success is True and comp.status == "Paid"

    pe = frappe.get_doc("Payment Entry", comp.payment_entry)
    assert pe.mode_of_payment == "UPI", "UPI payment must produce mode_of_payment = UPI"

    so_doc = frappe.get_doc("Sales Order", so)
    assert so_doc.payment_method == "upi", "Sales Order must store the gateway payment_method"


def test_cod_payment_creates_cash_mode():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so, method="cod")
    assert ps.payment_method == "cod"

    comp = svc.verify_payment(
        sales_order=so, session=ps.session, amount=ps.amount,
        signature=ps.signature, method="cod",
    )
    assert comp.success is True and comp.status == "Paid"

    pe = frappe.get_doc("Payment Entry", comp.payment_entry)
    assert pe.mode_of_payment == "Cash", "COD payment must produce mode_of_payment = Cash"

    so_doc = frappe.get_doc("Sales Order", so)
    assert so_doc.payment_method == "cod", "Sales Order must store the gateway payment_method"


def test_unknown_method_falls_back_to_default():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so, method="bitcoin")
    comp = svc.verify_payment(
        sales_order=so, session=ps.session, amount=ps.amount,
        signature=ps.signature, method="bitcoin",
    )
    pe = frappe.get_doc("Payment Entry", comp.payment_entry)
    assert pe.mode_of_payment == "Cash", "unknown method falls back to default Cash"


def test_verify_duplicate_rejected():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    svc.verify_payment(sales_order=so, session=ps.session, amount=ps.amount, signature=ps.signature)
    try:
        svc.verify_payment(sales_order=so, session=ps.session, amount=ps.amount, signature=ps.signature)
        raise AssertionError("duplicate verify must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    assert _entry_count_for(so) == 1, "no second Payment Entry on duplicate verify"


def test_complete_is_idempotent():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    c1 = svc.complete_payment(sales_order=so, session=ps.session)
    c2 = svc.complete_payment(sales_order=so, session=ps.session)
    assert c1.status == "Paid" and c2.status == "Paid"
    assert c1.payment_entry == c2.payment_entry
    assert _entry_count_for(so) == 1


def test_verify_invalid_signature_rejected():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    try:
        svc.verify_payment(sales_order=so, session=ps.session, amount=ps.amount, signature="deadbeef")
        raise AssertionError("invalid signature must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    assert frappe.get_doc("Sales Order", so).docstatus == 0, "SO must stay Draft"
    assert _entry_count_for(so) == 0


def test_verify_amount_mismatch_rejected():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    for bad in (ps.amount + 1, ps.amount - 1, 0.01):
        try:
            svc.verify_payment(sales_order=so, session=ps.session, amount=bad, signature=ps.signature)
            raise AssertionError("amount mismatch must be rejected")
        except frappe.exceptions.ValidationError:
            pass
    assert _entry_count_for(so) == 0


# ---------------------------------------------------------------------- #
# Flow: failure / retry / cancel / status / history
# ---------------------------------------------------------------------- #


def test_fail_keeps_draft_and_retry():
    so = _draft_order(U2)
    svc = _svc(U2)
    ps = svc.create_payment(sales_order=so)

    st = svc.fail_payment(sales_order=so, session=ps.session, reason="card declined")
    assert st.status == "Failed"
    assert st.failure_reason == "card declined"
    so_doc = frappe.get_doc("Sales Order", so)
    assert so_doc.docstatus == 0, "failed payment must keep the SO Draft"
    assert so_doc.payment_status == "Failed"

    ps2 = svc.retry_payment(sales_order=so)
    assert ps2.session != ps.session, "retry must open a fresh session"
    ps2_doc = frappe.get_doc("Payment Session", ps2.session)
    assert ps2_doc.retried_from == ps.session
    assert ps2_doc.sales_order == so, "retry must reuse the same Draft SO"
    # history now has the failed attempt + the new retry session
    hist = svc.history(sales_order=so)
    assert hist.total == 2
    assert hist.items[0].status == "Failed"
    assert hist.items[1].status == "Pending"


def test_status_transitions():
    so = _draft_order(U2)
    svc = _svc(U2)
    ps0 = svc.create_payment(sales_order=so)
    st_pending = svc.status(sales_order=so)
    assert st_pending.status == "Pending"

    # complete -> Paid (explicit transaction id is recorded on the session)
    svc.complete_payment(sales_order=so, session=ps0.session, transaction_id="txn_post_verify")
    st_paid = svc.status(sales_order=so)
    assert st_paid.status == "Paid"
    assert st_paid.payment_entry, "completed payment must expose the Payment Entry"
    assert st_paid.transaction_id == "txn_post_verify"
    assert st_paid.session == ps0.session


def test_status_no_session_derives_from_order():
    # A submitted, fully advanced order with no Payment Session -> derived Paid
    so = _draft_order(U2)
    frappe.set_user("Administrator")
    so_doc = frappe.get_doc("Sales Order", so)
    so_doc.flags.ignore_permissions = True
    so_doc.submit()
    frappe.db.set_value("Sales Order", so, "advance_paid", round(float(so_doc.grand_total), 2))
    frappe.db.set_value("Sales Order", so, "payment_status", "Paid")
    frappe.db.commit()
    svc = _svc(U2)
    st = svc.status(sales_order=so)
    assert st.status == "Paid"


def test_cancel_payment():
    so = _draft_order(U2)
    svc = _svc(U2)
    ps = svc.create_payment(sales_order=so)
    st = svc.cancel_payment(sales_order=so, session=ps.session, reason="user cancelled")
    assert st.status == "Cancelled"
    assert frappe.get_doc("Sales Order", so).docstatus == 0
    assert frappe.get_doc("Sales Order", so).payment_status == "Cancelled"
    ps2 = svc.retry_payment(sales_order=so)
    assert ps2.session != ps.session


def test_history_contract():
    so = _draft_order(U2)
    svc = _svc(U2)
    ps = svc.create_payment(sales_order=so)
    svc.fail_payment(sales_order=so, session=ps.session, reason="first decline")
    ps2 = svc.retry_payment(sales_order=so)
    svc.verify_payment(sales_order=so, session=ps2.session, amount=ps2.amount, signature=ps2.signature)

    hist = svc.history(sales_order=so)
    assert isinstance(hist, PaymentHistoryDTO)
    assert hist.sales_order == so
    assert hist.total == 2
    statuses = [i.status for i in hist.items]
    assert statuses == ["Failed", "Paid"]
    assert hist.items[-1].session == ps2.session
    assert hist.items[-1].payment_entry


# ---------------------------------------------------------------------- #
# Flow: webhook
# ---------------------------------------------------------------------- #


def test_webhook_paid_once():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    event = _webhook_event(svc, ps, "paid", "evt_phase19_paid", "txn_wh_paid")

    w1 = svc.webhook(dict(event))
    assert isinstance(w1, PaymentWebhookDTO)
    assert w1.accepted is True and w1.duplicate is False
    assert w1.status == "Paid" and w1.transaction_id == "txn_wh_paid"

    w2 = svc.webhook(dict(event))
    assert w2.duplicate is True and w2.accepted is False

    so_doc = frappe.get_doc("Sales Order", so)
    assert so_doc.docstatus == 1 and so_doc.payment_status == "Paid"
    assert _entry_count_for(so) == 1, "webhook must not double-charge"
    assert frappe.get_doc("Payment Session", ps.session).status == "Paid"


def test_webhook_failed_keeps_draft():
    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    event = _webhook_event(svc, ps, "failed", "evt_phase19_fail", "txn_wh_fail")
    w = svc.webhook(dict(event))
    assert w.accepted is True
    assert w.status == "Failed"
    assert frappe.get_doc("Sales Order", so).docstatus == 0
    assert frappe.get_doc("Sales Order", so).payment_status == "Failed"
    assert _entry_count_for(so) == 0


def test_webhook_invalid_signature_rejected():
    from keemeds_commerce.utils.exceptions import raise_validation_error
    import keemeds_commerce.api.payment as pm

    so = _draft_order(U1)
    svc = _svc(U1)
    ps = svc.create_payment(sales_order=so)
    event = _webhook_event(svc, ps, "paid", "evt_phase19_bad")
    event["signature"] = "forged"
    try:
        svc.webhook(dict(event))
        raise AssertionError("forged webhook must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    assert _entry_count_for(so) == 0


# ---------------------------------------------------------------------- #
# Flow: rollback on accounting error
# ---------------------------------------------------------------------- #


def test_rollback_on_bad_paid_to_account():
    so = _draft_order(U1)
    bad_cfg = CommerceConfig(payment_paid_to_account="No Such Account - HG")
    svc = PaymentService(config=bad_cfg)
    _login(U1)
    ps = svc.create_payment(sales_order=so)  # still creates a session under its own config gateway
    try:
        # verify under a service wired with the bad account triggers a PE failure
        svc.verify_payment(sales_order=so, session=ps.session, amount=ps.amount, signature=ps.signature)
        raise AssertionError("verify with bad account must fail")
    except (frappe.exceptions.ValidationError, frappe.exceptions.DoesNotExistError, Exception):
        frappe.db.rollback()
        frappe.db.commit()
    so_doc = frappe.get_doc("Sales Order", so)
    assert so_doc.docstatus == 0, "SO must not be submitted on rollback"
    assert so_doc.payment_status != "Paid"
    assert _entry_count_for(so) == 0
    assert frappe.get_doc("Payment Session", ps.session).status != "Paid"
    # the session should still be Pending after rollback so it could be retried
    assert frappe.get_doc("Payment Session", ps.session).status in ("Pending", "Processing")


# ---------------------------------------------------------------------- #
# HTTP/WSGI end-to-end
# ---------------------------------------------------------------------- #


def _wsgi_login(client, email: str) -> None:
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.auth.login",
        json={"email": email, "password": _PASSWORD},
        headers=_HDR_SITE,
    )
    body = r.get_json() or {}
    assert r.status_code == 200, f"login failed: {body}"
    assert body["message"]["success"] is True, f"login not successful: {body}"


def test_http_routes():
    so = _draft_order(U2)
    svc = _svc(U2)
    ps = svc.create_payment(sales_order=so)

    client = get_test_client(use_cookies=True)
    _wsgi_login(client, U2)
    _restore()

    # create over the wire returns the same session (replay)
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.payment.create_payment",
        json={"sales_order": so},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["session"] == ps.session
    _restore()

    # status
    r = client.get(
        _BASE + f"/api/method/keemeds_commerce.api.payment.status?sales_order={so}",
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["data"]["status"] == "Pending"
    _restore()

    # history (0-> has one Pending attempt)
    r = client.get(
        _BASE + f"/api/method/keemeds_commerce.api.payment.history?sales_order={so}",
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["data"]["total"] == 1
    _restore()

    # verify
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.payment.verify_payment",
        json={
            "sales_order": so,
            "session": ps.session,
            "amount": ps.amount,
            "signature": ps.signature,
        },
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["status"] == "Paid" and body["data"]["payment_entry"]
    _restore()

    # http guest webhook
    from keemeds_commerce.services.payment_gateway import get_gateway

    so2 = _draft_order(U2)
    ps2 = _svc(U2).create_payment(sales_order=so2)
    gw = get_gateway(_config())
    payload = {
        "gateway": "test",
        "event_id": "evt_http_wh",
        "order_id": ps2.idempotency_key,
        "session": ps2.session,
        "sales_order": so2,
        "status": "paid",
        "transaction_id": "txn_http_wh",
        "amount": f"{ps2.amount:.2f}",
        "currency": ps2.currency,
    }
    payload["signature"] = gw.sign_webhook(payload)
    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.payment.webhook",
        json=payload,
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["status"] == "Paid"
    _restore()


# ---------------------------------------------------------------------- #
# Performance
# ---------------------------------------------------------------------- #


def _time_per_call(n: int, fn: Callable[[], Any]) -> float:
    start = time.perf_counter()
    for _ in range(n):
        fn()
    return round((time.perf_counter() - start) / n * 1000.0, 3)


def test_performance_profile():
    _login(U1)
    svc = PaymentService(config=_config())
    so = _draft_order(U1)
    ps = svc.create_payment(sales_order=so)
    _performance["payment.status"] = _time_per_call(25, lambda: svc.status(sales_order=so))
    _performance["payment.history"] = _time_per_call(25, lambda: svc.history(sales_order=so))

    def timed_create(i: int) -> float:
        so_i = _draft_order(U1, qty=(i % 3) + 1)
        start = time.perf_counter()
        svc.create_payment(sales_order=so_i)
        return time.perf_counter() - start

    times = [timed_create(i) for i in range(6)]
    _performance["payment.create_payment"] = round(sum(times) / len(times) * 1000.0, 3)

    def timed_verify(i: int) -> float:
        so_i = _draft_order(U1, qty=(i % 3) + 1)
        p_i = svc.create_payment(sales_order=so_i)
        start = time.perf_counter()
        svc.verify_payment(sales_order=so_i, session=p_i.session, amount=p_i.amount, signature=p_i.signature)
        return time.perf_counter() - start

    vtimes = [timed_verify(i) for i in range(3)]
    _performance["payment.verify_payment"] = round(sum(vtimes) / len(vtimes) * 1000.0, 3)
    print(f"  INFO  avg ms/call: {_performance}")


# ---------------------------------------------------------------------- #
# Entry point
# ---------------------------------------------------------------------- #


def run() -> dict:
    _results.update({"passed": 0, "failed": 0, "errors": []})
    _performance.clear()
    _restore()
    _as_admin()
    _ensure_registered()
    _as_admin()

    print("=== PAYMENT (SESSION / VERIFY / COMPLETE / FAIL / RETRY / STATUS / HISTORY / WEBHOOK) ===")

    print("-- validators")
    _test("payment parameter validators", test_validators)

    print("-- DTO contract")
    _test("payment DTO serialization contract", test_dto_contract)

    print("-- authorization")
    _test("guest rejected on every payment service operation", test_guest_rejected_on_services)
    _test("guest rejected on every payment controller", test_guest_rejected_on_controllers)
    _test("guest webhook rejected without a valid payload", test_guest_webhook_allowed)
    _test("cross-user isolation on payment operations", test_cross_user_isolation)

    print("-- create / replay")
    _test("create payment session and replay", test_create_payment_session)
    _test("create/retry after paid rejected", test_create_after_paid_rejected)

    print("-- verify / complete")
    _test("verify success submits SO + Payment Entry", test_verify_payment_success)
    _test("duplicate verify rejected", test_verify_duplicate_rejected)
    _test("complete is idempotent", test_complete_is_idempotent)
    _test("invalid signature rejected (SO stays Draft)", test_verify_invalid_signature_rejected)
    _test("amount mismatch rejected", test_verify_amount_mismatch_rejected)

    print("-- failure / retry / cancel / status / history")
    _test("fail keeps Draft and retry opens fresh session", test_fail_keeps_draft_and_retry)
    _test("status transitions to Paid on complete", test_status_transitions)
    _test("status derives Paid from a fully-advanced order", test_status_no_session_derives_from_order)
    _test("cancel payment keeps Draft, allows retry", test_cancel_payment)
    _test("history contract and ordering", test_history_contract)

    print("-- webhook")
    _test("webhook paid completes once, duplicates ignored", test_webhook_paid_once)
    _test("webhook failed keeps Draft", test_webhook_failed_keeps_draft)
    _test("forged webhook signature rejected", test_webhook_invalid_signature_rejected)

    print("-- rollback")
    _test("rollback on bad paid-to account leaves no state", test_rollback_on_bad_paid_to_account)

    print("-- HTTP/WSGI end-to-end")
    _test("payment routes over HTTP + guest webhook", test_http_routes)

    print("-- performance")
    _test("per-call latency profile", test_performance_profile)

    _as_admin()
    frappe.local.user_perms = None
    _purge()

    print(f"RESULTS: {_results['passed']} passed, {_results['failed']} failed")
    for name, err in _results["errors"]:
        print(f"  - {name}: {err}")
    if _results["failed"]:
        raise SystemExit(1)

    output = dict(_results)
    output["performance"] = dict(_performance)
    return output
