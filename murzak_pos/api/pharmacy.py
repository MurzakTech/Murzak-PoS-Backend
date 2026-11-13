"""
Selling prescription-only medicines: which items on a sale are flagged, and the dispensing record a sale of
them must carry. The rules themselves are in pharmacy_rules.py.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, getdate, nowdate

from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company
from murzak_pos.api.pharmacy_rules import (
	REGISTER_MAX_DAYS,
	check,
	clean_dispensing,
	controlled_register,
	dispensing_fields,
	register_rows,
	register_summary,
	remark,
	rx_codes,
)

FLAG_FIELD = "custom_prescription_only"


def prescription_items(item_codes) -> list[str]:
	"""The items among these that are marked prescription-only. None when this site does not have the flag yet."""
	codes = [c for c in dict.fromkeys(item_codes) if c]
	if not codes or not frappe.get_meta("Item").has_field(FLAG_FIELD):
		return []
	flagged = frappe.get_all("Item", filters={"item_code": ["in", codes], FLAG_FIELD: 1}, pluck="item_code")
	return [code for code in codes if code in set(flagged)]


def review_sale(items, dispensing, sold_offline: bool) -> dict:
	"""
	What a sale's prescription-only items mean for its invoice: {"note": the line for the remarks or None, "fields":
	the dispensing record as separate invoice fields (empty when the sale has none)}.
	Refuses the sale when it has them and no complete dispensing record.
	"""
	flagged = prescription_items([row.get("item_code") for row in items])
	record = clean_dispensing(dispensing)
	status = check(flagged, record, bool(sold_offline))
	if status == "refuse":
		names = ", ".join(flagged)
		frappe.throw(
			_(
				"This sale includes prescription-only medicines ({0}). Enter the patient's name and who prescribed it."
			).format(names),
			frappe.ValidationError,
		)
	if not flagged:
		return {"note": None, "fields": {}}
	dispenser = frappe.db.get_value("User", frappe.session.user, "full_name") or ""
	return {
		"note": remark(flagged, record, dispenser, status),
		"fields": dispensing_fields(flagged, record, dispenser, status),
	}


def sale_remark(items, dispensing, sold_offline: bool) -> str | None:
	"""
	The line to write on an invoice for its prescription-only items, or None when it has none.
	Refuses the sale when it has them and no complete dispensing record.
	"""
	return review_sale(items, dispensing, sold_offline)["note"]


def write_dispensing_fields(doc, fields: dict) -> None:
	"""Put the dispensing record on the invoice's own fields, on a site that has them (older sites keep the remark only)."""
	if not fields:
		return
	meta = doc.meta
	for fieldname, value in fields.items():
		if meta.has_field(fieldname):
			doc.set(fieldname, value)


# --- the dispensing register --------------------------------------------------------------------------------

REGISTER_DOCTYPES = ("POS Invoice", "Sales Invoice")
REGISTER_FIELDS = (
	"custom_rx_status",
	"custom_rx_items",
	"custom_rx_patient",
	"custom_rx_prescriber",
	"custom_rx_reference",
	"custom_rx_dispensed_by",
)
REGISTER_LIMIT = 5000


def _register_dates(from_date, to_date):
	end = getdate(to_date) if to_date else getdate(nowdate())
	start = getdate(from_date) if from_date else add_days(end, -29)
	if start > end:
		frappe.throw(_("The start date is after the end date."), frappe.ValidationError)
	if date_diff(end, start) >= REGISTER_MAX_DAYS:
		frappe.throw(_("Choose a period of a year or less."), frappe.ValidationError)
	return start, end


@frappe.whitelist()
def get_dispensing_register(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	warehouse: str | None = None,
) -> dict:
	"""
	Every prescription-only medicine sold in the period (30 days to today unless said): date, medicine, quantity,
	batch, patient, prescriber, prescription reference and who dispensed it. For inspections.

	It names patients, so only the owner and managers may read it. Sales made before the dispensing fields existed
	carry the record in their remarks only and are not listed.
	"""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	if warehouse:
		ensure_record_access("Warehouse", warehouse)
	start, end = _register_dates(from_date, to_date)

	invoices, lines = [], []
	for doctype in REGISTER_DOCTYPES:
		meta = frappe.get_meta(doctype)
		if not all(meta.has_field(f) for f in REGISTER_FIELDS):
			continue
		filters = {
			"company": company,
			"docstatus": 1,
			"custom_rx_status": ["is", "set"],
			"posting_date": ["between", [start, end]],
		}
		if doctype == "Sales Invoice":
			# A shift's POS sales merged into one Sales Invoice copy the first sale's fields: the POS sales are listed
			# themselves, so the merged invoice is left out or they would be counted twice
			filters["is_consolidated"] = 0
		found = frappe.get_all(
			doctype,
			filters=filters,
			fields=["name", "posting_date", "posting_time", "is_return", *REGISTER_FIELDS],
			order_by="posting_date desc, posting_time desc",
			limit_page_length=REGISTER_LIMIT,
		)
		if not found:
			continue
		invoices.extend(found)
		codes = sorted({code for inv in found for code in rx_codes(inv.get("custom_rx_items"))})
		lines.extend(
			frappe.get_all(
				f"{doctype} Item",
				filters={"parent": ["in", [inv.name for inv in found]], "item_code": ["in", codes]},
				fields=["parent", "item_code", "item_name", "qty", "uom", "batch_no", "warehouse"],
				limit_page_length=REGISTER_LIMIT * 5,
			)
		)

	rows = register_rows(invoices, lines, warehouse or None)
	return {
		"success": True,
		"data": {
			"company": company,
			"from_date": str(start),
			"to_date": str(end),
			"rows": rows,
			"summary": register_summary(rows),
			"truncated": len(invoices) >= REGISTER_LIMIT,
		},
	}


