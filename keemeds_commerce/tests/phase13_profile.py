"""
Phase 13 Validation — Customer Profile & Address APIs

End-to-end validation through the actual Frappe request lifecycle.
Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase13_profile.run
"""

from __future__ import annotations

import json
import time

import frappe
from frappe.auth import CookieManager, LoginManager

from keemeds_commerce.services.auth_service import AuthService
from keemeds_commerce.services.customer_service import CustomerService

_TEST_EMAIL = "test.phase13@example.com"
_TEST_PHONE = "+919876543210"
_TEST_PASSWORD = "TestPass123!"
_TEST_FULL_NAME = "Phase Thirteen User"

_auth_svc: AuthService | None = None
_cust_svc: CustomerService | None = None
_created_address_names: list[str] = []


def _get_auth() -> AuthService:
    global _auth_svc
    if _auth_svc is None:
        _auth_svc = AuthService()
    return _auth_svc


def _get_cust() -> CustomerService:
    global _cust_svc
    if _cust_svc is None:
        _cust_svc = CustomerService()
    return _cust_svc


def _setup():
    from frappe.utils import set_request

    set_request(path="/")
    if not getattr(frappe.local, "cookie_manager", None):
        frappe.local.cookie_manager = CookieManager()
    if not getattr(frappe.local, "login_manager", None):
        frappe.local.login_manager = LoginManager()
    frappe.local.form_dict = frappe._dict()


def _as_guest():
    frappe.session.user = "Guest"


def _as_admin():
    frappe.db.commit()
    frappe.local.user_perms = None
    frappe.local.login_manager.login_as("Administrator")
    frappe.db.commit()


def _as_test_user():
    _get_auth().login(email=_TEST_EMAIL, password=_TEST_PASSWORD)


