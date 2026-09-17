"""
Phase 18 — Checkout API Layer (Summary / Validate / Draft Sales Order)

Regression guard for the production-ready checkout endpoints backed by ERPNext
Sales Order Drafts.

Coverage
--------
Validators:
- ``shipping_address_name`` / ``billing_address_name`` cleaning (missing/blank ->
  None, non-string rejected, trimmed names preserved).

Domain DTOs:
- Checkout summary contract (``user_email``, ``customer_id``,
  ``customer_name``, ``currency``, ``items``, ``subtotal``, ``discount``,
  ``tax``, ``shipping_charge``, ``grand_total``, ``shipping_address``,
  ``billing_address``), line-item contract and Draft-order contract.

Authorization & ownership:
- Guest sessions (service + controller + HTTP/WSGI) are rejected on every
  checkout operation.
- The cart is scoped to the session user; foreign and non-existent addresses
  are rejected; the Draft Sales Order is created for the session user's
  customer.

Validation states:
- Empty cart, missing default shipping address, disabled item, out-of-stock
  item, quantity exceeding available stock (all atomic, cart intact).

Totals:
- ``subtotal - discount + tax + shipping_charge`` math with overridden
  ``CommerceConfig`` values, and parity between the summary and the persisted
  Draft Sales Order (ERPNext recompute).

Order creation:
- Creates a Draft Sales Order only (``docstatus`` 0, status ``Draft``), never
  submits, never clears the cart, and never processes payment.

HTTP/WSGI end-to-end:
- All three routes through ``/api/method/keemeds_commerce.api.checkout.*``.

Performance:
- average per-call latency for summary, validate and create_order.

Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase18_checkout.run
"""

from __future__ import annotations

import time
import traceback as tb
from typing import Any, Callable

import frappe
from frappe.auth import CookieManager, LoginManager
from frappe.utils import get_test_client, set_request

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.services.auth_service import AuthService
from keemeds_commerce.services.cart_service import CartService
from keemeds_commerce.services.checkout_service import CheckoutService
from keemeds_commerce.services.customer_service import CustomerService
from keemeds_commerce.services.pricing_service import PricingService
from keemeds_commerce.services.stock_service import StockService

_SITE = "keemeds-commerce.local"
_BASE = f"http://{_SITE}"
_HDR_SITE = {"X-Frappe-Site-Name": _SITE}

_PASSWORD = "TestPass123!"

_USERS = [
    {
        "email": "checkout.u1@example.com",
        "first_name": "Checkout",
        "last_name": "User One",
        "full_name": "Checkout User One",
        "mobile_no": "+918002000001",
    },
    {
        "email": "checkout.u2@example.com",
        "first_name": "Checkout",
        "last_name": "User Two",
        "full_name": "Checkout User Two",
        "mobile_no": "+918002000002",
    },
    {
        "email": "checkout.u3@example.com",
        "first_name": "Checkout",
        "last_name": "User Three",
        "full_name": "Checkout User Three",
        "mobile_no": "+918002000003",
    },
]

U1 = _USERS[0]["email"]
U2 = _USERS[1]["email"]
U3 = _USERS[2]["email"]

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
    """Re-initialise request-local state (also handles post-WSGI sessions)."""
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
        for so in frappe.get_all("Sales Order", filters={"customer": full}, pluck="name"):
            frappe.delete_doc("Sales Order", so, force=1, ignore_permissions=True)
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
_st: StockService | None = None


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


def _stock() -> StockService:
    global _st
    if _st is None:
        _st = StockService(_config())
    return _st


def _price(item_code: str) -> float:
    return float(_pricing().get_price(item_code)[0] or 0.0)


def _available(item_code: str) -> float:
    return float(_stock().get_available_qty(item_code) or 0.0)


def _add_first_address(email: str) -> str:
    """Create the user's first address (auto primary+shipping)."""
    _login(email)
    _restore()
    addr = CustomerService().create_address(
        address_type="Shipping",
        address_line1="1 Market Road",
        city="Mumbai",
        state="Maharashtra",
        country="India",
        pincode="400001",
    )
    return addr.name


# ---------------------------------------------------------------------- #
# Realm helpers for state-restoring tests
# ---------------------------------------------------------------------- #


def _with_disabled_item(item_code: str, fn: Callable[[], None]) -> None:
    frappe.db.commit()
    doc = frappe.get_doc("Item", item_code)
    doc.disabled = 1
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    try:
        fn()
    finally:
        frappe.db.commit()
        doc = frappe.get_doc("Item", item_code)
        doc.disabled = 0
        doc.save(ignore_permissions=True)
        frappe.db.commit()


