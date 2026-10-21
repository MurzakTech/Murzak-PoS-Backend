"""
Purchase receipts (goods received from a supplier) and purchase returns (a debit note against a purchase invoice).

These are the calls behind the Purchases > Receipts screens. They are reached as
`murzak_pos.api.purchase.<name>` (purchase.py re-exports them), which is where the screens look for them.

Every call is limited to the businesses the caller belongs to. A receipt is submitted as soon as it is
saved, because that is what puts the goods into stock.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from murzak_pos.api.access_control import ensure_record_access, require_stock_role
from murzak_pos.api.payment_gateway_common import resolve_company

MAX_PAGE_SIZE = 100


def _reply(data=None, message: str | None = None) -> dict:
	result = {"success": True}
	if message:
		result["message"] = message
	if data is not None:
		result["data"] = data
	return result


def _lines(items) -> list[dict]:
	"""The lines of a request, read from JSON if need be and checked for the basics."""
	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not items:
		frappe.throw(_("Add at least one item."), frappe.ValidationError)
	return list(items)


def requested_quantities(items) -> dict[str, float]:
	"""Quantity asked for per item code. Every line needs an item and a quantity above zero."""
	wanted: dict[str, float] = {}
	for line in items:
		code = (line.get("item_code") or "").strip()
		if not code:
			frappe.throw(_("Every line needs an item code."), frappe.ValidationError)
		if flt(line.get("qty")) <= 0:
			frappe.throw(
				_("The quantity of {0} must be more than zero.").format(code), frappe.ValidationError
			)
		wanted[code] = wanted.get(code, 0) + flt(line["qty"])
	return wanted


def share_over_lines(
	wanted: dict[str, float], available_lines: list[tuple[int, str, float]]
) -> dict[int, float]:
	"""
	Spread each item's quantity over its lines, in order, up to what each line can still give.

	`available_lines` is (line number, item code, quantity still returnable). Returns line number -> quantity.
	Asking for more than is left refuses the whole request, naming the item and how much is left.
	"""
	taken: dict[int, float] = {}
	for code, qty in wanted.items():
		remaining = qty
		left = 0.0
		for number, line_code, can_give in available_lines:
			if line_code != code:
				continue
			left += can_give
			take = min(remaining, can_give)
			if take > 0:
				taken[number] = take
				remaining -= take
		if remaining > 1e-9:
			frappe.throw(
				_("Cannot return more than is left to return for {0}. Quantity left: {1}").format(code, left),
				frappe.ValidationError,
			)
	return taken


# --- purchase receipts ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def create_purchase_receipt(
	supplier: str,
	items,
	company: str | None = None,
	posting_date: str | None = None,
	warehouse: str | None = None,
) -> dict:
	"""Record goods received from a supplier. The receipt is submitted, so the stock goes up at once."""
	require_stock_role()
	company = resolve_company(company)
	supplier = (supplier or "").strip()
	if not supplier or not frappe.db.exists("Supplier", supplier):
		frappe.throw(_("Choose a supplier."), frappe.ValidationError)
	ensure_record_access("Supplier", supplier)

	rows = []
	for line in _lines(items):
		code = (line.get("item_code") or "").strip()
		if not code:
			frappe.throw(_("Every line needs an item code."), frappe.ValidationError)
		if flt(line.get("qty")) <= 0:
			frappe.throw(
				_("The quantity of {0} must be more than zero.").format(code), frappe.ValidationError
			)
		ensure_record_access("Item", code)
		if not frappe.db.get_value("Item", code, "is_purchase_item"):
			frappe.db.set_value("Item", code, "is_purchase_item", 1, update_modified=False)
		target = line.get("warehouse") or warehouse
		if not target:
			frappe.throw(_("Choose a warehouse for {0}.").format(code), frappe.ValidationError)
		if frappe.db.get_value("Warehouse", target, "company") != company:
			frappe.throw(
				_("Warehouse {0} does not belong to {1}.").format(target, company), frappe.PermissionError
			)
		rows.append(
			{"item_code": code, "qty": flt(line["qty"]), "rate": flt(line.get("rate")), "warehouse": target}
		)

	doc = frappe.get_doc(
		{
			"doctype": "Purchase Receipt",
			"supplier": supplier,
			"company": company,
			"posting_date": getdate(posting_date) if posting_date else None,
			"set_posting_time": 1 if posting_date else 0,
			"items": rows,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return _reply(
		{"name": doc.name, "grand_total": doc.grand_total}, _("Purchase Receipt created successfully")
	)


def _receipt_dict(doc) -> dict:
	return {
		"name": doc.name,
		"supplier": doc.supplier,
		"supplier_name": doc.supplier_name,
		"company": doc.company,
		"posting_date": str(doc.posting_date),
		"docstatus": doc.docstatus,
		"status": doc.status,
		"total": flt(doc.total),
		"grand_total": flt(doc.grand_total),
		"items_count": len(doc.items),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"rate": flt(row.rate),
				"amount": flt(row.amount),
				"warehouse": row.warehouse,
			}
			for row in doc.items
		],
	}


@frappe.whitelist()
def get_purchase_receipt(name: str) -> dict:
	require_stock_role()
	if not name or not frappe.db.exists("Purchase Receipt", name):
		frappe.throw(_("Purchase Receipt {0} was not found.").format(name), frappe.DoesNotExistError)
	ensure_record_access("Purchase Receipt", name)
	return _reply(_receipt_dict(frappe.get_doc("Purchase Receipt", name)))


@frappe.whitelist()
def list_purchase_receipts(
	company: str | None = None,
	page: int = 1,
	page_size: int = 20,
	supplier: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	docstatus=None,
) -> dict:
	"""A page of the business's receipts, newest first. `data.count` is the total, for the page buttons."""
	require_stock_role()
	company = resolve_company(company)
	size = max(1, min(cint(page_size) or 20, MAX_PAGE_SIZE))
	page = max(1, cint(page) or 1)

	filters: dict = {"company": company, "is_return": 0}
	if supplier:
		filters["supplier"] = ["like", f"%{supplier}%"]
	if from_date and to_date:
		filters["posting_date"] = ["between", [from_date, to_date]]
	elif from_date:
		filters["posting_date"] = [">=", from_date]
	elif to_date:
		filters["posting_date"] = ["<=", to_date]
	if docstatus not in (None, ""):
		filters["docstatus"] = cint(docstatus)

	rows = frappe.get_all(
		"Purchase Receipt",
		filters=filters,
		fields=[
			"name",
			"supplier",
			"supplier_name",
			"company",
			"posting_date",
			"docstatus",
			"status",
			"total",
			"grand_total",
		],
		order_by="posting_date desc, creation desc",
		limit_start=(page - 1) * size,
		limit_page_length=size,
	)
	counts = {}
	if rows:
		for row in frappe.get_all(
			"Purchase Receipt Item",
			filters={"parent": ["in", [r.name for r in rows]]},
			fields=["parent", "count(name) as n"],
			group_by="parent",
		):
			counts[row.parent] = row.n
	for row in rows:
		row["items_count"] = counts.get(row.name, 0)
		row["posting_date"] = str(row.posting_date)

	total = frappe.db.count("Purchase Receipt", filters)
	return _reply({"data": rows, "count": total})


