"""
Phase 17 — ERP-Backed Cart & Wishlist API Layer

Regression guard for the production-ready Cart and Wishlist endpoints backed by
ERPNext DocTypes (:doc:`Cart`/:doc:`Cart Item`, :doc:`Wishlist`/:doc:`Wishlist Item`).

Coverage
--------
Validators:
- ``item_code`` required / cleaned, ``quantity`` > 0, finite, non-numeric/zero/
  negative/bool/blank rejections.

Domain DTOs:
- Cart item contract (``item_code``, ``quantity``, ``selling_price``,
  ``item_name``, ``brand``, ``image``, ``stock_status``, ``subtotal``) and cart
  envelope (``items``, ``total_items``, ``subtotal``, ``grand_total``).

Authorization & ownership:
- Guest sessions (service + controller) are rejected on every cart and wishlist
  operation.
- Each Website User owns exactly one Cart / Wishlist document; no cross-user
  visibility, no shared documents.

Cart flows (service + controller + HTTP/WSGI):
- add (new), add (merge), update, remove (idempotent), clear (idempotent),
  empty cart totals, per-line and cart-level totals.

Stock & item validation (all writes atomic, failures leave the cart intact):
- missing item, disabled item, out-of-stock item, quantity > available stock,
  quantity 0 / negative, update of a non-cart item.

Wishlist flows:
- add, duplicate add (no-op prevents duplicates), remove (idempotent), missing
  item rejection, guest rejection.

HTTP/WSGI end-to-end (exact storefront pattern: cookie session, JSON body):
- all five cart routes (GET/POST/PUT/DELETE/DELETE) and all three wishlist
  routes (GET/POST/DELETE) through ``/api/method/...``.

Performance:
- average per-call latency for each cart and wishlist operation (recorded in
  ``results["performance"]``, printed, no hard threshold).

Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase17_cart_wishlist.run
"""

from __future__ import annotations

import time
import traceback as tb
from typing import Any, Callable

import frappe
from frappe.auth import CookieManager, LoginManager
from frappe.utils import get_test_client, set_request

from keemeds_commerce.services.auth_service import AuthService
from keemeds_commerce.services.cart_service import CartService
from keemeds_commerce.services.pricing_service import PricingService
from keemeds_commerce.services.stock_service import StockService
from keemeds_commerce.services.wishlist_service import WishlistService

_SITE = "keemeds-commerce.local"
_BASE = f"http://{_SITE}"
_HDR_SITE = {"X-Frappe-Site-Name": _SITE}

_PASSWORD = "TestPass123!"