def _with_stock(item_code: str, value: float, fn: Callable[[], None]) -> None:
    """Set every Bin row for ``item_code`` to ``value``, run ``fn``, restore."""
    frappe.db.commit()
    bins = frappe.db.sql(
        "SELECT name, actual_qty FROM `tabBin` WHERE item_code = %s",
        (item_code,),
        as_dict=True,
    )
    assert bins, f"no Bin rows for {item_code} — stock fixture expected"
    for b in bins:
        frappe.db.sql(
            "UPDATE `tabBin` SET actual_qty = %s WHERE name = %s",
            (value, b.name),
        )
    frappe.db.commit()
    try:
        fn()
    finally:
        frappe.db.commit()
        for b in bins:
            frappe.db.sql(
                "UPDATE `tabBin` SET actual_qty = %s WHERE name = %s",
                (b.actual_qty, b.name),
            )
        frappe.db.commit()


# ---------------------------------------------------------------------- #
# Validators
# ---------------------------------------------------------------------- #

from keemeds_commerce.validators import checkout_params  # noqa: E402


def test_optional_address_name_rules():
    assert checkout_params.optional_address_name({}, "shipping_address_name") is None
    assert checkout_params.optional_address_name(
        frappe._dict(shipping_address_name=None), "shipping_address_name"
    ) is None
    assert checkout_params.optional_address_name(
        frappe._dict(shipping_address_name="   "), "shipping_address_name"
    ) is None
    assert checkout_params.optional_address_name(
        frappe._dict(shipping_address_name="  ADDR-0011  "), "shipping_address_name"
    ) == "ADDR-0011"
    try:
        checkout_params.optional_address_name(
            frappe._dict(billing_address_name=123), "billing_address_name"
        )
        raise AssertionError("non-string address name must be rejected")
    except frappe.exceptions.ValidationError:
        pass


# ---------------------------------------------------------------------- #
# DTO contract
# ---------------------------------------------------------------------- #

from keemeds_commerce.domain.checkout import (  # noqa: E402
    CheckoutItemDTO,
    CheckoutOrderDTO,
    CheckoutSummaryDTO,
)


def test_checkout_dto_contract():
    item = CheckoutItemDTO(
        item_code="MED-001",
        item_name="Paracetamol",
        brand="Abbott",
        image="/files/item_images/MED-001-1.webp",
        quantity=2.0,
        selling_price=20.0,
        subtotal=40.0,
        stock_status="in_stock",
    )
    d = item.to_dict()
    for key in ("item_code", "item_name", "brand", "image", "quantity",
                "selling_price", "subtotal", "stock_status"):
        assert key in d, f"missing {key}"

    summary = CheckoutSummaryDTO(
        user_email="a@b.c",
        customer_id="Customer ABC",
        customer_name="ABC",
        currency="INR",
        items=[item],
        subtotal=40.0,
        discount=0.0,
        tax=0.0,
        shipping_charge=0.0,
        grand_total=40.0,
    )
    s = summary.to_dict()
    for key in ("user_email", "customer_id", "customer_name", "currency", "items",
                "subtotal", "discount", "tax", "shipping_charge", "grand_total",
                "shipping_address", "billing_address"):
        assert key in s, f"missing summary key {key}"
    assert s["shipping_address"] is None and s["billing_address"] is None

    order = CheckoutOrderDTO(sales_order="SAL-ORD-2026-00001", status="Draft", docstatus=0, grand_total=40.0)
    o = order.to_dict()
    assert all(k in o for k in ("sales_order", "status", "docstatus", "grand_total", "currency"))
    assert o["docstatus"] == 0 and o["status"] == "Draft"


# ---------------------------------------------------------------------- #
# Authorization
# ---------------------------------------------------------------------- #

def test_guest_rejected_on_services():
    _as_guest()
    from keemeds_commerce.services.checkout_service import CheckoutService

    svc = CheckoutService()
    for action in (
        lambda: svc.get_summary(),
        lambda: svc.validate(),
        lambda: svc.create_order(),
    ):
        try:
            action()
            raise AssertionError("guest must be rejected")
        except frappe.exceptions.ValidationError:
            pass


def test_guest_rejected_on_controllers():
    _restore()
    _as_guest()
    from keemeds_commerce.api import checkout as api

    frappe.local.form_dict = frappe._dict()
    for action in (api.summary, api.validate, api.create_order):
        try:
            action()
            raise AssertionError("guest controller call must be rejected")
        except frappe.exceptions.ValidationError:
            pass


