"""
Batches and expiry dates: switching a product to batch tracking, and giving each received line its batch.

The rules are in batch_rules.py. ERPNext does the selling side by itself: a sale that names no batch takes the one that
expires first (batch_order.pick_earliest_expiry_first) and an expired batch is refused (ERPNext's stock controller).

ERPNext lets a product change to batch tracking only before its first submitted transaction. A product with history
says so in plain words; moving its stock to a batch-tracked copy is a separate step (not in this file yet).
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from murzak_pos.api import batch_rules as rules
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.etims_optional import relax_etims_mandatory
from murzak_pos.api.overview_calc import nairobi_today


def tracking(item_code: str) -> dict:
	"""Whether a product tracks batches and expiry dates: {"batches": bool, "expiry": bool}."""
	row = frappe.db.get_value("Item", item_code, ["has_batch_no", "has_expiry_date"], as_dict=True)
	if not isinstance(row, dict):
		row = {}
	return {"batches": bool(row.get("has_batch_no")), "expiry": bool(row.get("has_expiry_date"))}


def _has_history(item_code: str) -> bool:
	return bool(frappe.db.exists("Stock Ledger Entry", {"item_code": item_code, "is_cancelled": 0}))


def ensure_batch(item_code: str, batch_no: str, expiry) -> str:
	"""
	The batch to receive into: the one named (made when new), else one made from the product code and expiry. A batch
	number already used for another product is refused, because ERPNext's batch numbers are shared by the whole site.
	"""
	name = rules.clean_batch_no(batch_no) or rules.auto_batch_no(item_code, expiry)
	existing = frappe.db.get_value("Batch", name, ["item", "expiry_date"], as_dict=True)
	if existing:
		if existing.item != item_code:
			frappe.throw(
				_("Batch {0} belongs to another product. Check the batch number on the pack.").format(name),
				frappe.ValidationError,
			)
		if expiry and not existing.expiry_date:
			frappe.db.set_value("Batch", name, "expiry_date", expiry, update_modified=False)
		return name
	batch = frappe.get_doc(
		{
			"doctype": "Batch",
			"batch_id": name,
			"item": item_code,
			"expiry_date": expiry,
		}
	)
	batch.insert(ignore_permissions=True)
	return batch.name


def checked_expiry(item_code: str, typed: dict | None, item_name: str | None = None):
	"""
	The expiry date to receive a batch-tracked product with, or None for a product that does not track expiry. Refuses,
	in plain words, a missing or impossible date and stock that has already expired.
	"""
	track = tracking(item_code)
	typed = typed or {}
	if not track["batches"] or not (track["expiry"] or typed.get("expiry_date")):
		return None
	label = item_name or frappe.db.get_value("Item", item_code, "item_name") or item_code
	expiry, problem = rules.check_expiry(typed.get("expiry_date"), nairobi_today(datetime.now(timezone.utc)))
	if problem == rules.MISSING:
		frappe.throw(_("Enter the expiry date printed on {0}.").format(label), frappe.ValidationError)
	if problem == rules.BAD:
		frappe.throw(_("The expiry date of {0} is not a real date.").format(label), frappe.ValidationError)
	if problem == rules.PAST:
		frappe.throw(
			_(
				"{0} has already expired. Do not put expired stock on the shelf; return it to the supplier."
			).format(label),
			frappe.ValidationError,
		)
	return expiry


def check_lines(item_codes, batch_of: dict) -> None:
	"""Check every batch-tracked product of a delivery before anything is saved, so a refusal changes nothing."""
	for code in dict.fromkeys(c for c in item_codes if c):
		checked_expiry(code, (batch_of or {}).get(code))


def apply_to_row(row, item_code: str, typed: dict | None, item_name: str | None = None) -> None:
	"""
	Give one received line its batch, when its product tracks batches. `row` is a document row or a dict. The expiry
	date is required for a product that tracks it, and stock that has already expired is refused.
	"""
	if not tracking(item_code)["batches"]:
		return
	typed = typed or {}
	expiry = checked_expiry(item_code, typed, item_name)
	batch = ensure_batch(item_code, typed.get("batch_no"), expiry)
	values = {"batch_no": batch, "use_serial_batch_fields": 1}
	if isinstance(row, dict):
		row.update(values)
	else:
		for key, value in values.items():
			row.set(key, value)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=120, seconds=60 * 60)
def set_batch_tracking(item_code: str, on, company: str | None = None) -> dict:
	"""
	Switch a product to (or off) tracking batches and expiry dates. Allowed only before the product's first sale or
	delivery, as ERPNext requires; a product with history is refused with a plain sentence.
	"""
	from murzak_pos.api.access_control import require_catalogue_role
	from murzak_pos.api.payment_gateway_common import resolve_company

	require_catalogue_role()
	resolve_company(company)
	ensure_record_access("Item", item_code)
	want = str(on).strip().lower() in ("1", "true", "yes", "on")
	item = frappe.get_doc("Item", item_code)
	if bool(item.has_batch_no) == want and bool(item.has_expiry_date) == want:
		return {"success": True, "message": _("Saved."), "data": {"item_code": item_code, "batches": want}}
	if not item.is_stock_item:
		frappe.throw(
			_("{0} is not kept in stock, so it has no batches.").format(item.item_name),
			frappe.ValidationError,
		)
	if bool(item.has_batch_no) != want and _has_history(item_code):
		frappe.throw(
			_(
				"{0} has already been sold or received, so batch tracking cannot be changed on it. Ask Murzak support to move its stock to a batch-tracked copy."
			).format(item.item_name),
			frappe.ValidationError,
		)
	item.has_batch_no = 1 if want else 0
	item.has_expiry_date = 1 if want else 0
	# Batches are made at receiving, with the expiry printed on the pack, never from a number series
	item.create_new_batch = 0
	relax_etims_mandatory(item)
	item.save(ignore_permissions=True)
	return {"success": True, "message": _("Saved."), "data": {"item_code": item_code, "batches": want}}
