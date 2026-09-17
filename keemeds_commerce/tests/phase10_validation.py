"""
Phase 10 Validation — Product Catalog API

End-to-end validation through the actual Frappe request lifecycle.
Run: bench --site keemeds-commerce.local execute keemeds_commerce.tests.phase10_validation:run
"""

from __future__ import annotations

import json
import time

import frappe


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

    # ------------------------------------------------------------------ #
    # PRODUCT LISTING
    # ------------------------------------------------------------------ #
    print("\n=== PRODUCT LISTING ===")

    def test_basic_listing():
        frappe.local.form_dict = {}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        assert result["success"] is True
        assert "data" in result
        assert "items" in result["data"]
        assert "pagination" in result["data"]
        results["samples"]["listing_basic"] = result
    _test("Basic listing returns success with items and pagination", test_basic_listing)

    def test_pagination():
        frappe.local.form_dict = {"page": "1", "page_size": "3"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        items = result["data"]["items"]
        pagination = result["data"]["pagination"]
        assert len(items) <= 3, f"Expected <= 3 items, got {len(items)}"
        assert pagination["page"] == 1
        assert pagination["page_size"] == 3
        assert pagination["total_records"] > 0
        assert pagination["total_pages"] > 0
    _test("Pagination: page_size=3 limits results", test_pagination)

    def test_page2():
        frappe.local.form_dict = {"page": "1", "page_size": "3"}
        from keemeds_commerce.api.products import list_products
        page1 = list_products()
        frappe.local.form_dict = {"page": "2", "page_size": "3"}
        page2 = list_products()
        p1_codes = [i["item_code"] for i in page1["data"]["items"]]
        p2_codes = [i["item_code"] for i in page2["data"]["items"]]
        assert p1_codes != p2_codes or len(page2["data"]["items"]) == 0
        assert page2["data"]["pagination"]["page"] == 2
    _test("Pagination: page 2 returns different items", test_page2)

    def test_search():
        frappe.local.form_dict = {"search": "Paracetamol", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        items = result["data"]["items"]
        assert len(items) > 0, "Search for Paracetamol should return results"
        for item in items:
            haystack = (item["item_name"] + item["item_code"] + item.get("brand", "") + item.get("manufacturer", "")).lower()
            assert "paracetamol" in haystack
    _test("Search: Paracetamol returns matching items", test_search)

    def test_brand_filter():
        frappe.local.form_dict = {"brand": "Abbott", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        items = result["data"]["items"]
        assert len(items) > 0
        for item in items:
            assert item["brand"] == "Abbott", f"Got brand={item['brand']}"
    _test("Brand filter: Abbott returns only Abbott items", test_brand_filter)

    def test_manufacturer_filter():
        frappe.local.form_dict = {"manufacturer": "Abbott India", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        items = result["data"]["items"]
        assert len(items) > 0
        for item in items:
            assert item["manufacturer"] == "Abbott India"
    _test("Manufacturer filter: Abbott India returns matching items", test_manufacturer_filter)

    def test_item_group_filter():
        frappe.local.form_dict = {"item_group": "Allopathic", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        items = result["data"]["items"]
        assert len(items) > 0
        for item in items:
            assert item["item_group"] == "Allopathic"
    _test("Item Group filter: Allopathic returns matching items", test_item_group_filter)

    def test_stock_filter():
        frappe.local.form_dict = {"in_stock": "1", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        for item in result["data"]["items"]:
            assert item["in_stock"] is True, f"Item {item['item_code']} not in_stock"
    _test("Stock filter: in_stock=1 returns only in-stock items", test_stock_filter)

    def test_sort_item_name():
        frappe.local.form_dict = {"sort": "item_name", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        names = [i["item_name"] for i in result["data"]["items"]]
        assert names == sorted(names), f"Not sorted: {names[:5]}"
    _test("Sorting: sort=item_name returns alphabetically sorted", test_sort_item_name)

    def test_sort_price():
        frappe.local.form_dict = {"sort": "price", "page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        prices = [(i["selling_price"], i["item_code"]) for i in result["data"]["items"] if i["selling_price"] is not None]
        for j in range(len(prices) - 1):
            assert prices[j][0] <= prices[j + 1][0], f"Price sort broken: {prices[j]} > {prices[j+1]}"
    _test("Sorting: sort=price returns ascending price order", test_sort_price)

    # ------------------------------------------------------------------ #
    # PRODUCT DETAIL
    # ------------------------------------------------------------------ #
    print("\n=== PRODUCT DETAIL ===")

    def test_detail_existing():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        result = get_product()
        assert result["success"] is True
        assert result["data"]["item_code"] == "MED-001"
        results["samples"]["detail_MED001"] = result
    _test("Detail: existing item MED-001 returns success", test_detail_existing)

    def test_detail_images():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        result = get_product()
        images = result["data"]["images"]
        assert images["primary_image"], "primary_image empty"
        assert images["gallery"], "gallery empty"
        assert len(images["gallery"]) == 3, f"Expected 3 gallery, got {len(images['gallery'])}"
        assert "MED-001-1.webp" in images["primary_image"]
    _test("Detail: images returned with correct format", test_detail_images)

    def test_detail_price():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        result = get_product()
        assert result["data"]["selling_price"] is not None
        assert result["data"]["selling_price"] > 0
        assert result["data"]["currency"] == "INR"
    _test("Detail: price returned correctly", test_detail_price)

    def test_detail_stock():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        result = get_product()
        assert result["data"]["in_stock"] is True
        assert result["data"]["available_qty"] > 0
    _test("Detail: stock info returned correctly", test_detail_stock)

    def test_detail_attributes():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        result = get_product()
        d = result["data"]
        assert d["strength"] == "650 mg", f"Got strength='{d['strength']}'"
        assert d["dosage_form"] == "Tablet", f"Got dosage_form='{d['dosage_form']}'"
        assert d["salt_composition"] == "Paracetamol", f"Got salt='{d['salt_composition']}'"
        assert d["item_group"] == "Allopathic"
        assert d["brand"] == "Abbott"
        assert d["manufacturer"] == "Abbott India"
    _test("Detail: attributes parsed correctly for MED-001", test_detail_attributes)

    def test_detail_description():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        result = get_product()
        assert "description" in result["data"]
    _test("Detail: description field present", test_detail_description)

    def test_disabled_item_hidden():
        """Disabled items should not be returned."""
        frappe.local.form_dict = {"item_code": "DISABLED-TEST"}
        from keemeds_commerce.api.products import get_product
        try:
            get_product()
            raise AssertionError("Should have raised DoesNotExistError")
        except frappe.exceptions.DoesNotExistError:
            pass
    _test("Detail: disabled/non-existent item raises DoesNotExistError", test_disabled_item_hidden)

    # ------------------------------------------------------------------ #
    # ERROR HANDLING
    # ------------------------------------------------------------------ #
    print("\n=== ERROR HANDLING ===")

    def test_missing_item_code():
        frappe.local.form_dict = {}
        from keemeds_commerce.api.products import get_product
        try:
            get_product()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Error: missing item_code -> ValidationError (HTTP 400)", test_missing_item_code)

    def test_invalid_item_code():
        frappe.local.form_dict = {"item_code": "NONEXISTENT-999"}
        from keemeds_commerce.api.products import get_product
        try:
            get_product()
            raise AssertionError("Should have raised DoesNotExistError")
        except frappe.exceptions.DoesNotExistError:
            pass
    _test("Error: non-existent item -> DoesNotExistError (HTTP 404)", test_invalid_item_code)

    def test_no_traceback_in_error():
        frappe.local.form_dict = {}
        from keemeds_commerce.api.products import get_product
        try:
            get_product()
        except frappe.exceptions.ValidationError as e:
            err = str(e)
            assert "Traceback" not in err
            assert "File " not in err
    _test("Error: no Python traceback exposed", test_no_traceback_in_error)

    def test_invalid_sort():
        frappe.local.form_dict = {"sort": "invalid_xyz"}
        from keemeds_commerce.api.products import list_products
        try:
            list_products()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Error: invalid sort -> ValidationError", test_invalid_sort)

    def test_invalid_page():
        frappe.local.form_dict = {"page": "abc"}
        from keemeds_commerce.api.products import list_products
        try:
            list_products()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Error: invalid page -> ValidationError", test_invalid_page)

    def test_negative_page():
        frappe.local.form_dict = {"page": "-1"}
        from keemeds_commerce.api.products import list_products
        try:
            list_products()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Error: negative page -> ValidationError", test_negative_page)

    def test_zero_page_size():
        frappe.local.form_dict = {"page_size": "0"}
        from keemeds_commerce.api.products import list_products
        try:
            list_products()
            raise AssertionError("Should have raised ValidationError")
        except frappe.exceptions.ValidationError:
            pass
    _test("Error: page_size=0 -> ValidationError", test_zero_page_size)

    def test_oversized_page_size():
        frappe.local.form_dict = {"page_size": "999"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        assert result["data"]["pagination"]["page_size"] <= 100
    _test("Clamp: oversized page_size clamped to 100", test_oversized_page_size)

    # ------------------------------------------------------------------ #
    # PERFORMANCE
    # ------------------------------------------------------------------ #
    print("\n=== PERFORMANCE ===")

    def test_listing_speed():
        frappe.local.form_dict = {"page_size": "20"}
        from keemeds_commerce.api.products import list_products
        start = time.perf_counter()
        list_products()
        elapsed = time.perf_counter() - start
        results["performance"]["listing_20"] = elapsed
        assert elapsed < 5.0, f"Listing took {elapsed:.2f}s"
    _test("Performance: listing < 5s", test_listing_speed)

    def test_detail_speed():
        frappe.local.form_dict = {"item_code": "MED-001"}
        from keemeds_commerce.api.products import get_product
        start = time.perf_counter()
        get_product()
        elapsed = time.perf_counter() - start
        results["performance"]["detail"] = elapsed
        assert elapsed < 3.0, f"Detail took {elapsed:.2f}s"
    _test("Performance: detail < 3s", test_detail_speed)

    def test_minimal_fields():
        frappe.local.form_dict = {"page_size": "5"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        if result["data"]["items"]:
            item = result["data"]["items"][0]
            expected = {
                "item_code", "item_name", "item_group", "brand", "manufacturer",
                "strength", "dosage_form", "salt_composition",
                "selling_price", "currency", "in_stock", "available_qty", "images",
            }
            actual = set(item.keys())
            assert actual == expected, f"Unexpected: {actual - expected}, Missing: {expected - actual}"
    _test("Fields: listing items contain only expected fields", test_minimal_fields)

    def test_pagination_limits():
        frappe.local.form_dict = {"page_size": "100"}
        from keemeds_commerce.api.products import list_products
        result = list_products()
        assert len(result["data"]["items"]) <= 100
    _test("Pagination: max page_size respected", test_pagination_limits)

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

    def test_services_imports():
        import importlib
        mods = [
            "keemeds_commerce.services.product_service",
            "keemeds_commerce.services.pricing_service",
            "keemeds_commerce.services.stock_service",
            "keemeds_commerce.services.image_resolver",
            "keemeds_commerce.services.item_name_parser",
        ]
        for m in mods:
            importlib.import_module(m)
    _test("Regression: service modules importable", test_services_imports)

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
        print("\n--- Sample: Product Listing ---")
        s = results["samples"]["listing_basic"]
        print(json.dumps(s, indent=2, default=str)[:3000])
        print("\n--- Sample: Product Detail (MED-001) ---")
        s2 = results["samples"]["detail_MED001"]
        print(json.dumps(s2, indent=2, default=str)[:3000])

    if results["performance"]:
        print("\n--- Performance ---")
        for k, v in results["performance"].items():
            print(f"  {k}: {v:.4f}s")

    return results