_USERS = [
    {
        "email": "cart.u1@example.com",
        "first_name": "Cart",
        "last_name": "User One",
        "full_name": "Cart User One",
        "mobile_no": "+918001000001",
    },
    {
        "email": "cart.u2@example.com",
        "first_name": "Cart",
        "last_name": "User Two",
        "full_name": "Cart User Two",
        "mobile_no": "+918001000002",
    },
    {
        "email": "cart.u3@example.com",
        "first_name": "Cart",
        "last_name": "User Three",
        "full_name": "Cart User Three",
        "mobile_no": "+918001000003",
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
        from keemeds_commerce.config.commerce_config import CommerceConfig

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


# ---------------------------------------------------------------------- #
# Realm helpers for state-restoring tests
# ---------------------------------------------------------------------- #


def _with_disabled_item(item_code: str, fn: Callable[[], None]) -> None:
    """Temporarily disable an Item, run ``fn``, always re-enable it."""
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


def _with_zeroed_stock(item_code: str, fn: Callable[[], None]) -> None:
    """Zero every Bin row for ``item_code``, run ``fn``, always restore."""
    frappe.db.commit()
    bins = frappe.db.sql(
        "SELECT name, actual_qty FROM `tabBin` WHERE item_code = %s",
        (item_code,),
        as_dict=True,
    )
    assert bins, f"no Bin rows for {item_code} — stock fixture expected"
    for b in bins:
        frappe.db.sql("UPDATE `tabBin` SET actual_qty = 0 WHERE name = %s", (b.name,))
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

from keemeds_commerce.validators import cart_params  # noqa: E402


def test_require_item_code_cleans_and_requires():
    assert cart_params.require_item_code(frappe._dict(item_code="  MED-001  ")) == "MED-001"
    for bad in (None, "", "   "):
        try:
            cart_params.require_item_code(frappe._dict(item_code=bad))
            raise AssertionError(f"expected rejection for {bad!r}")
        except frappe.exceptions.ValidationError:
            pass


def test_require_quantity_rules():
    assert cart_params.require_quantity(2) == 2.0
    assert cart_params.require_quantity("2.5") == 2.5
    assert cart_params.require_quantity(0.5) == 0.5
    for bad in (None, "", 0, -1, -0.1, "abc", float("nan"), float("inf"), float("-inf"), True):
        try:
            cart_params.require_quantity(bad)
            raise AssertionError(f"expected rejection for {bad!r}")
        except frappe.exceptions.ValidationError:
            pass


# ---------------------------------------------------------------------- #
# DTO contract
# ---------------------------------------------------------------------- #

from keemeds_commerce.domain.cart import CartDTO, CartItemDTO, WishlistDTO  # noqa: E402


def test_cart_dto_contract():
    item = CartItemDTO(
        item_code="MED-001",
        quantity=2.0,
        selling_price=20.0,
        item_name="Paracetamol",
        brand="Abbott",
        image="http://localhost:8000/files/x.webp",
        stock_status="in_stock",
        subtotal=40.0,
    )
    d = item.to_dict()
    assert set(d) == {
        "item_code",
        "quantity",
        "selling_price",
        "item_name",
        "brand",
        "image",
        "stock_status",
        "subtotal",
    }
    cart = CartDTO(items=[item], total_items=2.0, subtotal=40.0, grand_total=40.0)
    c = cart.to_dict()
    assert list(c) == ["items", "total_items", "subtotal", "grand_total"]
    empty = CartDTO().to_dict()
    assert empty == {"items": [], "total_items": 0.0, "subtotal": 0.0, "grand_total": 0.0}
    w = WishlistDTO().to_dict()
    assert w == {"items": [], "total_items": 0}


# ---------------------------------------------------------------------- #
# Authorization
# ---------------------------------------------------------------------- #

from keemeds_commerce.api import cart as cart_api  # noqa: E402
from keemeds_commerce.api import wishlist as wishlist_api  # noqa: E402


def test_guest_rejected_everywhere():
    _as_guest()
    cs = CartService()
    ws = WishlistService()
    for op in (
        lambda: cs.get_cart(),
        lambda: cs.add_item(item_code="MED-001", quantity=1),
        lambda: cs.update_item(item_code="MED-001", quantity=1),
        lambda: cs.remove_item(item_code="MED-001"),
        lambda: cs.clear_cart(),
        lambda: ws.get_wishlist(),
        lambda: ws.add_item(item_code="MED-001"),
        lambda: ws.remove_item(item_code="MED-001"),
        lambda: cart_api.get_cart(),
        lambda: wishlist_api.get_wishlist(),
        lambda: cart_api.add_item(),
        lambda: wishlist_api.add_item(),
    ):
        try:
            op()
            raise AssertionError("guest must be rejected")
        except frappe.exceptions.ValidationError:
            pass


# ---------------------------------------------------------------------- #
# Cart service flows
# ---------------------------------------------------------------------- #


def test_cart_empty_totals():
    _login(U1)
    CartService().clear_cart()
    cart = CartService().get_cart().to_dict()
    assert cart == {"items": [], "total_items": 0.0, "subtotal": 0.0, "grand_total": 0.0}


def test_cart_add_and_enrichment():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    code = "MED-001"
    qty = 2.0
    price = _price(code)
    cart = cs.add_item(item_code=code, quantity=qty).to_dict()
    items = cart["items"]
    assert len(items) == 1
    row = items[0]
    assert row["item_code"] == code
    assert row["quantity"] == qty
    assert row["selling_price"] == price and price > 0
    assert row["item_name"], "item_name must be resolved"
    assert row["brand"], "brand must be resolved"
    assert row["image"].startswith("http"), "image must be an absolute URL"
    assert row["stock_status"] == "in_stock"
    assert row["subtotal"] == round(qty * price, 2)
    assert cart["total_items"] == qty
    assert cart["subtotal"] == row["subtotal"]
    assert cart["grand_total"] == cart["subtotal"]


def test_cart_add_merges_duplicate():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    code = "MED-001"
    cs.add_item(item_code=code, quantity=1)
    cart = cs.add_item(item_code=code, quantity=1).to_dict()
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 2.0, f"got {cart['items'][0]['quantity']}"
    assert cart["total_items"] == 2.0
    assert cart["items"][0]["subtotal"] == round(2.0 * _price(code), 2)


def test_cart_multi_item_totals():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=2)
    cs.add_item(item_code="MED-002", quantity=1)
    cart = cs.get_cart().to_dict()
    assert len(cart["items"]) == 2
    expected = round(2.0 * _price("MED-001") + 1.0 * _price("MED-002"), 2)
    assert cart["total_items"] == 3.0
    assert cart["subtotal"] == expected
    assert cart["grand_total"] == expected
    codes = {i["item_code"] for i in cart["items"]}
    assert codes == {"MED-001", "MED-002"}


def test_cart_update_quantity():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=3)
    cart = cs.update_item(item_code="MED-001", quantity=1).to_dict()
    assert cart["items"][0]["quantity"] == 1.0
    assert cart["total_items"] == 1.0
    assert cart["items"][0]["subtotal"] == round(_price("MED-001"), 2)