def test_guest_rejected_over_http():
    client = get_test_client(use_cookies=True)
    _restore()

    r = client.get(
        _BASE + "/api/method/keemeds_commerce.api.checkout.summary",
        headers=_HDR_SITE,
    )
    assert r.status_code != 200, f"guest summary must not succeed: {r.get_json()}"
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.checkout.create_order",
        json={},
        headers=_HDR_SITE,
    )
    assert r.status_code != 200, f"guest create_order must not succeed: {r.get_json()}"
    _restore()


# ---------------------------------------------------------------------- #
# Validation states
# ---------------------------------------------------------------------- #

def test_empty_cart_rejected():
    _login(U1)
    CartService().clear_cart()
    from keemeds_commerce.api import checkout as api

    svc = CheckoutService()
    frappe.local.form_dict = frappe._dict()
    for action in (
        lambda: svc.get_summary(),
        lambda: svc.validate(),
        lambda: svc.create_order(),
        api.summary,
        api.validate,
        api.create_order,
    ):
        try:
            action()
            raise AssertionError("empty cart must be rejected")
        except frappe.exceptions.ValidationError as e:
            assert "empty" in str(e).lower(), str(e)


def test_default_shipping_address_required_and_created():
    _login(U2)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=2)
    svc = CheckoutService()
    try:
        svc.get_summary()
        raise AssertionError("missing default shipping address must be rejected")
    except frappe.exceptions.ValidationError as e:
        assert "default shipping address" in str(e), str(e)

    addr_name = _add_first_address(U2)
    s = svc.get_summary()
    assert s.shipping_address is not None and s.shipping_address.name == addr_name
    assert s.billing_address.name == addr_name, "billing falls back to shipping"


def test_explicit_addresses_respected():
    _login(U3)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    CustomerService().create_address(
        address_type="Shipping",
        address_line1="1 Main St",
        city="Delhi",
        country="India",
    )
    second = CustomerService().create_address(
        address_type="Billing",
        address_line1="2 Side St",
        city="Mumbai",
        country="India",
    )
    svc = CheckoutService()
    s = svc.get_summary(shipping_address_name=second.name, billing_address_name=second.name)
    assert s.shipping_address.name == second.name
    assert s.billing_address.name == second.name
    assert s.shipping_address.address_line1 == "2 Side St"


def test_foreign_or_missing_address_rejected():
    _login(U2)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    _add_first_address(U2)

    foreign_name = _add_first_address(U3)
    _login(U2)
    svc = CheckoutService()
    try:
        svc.get_summary(shipping_address_name=foreign_name)
        raise AssertionError("foreign address must be rejected")
    except frappe.exceptions.PermissionError:
        pass

    try:
        svc.get_summary(shipping_address_name="ADDR-NOT-REAL")
        raise AssertionError("missing address must be rejected")
    except frappe.exceptions.DoesNotExistError:
        pass


def test_disabled_item_rejected():
    _login(U3)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-004", quantity=1)
    svc = CheckoutService()

    def run():
        for action in (svc.get_summary, svc.validate, svc.create_order):
            try:
                action()
                raise AssertionError("disabled item must block checkout")
            except frappe.exceptions.ValidationError as e:
                assert "unavailable" in str(e), str(e)

    _add_first_address(U3)
    _with_disabled_item("MED-004", run)


def test_out_of_stock_rejected_atomically():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=2)
    _add_first_address(U1)
    svc = CheckoutService()

    so_count_before = frappe.db.sql("SELECT COUNT(*) FROM `tabSales Order`")[0][0]

    def run():
        for action in (svc.get_summary, svc.validate, svc.create_order):
            try:
                action()
                raise AssertionError("out-of-stock item must block checkout")
            except frappe.exceptions.ValidationError as e:
                assert "stock" in str(e).lower(), str(e)
        # cart untouched, no order leaked
        assert CartService().get_cart().total_items == 2.0
        so_count_after = frappe.db.sql("SELECT COUNT(*) FROM `tabSales Order`")[0][0]
        assert so_count_after == so_count_before, "no Sales Order may be created"

    _with_stock("MED-001", 0.0, run)


def test_quantity_exceeds_stock_rejected():
    _login(U2)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=5)
    _add_first_address(U2)
    svc = CheckoutService()

    def run():
        try:
            svc.validate()
            raise AssertionError("over-stock quantity must be rejected")
        except frappe.exceptions.ValidationError as e:
            assert "exceeds available stock" in str(e), str(e)

    # reduce available stock below the carted quantity (MED-001 carted 5)
    _with_stock("MED-001", 3.0, run)


