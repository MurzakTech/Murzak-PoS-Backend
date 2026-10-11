"""
Live "something changed" messages for the till screens.

The server tells a screen only that a payment or a kitchen ticket changed; the screen then asks the
database for the facts. A missed message therefore costs a delay and never a wrong total, and the till
keeps polling (more slowly) as its safety net.

A message carries only the record name, its status, a reference and an amount: never keys, tokens or
phone numbers. It is sent after the database change is saved, so a screen that reacts at once finds
the new state.
"""

from __future__ import annotations

import frappe

EVENT = "murzak_pos_event"

# Payment records and the gateway each one belongs to
PAYMENT_DOCTYPES = {"MPESA Transaction Log": "M-Pesa", "POS Gateway Transaction": None}
# Users that never receive a screen message
NOT_RECIPIENTS = {"Guest", "Administrator"}


def payment_message(doc) -> dict:
	"""What a screen is told about a payment. Nothing secret or personal."""
	gateway = PAYMENT_DOCTYPES.get(doc.doctype) or doc.get("gateway")
	return {
		"kind": "payment",
		"doctype": doc.doctype,
		"name": doc.name,
		"status": doc.get("status"),
		"reference": doc.get("mpesa_receipt_number") or doc.get("confirmation_code"),
		"amount": doc.get("amount"),
		"gateway": gateway,
		"company": doc.get("company"),
	}


def kitchen_message(doc) -> dict:
	return {
		"kind": "kitchen",
		"doctype": doc.doctype,
		"name": doc.name,
		"status": doc.get("status"),
		"station": doc.get("station"),
		"number": doc.get("number"),
		"company": doc.get("company"),
	}


def users_of_company(company: str) -> list[str]:
	"""Enabled users who work for a business: by their company field, a company permission or a default."""
	if not company:
		return []
	users = set(frappe.get_all("User", filters={"custom_company": company, "enabled": 1}, pluck="name"))
	users.update(
		frappe.get_all("User Permission", filters={"allow": "Company", "for_value": company}, pluck="user")
	)
	users.update(
		frappe.get_all("DefaultValue", filters={"defkey": "Company", "defvalue": company}, pluck="parent")
	)
	enabled = set(frappe.get_all("User", filters={"name": ["in", list(users)], "enabled": 1}, pluck="name"))
	return sorted(enabled - NOT_RECIPIENTS) if enabled else []


def payment_recipients(doc) -> list[str]:
	"""The user who started the payment; a payment nobody started (a direct till payment) goes to the business."""
	starter = doc.get("owner")
	if starter and starter not in NOT_RECIPIENTS:
		return [starter]
	return users_of_company(doc.get("company"))


def send(message: dict, users: list[str]) -> None:
	for user in users:
		frappe.publish_realtime(EVENT, message=message, user=user, after_commit=True)


def _status_changed(doc) -> bool:
	before = doc.get_doc_before_save()
	return before is None or before.get("status") != doc.get("status")


def on_payment_update(doc, method=None) -> None:
	"""A payment reached a final answer (paid, failed, cancelled...). Still-pending records say nothing."""
	try:
		if doc.get("status") in (None, "", "Pending") or not _status_changed(doc):
			return
		send(payment_message(doc), payment_recipients(doc))
	except Exception:
		# A message that cannot be sent must never undo or block a payment
		frappe.log_error("Realtime payment message", frappe.get_traceback())


def on_kitchen_ticket_update(doc, method=None) -> None:
	"""A kitchen ticket was created or changed status: tell the business's staff screens to refresh."""
	try:
		if not _status_changed(doc):
			return
		send(kitchen_message(doc), users_of_company(doc.get("company")))
	except Exception:
		frappe.log_error("Realtime kitchen message", frappe.get_traceback())