def test_cart_update_missing_item_rejected():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-002", quantity=1)
    try:
        cs.update_item(item_code="MED-099", quantity=1)
        raise AssertionError("update of a non-cart item must be rejected")
    except frappe.exceptions.ValidationError:
        pass


def test_cart_remove_and_idempotent():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=2)
    cs.add_item(item_code="MED-002", quantity=1)
    cart = cs.remove_item(item_code="MED-001").to_dict()
    assert len(cart["items"]) == 1 and cart["items"][0]["item_code"] == "MED-002"
    again = cs.remove_item(item_code="MED-001").to_dict()
    assert again["items"] == cart["items"]
    cart2 = cs.remove_item(item_code="MED-002").to_dict()
    assert cart2 == {"items": [], "total_items": 0.0, "subtotal": 0.0, "grand_total": 0.0}


def test_cart_clear_idempotent():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    cs.add_item(item_code="MED-002", quantity=2)
    empty1 = cs.clear_cart().to_dict()
    empty2 = cs.clear_cart().to_dict()
    for empty in (empty1, empty2):
        assert empty == {"items": [], "total_items": 0.0, "subtotal": 0.0, "grand_total": 0.0}


# ---------------------------------------------------------------------- #
# Item & stock validation (atomicity)
# ---------------------------------------------------------------------- #


def test_cart_missing_item_rejected_and_atomic():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    before = cs.get_cart().to_dict()
    try:
        cs.add_item(item_code="MED-9999", quantity=1)
        raise AssertionError("missing item must be rejected")
    except (frappe.exceptions.ValidationError, frappe.exceptions.DoesNotExistError):
        pass
    assert cs.get_cart().to_dict() == before, "failed add must not mutate the cart"


def test_cart_disabled_item_rejected_and_atomic():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    before = cs.get_cart().to_dict()

    def attempt():
        try:
            cs.add_item(item_code="MED-004", quantity=1)
            raise AssertionError("disabled item must be rejected")
        except frappe.exceptions.ValidationError:
            pass
        assert cs.get_cart().to_dict() == before

    _with_disabled_item("MED-004", attempt)


def test_cart_out_of_stock_rejected_and_atomic():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    before = cs.get_cart().to_dict()

    def attempt():
        try:
            cs.add_item(item_code="MED-005", quantity=1)
            raise AssertionError("out-of-stock item must be rejected")
        except frappe.exceptions.ValidationError as e:
            assert "out of stock" in str(e)
        assert cs.get_cart().to_dict() == before

    _with_zeroed_stock("MED-005", attempt)