# ---------------------------------------------------------------------- #
# Totals maths
# ---------------------------------------------------------------------- #

def _totals_cfg() -> CommerceConfig:
    return CommerceConfig(
        checkout_tax_rate=18.0,
        checkout_shipping_charge=50.0,
        checkout_discount_amount=10.0,
    )


def test_summary_totals_math():
    _login(U1)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=2)
    CartService().add_item(item_code="MED-002", quantity=1)
    _add_first_address(U1)

    p1, p2 = _price("MED-001"), _price("MED-002")
    subtotal = round(p1 * 2 + p2, 2)
    svc = CheckoutService(config=_totals_cfg())
    s = svc.validate()
    assert s.subtotal == subtotal, (s.subtotal, subtotal)
    assert s.discount == 10.0
    taxable = round(subtotal - 10.0, 2)
    assert s.tax == round(taxable * 18.0 / 100.0, 2)
    assert s.shipping_charge == 50.0
    assert s.grand_total == round(taxable + s.tax + 50.0, 2)
    assert s.currency == "INR"
    assert all(item.stock_status == "in_stock" for item in s.items)
    assert len(s.items) == 2


# ---------------------------------------------------------------------- #
# Order creation
# ---------------------------------------------------------------------- #

def test_create_order_draft_and_parity():
    _login(U1)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=2)
    CartService().add_item(item_code="MED-002", quantity=1)
    _add_first_address(U1)

    cfg = _totals_cfg()
    svc = CheckoutService(config=cfg)
    s = svc.validate()
    o = svc.create_order()
    orders_owned = frappe.db.sql(
        """SELECT COUNT(*) FROM `tabSales Order` WHERE customer = %s""",
        (s.customer_id,),
    )[0][0]
    assert orders_owned >= 1
    doc = frappe.get_doc("Sales Order", o.sales_order)
    assert o.docstatus == 0
    assert o.status == "Draft"
    assert o.grand_total == s.grand_total, (o.grand_total, s.grand_total)
    assert doc.net_total == round(s.subtotal - s.discount, 2)
    assert doc.grand_total == s.grand_total
    assert doc.currency == "INR"
    assert doc.customer == s.customer_id

    # lines persisted with the summary's quantities & rates
    persisted = {row.item_code: row for row in doc.items}
    for item in s.items:
        row = persisted[item.item_code]
        assert float(row.qty) == item.quantity
        assert float(row.rate) == item.selling_price

    # addresses persisted on the draft
    assert doc.shipping_address_name == s.shipping_address.name
    assert doc.customer_address == s.billing_address.name

    # tax + shipping rows persisted
    types = {t.charge_type for t in doc.taxes}
    assert "On Net Total" in types and "Actual" in types, types

    # order never submits, clears the cart or moves stock
    assert doc.docstatus == 0
    assert CartService().get_cart().total_items == 3.0


def test_create_order_default_totals_zero_config():
    _login(U3)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=1)
    _add_first_address(U3)
    svc = CheckoutService()
    s = svc.validate()
    assert s.discount == 0.0 and s.tax == 0.0 and s.shipping_charge == 0.0
    assert s.grand_total == s.subtotal
    o = svc.create_order()
    doc = frappe.get_doc("Sales Order", o.sales_order)
    assert doc.grand_total == s.subtotal
    assert not doc.taxes, "no tax rows expected with zero config"


def test_order_owned_by_session_customer():
    _login(U2)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=1)
    _add_first_address(U2)
    svc = CheckoutService()
    s = svc.validate()
    o = svc.create_order()
    assert s.customer_id != _registered[U1], "customers must differ"
    doc = frappe.get_doc("Sales Order", o.sales_order)
    assert doc.customer == _registered[U2]
    assert doc.customer == s.customer_id


def _draft_count(customer: str) -> int:
    return frappe.db.sql(
        "SELECT COUNT(*) FROM `tabSales Order` WHERE customer = %s AND docstatus = 0",
        (customer,),
    )[0][0]


def test_duplicate_create_order_replays_single_draft():
    _login(U3)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=2)
    _add_first_address(U3)
    svc = CheckoutService()
    customer = _registered[U3]

    before = _draft_count(customer)
    first = svc.create_order()
    assert _draft_count(customer) == before + 1
    second = svc.create_order()
    assert second.sales_order == first.sales_order, "double Place Order must replay"
    assert _draft_count(customer) == before + 1, "no extra Draft on replay"

    # A genuine change of order content must create a new Draft
    cs.update_item(item_code="MED-001", quantity=4)
    third = svc.create_order()
    assert third.sales_order != first.sales_order
    assert _draft_count(customer) == before + 2

    # Disabling the guard (zero window) always creates a new Draft
    svc_no_window = CheckoutService(config=CommerceConfig(checkout_duplicate_window_minutes=0))
    fourth = svc_no_window.create_order()
    assert fourth.sales_order != third.sales_order
    assert _draft_count(customer) == before + 3


