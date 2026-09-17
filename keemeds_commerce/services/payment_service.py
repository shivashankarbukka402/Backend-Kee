"""
Payment Service

Gateway-agnostic business logic for charging a Draft Sales Order. The service
owns the lifecycle of a :doc:`Payment Session` and translates a successful
verification into a submitted ERPNext Payment Entry linked to the Sales Order.

Lifecycle
---------
- ``create_payment`` — validates the Draft Sales Order and customer, generates
  a unique idempotency key + session token, and returns a gateway-ready payload.
  An active (Pending/Processing) session for the same order is replayed instead
  of creating a duplicate.
- ``verify_payment`` — validates ownership, amount and gateway signature,
  rejects duplicate/terminal verification, then completes the payment.
- ``complete_payment`` — (after successful verification) submits the Sales
  Order and creates+submits an ERPNext Payment Entry referencing it, links the
  entry to the session, and updates the Sales Order payment status. Idempotent:
  a session that is already Paid never creates a second Payment Entry.
- ``fail_payment`` / ``cancel_payment`` — record the failure reason, keep the
  Sales Order Draft, and allow a retry.
- ``retry_payment`` — re-uses the original Draft Sales Order (never duplicates
  the order) and opens a fresh session chained to the previous attempt.
- ``status`` — latest session status; falls back to the Payment Entry /
  ``advance_paid`` signals on the Sales Order.
- ``webhook`` — authenticates the gateway event, ignores duplicate callbacks,
  and drives the same completion path.
- ``history`` — every attempt for an order (transaction id, gateway, method,
  amount, status, timestamp).

Design notes
------------
- Validation reuses :class:`CheckoutService` for the authenticated-user ->
  customer link; the Sales Order's ``grand_total`` is the single source of
  truth for the payable amount (no pricing logic is duplicated here).
- Writes are atomic: the Sales Order submission, Payment Entry, session
  transition and Sales Order payment-status update land in one commit and are
  rolled back together on failure. Failed/cancelled payments leave the Sales
  Order untouched (Draft).
- All mutations run under a MySQL advisory lock scoped to the session (or the
  Sales Order for session creation) so concurrent callbacks/verifies/retries
  cannot double-charge.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from typing import Any

import frappe
from frappe import _
from frappe.utils import today

from keemeds_commerce.config.commerce_config import CommerceConfig
from keemeds_commerce.domain.payment import (
    PaymentCompletionDTO,
    PaymentHistoryDTO,
    PaymentHistoryItemDTO,
    PaymentSessionDTO,
    PaymentStatusDTO,
    PaymentWebhookDTO,
)
from keemeds_commerce.services.checkout_service import CheckoutService
from keemeds_commerce.services.payment_gateway import (
    GatewayIntent,
    PaymentGatewayAdapter,
    get_gateway,
)
from keemeds_commerce.utils.exceptions import (
    raise_not_found,
    raise_permission_error,
    raise_validation_error,
)

logger = logging.getLogger("keemeds_commerce.services.payment")

_SESSION_DOCTYPE = "Payment Session"
_GATEWAY_METHOD = "test"

#: Seconds a caller waits while another request serialises the same
#: session/order before giving up.
_LOCK_TIMEOUT_SECONDS = 5

#: Statuses that are final and forbid further verification/completion.
_TERMINAL_FOR_VERIFY = frozenset({"Paid", "Failed", "Cancelled", "Processing"})

_WEBHOOK_SERVICE_PREFIX = "keemeds."


class PaymentService:
    """
    Orchestrates payment sessions, verification and Payment Entry creation.
    """

    def __init__(
        self,
        config: CommerceConfig | None = None,
        checkout: CheckoutService | None = None,
        gateway: PaymentGatewayAdapter | None = None,
    ) -> None:
        self._config = config or CommerceConfig()
        self._checkout = checkout or CheckoutService(self._config)
        self._gateway = gateway or get_gateway(self._config)
        _ensure_payment_schema()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def create_payment(
        self,
        sales_order: str,
        shipping_address_name: str | None = None,
        billing_address_name: str | None = None,
        method: str | None = None,
    ) -> PaymentSessionDTO:
        """
        Create (or replay) a gateway-ready payment session for a Draft Sales
        Order owned by the session user.
        """
        user = self._current_user()
        so = self._load_owned_order(user, sales_order, require_draft=True)
        customer = so.customer
        amount = round(float(so.grand_total or 0.0), 2)
        if amount <= 0:
            raise_validation_error(_("Payable amount must be greater than zero."))
        currency = so.currency or "INR"

        lock = _lock_name("so", so.name)
        if not _acquire_lock(lock):
            raise_validation_error(_("Payment processing is busy. Please try again."))
        try:
            latest = self._latest_session(so.name)
            if latest and latest["status"] == "Paid":
                raise_validation_error(_("This order has already been paid."))
            active = self._active_session(so.name)
            if active:
                logger.info("Replaying active payment session %s for %s", active, user)
                return self._session_dto(frappe.get_doc(_SESSION_DOCTYPE, active))

            doc = frappe.get_doc(
                {
                    "doctype": _SESSION_DOCTYPE,
                    "user_email": user,
                    "customer": customer,
                    "sales_order": so.name,
                    "status": "Pending",
                    "gateway": (self._config.payment_gateway or "TEST").upper(),
                    "payment_method": method or _GATEWAY_METHOD,
                    "amount": amount,
                    "currency": currency,
                    "amount_in_paise": int(round(amount * 100)),
                    "idempotency_key": uuid.uuid4().hex,
                    "session_token": secrets.token_urlsafe(24),
                }
            )
            try:
                doc.flags.ignore_permissions = True
                doc.insert()
                intent = self._intent_for_doc(doc)
                payload = self._gateway.build_payload(intent)
                doc.signature = payload["signature"]
                doc.payload = json.dumps(payload, sort_keys=True)
                doc.save()
            except Exception:
                frappe.db.rollback()
                raise
            else:
                frappe.db.commit()
            logger.info(
                "Payment Session %s created for %s (amount %s %s)",
                doc.name,
                user,
                amount,
                currency,
            )
            return self._session_dto(frappe.get_doc(_SESSION_DOCTYPE, doc.name))
        finally:
            _release_lock(lock)

    def verify_payment(
        self,
        sales_order: str | None = None,
        session: str | None = None,
        amount: float = 0.0,
        signature: str = "",
        method: str | None = None,
    ) -> PaymentCompletionDTO:
        """
        Validate ownership, amount and gateway signature; reject duplicate
        verification; and complete the payment.
        """
        user = self._current_user()
        ps = self._session_for_user(user, sales_order, session)
        _assert_owned_by(user, ps)

        expected = round(float(ps.amount or 0.0), 2)
        if round(float(amount or 0.0), 2) != expected:
            raise_validation_error(
                _("Payment amount does not match the payable amount.")
            )
        so = frappe.get_doc("Sales Order", ps.sales_order)
        if expected != round(float(so.grand_total or 0.0), 2):
            raise_validation_error(_("Payable amount mismatch for this order."))
        if ps.status in _TERMINAL_FOR_VERIFY:
            raise_validation_error(
                self._verify_reject_reason(ps.status)
            )

        gv = self._gateway.verify(self._intent_for_doc(ps), expected, ps.currency, signature)
        if not gv.valid:
            raise_validation_error(gv.reason or _("Payment verification failed."))

        return self._serialised_complete(ps.name, transaction_id=gv.transaction_id, method=method)

    def complete_payment(
        self,
        sales_order: str | None = None,
        session: str | None = None,
        transaction_id: str | None = None,
        method: str | None = None,
    ) -> PaymentCompletionDTO:
        """
        Complete an already-verified payment idempotently (never a second
        Payment Entry), or raise when there is no session.
        """
        user = self._current_user()
        ps = self._session_for_user(user, sales_order, session)
        _assert_owned_by(user, ps)
        if ps.status == "Paid":
            return self._completion_dto(ps)
        if ps.status in ("Failed", "Cancelled"):
            raise_validation_error(
                _("A {0} payment cannot be completed. Retry it instead.").format(ps.status)
            )
        return self._serialised_complete(ps.name, transaction_id=transaction_id or "", method=method)

    def fail_payment(
        self,
        sales_order: str | None = None,
        session: str | None = None,
        reason: str = "Payment failed.",
    ) -> PaymentStatusDTO:
        """
        Mark the payment as failed; the Sales Order stays Draft and can be
        retried. Idempotent once failed/cancelled.
        """
        return self._set_failure("Failed", sales_order, session, reason)

    def cancel_payment(
        self,
        sales_order: str | None = None,
        session: str | None = None,
        reason: str = "Payment cancelled.",
    ) -> PaymentStatusDTO:
        """
        Mark the payment as cancelled (used by explicit cancellation UI).
        """
        return self._set_failure("Cancelled", sales_order, session, reason)

    def retry_payment(
        self,
        sales_order: str,
        shipping_address_name: str | None = None,
        billing_address_name: str | None = None,
        method: str | None = None,
    ) -> PaymentSessionDTO:
        """
        Open a fresh payment session for the existing Draft Sales Order.
        Never creates a duplicate order; preserves the payment history.
        """
        user = self._current_user()
        so = self._load_owned_order(user, sales_order, require_draft=True)
        amount = round(float(so.grand_total or 0.0), 2)
        if amount <= 0:
            raise_validation_error(_("Payable amount must be greater than zero."))
        currency = so.currency or "INR"

        lock = _lock_name("so", so.name)
        if not _acquire_lock(lock):
            raise_validation_error(_("Payment processing is busy. Please try again."))
        try:
            active = self._active_session(so.name)
            if active:
                return self._session_dto(frappe.get_doc(_SESSION_DOCTYPE, active))
            latest = self._latest_session(so.name)
            if latest and latest["status"] == "Paid":
                raise_validation_error(_("This order has already been paid."))

            doc = frappe.get_doc(
                {
                    "doctype": _SESSION_DOCTYPE,
                    "user_email": user,
                    "customer": so.customer,
                    "sales_order": so.name,
                    "status": "Pending",
                    "gateway": (self._config.payment_gateway or "TEST").upper(),
                    "payment_method": method or _GATEWAY_METHOD,
                    "amount": amount,
                    "currency": currency,
                    "amount_in_paise": int(round(amount * 100)),
                    "idempotency_key": uuid.uuid4().hex,
                    "session_token": secrets.token_urlsafe(24),
                    "retried_from": (
                        latest["name"]
                        if latest and latest["status"] in ("Failed", "Cancelled")
                        else ""
                    ),
                }
            )
            try:
                doc.flags.ignore_permissions = True
                doc.insert()
                payload = self._gateway.build_payload(self._intent_for_doc(doc))
                doc.signature = payload["signature"]
                doc.payload = json.dumps(payload, sort_keys=True)
                doc.save()
            except Exception:
                frappe.db.rollback()
                raise
            else:
                frappe.db.commit()
            logger.info("Payment Session %s retried for order %s", doc.name, so.name)
            return self._session_dto(frappe.get_doc(_SESSION_DOCTYPE, doc.name))
        finally:
            _release_lock(lock)

    def status(
        self,
        sales_order: str | None = None,
        session: str | None = None,
    ) -> PaymentStatusDTO:
        """
        Return the current payment status for an order or a specific session.
        """
        user = self._current_user()
        if session:
            ps = self._session_for_user(user, None, session)
            _assert_owned_by(user, ps)
            return PaymentStatusDTO(
                status=ps.status,
                sales_order=ps.sales_order,
                session=ps.name,
                amount=round(float(ps.amount or 0.0), 2),
                currency=ps.currency or "INR",
                transaction_id=ps.transaction_id or "",
                payment_entry=ps.payment_entry or "",
                failure_reason=ps.failure_reason or "",
            )
        so = self._load_owned_order(user, sales_order or "", require_draft=False)
        latest = self._latest_session(so.name)
        if latest:
            return PaymentStatusDTO(
                status=latest["status"],
                sales_order=so.name,
                session=latest["name"],
                amount=round(float(latest["amount"] or 0.0), 2),
                currency=latest["currency"] or "INR",
                transaction_id=latest["transaction_id"] or "",
                payment_entry=latest["payment_entry"] or "",
                failure_reason=latest["failure_reason"] or "",
            )
        status = _derive_paid_from_order(so)
        return PaymentStatusDTO(
            status=status,
            sales_order=so.name,
            amount=round(float(so.grand_total or 0.0), 2),
            currency=so.currency or "INR",
        )

    def history(self, sales_order: str) -> PaymentHistoryDTO:
        """
        Ordered payment history for an order owned by the session user.
        """
        user = self._current_user()
        so = self._load_owned_order(user, sales_order, require_draft=False)
        rows = frappe.db.sql(
            """
            SELECT name, status, gateway, payment_method, amount, currency,
                   transaction_id, sales_order, payment_entry, failure_reason,
                   creation
            FROM `tabPayment Session`
            WHERE sales_order = %s
            ORDER BY creation, name
            """,
            (so.name,),
            as_dict=True,
        )
        items = [
            PaymentHistoryItemDTO(
                transaction_id=(r["transaction_id"] or ""),
                gateway=(r["gateway"] or ""),
                payment_method=(r["payment_method"] or _GATEWAY_METHOD),
                amount=round(float(r["amount"] or 0.0), 2),
                currency=(r["currency"] or "INR"),
                status=r["status"],
                timestamp=r["creation"].strftime("%Y-%m-%d %H:%M:%S") if r["creation"] else "",
                session=r["name"],
                sales_order=so.name,
                payment_entry=(r["payment_entry"] or ""),
                failure_reason=(r["failure_reason"] or ""),
            )
            for r in rows
        ]
        return PaymentHistoryDTO(sales_order=so.name, items=items, total=len(items))

    def webhook(self, payload: dict) -> PaymentWebhookDTO:
        """
        Authenticate a gateway callback, ignore duplicates, and drive the
        completion/failure path. Single atomic commit per processed event.
        """
        if not isinstance(payload, dict):
            raise_validation_error(_("A webhook payload is required."))
        gv = self._gateway.authenticate_webhook(payload)
        if not gv.valid:
            raise_validation_error(gv.reason or _("Webhook authentication failed."))

        event_id = gv.extra.get("event_id") or ""
        ps = self._find_webhook_session(payload)
        session_name = ps["name"] if isinstance(ps, dict) else ps
        _record = _webhook_processed(event_id, payload.get("gateway") or self._gateway.name)
        if _record and _record[0] == "Completed":
            logger.info("Ignoring duplicate webhook event %s", event_id)
            return PaymentWebhookDTO(
                accepted=False, duplicate=True, event_id=event_id,
                session=session_name, sales_order=ps["sales_order"] or "",
                status="",
                transaction_id="",
            )

        lock = _lock_name("ps", session_name)
        if not _acquire_lock(lock):
            raise_validation_error(_("Webhook processing is busy. Please try again."))
        try:
            doc = frappe.get_doc(_SESSION_DOCTYPE, session_name)
            if gv.status == "Paid":
                if doc.status == "Paid":
                    return PaymentWebhookDTO(
                        accepted=False, duplicate=True, event_id=event_id,
                        session=doc.name, sales_order=doc.sales_order,
                        status="Paid", transaction_id=doc.transaction_id or "",
                    )
                doc.status = "Processing"
                if gv.transaction_id:
                    doc.transaction_id = gv.transaction_id
                doc.save(ignore_permissions=True)
                completion = self._complete_session(doc, transaction_id=gv.transaction_id or "")
                _record_webhook(event_id, doc.name, "Paid", payload)
                frappe.db.commit()
                return PaymentWebhookDTO(
                    accepted=True, duplicate=False, event_id=event_id,
                    session=doc.name, sales_order=doc.sales_order,
                    status=completion.status, transaction_id=completion.transaction_id,
                )
            # Failed event
            if doc.status == "Paid":
                return PaymentWebhookDTO(
                    accepted=False, duplicate=True, event_id=event_id,
                    session=doc.name, sales_order=doc.sales_order,
                    status="Paid", transaction_id=doc.transaction_id or "",
                )
            if doc.status in ("Failed", "Cancelled"):
                return PaymentWebhookDTO(
                    accepted=True, duplicate=True, event_id=event_id,
                    session=doc.name, sales_order=doc.sales_order,
                    status=doc.status, transaction_id=doc.transaction_id or "",
                )
            doc.status = "Failed"
            doc.failure_reason = gv.reason or "Payment failed at the gateway."
            if gv.transaction_id:
                doc.transaction_id = gv.transaction_id
            doc.save(ignore_permissions=True)
            frappe.db.set_value(
                "Sales Order", doc.sales_order, "payment_status", "Failed"
            )
            _record_webhook(event_id, doc.name, "Failed", payload)
            frappe.db.commit()
            logger.info("Webhook %s marked session %s failed", event_id, doc.name)
            return PaymentWebhookDTO(
                accepted=True, duplicate=False, event_id=event_id,
                session=doc.name, sales_order=doc.sales_order,
                status="Failed", transaction_id=doc.transaction_id or "",
            )
        finally:
            _release_lock(lock)

    # ------------------------------------------------------------------ #
    # Completion (atomic: Payment Entry + session + Sales Order status)
    # ------------------------------------------------------------------ #

    def _serialised_complete(
        self, session_name: str, transaction_id: str, method: str | None = None
    ) -> PaymentCompletionDTO:
        """Complete a session under its advisory lock (verify/complete path)."""
        lock = _lock_name("ps", session_name)
        if not _acquire_lock(lock):
            raise_validation_error(_("Payment processing is busy. Please try again."))
        try:
            doc = frappe.get_doc(_SESSION_DOCTYPE, session_name)
            if doc.status in _TERMINAL_FOR_VERIFY and doc.status != "Paid":
                raise_validation_error(self._verify_reject_reason(doc.status))
            if doc.status == "Paid":
                return self._completion_dto(doc)
            doc.status = "Processing"
            if method:
                doc.payment_method = method
            doc.save(ignore_permissions=True)
            completion = self._complete_session(doc, transaction_id=transaction_id or "")
            return completion
        finally:
            _release_lock(lock)

    def _complete_session(
        self, session_doc, transaction_id: str
    ) -> PaymentCompletionDTO:
        """
        Create + submit the Payment Entry, link it to the session, mark the
        session Paid and update the Sales Order payment status. Commits once.
        Assumes the caller holds the session advisory lock.
        """
        if session_doc.payment_entry:
            return self._completion_dto(session_doc)
        transaction_id = transaction_id or session_doc.transaction_id or ""
        payment_method = session_doc.payment_method or ""
        pe_name = self._create_payment_entry(session_doc, transaction_id, payment_method)
        session_doc.transaction_id = transaction_id
        session_doc.payment_entry = pe_name
        session_doc.status = "Paid"
        session_doc.save(ignore_permissions=True)
        frappe.db.set_value(
            "Sales Order", session_doc.sales_order, "payment_status", "Paid"
        )
        if payment_method:
            frappe.db.set_value(
                "Sales Order", session_doc.sales_order, "payment_method", payment_method
            )
        frappe.db.commit()
        logger.info(
            "Payment Entry %s submitted for order %s (session %s)",
            pe_name,
            session_doc.sales_order,
            session_doc.name,
        )
        return self._completion_dto(session_doc)

    def _create_payment_entry(self, session_doc, transaction_id: str, payment_method: str = "") -> str:
        """
        Build and submit an ERPNext Payment Entry referencing the Sales Order.
        The Draft Sales Order is submitted first because ERPNext only allows
        Payment Entry allocation against a submitted Sales Order; the standard
        accounting flow (controller, GL posting, advance_paid update) is used.
        No commit — the caller commits the whole transaction atomically.

        ERPNext's ``Payment Entry.validate`` → ``set_missing_values`` →
        ``get_account_details`` calls ``frappe.has_permission("Payment Entry",
        throw=True)`` which is a **global** permission check that ignores the
        document's ``flags.ignore_permissions``.  A Website User therefore
        cannot drive this code path without temporary elevation to
        Administrator.

        ``frappe.set_user()`` must be wrapped with full session-state
        preservation: the function overwrites ``session.sid`` with the
        plain username and replaces ``session.data`` (containing the CSRF
        token, session_ip, session_expiry, …) with an empty ``_dict()``.
        When ``Session.update()`` runs in ``after_response`` it would persist
        that corrupted state to Redis, causing the *next* request to fail
        session-resume and fall back to Guest — producing 403 FORBIDDEN on
        every authenticated endpoint.  We therefore snapshot the session
        dict and ``form_dict`` before elevation and restore them in the
        ``finally`` block.
        """
        so = frappe.get_doc("Sales Order", session_doc.sales_order)
        amount = round(float(session_doc.amount or 0.0), 2)
        buy_order = frappe.get_doc("Sales Order", so.name)

        mode_of_payment = self._config.payment_method_mode_map.get(
            (payment_method or "").lower(),
            self._config.payment_mode_of_payment,
        )

        pe = frappe.get_doc(
            {
                "doctype": "Payment Entry",
                "payment_type": "Receive",
                "posting_date": today(),
                "company": self._config.payment_company or so.company,
                "mode_of_payment": mode_of_payment,
                "party_type": "Customer",
                "party": so.customer,
                "paid_from": self._config.payment_paid_from_account,
                "paid_to": self._config.payment_paid_to_account,
                "paid_amount": amount,
                "received_amount": amount,
                "source_exchange_rate": 1.0,
                "target_exchange_rate": 1.0,
                "reference_no": transaction_id or session_doc.idempotency_key,
                "reference_date": today(),
                "references": [
                    {
                        "reference_doctype": "Sales Order",
                        "reference_name": so.name,
                        "total_amount": round(float(so.grand_total or 0.0), 2),
                        "allocated_amount": amount,
                    }
                ],
            }
        )
        pe.flags.ignore_permissions = True

        previous_user = frappe.session.user
        if previous_user == "Administrator":
            if buy_order.docstatus == 0:
                buy_order.flags.ignore_permissions = True
                buy_order.submit()
            pe.insert()
            pe.submit()
        else:
            saved_session = frappe.local.session.copy()
            saved_form_dict = frappe.local.form_dict
            try:
                frappe.set_user("Administrator")
                if buy_order.docstatus == 0:
                    buy_order.flags.ignore_permissions = True
                    buy_order.submit()
                pe.insert()
                pe.submit()
            finally:
                frappe.set_user(previous_user)
                frappe.local.session.update(saved_session)
                frappe.local.form_dict = saved_form_dict

        return pe.name

    # ------------------------------------------------------------------ #
    # Failure / status helpers
    # ------------------------------------------------------------------ #

    def _set_failure(
        self,
        status: str,
        sales_order: str | None,
        session: str | None,
        reason: str,
    ) -> PaymentStatusDTO:
        user = self._current_user()
        ps = self._session_for_user(user, sales_order, session)
        _assert_owned_by(user, ps)
        if ps.status == "Paid":
            raise_validation_error(
                _("An already paid payment cannot be marked {0}.").format(status)
            )
        if ps.status in ("Failed", "Cancelled"):
            return PaymentStatusDTO(
                status=ps.status, sales_order=ps.sales_order, session=ps.name,
                amount=round(float(ps.amount or 0.0), 2), currency=ps.currency or "INR",
                transaction_id=ps.transaction_id or "",
                payment_entry=ps.payment_entry or "",
                failure_reason=ps.failure_reason or "",
            )
        ps.status = status
        ps.failure_reason = reason or ""
        ps.save(ignore_permissions=True)
        frappe.db.set_value(
            "Sales Order", ps.sales_order, "payment_status", status
        )
        frappe.db.commit()
        logger.info("Payment Session %s marked %s (%s)", ps.name, status, reason)
        return PaymentStatusDTO(
            status=status, sales_order=ps.sales_order, session=ps.name,
            amount=round(float(ps.amount or 0.0), 2), currency=ps.currency or "INR",
            transaction_id=ps.transaction_id or "",
            failure_reason=reason or "",
        )

    # ------------------------------------------------------------------ #
    # Lookups / ownership
    # ------------------------------------------------------------------ #

    def _current_user(self) -> str:
        user = frappe.session.user
        if not user or user == "Guest":
            raise_validation_error(_("You must be logged in to perform this action."))
        return user

    def _load_owned_order(self, user: str, sales_order: str, *, require_draft: bool):
        if not sales_order:
            raise_validation_error(_("Sales Order is required."))
        if not frappe.db.exists("Sales Order", sales_order):
            raise_not_found("Sales Order", sales_order)
        so = frappe.get_doc("Sales Order", sales_order)
        customer = self._checkout._customer_for(user)["name"]
        if so.customer != customer:
            raise_permission_error()
        if require_draft and so.docstatus != 0:
            raise_validation_error(
                _("Only Draft sales orders can be paid; '{0}' is submitted.").format(
                    sales_order
                )
            )
        if (so.status or "Draft") == "Cancelled":
            raise_validation_error(_("A cancelled sales order cannot be paid."))
        return so

    def _session_for_user(self, user: str, sales_order: str | None, session: str | None):
        if session:
            if not frappe.db.exists(_SESSION_DOCTYPE, session):
                raise_not_found(_SESSION_DOCTYPE, session)
            doc = frappe.get_doc(_SESSION_DOCTYPE, session)
            if doc.user_email != user:
                raise_permission_error()
            if sales_order and doc.sales_order != sales_order:
                raise_validation_error(
                    _("Payment session does not belong to this order.")
                )
            return doc
        if not sales_order:
            raise_validation_error(_("Sales Order is required."))
        so = self._load_owned_order(user, sales_order, require_draft=False)
        latest = self._latest_session(so.name)
        if not latest:
            raise_validation_error(
                _("No payment session has been created for this order yet.")
            )
        return frappe.get_doc(_SESSION_DOCTYPE, latest["name"])

    def _find_webhook_session(self, payload: dict):
        session_name = str(payload.get("session") or "").strip()
        order = str(payload.get("sales_order") or "").strip()
        order_id = str(payload.get("order_id") or "").strip()
        if session_name and frappe.db.exists(_SESSION_DOCTYPE, session_name):
            return {"name": session_name, "sales_order": ""}
        if order_id:
            rows = frappe.db.sql(
                "SELECT name, sales_order FROM `tabPayment Session` "
                "WHERE idempotency_key = %s ORDER BY creation DESC LIMIT 1",
                (order_id,),
                as_dict=True,
            )
            if rows:
                return {"name": rows[0]["name"], "sales_order": rows[0]["sales_order"]}
        if order:
            rows = frappe.db.sql(
                "SELECT name, sales_order FROM `tabPayment Session` "
                "WHERE sales_order = %s ORDER BY creation DESC LIMIT 1",
                (order,),
                as_dict=True,
            )
            if rows:
                return {"name": rows[0]["name"], "sales_order": rows[0]["sales_order"]}
        raise_not_found(_SESSION_DOCTYPE, session_name or order or order_id)

    def _latest_session(self, sales_order: str) -> dict | None:
        rows = frappe.db.sql(
            """
            SELECT name, status, amount, currency, transaction_id,
                   payment_entry, failure_reason
            FROM `tabPayment Session`
            WHERE sales_order = %s
            ORDER BY creation DESC, name DESC
            LIMIT 1
            """,
            (sales_order,),
            as_dict=True,
        )
        return rows[0] if rows else None

    def _active_session(self, sales_order: str) -> str | None:
        rows = frappe.db.sql(
            """
            SELECT name FROM `tabPayment Session`
            WHERE sales_order = %s AND status IN ('Pending', 'Processing')
            ORDER BY creation DESC LIMIT 1
            """,
            (sales_order,),
        )
        return rows[0][0] if rows else None

    # ------------------------------------------------------------------ #
    # DTO assembly
    # ------------------------------------------------------------------ #

    def _intent_for_doc(self, doc) -> GatewayIntent:
        customer_name = frappe.db.get_value("Customer", doc.customer, "customer_name")
        return GatewayIntent(
            gateway=doc.gateway or (self._config.payment_gateway or "TEST").upper(),
            session=doc.name,
            sales_order=doc.sales_order,
            idempotency_key=doc.idempotency_key or "",
            amount=round(float(doc.amount or 0.0), 2),
            currency=doc.currency or "INR",
            customer=doc.customer or "",
            customer_name=customer_name or doc.customer or "",
            user_email=doc.user_email or "",
            payment_method=doc.payment_method or _GATEWAY_METHOD,
        )

    def _session_dto(self, doc) -> PaymentSessionDTO:
        payload: dict
        try:
            payload = json.loads(doc.payload or "{}") if doc.payload else {}
        except (TypeError, ValueError):
            payload = {}
        return PaymentSessionDTO(
            session=doc.name,
            session_token=doc.session_token or "",
            idempotency_key=doc.idempotency_key or "",
            sales_order=doc.sales_order or "",
            customer=doc.customer or "",
            customer_name=frappe.db.get_value("Customer", doc.customer, "customer_name") or "",
            user_email=doc.user_email or "",
            gateway=doc.gateway or "",
            payment_method=doc.payment_method or _GATEWAY_METHOD,
            amount=round(float(doc.amount or 0.0), 2),
            currency=doc.currency or "INR",
            amount_in_paise=int(doc.amount_in_paise or round(float(doc.amount or 0.0) * 100)),
            status=doc.status or "Pending",
            signature=doc.signature or "",
            payload=payload,
            created_on=doc.creation.strftime("%Y-%m-%d %H:%M:%S") if doc.creation else "",
        )

    def _completion_dto(self, doc) -> PaymentCompletionDTO:
        return PaymentCompletionDTO(
            success=True,
            session=doc.name,
            sales_order=doc.sales_order,
            payment_entry=doc.payment_entry or "",
            transaction_id=doc.transaction_id or "",
            status="Paid",
            amount=round(float(doc.amount or 0.0), 2),
            currency=doc.currency or "INR",
        )

    def _verify_reject_reason(self, status: str) -> str:
        if status == "Paid":
            return _("Payment is already verified and paid.")
        if status == "Processing":
            return _("Payment verification is already in progress.")
        return _("This payment session can no longer be verified.")


# ---------------------------------------------------------------------- #
# Module-level helpers
# ---------------------------------------------------------------------- #


def _assert_owned_by(user: str, session_doc) -> None:
    if session_doc.user_email != user:
        raise_permission_error()


def _derive_paid_from_order(so) -> str:
    grand_total = round(float(so.grand_total or 0.0), 2)
    if grand_total > 0 and float(so.advance_paid or 0.0) >= grand_total:
        return "Paid"
    return "Pending"


def _lock_name(scope: str, key: str) -> str:
    return f"keemeds_commerce_pay:{scope}:{key}"


def _acquire_lock(name: str) -> bool:
    """Acquire a MySQL advisory lock (connection-scoped, cross-process)."""
    value = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (name, _LOCK_TIMEOUT_SECONDS))
    return bool(value and value[0][0] == 1)


def _release_lock(name: str) -> None:
    """Best-effort release; never masks errors."""
    try:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (name,))
    except Exception:
        pass


def _webhook_service(gateway: str) -> str:
    return f"{_WEBHOOK_SERVICE_PREFIX}{gateway}.webhook"


def _webhook_processed(event_id: str, gateway: str):
    return frappe.db.get_value(
        "Integration Request",
        {
            "integration_request_service": _webhook_service(gateway),
            "status": "Completed",
            "data": event_id,
        },
        "name",
    )


def _record_webhook(event_id: str, session_name: str, status: str, payload: dict) -> None:
    """Record the processed event for callback idempotency (same transaction)."""
    doc = frappe.get_doc(
        {
            "doctype": "Integration Request",
            "integration_request_service": _webhook_service(
                payload.get("gateway") or "test"
            ),
            "status": "Completed",
            "request_description": str(status),
            "data": str(event_id),
            "reference_doctype": _SESSION_DOCTYPE,
            "reference_docname": session_name,
            "output": json.dumps(
                {
                    "event_id": event_id,
                    "session": session_name,
                    "status": status,
                },
                sort_keys=True,
            ),
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()


# ---------------------------------------------------------------------- #
# Schema bootstrap
# ---------------------------------------------------------------------- #


def _ensure_payment_schema() -> None:
    """
    Ensure the Sales Order ``payment_status`` and ``payment_method`` Custom
    Fields exist (idempotent).  Reuses the standard ERPNext Custom Field
    mechanism so order management can report a payment status and method
    without any custom DocType.
    """
    if not frappe.db.exists(
        "Custom Field", {"dt": "Sales Order", "fieldname": "payment_status"}
    ):
        frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "Sales Order",
                "fieldname": "payment_status",
                "label": "Payment Status",
                "fieldtype": "Select",
                "options": "Pending\nProcessing\nPaid\nFailed\nCancelled",
                "default": "Pending",
                "insert_after": "grand_total",
            }
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.clear_cache(doctype="Sales Order")
        frappe.db.commit()

    if not frappe.db.exists(
        "Custom Field", {"dt": "Sales Order", "fieldname": "payment_method"}
    ):
        frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "Sales Order",
                "fieldname": "payment_method",
                "label": "Payment Method",
                "fieldtype": "Data",
                "read_only": 1,
                "insert_after": "payment_status",
            }
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.clear_cache(doctype="Sales Order")
        frappe.db.commit()


def webhook_signature(payload: dict, gateway: PaymentGatewayAdapter | None = None) -> str:
    """
    Sign a webhook payload for tests/development gateways. Mirrors the canonical
    field order used by :meth:`PaymentGatewayAdapter.authenticate_webhook`.
    """
    from keemeds_commerce.services.payment_gateway import get_gateway

    g = gateway or get_gateway(CommerceConfig())
    if hasattr(g, "sign_webhook"):
        return g.sign_webhook(payload)
    raise_validation_error(_("Gateway does not support test signing."))