# --- the controlled medicines register ------------------------------------------------------------------------

CONTROLLED_FIELD = "custom_controlled_drug"

_POS_SALES_SQL = """
	select item.item_code as item_code, item.warehouse as warehouse, -item.stock_qty as qty,
		inv.posting_date as date, inv.posting_time as time, inv.name as reference, 'POS Invoice' as voucher_type
		{rx_fields}
	from `tabPOS Invoice Item` item
	join `tabPOS Invoice` inv on inv.name = item.parent
	where inv.company = %(company)s and inv.docstatus = 1 and inv.posting_date <= %(end)s
		and item.item_code in %(codes)s {warehouse}
"""


def _controlled_items(company: str) -> dict:
	if not frappe.get_meta("Item").has_field(CONTROLLED_FIELD):
		return {}
	rows = frappe.get_all(
		"Item",
		filters={"custom_company": company, CONTROLLED_FIELD: 1},
		fields=["name", "item_name"],
		limit_page_length=0,
	)
	return {r.name: r.item_name or r.name for r in rows}


def _ledger_entries(company: str, codes: list, end, warehouse: str | None) -> list[dict]:
	"""Stock movements from the ledger, without the merged shift invoices (their POS sales are read one by one instead)."""
	filters = {"company": company, "item_code": ["in", codes], "is_cancelled": 0, "posting_date": ["<=", end]}
	if warehouse:
		filters["warehouse"] = warehouse
	rows = frappe.get_all(
		"Stock Ledger Entry",
		filters=filters,
		fields=[
			"item_code",
			"warehouse",
			"actual_qty",
			"posting_date",
			"posting_time",
			"voucher_type",
			"voucher_no",
		],
		order_by="posting_date asc, posting_time asc, creation asc",
		limit_page_length=0,
	)
	sales = {r.voucher_no for r in rows if r.voucher_type == "Sales Invoice"}
	merged = (
		set(
			frappe.get_all(
				"Sales Invoice", filters={"name": ["in", list(sales)], "is_consolidated": 1}, pluck="name"
			)
		)
		if sales
		else set()
	)
	rx = _rx_of("Sales Invoice", list(sales - merged))
	return [
		{
			"item_code": r.item_code,
			"warehouse": r.warehouse,
			"qty": r.actual_qty,
			"date": r.posting_date,
			"time": r.posting_time,
			"voucher_type": r.voucher_type,
			"reference": r.voucher_no,
			**rx.get(r.voucher_no, {}),
		}
		for r in rows
		if not (r.voucher_type == "Sales Invoice" and r.voucher_no in merged)
	]


def _rx_of(doctype: str, names: list) -> dict:
	if not names or not all(frappe.get_meta(doctype).has_field(f) for f in REGISTER_FIELDS):
		return {}
	rows = frappe.get_all(
		doctype,
		filters={"name": ["in", names]},
		fields=["name", "custom_rx_patient", "custom_rx_prescriber", "custom_rx_dispensed_by"],
		limit_page_length=0,
	)
	return {
		r.name: {
			"patient": r.custom_rx_patient,
			"prescriber": r.custom_rx_prescriber,
			"dispensed_by": r.custom_rx_dispensed_by,
		}
		for r in rows
	}


def _pos_sales(company: str, codes: list, end, warehouse: str | None) -> list[dict]:
	"""Every POS sale of these medicines (they move stock only when the shift is merged, so they are read one by one)."""
	has_rx = all(frappe.get_meta("POS Invoice").has_field(f) for f in REGISTER_FIELDS)
	rx_fields = (
		", inv.custom_rx_patient as patient, inv.custom_rx_prescriber as prescriber, inv.custom_rx_dispensed_by as dispensed_by"
		if has_rx
		else ""
	)
	params = {"company": company, "end": end, "codes": tuple(codes)}
	where_store = ""
	if warehouse:
		where_store = "and item.warehouse = %(warehouse)s"
		params["warehouse"] = warehouse
	return frappe.db.sql(
		_POS_SALES_SQL.format(rx_fields=rx_fields, warehouse=where_store), params, as_dict=True
	)


@frappe.whitelist()
def get_controlled_register(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	warehouse: str | None = None,
) -> dict:
	"""
	The controlled medicines register for the period (30 days to today unless said): for each medicine marked
	Controlled, the opening balance, every movement with the balance after it, and the closing balance. Sales name the
	patient, so only the owner and managers may read it.
	"""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	if warehouse:
		ensure_record_access("Warehouse", warehouse)
	start, end = _register_dates(from_date, to_date)
	items = _controlled_items(company)
	entries = []
	if items:
		codes = list(items)
		entries = _ledger_entries(company, codes, end, warehouse or None) + _pos_sales(
			company, codes, end, warehouse or None
		)
	return {
		"success": True,
		"data": {
			"company": company,
			"from_date": str(start),
			"to_date": str(end),
			"medicines": controlled_register(items, entries, start),
		},
	}