# --- purchase returns (debit note against a purchase invoice) --------------------------------------


@frappe.whitelist(methods=["POST"])
def create_purchase_return(
	return_against: str,
	items,
	company: str | None = None,
	posting_date: str | None = None,
	remarks: str | None = None,
) -> dict:
	"""
	Send goods on a purchase invoice back to the supplier, as a debit note.

	ERPNext builds the return with every line at the quantity that can still go back, counting earlier
	returns, so that is where the limit comes from. Only the lines and quantities asked for are kept.
	"""
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	require_stock_role()
	company = resolve_company(company)
	if not return_against or not frappe.db.exists("Purchase Invoice", return_against):
		frappe.throw(
			_("Purchase Invoice {0} was not found.").format(return_against), frappe.DoesNotExistError
		)
	ensure_record_access("Purchase Invoice", return_against)
	invoice = frappe.db.get_value(
		"Purchase Invoice", return_against, ["docstatus", "is_return", "company"], as_dict=True
	)
	if invoice.company != company:
		frappe.throw(
			_("Purchase Invoice {0} was not found.").format(return_against), frappe.DoesNotExistError
		)
	if invoice.docstatus != 1:
		frappe.throw(_("Only a submitted purchase invoice can have goods returned."), frappe.ValidationError)
	if invoice.is_return:
		frappe.throw(_("Items cannot be returned from a return."), frappe.ValidationError)

	wanted = requested_quantities(_lines(items))
	return_doc = make_return_doc("Purchase Invoice", return_against)
	taken = share_over_lines(
		wanted, [(line.idx, line.item_code, -flt(line.qty)) for line in return_doc.items]
	)

	# Setting the table renumbers the lines, so the quantities are carried over in order, not by number
	kept = [(line, taken[line.idx]) for line in return_doc.items if line.idx in taken]
	return_doc.set("items", [line for line, _qty in kept])
	for line, qty in zip(return_doc.items, (qty for _line, qty in kept), strict=True):
		line.qty = -qty
		if line.meta.has_field("received_qty"):
			line.received_qty = -qty
	if posting_date:
		return_doc.posting_date = getdate(posting_date)
		return_doc.set_posting_time = 1
	if remarks:
		return_doc.remarks = remarks
	return_doc.insert(ignore_permissions=True)
	return_doc.submit()
	return _reply(
		{"name": return_doc.name, "return_against": return_against},
		_("Purchase Return created successfully"),
	)