def test_duplicate_create_order_does_not_replay_across_customers():
    _login(U1)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=2)
    _add_first_address(U1)
    o = CheckoutService().create_order()
    # Identical content for a different customer: must be a separate Draft
    _login(U2)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=2)
    _add_first_address(U2)
    o2 = CheckoutService().create_order()
    assert o.sales_order != o2.sales_order
    assert frappe.get_value("Sales Order", o.sales_order, "customer") == _registered[U1]
    assert frappe.get_value("Sales Order", o2.sales_order, "customer") == _registered[U2]


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


def test_http_checkout_routes():
    _login(U3)
    CartService().clear_cart()
    CartService().add_item(item_code="MED-001", quantity=2)
    _add_first_address(U3)

    client = get_test_client(use_cookies=True)
    _wsgi_login(client, U3)
    _restore()

    r = client.get(
        _BASE + "/api/method/keemeds_commerce.api.checkout.summary",
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    data = body["data"]
    assert data["subtotal"] == round(_price("MED-001") * 2, 2)
    assert data["grand_total"] == data["subtotal"]
    assert data["shipping_address"] is not None
    assert data["items"][0]["item_code"] == "MED-001"
    summary_grand = data["grand_total"]
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.checkout.validate",
        json={},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["items"], "validate must report cart lines"
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.checkout.create_order",
        json={},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    order = body["data"]
    assert order["docstatus"] == 0 and order["status"] == "Draft"
    assert frappe.db.exists("Sales Order", order["sales_order"])
    assert order["grand_total"] == summary_grand
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
    svc = CheckoutService()
    cart = CartService()
    _performance["checkout.summary"] = _time_per_call(25, svc.get_summary)
    _performance["checkout.validate"] = _time_per_call(25, svc.validate)
    cart.clear_cart()
    cart.add_item(item_code="MED-001", quantity=1)

    # Vary the cart between calls so each create_order builds a new Draft
    # (identical payloads replay the previous Draft by design).
    def timed_create(i: int) -> float:
        cart.update_item(item_code="MED-001", quantity=(i % 4) + 1)
        start = time.perf_counter()
        svc.create_order()
        return time.perf_counter() - start

    times = [timed_create(i) for i in range(4)]
    _performance["checkout.create_order"] = round(sum(times) / len(times) * 1000.0, 3)
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

    print("=== CHECKOUT (SUMMARY / VALIDATE / DRAFT SALES ORDER) ===")

    print("-- validators")
    _test("optional address-name cleaning rules", test_optional_address_name_rules)

    print("-- DTO contract")
    _test("checkout DTO serialization contract", test_checkout_dto_contract)

    print("-- authorization")
    _test("guest rejected on every checkout service operation", test_guest_rejected_on_services)
    _test("guest rejected on every checkout controller", test_guest_rejected_on_controllers)
    _test("guest rejected over HTTP/WSGI", test_guest_rejected_over_http)

    print("-- validation states")
    _test("empty cart rejected everywhere", test_empty_cart_rejected)
    _test("default shipping address required, then used", test_default_shipping_address_required_and_created)
    _test("explicit shipping/billing addresses respected", test_explicit_addresses_respected)
    _test("foreign or missing address rejected", test_foreign_or_missing_address_rejected)
    _test("disabled item rejected", test_disabled_item_rejected)
    _test("out-of-stock item rejected atomically", test_out_of_stock_rejected_atomically)
    _test("quantity above stock rejected", test_quantity_exceeds_stock_rejected)

    print("-- totals maths")
    _test("summary totals with tax/shipping/discount", test_summary_totals_math)

    print("-- order creation")
    _test("draft order created with ERPNext parity", test_create_order_draft_and_parity)
    _test("zero-config order totals match summary", test_create_order_default_totals_zero_config)
    _test("draft order owned by session customer", test_order_owned_by_session_customer)
    _test("duplicate Place Order replays a single Draft", test_duplicate_create_order_replays_single_draft)
    _test("identical carts never replay across customers", test_duplicate_create_order_does_not_replay_across_customers)

    print("-- HTTP/WSGI end-to-end")
    _test("checkout routes over HTTP (GET/POST/POST)", test_http_checkout_routes)

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