def test_cart_quantity_exceeds_stock_rejected_and_atomic():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    cs.add_item(item_code="MED-001", quantity=1)
    before = cs.get_cart().to_dict()
    available = _available("MED-001")
    try:
        cs.add_item(item_code="MED-001", quantity=available + 5)
        raise AssertionError("quantity above stock must be rejected")
    except frappe.exceptions.ValidationError as e:
        assert "exceeds available stock" in str(e)
    assert cs.get_cart().to_dict() == before, "failed add must not mutate the cart"


def test_cart_zero_and_negative_quantity_rejected():
    _login(U1)
    cs = CartService()
    cs.clear_cart()
    for qty in (0, -1, -0.5):
        try:
            cs.add_item(item_code="MED-002", quantity=qty)
            raise AssertionError(f"quantity {qty} must be rejected")
        except frappe.exceptions.ValidationError:
            pass
    assert cs.get_cart().to_dict() == {
        "items": [],
        "total_items": 0.0,
        "subtotal": 0.0,
        "grand_total": 0.0,
    }


# ---------------------------------------------------------------------- #
# Ownership
# ---------------------------------------------------------------------- #


def test_ownership_one_doc_per_user():
    _login(U1)
    cs1 = CartService()
    cs1.clear_cart()
    cs1.add_item(item_code="MED-001", quantity=1)
    _login(U2)
    cs2 = CartService()
    cs2.clear_cart()
    cs2.add_item(item_code="MED-010", quantity=2)
    _as_admin()
    carts = frappe.get_all("Cart", filters={"user": ["in", (U1, U2)]}, fields=["name", "user"])
    carts_u1 = [c["name"] for c in carts if c["user"] == U1]
    carts_u2 = [c["name"] for c in carts if c["user"] == U2]
    assert len(carts_u1) == 1, "exactly one Cart document per user"
    assert len(carts_u2) == 1, "exactly one Cart document per user"
    assert carts_u1 != carts_u2, "cart documents must be distinct per user"


def test_ownership_no_cross_user_visibility():
    _login(U1)
    cs1 = CartService()
    cs1.clear_cart()
    cs1.add_item(item_code="MED-020", quantity=1)
    items1 = {i["item_code"] for i in cs1.get_cart().to_dict()["items"]}
    _login(U2)
    cs2 = CartService()
    items2 = {i["item_code"] for i in cs2.get_cart().to_dict()["items"]}
    assert "MED-020" in items1
    assert "MED-020" not in items2, "U2 must not see U1's cart items"
    assert items2 == {"MED-010"}, f"U2 sees only their own items, got {items2}"
    captured = frappe.db.get_value("Cart", {"user": U1}, "user")
    assert captured == U1


# ---------------------------------------------------------------------- #
# Controllers (direct)
# ---------------------------------------------------------------------- #


def _form(**kwargs) -> None:
    frappe.local.form_dict = frappe._dict(kwargs)


def test_api_cart_all_endpoints():
    _login(U1)
    CartService().clear_cart()
    _form()
    r = cart_api.get_cart()
    assert r["success"] is True and "data" in r
    assert set(r["data"]) == {"items", "total_items", "subtotal", "grand_total"}

    _form(item_code="MED-001", quantity=2)
    r = cart_api.add_item()
    assert r["success"] is True
    assert r["data"]["items"][0]["quantity"] == 2.0

    _form(item_code="MED-001", quantity=1)
    r = cart_api.add_item()
    assert r["data"]["items"][0]["quantity"] == 3.0

    _form(item_code="MED-002", quantity=1)
    cart_api.add_item()

    _form(item_code="MED-002", quantity=4)
    r = cart_api.update_item()
    rows = {i["item_code"]: i for i in r["data"]["items"]}
    assert rows["MED-002"]["quantity"] == 4.0

    _form(item_code="MED-002")
    r = cart_api.remove_item()
    assert {i["item_code"] for i in r["data"]["items"]} == {"MED-001"}

    _form()
    r = cart_api.clear_cart()
    assert r["data"]["items"] == []


