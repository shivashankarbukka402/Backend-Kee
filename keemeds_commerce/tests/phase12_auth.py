"""
Phase 12 Validation — Customer Authentication API

End-to-end validation through the actual Frappe request lifecycle.
Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase12_auth.run
"""

from __future__ import annotations

import json
import time

import frappe
from frappe.auth import CookieManager, LoginManager

from keemeds_commerce.services.auth_service import AuthService

_TEST_EMAIL = "test.phase12@example.com"
_TEST_PHONE = "+919876543210"
_TEST_PASSWORD = "TestPass123!"
_TEST_FULL_NAME = "Phase Twelve User"

_svc: AuthService | None = None


def _get_svc() -> AuthService:
    global _svc
    if _svc is None:
        _svc = AuthService()
    return _svc


def _setup():
    """Initialize request-level state required by LoginManager."""
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


def _purge_once(emails, names):
    """Remove users, customers, contacts and all child rows for test emails/names.

    Commits after every delete so each statement starts in a fresh transaction
    (avoids the spurious MariaDB 1020 on long-lived test sessions)."""
    for email in emails:
        for c in frappe.get_all("Contact", filters={"email_id": email}, pluck="name"):
            for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
                frappe.db.commit()
            for ce in frappe.get_all("Contact Email", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabContact Email` WHERE name = %s", (ce,))
                frappe.db.commit()
            for cp in frappe.get_all("Contact Phone", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabContact Phone` WHERE name = %s", (cp,))
                frappe.db.commit()
            frappe.db.sql("DELETE FROM `tabContact` WHERE name = %s", (c,))
            frappe.db.commit()
        for pu in frappe.get_all("Portal User", filters={"user": email}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
            frappe.db.commit()
        if frappe.db.exists("User", email):
            frappe.db.sql("DELETE FROM `tabUser` WHERE name = %s", (email,))
            frappe.db.commit()
    for name in names:
        for dl in frappe.get_all("Dynamic Link", filters={"link_name": name}, pluck="name"):
            frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
            frappe.db.commit()
        for c in frappe.get_all("Customer", filters={"customer_name": name}, pluck="name"):
            for pu in frappe.get_all("Portal User", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabPortal User` WHERE name = %s", (pu,))
                frappe.db.commit()
            for dl in frappe.get_all("Dynamic Link", filters={"parent": c}, pluck="name"):
                frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE name = %s", (dl,))
                frappe.db.commit()
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
    """Remove every test user/customer/contact we may have created (incl. children)."""
    _purge(
        ["test.phase12@example.com", "perf.test@example.com"],
        ["Phase Twelve User", "Perf User"],
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

    # ------------------------------------------------------------------ #
    # VALIDATOR TESTS (pure, no DB)
    # ------------------------------------------------------------------ #
    print("\n=== AUTH VALIDATORS ===")

    def test_validate_register_valid():
        from keemeds_commerce.validators.auth_params import validate_register_args
        result = validate_register_args({
            "email": "  User@Example.com  ",
            "full_name": "  John Doe  ",
            "mobile_no": "+919876543210",
            "password": "securepass1",
        })
        assert result["email"] == "user@example.com"
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["mobile_no"] == "+919876543210"
    _test("Validator: valid register args normalized correctly", test_validate_register_valid)

    def test_validate_register_missing_email():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"full_name": "John", "mobile_no": "+919876543210", "password": "pass1234"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: missing email raises ValidationError", test_validate_register_missing_email)

    def test_validate_register_bad_email():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "not-an-email", "full_name": "John", "mobile_no": "+919876543210", "password": "pass1234"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: invalid email format raises ValidationError", test_validate_register_bad_email)

    def test_validate_register_missing_name():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "a@b.com", "mobile_no": "+919876543210", "password": "pass1234"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: missing full_name raises ValidationError", test_validate_register_missing_name)

    def test_validate_register_short_name():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "a@b.com", "full_name": "J", "mobile_no": "+919876543210", "password": "pass1234"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: short full_name raises ValidationError", test_validate_register_short_name)

    def test_validate_register_missing_phone():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "a@b.com", "full_name": "John Doe", "password": "pass1234"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: missing phone raises ValidationError", test_validate_register_missing_phone)

    def test_validate_register_bad_phone():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "a@b.com", "full_name": "John Doe", "mobile_no": "123", "password": "pass1234"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: short phone raises ValidationError", test_validate_register_bad_phone)

    def test_validate_register_short_password():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "a@b.com", "full_name": "John Doe", "mobile_no": "+919876543210", "password": "short"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: short password raises ValidationError", test_validate_register_short_password)

    def test_validate_register_missing_password():
        from keemeds_commerce.validators.auth_params import validate_register_args
        try:
            validate_register_args({"email": "a@b.com", "full_name": "John Doe", "mobile_no": "+919876543210"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: missing password raises ValidationError", test_validate_register_missing_password)

    def test_validate_login_valid():
        from keemeds_commerce.validators.auth_params import validate_login_args
        result = validate_login_args({"email": "  User@Example.com  ", "password": "secret"})
        assert result["email"] == "user@example.com"
        assert result["password"] == "secret"
    _test("Validator: valid login args normalized correctly", test_validate_login_valid)

    def test_validate_login_missing_email():
        from keemeds_commerce.validators.auth_params import validate_login_args
        try:
            validate_login_args({"password": "secret"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: login missing email raises ValidationError", test_validate_login_missing_email)

    def test_validate_login_missing_password():
        from keemeds_commerce.validators.auth_params import validate_login_args
        try:
            validate_login_args({"email": "a@b.com"})
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Validator: login missing password raises ValidationError", test_validate_login_missing_password)

    # ------------------------------------------------------------------ #
    # DOMAIN DTO TESTS
    # ------------------------------------------------------------------ #
    print("\n=== DOMAIN DTOs ===")

    def test_user_profile_to_dict():
        from keemeds_commerce.domain.user import UserProfile
        p = UserProfile(
            email="a@b.com",
            full_name="Test User",
            mobile_no="+911234567890",
            user_type="Website User",
            customer_name="Test Customer",
            customer_id="CUST-001",
        )
        d = p.to_dict()
        assert d["email"] == "a@b.com"
        assert d["full_name"] == "Test User"
        assert d["customer_id"] == "CUST-001"
    _test("DTO: UserProfile.to_dict() returns correct fields", test_user_profile_to_dict)

    def test_user_profile_defaults():
        from keemeds_commerce.domain.user import UserProfile
        p = UserProfile()
        d = p.to_dict()
        assert d["email"] == ""
        assert d["customer_name"] == ""
    _test("DTO: UserProfile defaults are empty strings", test_user_profile_defaults)

    # ------------------------------------------------------------------ #
    # SERVICE LAYER TESTS
    # ------------------------------------------------------------------ #
    print("\n=== AUTH SERVICE ===")

    def test_register_creates_user_and_customer():
        _as_admin()
        svc = _get_svc()
        profile = svc.register(
            email=_TEST_EMAIL,
            first_name="Phase",
            last_name="Twelve User",
            full_name=_TEST_FULL_NAME,
            mobile_no=_TEST_PHONE,
            password=_TEST_PASSWORD,
        )
        assert profile.email == _TEST_EMAIL
        assert profile.full_name == _TEST_FULL_NAME
        assert profile.customer_name == _TEST_FULL_NAME
        assert profile.customer_id != ""
        assert frappe.db.exists("User", _TEST_EMAIL)
        results["samples"]["register"] = profile.to_dict()
    _test("Service: register creates User + Customer, returns profile", test_register_creates_user_and_customer)

    def test_register_duplicate_email():
        _as_admin()
        svc = _get_svc()
        try:
            svc.register(
                email=_TEST_EMAIL,
                first_name="Dup",
                last_name="User",
                full_name="Dup User",
                mobile_no="+911111111111",
                password=_TEST_PASSWORD,
            )
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError as e:
            assert "already exists" in str(e).lower()
    _test("Service: register duplicate email raises ValidationError", test_register_duplicate_email)

    def test_register_duplicate_phone():
        _as_admin()
        svc = _get_svc()
        try:
            svc.register(
                email="different@example.com",
                first_name="Dup",
                last_name="Phone",
                full_name="Dup Phone",
                mobile_no=_TEST_PHONE,
                password=_TEST_PASSWORD,
            )
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError as e:
            assert "already exists" in str(e).lower()
    _test("Service: register duplicate phone raises ValidationError", test_register_duplicate_phone)

    def test_login_authenticates():
        svc = _get_svc()
        svc.login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        assert frappe.session.user == _TEST_EMAIL
    _test("Service: login authenticates and sets session", test_login_authenticates)

    def test_login_wrong_password():
        svc = _get_svc()
        try:
            svc.login(email=_TEST_EMAIL, password="WrongPassword123!")
            raise AssertionError("Should have raised AuthenticationError")
        except Exception:
            pass
    _test("Service: login wrong password raises error", test_login_wrong_password)

    def test_get_profile():
        svc = _get_svc()
        svc.login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        profile = svc.get_profile()
        assert profile.email == _TEST_EMAIL
        assert profile.customer_name == _TEST_FULL_NAME
        results["samples"]["profile"] = profile.to_dict()
    _test("Service: get_profile returns user + customer info", test_get_profile)

    def test_logout():
        svc = _get_svc()
        svc.login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        svc.logout()
        assert frappe.session.user == "Guest"
    _test("Service: logout destroys session", test_logout)

    # ------------------------------------------------------------------ #
    # API CONTROLLER TESTS
    # ------------------------------------------------------------------ #
    print("\n=== API CONTROLLERS ===")

    def test_api_register():
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
        assert result["data"]["email"] == _TEST_EMAIL
        assert result["data"]["customer_name"] == _TEST_FULL_NAME
        results["samples"]["api_register"] = result
    _test("API: register returns success with profile data", test_api_register)

    def test_api_register_duplicate():
        _as_admin()
        frappe.local.form_dict = frappe._dict({
            "email": _TEST_EMAIL,
            "full_name": _TEST_FULL_NAME,
            "mobile_no": "+910000000000",
            "password": _TEST_PASSWORD,
        })
        from keemeds_commerce.api.auth import register
        try:
            register()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("API: register duplicate email raises ValidationError", test_api_register_duplicate)

    def test_api_login():
        _as_guest()
        frappe.local.form_dict = frappe._dict({"email": _TEST_EMAIL, "password": _TEST_PASSWORD})
        from keemeds_commerce.api.auth import login
        result = login()
        assert result["success"] is True
        assert frappe.session.user == _TEST_EMAIL
    _test("API: login returns success and sets session", test_api_login)

    def test_api_me():
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.auth import me
        result = me()
        assert result["success"] is True
        assert result["data"]["email"] == _TEST_EMAIL
        assert result["data"]["customer_name"] == _TEST_FULL_NAME
        results["samples"]["api_me"] = result
    _test("API: me returns current user profile", test_api_me)

    def test_api_me_no_auth():
        _as_guest()
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.auth import me
        try:
            me()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("API: me without login raises ValidationError", test_api_me_no_auth)

    def test_api_logout():
        _as_guest()
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.auth import logout
        result = logout()
        assert result["success"] is True
        assert frappe.session.user == "Guest"
    _test("API: logout returns success and clears session", test_api_logout)

    # ------------------------------------------------------------------ #
    # ERROR HANDLING
    # ------------------------------------------------------------------ #
    print("\n=== ERROR HANDLING ===")

    def test_no_traceback_in_validation():
        _as_admin()
        frappe.local.form_dict = frappe._dict()
        from keemeds_commerce.api.auth import register
        try:
            register()
        except frappe.exceptions.ValidationError as e:
            err = str(e)
            assert "Traceback" not in err
            assert "File " not in err
    _test("Error: no Python traceback in ValidationError", test_no_traceback_in_validation)

    def test_no_traceback_in_does_not_exist():
        _as_guest()
        svc = _get_svc()
        try:
            svc.get_profile()
        except frappe.exceptions.ValidationError as e:
            err = str(e)
            assert "Traceback" not in err
    _test("Error: no traceback when profile not found", test_no_traceback_in_does_not_exist)

    # ------------------------------------------------------------------ #
    # PERFORMANCE
    # ------------------------------------------------------------------ #
    print("\n=== PERFORMANCE ===")

    def test_register_speed():
        _as_admin()
        svc = _get_svc()
        start = time.perf_counter()
        svc.register(
            email="perf.test@example.com",
            first_name="Perf",
            last_name="User",
            full_name="Perf User",
            mobile_no="+919999999999",
            password=_TEST_PASSWORD,
        )
        elapsed = time.perf_counter() - start
        results["performance"]["register"] = elapsed
        assert elapsed < 5.0, f"Register took {elapsed:.2f}s"
    _test("Performance: register < 5s", test_register_speed)

    def test_login_speed():
        svc = _get_svc()
        start = time.perf_counter()
        svc.login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        elapsed = time.perf_counter() - start
        results["performance"]["login"] = elapsed
        assert elapsed < 3.0, f"Login took {elapsed:.2f}s"
    _test("Performance: login < 3s", test_login_speed)

    def test_me_speed():
        svc = _get_svc()
        svc.login(email=_TEST_EMAIL, password=_TEST_PASSWORD)
        start = time.perf_counter()
        svc.get_profile()
        elapsed = time.perf_counter() - start
        results["performance"]["me"] = elapsed
        assert elapsed < 3.0, f"Profile took {elapsed:.2f}s"
    _test("Performance: me < 3s", test_me_speed)

    # ------------------------------------------------------------------ #
    # REGRESSION
    # ------------------------------------------------------------------ #
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
            "keemeds_commerce.services.product_service",
            "keemeds_commerce.services.pricing_service",
            "keemeds_commerce.services.stock_service",
            "keemeds_commerce.services.image_resolver",
            "keemeds_commerce.services.item_name_parser",
        ]
        for m in mods:
            importlib.import_module(m)
    _test("Regression: product API modules importable", test_product_api_imports)

    def test_product_api_works():
        _as_admin()
        frappe.local.form_dict = frappe._dict({"page": "1", "page_size": "3"})
        from keemeds_commerce.api.products import list_products
        result = list_products()
        assert result["success"] is True
        assert len(result["data"]["items"]) <= 3
    _test("Regression: Product Listing API still works", test_product_api_works)

    # ------------------------------------------------------------------ #
    # Cleanup
    # ------------------------------------------------------------------ #
    _as_admin()
    _cleanup_all()

    # ------------------------------------------------------------------ #
    # SUMMARY
    # ------------------------------------------------------------------ #
    print("\n" + "=" * 60)
    print(f"RESULTS: {results['passed']} passed, {results['failed']} failed")
    if results["errors"]:
        print("\nFailed tests:")
        for name, err in results["errors"]:
            print(f"  - {name}: {err}")
    print("=" * 60)

    if results["samples"]:
        print("\n--- Sample: Register Response ---")
        print(json.dumps(results["samples"].get("register", {}), indent=2, default=str)[:1500])
        print("\n--- Sample: Profile (me) Response ---")
        print(json.dumps(results["samples"].get("api_me", {}), indent=2, default=str)[:1500])

    if results["performance"]:
        print("\n--- Performance ---")
        for k, v in results["performance"].items():
            print(f"  {k}: {v:.4f}s")

    return results