def _purge_once(emails, names):
    """Remove users, customers, contacts and all child rows for test emails/names."""
    for email in emails:
        for c in frappe.get_all("Contact", filters={"email_id": email}, pluck="name"):
            for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
            for ce in frappe.get_all("Contact Email", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabContact Email` WHERE name = %s", (ce,))
            for cp in frappe.get_all("Contact Phone", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabContact Phone` WHERE name = %s", (cp,))
            frappe.db.sql("DELETE FROM `tabContact` WHERE name = %s", (c,))
        for pu in frappe.get_all("Portal User", filters={"user": email}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
        if frappe.db.exists("User", email):
            frappe.db.sql("DELETE FROM `tabUser` WHERE name = %s", (email,))
        frappe.db.commit()
    for name in names:
        for dl in frappe.get_all("Dynamic Link", filters={"link_name": name}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
        for c in frappe.get_all("Customer", filters={"customer_name": name}, pluck="name"):
            for pu in frappe.get_all("Portal User", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
            for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
            frappe.db.sql("DELETE FROM `tabCustomer` WHERE name = %s", (c,))
        frappe.db.commit()


def _purge(emails, names):
    """Idempotent cleanup with a retry: MariaDB intermittently raises 1020
    (``QueryDeadlockError``) on deletes from long-lived test sessions even on
    single-row statements. The purge re-lists rows so it is safe to rerun."""
    for _ in range(2):
        try:
            _purge_once(emails, names)
            return
        except frappe.exceptions.QueryDeadlockError:
            frappe.db.rollback()
            frappe.db.commit()
    _purge_once(emails, names)


def _cleanup_all():
    """Remove all test data (incl. orphaned child rows)."""
    _purge(
        ["test.phase13@example.com", "perf.test.p13@example.com"],
        ["Phase Thirteen User", "Perf P13 User"],
    )


def run():
    results: dict = {"passed": 0, "failed": 0, "errors": [], "performance": {}, "samples": {}}

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
    # 1. REGISTER + LOGIN + PROFILE
    # ================================================================== #
    print("\n=== REGISTER → LOGIN → PROFILE ===")

    def test_register():
        _as_admin()
        _get_auth().register(
            email=_TEST_EMAIL,
            first_name="Phase",
            last_name="Thirteen User",
            full_name=_TEST_FULL_NAME,
            mobile_no=_TEST_PHONE,
            password=_TEST_PASSWORD,
        )
        assert frappe.db.exists("User", _TEST_EMAIL)
    _test("Register creates user", test_register)

    def test_login():
        _as_guest()
        _get_auth().login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        assert frappe.session.user == _TEST_EMAIL
    _test("Login authenticates user", test_login)

    def test_get_profile():
        _as_test_user()
        profile = _get_cust().get_profile()
        assert profile.email == _TEST_EMAIL
        assert profile.customer_name == _TEST_FULL_NAME
        assert profile.customer_id != ""
        results["samples"]["profile"] = profile.to_dict()
    _test("Get profile returns correct data", test_get_profile)

    # ================================================================== #
    # 2. UPDATE PROFILE
    # ================================================================== #
    print("\n=== UPDATE PROFILE ===")

    def test_update_profile():
        _as_test_user()
        profile = _get_cust().update_profile(
            full_name="Updated Phase User",
            mobile_no="+919999999999",
            gender="Male",
        )
        d = profile.to_dict()
        assert profile.full_name == "Updated Phase User", f"full_name={d['full_name']!r}"
        assert profile.mobile_no == "+919999999999", f"mobile_no={d['mobile_no']!r}"
        assert profile.gender == "Male", f"gender={d['gender']!r}"
        results["samples"]["updated_profile"] = d
    _test("Update profile changes name, phone, gender", test_update_profile)

    def test_update_partial():
        _as_test_user()
        profile = _get_cust().update_profile(mobile_no="+918888888888")
        assert profile.mobile_no == "+918888888888"
        assert profile.gender == "Male"
    _test("Partial update preserves other fields", test_update_partial)

    # ================================================================== #
    # 3. CREATE ADDRESS
    # ================================================================== #
    print("\n=== CREATE ADDRESS ===")

    def test_create_billing():
        _as_test_user()
        addr = _get_cust().create_address(
            address_type="Billing",
            address_line1="123 Main Street",
            city="Mumbai",
            country="India",
            state="Maharashtra",
            pincode="400001",
            phone="+919876543210",
            email_id="billing@example.com",
            address_title="Home",
        )
        assert addr.name != ""
        assert addr.address_type == "Billing"
        assert addr.address_line1 == "123 Main Street"
        assert addr.city == "Mumbai"
        assert addr.is_primary_address is True
        assert addr.is_shipping_address is True
        _created_address_names.append(addr.name)
        results["samples"]["billing_addr"] = addr.to_dict()
    _test("Create billing address (first = auto-default)", test_create_billing)

    def test_create_shipping():
        _as_test_user()
        addr = _get_cust().create_address(
            address_type="Shipping",
            address_line1="456 Office Road",
            city="Bangalore",
            country="India",
            state="Karnataka",
            pincode="560001",
        )
        assert addr.name != ""
        assert addr.address_type == "Shipping"
        assert addr.city == "Bangalore"
        assert addr.is_primary_address is False
        assert addr.is_shipping_address is False
        _created_address_names.append(addr.name)
    _test("Create second address (not auto-default)", test_create_shipping)

    def test_create_third():
        _as_test_user()
        addr = _get_cust().create_address(
            address_type="Office",
            address_line1="789 Work Plaza",
            city="Chennai",
            country="India",
        )
        assert addr.name != ""
        _created_address_names.append(addr.name)
    _test("Create third address", test_create_third)

    # ================================================================== #
    # 4. LIST ADDRESSES
    # ================================================================== #
    print("\n=== LIST ADDRESSES ===")

    def test_list_addresses():
        _as_test_user()
        result = _get_cust().list_addresses()
        assert result.total >= 3
        assert len(result.addresses) >= 3
        types = {a.address_type for a in result.addresses}
        assert "Billing" in types
        assert "Shipping" in types
        assert "Office" in types
        results["samples"]["address_list"] = result.to_dict()
    _test("List returns all 3 addresses", test_list_addresses)

    # ================================================================== #
    # 5. GET SINGLE ADDRESS
    # ================================================================== #
    print("\n=== GET SINGLE ADDRESS ===")

    def test_get_address():
        _as_test_user()
        addr_name = _created_address_names[0]
        addr = _get_cust().get_address(addr_name)
        assert addr.name == addr_name
        assert addr.address_line1 == "123 Main Street"
    _test("Get single address by name", test_get_address)

    def test_get_nonexistent():
        _as_test_user()
        try:
            _get_cust().get_address("nonexistent-address-name")
            raise AssertionError("Should have raised DoesNotExistError")
        except frappe.exceptions.DoesNotExistError:
            pass
    _test("Get nonexistent address raises DoesNotExistError", test_get_nonexistent)

    # ================================================================== #
    # 6. UPDATE ADDRESS
    # ================================================================== #
    print("\n=== UPDATE ADDRESS ===")

    def test_update_address():
        _as_test_user()
        addr_name = _created_address_names[0]
        addr = _get_cust().update_address(
            addr_name,
            address_line1="123 Updated Street",
            city="Pune",
        )
        assert addr.address_line1 == "123 Updated Street"
        assert addr.city == "Pune"
    _test("Update address fields", test_update_address)

    # ================================================================== #
    # 7. DEFAULT ADDRESSES
    # ================================================================== #
    print("\n=== DEFAULT ADDRESSES ===")

    def test_set_default_shipping():
        _as_test_user()
        addr_name = _created_address_names[1]
        addr = _get_cust().set_default_shipping(addr_name)
        assert addr.is_shipping_address is True
        first = _get_cust().get_address(_created_address_names[0])
        assert first.is_shipping_address is False
    _test("Set default shipping (unsets previous)", test_set_default_shipping)

    def test_set_default_billing():
        _as_test_user()
        addr_name = _created_address_names[2]
        addr = _get_cust().set_default_billing(addr_name)
        assert addr.is_primary_address is True
        first = _get_cust().get_address(_created_address_names[0])
        assert first.is_primary_address is False
    _test("Set default billing (unsets previous)", test_set_default_billing)

    # ================================================================== #
    # 8. DELETE ADDRESS
    # ================================================================== #
    print("\n=== DELETE ADDRESS ===")

    def test_delete_address():
        _as_test_user()
        addr_name = _created_address_names[2]
        _get_cust().delete_address(addr_name)
        try:
            _get_cust().get_address(addr_name)
            raise AssertionError("Should have raised DoesNotExistError")
        except frappe.exceptions.DoesNotExistError:
            pass
        _created_address_names.remove(addr_name)
    _test("Delete address removes it", test_delete_address)

    def test_delete_last_primary_blocked():
        _as_test_user()
        remaining = _get_cust().list_addresses()
        primary = [a for a in remaining.addresses if a.is_primary_address]
        if primary:
            try:
                _get_cust().delete_address(primary[0].name)
                raise AssertionError("Should have raised ValidationError")
            except frappe.exceptions.ValidationError:
                pass
    _test("Cannot delete last primary address", test_delete_last_primary_blocked)

    # ================================================================== #
    # 9. UNAUTHORIZED ACCESS
    # ================================================================== #
    print("\n=== UNAUTHORIZED ACCESS ===")

    def test_unauthenticated_profile():
        _as_guest()
        try:
            _get_cust().get_profile()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError as e:
            assert "logged in" in str(e).lower()
    _test("Unauthenticated profile raises error", test_unauthenticated_profile)

    def test_unauthenticated_list():
        _as_guest()
        try:
            _get_cust().list_addresses()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Unauthenticated list raises error", test_unauthenticated_list)

    def test_unauthenticated_create():
        _as_guest()
        try:
            _get_cust().create_address(
                address_type="Billing",
                address_line1="Hacker St",
                city="Nowhere",
                country="Nowhereland",
            )
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Unauthenticated create raises error", test_unauthenticated_create)

    # ================================================================== #
    # 10. VALIDATION FAILURES
    # ================================================================== #
    print("\n=== VALIDATION FAILURES ===")

    def test_create_missing_type():
        _as_test_user()
        frappe.local.form_dict = frappe._dict({
            "address_line1": "123 St",
            "city": "Mumbai",
            "country": "India",
        })
        from keemeds_commerce.api.customer import create_address
        try:
            create_address()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Create missing address_type fails", test_create_missing_type)

    def test_create_missing_line1():
        _as_test_user()
        frappe.local.form_dict = frappe._dict({
            "address_type": "Billing",
            "city": "Mumbai",
            "country": "India",
        })
        from keemeds_commerce.api.customer import create_address
        try:
            create_address()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Create missing address_line1 fails", test_create_missing_line1)

    def test_create_missing_city():
        _as_test_user()
        frappe.local.form_dict = frappe._dict({
            "address_type": "Billing",
            "address_line1": "123 St",
            "country": "India",
        })
        from keemeds_commerce.api.customer import create_address
        try:
            create_address()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Create missing city fails", test_create_missing_city)

    def test_create_missing_country():
        _as_test_user()
        frappe.local.form_dict = frappe._dict({
            "address_type": "Billing",
            "address_line1": "123 St",
            "city": "Mumbai",
        })
        from keemeds_commerce.api.customer import create_address
        try:
            create_address()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Create missing country fails", test_create_missing_country)

    def test_create_invalid_type():
        _as_test_user()
        try:
            _get_cust().create_address(
                address_type="InvalidType",
                address_line1="123 St",
                city="Mumbai",
                country="India",
            )
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Create invalid address_type fails", test_create_invalid_type)

    # ================================================================== #
    # 11. API CONTROLLER TESTS
    # ================================================================== #
    print("\n=== API CONTROLLERS ===")

    def test_api_get_profile():
        _as_test_user()
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.customer import get_profile
        result = get_profile()
        assert result["success"] is True
        assert result["data"]["email"] == _TEST_EMAIL
    _test("API: get_profile works", test_api_get_profile)

    def test_api_update_profile():
        _as_test_user()
        frappe.local.form_dict = frappe._dict({"full_name": "API Updated User"})
        from keemeds_commerce.api.customer import update_profile
        result = update_profile()
        assert result["success"] is True
        assert result["data"]["full_name"] == "API Updated User"
    _test("API: update_profile works", test_api_update_profile)

    def test_api_list_addresses():
        _as_test_user()
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.customer import list_addresses
        result = list_addresses()
        assert result["success"] is True
        assert result["data"]["total"] >= 1
    _test("API: list_addresses works", test_api_list_addresses)

    def test_api_create_address():
        _as_test_user()
        frappe.local.form_dict = frappe._dict({
            "address_type": "Other",
            "address_line1": "API Created St",
            "city": "API City",
            "country": "India",
        })
        from keemeds_commerce.api.customer import create_address
        result = create_address()
        assert result["success"] is True
        assert result["data"]["address_line1"] == "API Created St"
        _created_address_names.append(result["data"]["name"])
    _test("API: create_address works", test_api_create_address)

    def test_api_get_address():
        _as_test_user()
        addr_name = _created_address_names[0]
        frappe.local.form_dict = frappe._dict({"address_name": addr_name})
        from keemeds_commerce.api.customer import get_address
        result = get_address()
        assert result["success"] is True
        assert result["data"]["name"] == addr_name
    _test("API: get_address works", test_api_get_address)

    def test_api_set_default_shipping():
        _as_test_user()
        addr_name = _created_address_names[-1]
        frappe.local.form_dict = frappe._dict({"address_name": addr_name})
        from keemeds_commerce.api.customer import set_default_shipping
        result = set_default_shipping()
        assert result["success"] is True
        assert result["data"]["is_shipping_address"] is True
    _test("API: set_default_shipping works", test_api_set_default_shipping)

    def test_api_set_default_billing():
        _as_test_user()
        addr_name = _created_address_names[-1]
        frappe.local.form_dict = frappe._dict({"address_name": addr_name})
        from keemeds_commerce.api.customer import set_default_billing
        result = set_default_billing()
        assert result["success"] is True
        assert result["data"]["is_primary_address"] is True
    _test("API: set_default_billing works", test_api_set_default_billing)

    def test_api_delete_address():
        _as_test_user()
        addr_name = _created_address_names[-1]
        frappe.local.form_dict = frappe._dict({"address_name": addr_name})
        from keemeds_commerce.api.customer import delete_address
        result = delete_address()
        assert result["success"] is True
        _created_address_names.remove(addr_name)
    _test("API: delete_address works", test_api_delete_address)

    def test_api_no_traceback():
        _as_guest()
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.customer import get_profile
        try:
            get_profile()
        except frappe.exceptions.ValidationError as e:
            assert "Traceback" not in str(e)
    _test("API: no traceback in ValidationError", test_api_no_traceback)

    # ================================================================== #
    # 12. PERFORMANCE
    # ================================================================== #
    print("\n=== PERFORMANCE ===")

    def test_profile_speed():
        _as_test_user()
        start = time.perf_counter()
        _get_cust().get_profile()
        elapsed = time.perf_counter() - start
        results["performance"]["profile"] = elapsed
        assert elapsed < 3.0, f"Profile took {elapsed:.2f}s"
    _test("Performance: profile < 3s", test_profile_speed)

    def test_list_speed():
        _as_test_user()
        start = time.perf_counter()
        _get_cust().list_addresses()
        elapsed = time.perf_counter() - start
        results["performance"]["list_addresses"] = elapsed
        assert elapsed < 3.0, f"List took {elapsed:.2f}s"
    _test("Performance: list_addresses < 3s", test_list_speed)

    def test_create_speed():
        _as_test_user()
        start = time.perf_counter()
        addr = _get_cust().create_address(
            address_type="Other",
            address_line1="Perf St",
            city="Perf City",
            country="India",
        )
        elapsed = time.perf_counter() - start
        results["performance"]["create_address"] = elapsed
        assert elapsed < 3.0, f"Create took {elapsed:.2f}s"
        _created_address_names.append(addr.name)
    _test("Performance: create_address < 3s", test_create_speed)

    # ================================================================== #
    # 13. REGRESSION
    # ================================================================== #
    print("\n=== REGRESSION ===")

    def test_master_data_imports():
        import importlib
        mods = [
            "keemeds_commerce.master_data.pipeline",
            "keemeds_commerce.master_data.import_manager",
            "keemeds_commerce.master_data.validators",
            "keemeds_commerce.master_data.logging_setup",
            "keemeds_commerce.master_data.config",
            "keemeds_commerce.master_data.models",
            "keemeds_commerce.master_data.bench_runtime",
        ]
        for m in mods:
            importlib.import_module(m)
    _test("Regression: master_data modules importable", test_master_data_imports)

    def test_product_api_imports():
        import importlib
        mods = [
            "keemeds_commerce.api.products",
            "keemeds_commerce.api.auth",
            "keemeds_commerce.api.customer",
            "keemeds_commerce.services.product_service",
            "keemeds_commerce.services.auth_service",
            "keemeds_commerce.services.customer_service",
        ]
        for m in mods:
            importlib.import_module(m)
    _test("Regression: all API modules importable", test_product_api_imports)

    def test_product_api_works():
        _as_admin()
        frappe.local.form_dict = frappe._dict({"page": "1", "page_size": "3"})
        from keemeds_commerce.api.products import list_products
        result = list_products()
        assert result["success"] is True
        assert len(result["data"]["items"]) <= 3
    _test("Regression: Product Listing API still works", test_product_api_works)

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
        print("\n--- Sample: Profile ---")
        print(json.dumps(results["samples"].get("profile", {}), indent=2, default=str)[:1000])
        print("\n--- Sample: Address List ---")
        print(json.dumps(results["samples"].get("address_list", {}), indent=2, default=str)[:1500])

    if results["performance"]:
        print("\n--- Performance ---")
        for k, v in results["performance"].items():
            print(f"  {k}: {v:.4f}s")

    return results
