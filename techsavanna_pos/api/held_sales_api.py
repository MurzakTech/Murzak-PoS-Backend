"""
Held sales: bills put on hold at a till (a table, a tab) and shared by every till of a business.
Held bills are not invoices: saving or deleting one never touches stock, accounts or reports.

Contract: HELD_SALES_SERVER_API.md in the Murzak-PoS-Frontend repository.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import frappe
from frappe import _
from frappe.utils import cint, flt

from techsavanna_pos.api.payment_gateway_common import resolve_company

MAX_LINES = 300
MAX_PAYLOAD = 256 * 1024
MAX_LABEL = 40
MAX_FIELD = 140
KEEP_DAYS = 30


class _Refused(Exception):
	"""A request the server declines, with a reason in plain words."""


def _person(user):
	return frappe.db.get_value("User", user, "full_name") or user


def _utc_now() -> datetime:
	return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_utc(value) -> datetime | None:
	"""
	Read a date and time as a naive UTC datetime, which is how the table keeps `held_at`.

	The till sends ISO text such as 2026-10-10T10:00:00.000Z. A value with no zone is taken as UTC.
	"""
	if not value:
		return None
	if isinstance(value, datetime):
		moment = value
	else:
		text = str(value).strip()
		if text[-1:] in ("Z", "z"):
			text = text[:-1] + "+00:00"
		try:
			moment = datetime.fromisoformat(text)
		except ValueError:
			return None
	if moment.tzinfo is not None:
		moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
	return moment


def _iso(value) -> str | None:
	"""Give a stored UTC time back in the same shape the till sent it: 2026-10-10T10:00:00.000Z."""
	moment = _to_utc(value)
	if moment is None:
		return None
	return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _clean_id(value) -> str:
	"""Ids are text chosen by the till; older bills may have sent a number."""
	held_id = "" if value is None else str(value).strip()
	if not held_id:
		raise _Refused(_("A bill id is required."))
	if len(held_id) > MAX_FIELD:
		raise _Refused(_("The bill id is too long."))
	return held_id


def _payload_text(payload) -> str:
	"""Check the sale against the limits and return it as JSON text, ready to store."""
	too_large = _Refused(_("This bill is too large to hold."))

	if isinstance(payload, str):
		if len(payload.encode("utf-8")) > MAX_PAYLOAD:
			raise too_large
		try:
			payload = json.loads(payload)
		except ValueError:
			raise _Refused(_("This bill could not be read."))

	if not isinstance(payload, dict):
		raise _Refused(_("This bill could not be read."))

	cart = payload.get("cart")
	if cart is not None and not isinstance(cart, list):
		raise _Refused(_("This bill could not be read."))
	if len(cart or []) > MAX_LINES:
		raise _Refused(_("This bill has more than {0} lines, which is too many to hold.").format(MAX_LINES))

	text = json.dumps(payload, separators=(",", ":"))
	if len(text.encode("utf-8")) > MAX_PAYLOAD:
		raise too_large
	return text


def _read_payload(text):
	try:
		return json.loads(text or "{}")
	except ValueError:
		return {}


def _find(company, held_id):
	return frappe.db.get_value("POS Held Sale", {"company": company, "held_id": held_id})


def _row(d):
	return {
		"id": d.held_id,
		"label": d.label or "",
		"customer": d.customer_name or "",
		"item_count": d.item_count or 0,
		"total": d.total or 0,
		"warehouse": d.warehouse,
		"held_at": _iso(d.held_at),
		"held_by": d.owner,
		"held_by_name": _person(d.owner),
		"modified": str(d.modified),
		"payload": _read_payload(d.payload),
	}


@frappe.whitelist(methods=["POST"])
def list_held_sales(company=None, warehouse=None):
	"""The open bills of a business, oldest first; with `warehouse`, that store's bills and store-less ones."""
	company = resolve_company(company)
	rows = frappe.get_all(
		"POS Held Sale",
		filters={"company": company},
		fields=[
			"held_id",
			"label",
			"customer_name",
			"item_count",
			"total",
			"warehouse",
			"held_at",
			"owner",
			"modified",
			"payload",
		],
		order_by="held_at asc",
	)
	if warehouse:  # bills for this store, and bills held with no store
		rows = [r for r in rows if not r.warehouse or r.warehouse == warehouse]
	return {"success": True, "data": {"held_sales": [_row(frappe._dict(r)) for r in rows]}}


@frappe.whitelist(methods=["POST"])
def save_held_sale(
	id, payload, company=None, label="", warehouse=None, held_at=None, customer="", item_count=0, total=0
):
	"""Add a bill, or replace the one with the same id. Saving the same bill twice changes nothing."""
	company = resolve_company(company)

	try:
		held_id = _clean_id(id)
		text = _payload_text(payload)
		held_time = _to_utc(held_at) if held_at else _utc_now()
		if held_time is None:
			raise _Refused(_("The time this bill was held could not be read."))
	except _Refused as refusal:
		return {"success": False, "message": str(refusal)}

	values = {
		"held_id": held_id,
		"company": company,
		"warehouse": warehouse or None,
		"label": (label or "").strip()[:MAX_LABEL],
		"customer_name": (customer or "")[:MAX_FIELD],
		"item_count": cint(item_count),
		"total": flt(total),
		"held_at": held_time,
		"payload": text,
	}

	# Access was checked by resolve_company above, so the save itself skips the desk permissions
	name = _find(company, held_id)
	doc = frappe.get_doc("POS Held Sale", name) if name else frappe.new_doc("POS Held Sale")
	doc.update(values)
	try:
		doc.save(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# The till retried while the first request was still saving: that one won, so replace its bill
		name = _find(company, held_id)
		if not name or not doc.is_new():
			raise
		doc = frappe.get_doc("POS Held Sale", name)
		doc.update(values)
		doc.save(ignore_permissions=True)

	return {
		"success": True,
		"data": {
			"id": doc.held_id,
			"held_by": doc.owner,
			"held_by_name": _person(doc.owner),
			"modified": str(doc.modified),
		},
	}


@frappe.whitelist(methods=["POST"])
def delete_held_sale(id, company=None):
	"""
	Remove a bill. This is also how a till brings a bill back, so `deleted` says whether this
	request was the one that removed it: false (not an error) when the bill is already gone.
	"""
	company = resolve_company(company)
	try:
		held_id = _clean_id(id)
	except _Refused as refusal:
		return {"success": False, "message": str(refusal)}

	# One statement, so that of two tills deleting at the same moment only one removes the row
	frappe.db.sql("DELETE FROM `tabPOS Held Sale` WHERE company=%s AND held_id=%s", (company, held_id))
	deleted = frappe.db.sql("SELECT ROW_COUNT()")[0][0] > 0
	return {"success": True, "data": {"id": held_id, "deleted": bool(deleted)}}


def purge_old_held_sales():
	"""
	Daily job: remove abandoned bills so they do not pile up.

	A bill goes only when it was held more than KEEP_DAYS days ago and has not been saved again in that
	time. The second test keeps a tab that is still being added to, and a bill held offline long ago
	that only reached the server today, whatever the clock on the till said.
	"""
	held_cutoff = _utc_now() - timedelta(days=KEEP_DAYS)
	saved_cutoff = frappe.utils.add_days(frappe.utils.now_datetime(), -KEEP_DAYS)
	frappe.db.sql(
		"DELETE FROM `tabPOS Held Sale` WHERE (held_at IS NULL OR held_at < %s) AND modified < %s",
		(held_cutoff, saved_cutoff),
	)