def test_api_wishlist_all_endpoints():
    _login(U1)
    ws = WishlistService()
    for code in ("MED-001", "MED-002"):
        ws.remove_item(code)
    _form()
    r = wishlist_api.get_wishlist()
    assert r["success"] is True
    assert set(r["data"]) == {"items", "total_items"}

    _form(item_code="MED-001")
    r = wishlist_api.add_item()
    assert r["data"]["total_items"] == 1

    _form(item_code="MED-001")
    r = wishlist_api.add_item()
    assert r["data"]["total_items"] == 1, "duplicate add must not grow the wishlist"

    _form(item_code="MED-002")
    r = wishlist_api.add_item()
    assert r["data"]["total_items"] == 2

    _form(item_code="MED-001")
    r = wishlist_api.remove_item()
    assert {i["item_code"] for i in r["data"]["items"]} == {"MED-002"}


def test_api_controllers_validate_params():
    _login(U1)
    try:
        _form()
        cart_api.add_item()
        raise AssertionError("missing item_code must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    try:
        _form(item_code="MED-001", quantity=0)
        cart_api.add_item()
        raise AssertionError("zero quantity must be rejected")
    except frappe.exceptions.ValidationError:
        pass
    try:
        _form()
        wishlist_api.add_item()
        raise AssertionError("missing item_code must be rejected")
    except frappe.exceptions.ValidationError:
        pass


# ---------------------------------------------------------------------- #
# Wishlist service flows
# ---------------------------------------------------------------------- #


def test_wishlist_dedupe_remove_missing_item():
    _login(U2)
    ws = WishlistService()
    ws.remove_item("MED-001")
    ws.add_item(item_code="MED-001")
    ws.add_item(item_code="MED-002")
    w1 = ws.get_wishlist().to_dict()
    assert w1["total_items"] == 2
    ws.add_item(item_code="MED-001")
    assert ws.get_wishlist().to_dict() == w1, "duplicate add must be a no-op"
    w2 = ws.remove_item(item_code="MED-001").to_dict()
    assert {i["item_code"] for i in w2["items"]} == {"MED-002"}
    assert ws.remove_item(item_code="MED-001").to_dict() == w2, "remove must be idempotent"


def test_wishlist_missing_item_rejected():
    _login(U2)
    ws = WishlistService()
    try:
        ws.add_item(item_code="MED-9999")
        raise AssertionError("missing item must be rejected")
    except (frappe.exceptions.ValidationError, frappe.exceptions.DoesNotExistError):
        pass


# ---------------------------------------------------------------------- #
# HTTP/WSGI end-to-end (storefront pattern)
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


def test_http_cart_routes():
    client = get_test_client(use_cookies=True)
    _wsgi_login(client, U3)
    _restore()

    r = client.get(_BASE + "/api/method/keemeds_commerce.api.cart.get_cart", headers=_HDR_SITE)
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["items"] == [] and body["data"]["total_items"] == 0.0
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.cart.add_item",
        json={"item_code": "MED-001", "quantity": 2},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["items"][0]["quantity"] == 2.0
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.cart.add_item",
        json={"item_code": "MED-001", "quantity": 1},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["data"]["items"][0]["quantity"] == 3.0, "HTTP add must merge"
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.cart.add_item",
        json={"item_code": "MED-002", "quantity": 1},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert len(body["data"]["items"]) == 2
    assert body["data"]["total_items"] == 4.0
    _restore()

    r = client.put(
        _BASE + "/api/method/keemeds_commerce.api.cart.update_item",
        json={"item_code": "MED-001", "quantity": 1},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["success"] is True and body["data"]["items"][0]["quantity"] == 1.0
    _restore()

    r = client.delete(
        _BASE + "/api/method/keemeds_commerce.api.cart.remove_item",
        data={"item_code": "MED-002"},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["success"] is True
    assert {i["item_code"] for i in body["data"]["items"]} == {"MED-001"}
    _restore()

    r = client.delete(
        _BASE + "/api/method/keemeds_commerce.api.cart.clear_cart",
        data={},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["success"] is True and body["data"]["items"] == []
    _restore()


def test_http_wishlist_routes():
    client = get_test_client(use_cookies=True)
    _wsgi_login(client, U3)
    _restore()

    r = client.get(
        _BASE + "/api/method/keemeds_commerce.api.wishlist.get_wishlist",
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert r.status_code == 200 and body["success"] is True
    assert body["data"]["items"] == []
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.wishlist.add_item",
        json={"item_code": "MED-001"},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["success"] is True and body["data"]["total_items"] == 1
    _restore()

    r = client.post(
        _BASE + "/api/method/keemeds_commerce.api.wishlist.add_item",
        json={"item_code": "MED-001"},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["data"]["total_items"] == 1, "HTTP duplicate add must be a no-op"
    _restore()

    r = client.delete(
        _BASE + "/api/method/keemeds_commerce.api.wishlist.remove_item",
        data={"item_code": "MED-001"},
        headers=_HDR_SITE,
    )
    body = (r.get_json() or {})["message"]
    assert body["success"] is True and body["data"]["items"] == []
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
    cs = CartService()
    ws = WishlistService()
    cs.clear_cart()
    ws.remove_item("MED-001")
    # MED-001 on-hand is 10, so the accumulating add loop must not exceed it
    # (quantity 1 per iteration against MED-001 stock 10).
    n = 10
    _performance["cart.add_item"] = _time_per_call(n, lambda: cs.add_item(item_code="MED-001", quantity=1))
    n = 25
    _performance["cart.get_cart"] = _time_per_call(n, cs.get_cart)
    _performance["cart.update_item"] = _time_per_call(
        n, lambda: cs.update_item(item_code="MED-001", quantity=1)
    )
    _performance["cart.remove_item"] = _time_per_call(n, lambda: cs.remove_item(item_code="MED-001"))
    _performance["cart.clear_cart"] = _time_per_call(n, cs.clear_cart)
    _performance["wishlist.add_item"] = _time_per_call(n, lambda: ws.add_item(item_code="MED-001"))
    _performance["wishlist.get_wishlist"] = _time_per_call(n, ws.get_wishlist)
    _performance["wishlist.remove_item"] = _time_per_call(n, lambda: ws.remove_item(item_code="MED-001"))
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

    print("=== CART & WISHLIST (ERP-BACKED) ===")

    print("-- validators")
    _test("item_code required + trimmed", test_require_item_code_cleans_and_requires)
    _test("quantity positive/finite/typed enforcement", test_require_quantity_rules)

    print("-- DTO contract")
    _test("cart/wishlist DTO serialization contract", test_cart_dto_contract)

    print("-- authorization")
    _test("guest rejected on every cart/wishlist operation", test_guest_rejected_everywhere)

    print("-- cart service")
    _test("empty cart totals", test_cart_empty_totals)
    _test("add item + enrichment", test_cart_add_and_enrichment)
    _test("add merges duplicate lines", test_cart_add_merges_duplicate)
    _test("multi-item totals", test_cart_multi_item_totals)
    _test("update quantity", test_cart_update_quantity)
    _test("update of non-cart item rejected", test_cart_update_missing_item_rejected)
    _test("remove + idempotent", test_cart_remove_and_idempotent)
    _test("clear idempotent", test_cart_clear_idempotent)

    print("-- item & stock validation (atomic)")
    _test("missing item rejected atomically", test_cart_missing_item_rejected_and_atomic)
    _test("disabled item rejected atomically", test_cart_disabled_item_rejected_and_atomic)
    _test("out-of-stock item rejected atomically", test_cart_out_of_stock_rejected_and_atomic)
    _test("quantity above stock rejected atomically", test_cart_quantity_exceeds_stock_rejected_and_atomic)
    _test("zero/negative quantity rejected", test_cart_zero_and_negative_quantity_rejected)

    print("-- ownership")
    _test("one Cart document per user", test_ownership_one_doc_per_user)
    _test("no cross-user visibility", test_ownership_no_cross_user_visibility)

    print("-- controllers")
    _test("cart API endpoints", test_api_cart_all_endpoints)
    _test("wishlist API endpoints", test_api_wishlist_all_endpoints)
    _test("controller parameter validation", test_api_controllers_validate_params)

    print("-- wishlist service")
    _test("dedupe + idempotent remove", test_wishlist_dedupe_remove_missing_item)
    _test("missing item rejected", test_wishlist_missing_item_rejected)

    print("-- HTTP/WSGI end-to-end")
    _test("cart routes over HTTP (GET/POST/PUT/DELETE/DELETE)", test_http_cart_routes)
    _test("wishlist routes over HTTP (GET/POST/DELETE)", test_http_wishlist_routes)

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