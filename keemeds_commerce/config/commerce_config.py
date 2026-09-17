"""
Commerce Configuration

Centralized, immutable configuration for the Phase 10 Commerce (Product
Catalog) API layer.

Following Clean Architecture, all tunables for the commerce API live here so
the controllers and services never hard-code limits or URLs.

Attributes
----------
default_page_size:
    Default number of products returned per page when none is supplied.
max_page_size:
    Upper bound enforced on any requested page size (defensive ceiling).
item_image_url_prefix:
    Public URL prefix (under the ERPNext site URL) where the Phase 9.5 product
    gallery images are served. The gallery file names (``MED-001-1.webp`` ...)
    are appended to this prefix to build public URLs. Never a filesystem path.
selling_price_list:
    Name of the ERPNext price list used as the storefront selling price.
published_item_groups:
    Optional allow-list of Item Group names considered "published". When empty,
    every enabled item is considered published. Enables future catalog scoping.
reserved_item_group_suffixes:
    Item-group names that must never be surfaced (an empty default).
warehouses:
    Optional allow-list of Warehouse names whose stock is aggregated for stock
    availability. When empty every (non-group) warehouse is included.
image_gallery_slots:
    Number of gallery images per product (mirrors the Phase 9.5 ``images_per_item``).
checkout_discount_amount:
    Flat discount (order currency) applied before taxes; ``0.0`` means none.
checkout_tax_rate:
    Flat percentage tax applied to the discounted net total when no ERPNext tax
    template is configured.
checkout_shipping_charge:
    Flat shipping/delivery charge in the order currency.
checkout_delivery_lead_days:
    Days added to today for the Draft Sales Order delivery date.
checkout_duplicate_window_minutes:
    Minutes during which an identical pending Draft Sales Order is replayed so
    a double-clicked/retried Place Order never creates a duplicate draft.
checkout_tax_account / checkout_shipping_account:
    ERPNext Account heads for the tax / shipping rows on the Draft Sales Order.
payment_gateway:
    Payment gateway adapter name. ``TEST`` is the built-in, gateway-agnostic
    adapter used until a live gateway (Razorpay / PhonePe / Cashfree) is wired.
payment_verify_secret / payment_webhook_secret:
    Gateway-agnostic HMAC secrets that sign the payment intent and the webhook
    callbacks. Defaults are for development only — production must override
    them (site config) and match the live gateway's keys.
payment_company / payment_mode_of_payment / payment_paid_from_account /
payment_paid_to_account:
    ERPNext accounting targets for the Payment Entry created after a successful
    verification. Defaults mirror the site's Chart of Accounts and seed data.
payment_method_mode_map:
    Maps gateway-level ``payment_method`` values (e.g. "upi", "cod") to
    ERPNext ``Mode of Payment`` names so the correct accounting mode is
    used on the Payment Entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class CommerceConfig:
    """
    Immutable configuration for the commerce product-catalog API.
    """

    default_page_size: int = 20
    max_page_size: int = 100
    item_image_url_prefix: str = "/files/item_images"
    selling_price_list: str = "Standard Selling"
    published_item_groups: tuple[str, ...] = ()
    warehouses: tuple[str, ...] = ()
    image_gallery_slots: int = 3
    image_extension: str = ".webp"

    #: Flat discount (in the order currency) applied before taxes. When this
    #: system adds a promotion engine the value will come from ERPNext pricing
    #: rules instead; 0.0 means "no discount".
    checkout_discount_amount: float = 0.0
    #: Flat percentage tax applied to the discounted net total when no ERPNext
    #: ``Sales Taxes and Charges Template`` is configured for the sale.
    checkout_tax_rate: float = 0.0
    #: Flat shipping/delivery charge in the order currency.
    checkout_shipping_charge: float = 0.0
    #: Days added to today for the Draft Sales Order delivery date.
    checkout_delivery_lead_days: int = 0
    #: Minutes during which an identical pending Draft Sales Order (same items,
    #: quantities, addresses and total) is replayed instead of creating a
    #: duplicate. Guards storefront double-clicks/taps and retries.
    checkout_duplicate_window_minutes: int = 10
    #: ERPNext Account heads used for the tax and shipping rows persisted on the
    #: Draft Sales Order when the corresponding charge is non-zero.
    checkout_tax_account: str = "Duties and Taxes - HG"
    checkout_shipping_account: str = "Freight and Forwarding Charges - HG"

    #: Payment gateway adapter name. ``TEST`` is the built-in, gateway-agnostic
    #: adapter used until a live gateway is integrated.
    payment_gateway: str = "TEST"
    #: HMAC secret signing the payment intent (issued at create_payment and
    #: validated at verify_payment). Development default only.
    payment_verify_secret: str = "kc-dev-verify-secret"
    #: HMAC secret used to authenticate webhook callbacks. Development default
    #: only — must match the live gateway's secret in production.
    payment_webhook_secret: str = "kc-dev-webhook-secret"
    #: Accounting targets for the Payment Entry created on success. For a
    #: customer "Receive" entry ERPNext treats ``paid_from`` as the party
    #: (receivable) account and ``paid_to`` as the company cash/bank account.
    payment_company: str = "HG Infotech"
    #: Default ERPNext mode of payment when no mapping exists for the gateway
    #: payment method. Kept for backward compatibility.
    payment_mode_of_payment: str = "Cash"
    #: Mapping from gateway-level ``payment_method`` values (e.g. "upi",
    #: "card", "cod") to ERPNext ``Mode of Payment`` names. The gateway
    #: method is selected by the customer at checkout; the resolved mode is
    #: written onto the Payment Entry and Sales Order.
    payment_method_mode_map: dict[str, str] = field(default_factory=lambda: {
        "upi": "UPI",
        "cod": "Cash",
        "cash": "Cash",
        "card": "Card",
        "netbanking": "Net Banking",
        "wallet": "Wallet",
    })
    #: Customer's receivable account debited by a received payment.
    payment_paid_from_account: str = "Debtors - HG"
    #: Company cash/bank account credited by a received payment.
    payment_paid_to_account: str = "Cash - HG"

    #: Valid ``sort`` values accepted by the listing API.
    sort_options: Final[tuple[str, ...]] = ("item_name", "item_code", "price", "newest")
    #: Default sort when none supplied.
    default_sort: Final[str] = "item_name"
    #: The ERPNext Item base URL fragment used to build public gallery URLs.
    _gallery_filename_prefix: Final[str] = ""

    def gallery_filenames(self, item_code: str) -> list[str]:
        """
        Return the ordered public-file gallery names for an item code.

        Mirrors the deterministic Phase 9.5 naming (``MED-001-1.webp`` ...
        ``MED-001-<slots>.webp``) so the storefront images match the generated
        gallery without any filesystem dependency.
        """
        return [
            f"{item_code}-{slot + 1}{self.image_extension}"
            for slot in range(self.image_gallery_slots)
        ]
