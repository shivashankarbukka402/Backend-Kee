"""
Payment Session

A single payment attempt against a Draft Sales Order. One Sales Order can own
many sessions (retries) but only one successful session may reach ``Paid`` and
own a Payment Entry.

Statuses: Pending -> Processing -> Paid | Failed | Cancelled
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class PaymentSession(Document):
    pass