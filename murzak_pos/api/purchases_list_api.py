"""
The Purchases page: one list of orders, bills and returns, the panel for one purchase, the guided Receive goods
step and the direct bill (ticket P4-5).

`list_purchases`, `get_purchase`, `receive_goods` and `create_direct_bill` return what the contract
docs/redesign/P4-5-contract.md describes. They are new; no existing call, report or record is changed.

How it is built
- The rules and the arithmetic are in purchases_list_calc.py (plain functions, no database). This file reads and
  writes the database in small functions, one job each, and joins the two.
- Receive goods is ONE transaction. It reuses the call that already makes a goods received note from an order
  (stock.create_pr_from_po, which raises the stock) and ERPNext's own way of making a bill from a receipt (the same
  one create_purchase_invoice_from_grn uses). The existing GRN and bill calls each commit on their own, so they cannot
  be chained without leaving a half done record when the second one fails; this call commits once, at the very end,
  and rolls everything back if any step fails.
- Only an owner or a manager may use these calls (`role_tiers`): a cashier is refused before anything is read.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import cint

from murzak_pos.api import business_lists, role_tiers
from murzak_pos.api import overview_calc as calc
from murzak_pos.api import purchases_list_calc as rules
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company
from murzak_pos.api.purchase_orders_api import CANCEL_PURCHASE_ROLES, _order_lines

# --- the clock (small on purpose, so tests can replace it) ------------------------------------------


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


# --- who may use these calls -------------------------------------------------------------------------


def _require_buyer() -> str:
	"""Owner or manager only. Done first, before any record is read. Returns the tier."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if tier == role_tiers.CASHIER:
		frappe.throw(_("Only an owner or a manager can use Purchases."), frappe.PermissionError)
	return tier


def _refusal(error_type: str, message: str) -> dict:
	"""An expected refusal: a plain sentence and a short code the page can act on (the reply is still HTTP 200)."""
	return {"success": False, "message": message, "error_type": error_type}


def _refuse_period(code: str):
	"""Refuse a period that cannot be used, in words the person can act on."""
	if code == "need_dates":
		frappe.throw(
			_("Please give both a start date and an end date for the purchases list."),
			frappe.ValidationError,
		)
	if code == "bad_date":
		frappe.throw(_("Purchases list dates must look like 2026-10-13."), frappe.ValidationError)
	if code == "order":
		frappe.throw(
			_("The purchases list start date must not be after the end date."), frappe.ValidationError
		)
	if code == "too_long":
		frappe.throw(_("Please choose a purchases list period of one year or less."), frappe.ValidationError)
	frappe.throw(
		_("Choose a purchases list period: today, week, 7d, 30d, month or custom."), frappe.ValidationError
	)


def _refuse_list(code: str):
	if code == "bad_doctype":
		frappe.throw(_("Choose Purchase Order or Purchase Invoice."), frappe.ValidationError)
	frappe.throw(_("Choose a tab: all, orders or returns."), frappe.ValidationError)


def plain_text(error, limit: int = 240) -> str:
	"""The words of an error without tags, line breaks or a long tail, so a person can read it."""
	text = re.sub(r"<[^>]*>", " ", str(error or ""))
	text = re.sub(r"\s+", " ", text).strip()
	return text[:limit]


# --- the list ----------------------------------------------------------------------------------------


@frappe.whitelist()
def list_purchases(
	tab="all",
	search=None,
	supplier=None,
	period="30d",
	from_date=None,
	to_date=None,
	page=1,
	page_size=20,
	company=None,
):
	"""The Purchases page list for a tab and a period (see the module notes and the contract for the answer)."""
	_require_buyer()
	ensure_record_access("Supplier", supplier)

	try:
		tab = rules.normalize_tab(tab)
	except rules.ListError as error:
		_refuse_list(error.code)
	today = calc.nairobi_today(_now_utc())
	try:
		resolved = calc.resolve_period(period, from_date, to_date, today)
	except calc.PeriodError as error:
		_refuse_period(error.code)
	company = resolve_company(company)
	scope = {
		"tab": tab,
		"company": company,
		"supplier": supplier or None,
		"period": resolved,
		"search": rules.parse_search(search),
	}
	try:
		data = _purchases_page(scope, page, page_size)
		data["summary"] = _summary(scope)
	except Exception:
		frappe.log_error("Purchases list error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the purchases. Please try again.")}
	return {"success": True, "data": data}


def _letter_allows(parsed, kind_letter: str) -> bool:
	"""A short number typed with a letter (O12, B12, R12) only answers a row of that kind."""
	return not parsed or not parsed.get("letter") or parsed["letter"] == kind_letter


def _search_sql(alias: str, parsed, with_bill_no: bool, values: dict) -> str:
	"""The search as SQL conditions on one leg of the list, filling `values`."""
	if not parsed:
		return ""
	values["like"] = parsed["like"]
	parts = [f"{alias}.supplier_name LIKE %(like)s", f"{alias}.supplier LIKE %(like)s"]
	if with_bill_no:
		parts.append(f"{alias}.bill_no LIKE %(like)s")
	if parsed["seq"] is None:
		parts.append(f"{alias}.name LIKE %(like)s")
	else:
		values["regex"] = rules.number_regex(parsed["seq"])
		parts.append(f"{alias}.name REGEXP %(regex)s")
	return " AND (" + " OR ".join(parts) + ")"


def _union(scope: dict):
	"""(SQL, values) for the combined list of the tab: orders, bills not tied to an order, and returns."""
	parts = rules.tab_parts(scope["tab"])
	parsed = scope["search"]
	values = {
		"company": scope["company"],
		"from": scope["period"]["from"],
		"to": scope["period"]["to"],
	}
	if scope["supplier"]:
		values["supplier"] = scope["supplier"]
	legs = []

	if parts["orders"] and _letter_allows(parsed, "O"):
		sql = (
			"SELECT 'Purchase Order' AS doctype, po.name AS name, po.supplier AS supplier, "
			"po.supplier_name AS supplier_name, po.transaction_date AS posting_date, po.grand_total AS grand_total, "
			"po.docstatus AS docstatus, po.status AS status, 0 AS is_return, po.per_received AS per_received, "
			"po.per_billed AS per_billed, 0 AS outstanding_amount, NULL AS due_date, '' AS bill_no, "
			"po.creation AS creation FROM `tabPurchase Order` po "
			"WHERE po.company = %(company)s AND po.transaction_date BETWEEN %(from)s AND %(to)s"
		)
		if scope["supplier"]:
			sql += " AND po.supplier = %(supplier)s"
		sql += _search_sql("po", parsed, False, values)
		legs.append(sql)

	wants_bills = parts["bills"] and _letter_allows(parsed, "B")
	wants_returns = parts["returns"] and _letter_allows(parsed, "R")
	if wants_bills or wants_returns:
		sql = (
			"SELECT 'Purchase Invoice' AS doctype, pi.name AS name, pi.supplier AS supplier, "
			"pi.supplier_name AS supplier_name, pi.posting_date AS posting_date, pi.grand_total AS grand_total, "
			"pi.docstatus AS docstatus, pi.status AS status, pi.is_return AS is_return, 0 AS per_received, "
			"0 AS per_billed, pi.outstanding_amount AS outstanding_amount, pi.due_date AS due_date, "
			"pi.bill_no AS bill_no, pi.creation AS creation FROM `tabPurchase Invoice` pi "
			"WHERE pi.company = %(company)s AND pi.posting_date BETWEEN %(from)s AND %(to)s"
		)
		if wants_bills and wants_returns:
			sql += (
				" AND (pi.is_return = 1 OR NOT EXISTS (SELECT 1 FROM `tabPurchase Invoice Item` pii "
				"WHERE pii.parent = pi.name AND IFNULL(pii.purchase_order, '') != ''))"
			)
		elif wants_returns:
			sql += " AND pi.is_return = 1"
		else:
			sql += (
				" AND pi.is_return = 0 AND NOT EXISTS (SELECT 1 FROM `tabPurchase Invoice Item` pii "
				"WHERE pii.parent = pi.name AND IFNULL(pii.purchase_order, '') != '')"
			)
		if scope["supplier"]:
			sql += " AND pi.supplier = %(supplier)s"
		sql += _search_sql("pi", parsed, True, values)
		legs.append(sql)

	if not legs:
		# a short number with a letter that no row of this tab can have
		legs.append(
			"SELECT 'Purchase Invoice' AS doctype, '' AS name, '' AS supplier, '' AS supplier_name, "
			"NULL AS posting_date, 0 AS grand_total, 0 AS docstatus, '' AS status, 0 AS is_return, "
			"0 AS per_received, 0 AS per_billed, 0 AS outstanding_amount, NULL AS due_date, '' AS bill_no, "
			"NULL AS creation FROM `tabPurchase Invoice` pi WHERE 1 = 0"
		)
	return " UNION ALL ".join(legs), values


def _purchases_page(scope: dict, page, page_size) -> dict:
	"""rows, total, page, page_size, pages for any tab."""
	union, values = _union(scope)
	counted = frappe.db.sql(f"SELECT COUNT(*) AS n FROM ({union}) p", values, as_dict=True)
	total = int(counted[0].get("n") or 0) if counted else 0
	numbers = rules.page_numbers(page, page_size, total)

	rows: list[dict] = []
	if total:
		values.update(limit=numbers["page_size"], offset=numbers["offset"])
		found = frappe.db.sql(
			f"SELECT * FROM ({union}) p ORDER BY p.posting_date DESC, p.creation DESC, p.name DESC "
			"LIMIT %(limit)s OFFSET %(offset)s",
			values,
			as_dict=True,
		)
		rows = [rules.list_row(r) for r in found]
	return {
		"rows": rows,
		"total": total,
		"page": numbers["page"],
		"page_size": numbers["page_size"],
		"pages": numbers["pages"],
	}


def _summary(scope: dict) -> dict:
	"""
	Orders open and bills unpaid are what is open right now, so they ignore the period; returned follows the period.
	All three follow the supplier choice and ignore the tab.
	"""
	values = {"company": scope["company"], "from": scope["period"]["from"], "to": scope["period"]["to"]}
	supplier_po = supplier_pi = ""
	if scope["supplier"]:
		values["supplier"] = scope["supplier"]
		supplier_po = " AND po.supplier = %(supplier)s"
		supplier_pi = " AND pi.supplier = %(supplier)s"
	open_orders = frappe.db.sql(
		"SELECT COUNT(*) AS n FROM `tabPurchase Order` po WHERE po.company = %(company)s AND po.docstatus = 1 "
		"AND po.status NOT IN ('Closed', 'Completed', 'Cancelled') AND po.per_received < 100" + supplier_po,
		values,
		as_dict=True,
	)
	unpaid = frappe.db.sql(
		"SELECT IFNULL(SUM(pi.outstanding_amount), 0) AS total FROM `tabPurchase Invoice` pi "
		"WHERE pi.company = %(company)s AND pi.docstatus = 1 AND pi.is_return = 0 "
		"AND pi.outstanding_amount > 0" + supplier_pi,
		values,
		as_dict=True,
	)
	returned = frappe.db.sql(
		"SELECT IFNULL(SUM(ABS(pi.grand_total)), 0) AS total FROM `tabPurchase Invoice` pi "
		"WHERE pi.company = %(company)s AND pi.docstatus = 1 AND pi.is_return = 1 "
		"AND pi.posting_date BETWEEN %(from)s AND %(to)s" + supplier_pi,
		values,
		as_dict=True,
	)
	return rules.build_summary(
		(open_orders[0] if open_orders else {}).get("n"),
		(unpaid[0] if unpaid else {}).get("total"),
		(returned[0] if returned else {}).get("total"),
	)


# --- one purchase, for the panel ---------------------------------------------------------------------


@frappe.whitelist()
def get_purchase(id, doctype=None):
	"""The panel for one purchase (see the contract for the answer)."""
	_require_buyer()
	# A purchase of another business is refused whichever kind of document it is; a number that is not a document
	# of that kind has no owner recorded and passes, and the call then says it was not found
	ensure_record_access("Purchase Order", id)
	ensure_record_access("Purchase Invoice", id)

	try:
		doctype = rules.normalize_doctype(doctype)
	except rules.ListError as error:
		_refuse_list(error.code)
	kind = _kind_of(id, doctype)
	if kind is None:
		return _not_found(id)
	try:
		doc = frappe.get_doc(kind, id)
	except frappe.DoesNotExistError:
		return _not_found(id)
	try:
		data = _panel(doc)
	except Exception:
		frappe.log_error("Purchase panel error", f"{kind} {id}\n{frappe.get_traceback()}")
		return {"success": False, "message": _("Could not load this purchase. Please try again.")}
	return {"success": True, "data": data}


def _not_found(id):
	return _refusal("not_found", _("Purchase {0} was not found.").format(id))


def _kind_of(id, doctype):
	"""The kind of document `id` is: the one asked for if it exists, otherwise whichever kind has it. None if neither."""
	for kind in (doctype,) if doctype else rules.KINDS:
		if frappe.db.exists(kind, id):
			return kind
	return None


def _linked_parents(child_doctype: str, order: str) -> list[str]:
	"""The submitted documents (receipts or bills) that carry lines of this order."""
	found = frappe.db.sql(
		f"SELECT DISTINCT c.parent AS name FROM `tab{child_doctype}` c "
		"WHERE c.purchase_order = %(order)s AND c.docstatus = 1",
		{"order": order},
		as_dict=True,
	)
	return [r["name"] for r in found]


def _bill_rows(names: list[str]) -> list[dict]:
	if not names:
		return []
	return frappe.get_all(
		"Purchase Invoice",
		filters={"name": ["in", names], "docstatus": 1},
		fields=[
			"name",
			"bill_no",
			"bill_date",
			"posting_date",
			"due_date",
			"grand_total",
			"outstanding_amount",
			"is_return",
			"return_against",
			"docstatus",
		],
		order_by="posting_date asc, creation asc",
	)


def _bill_view(row: dict) -> dict:
	return {
		"id": row.get("name"),
		"number": rules.short_number(rules.INVOICE, row.get("name"), bool(calc.num(row.get("is_return")))),
		"bill_no": row.get("bill_no") or "",
		"bill_date": rules.date_text(row.get("bill_date")),
		"date": rules.date_text(row.get("posting_date")),
		"due_date": rules.date_text(row.get("due_date")),
		"total": abs(calc.money(row.get("grand_total"))),
		"outstanding": max(calc.money(row.get("outstanding_amount")), 0.0),
		"is_return": bool(calc.num(row.get("is_return"))),
		"docstatus": cint(row.get("docstatus")),
	}


def _receipt_views(names: list[str]) -> list[dict]:
	if not names:
		return []
	found = frappe.get_all(
		"Purchase Receipt",
		filters={"name": ["in", names], "docstatus": 1},
		fields=["name", "posting_date", "grand_total", "is_return"],
		order_by="posting_date asc, creation asc",
	)
	return [
		{
			"id": r.get("name"),
			"number": rules.short_number(rules.RECEIPT, r.get("name")),
			"date": rules.date_text(r.get("posting_date")),
			"total": abs(calc.money(r.get("grand_total"))),
			"is_return": bool(calc.num(r.get("is_return"))),
		}
		for r in found
	]


def _payment_modes(company) -> list[dict]:
	"""The ways the shop pays a supplier: [{name, kind}] with kind cash, mpesa, card or other."""
	names = business_lists.modes_of_payment_for(company) or []
	types = {}
	if names:
		for row in frappe.get_all(
			"Mode of Payment", filters={"name": ["in", names]}, fields=["name", "type"]
		):
			types[row.get("name")] = row.get("type")
	modes = []
	for name in names:
		kind = {calc.CASH: "cash", calc.MPESA: "mpesa", calc.CARD: "card"}.get(
			calc.classify_method(name, types.get(name)), "other"
		)
		modes.append({"name": name, "kind": kind})
	return modes


def _notes_for(doctype: str, name: str) -> list[dict]:
	"""The notes written on a purchase (comments), oldest first: [{text, date}]."""
	found = frappe.get_all(
		"Comment",
		filters={"reference_doctype": doctype, "reference_name": name, "comment_type": "Comment"},
		fields=["content", "creation"],
		order_by="creation asc",
		limit_page_length=20,
	)
	notes = [
		{
			"text": plain_text(r.get("content"), rules.MAX_NOTE_LENGTH),
			"date": rules.date_text(r.get("creation")),
		}
		for r in found
	]
	return [n for n in notes if n["text"]]


def _can_cancel_bill() -> bool:
	user = frappe.session.user
	return user == "Administrator" or bool(CANCEL_PURCHASE_ROLES.intersection(frappe.get_roles(user)))


def _panel(doc) -> dict:
	"""The panel data for an order or a bill that has been read."""
	is_order = doc.doctype == rules.ORDER
	is_return = bool(calc.num(doc.get("is_return"))) if not is_order else False
	status = rules.derive_status(
		doc.doctype,
		doc.docstatus,
		is_return,
		doc.get("status"),
		doc.get("per_received"),
		doc.get("per_billed"),
		doc.get("outstanding_amount"),
	)
	if is_order:
		lines = [rules.line_view(row) for row in doc.items]
		receipts = _receipt_views(_linked_parents("Purchase Receipt Item", doc.name))
		bill_rows = [_bill_view(r) for r in _bill_rows(_linked_parents("Purchase Invoice Item", doc.name))]
	else:
		lines = [rules.bill_line_view(row) for row in doc.items]
		receipt_names = sorted(
			{row.get("purchase_receipt") for row in doc.items if row.get("purchase_receipt")}
		)
		receipts = _receipt_views(receipt_names)
		bill_rows = [] if doc.docstatus != 1 else [_bill_view(doc)]
	bills = [b for b in bill_rows if not b["is_return"]]
	returns = [b for b in bill_rows if b["is_return"]]
	if not is_order:
		against = frappe.get_all(
			"Purchase Invoice",
			filters={"return_against": doc.name, "docstatus": 1},
			fields=[
				"name",
				"grand_total",
				"posting_date",
				"bill_no",
				"bill_date",
				"due_date",
				"outstanding_amount",
				"is_return",
				"docstatus",
			],
		)
		returns = [_bill_view(r) for r in against]
	to_pay = rules.first_unpaid_bill(bills)
	company = doc.get("company")

	can_cancel = False
	if doc.docstatus == 1 and status != "cancelled":
		if is_order:
			can_cancel = not receipts and not bills
		else:
			can_cancel = (not is_return) and _can_cancel_bill()
	return_against = None
	if not is_return and doc.docstatus == 1:
		return_against = doc.name if not is_order else (bills[0]["id"] if bills else None)

	data = {
		"id": doc.name,
		"doctype": doc.doctype,
		"number": rules.short_number(doc.doctype, doc.name, is_return),
		"status": status,
		"part_received": rules.part_received(doc.doctype, doc.docstatus, doc.get("per_received")),
		"supplier": {
			"id": doc.get("supplier"),
			"name": rules.supplier_label(doc.get("supplier_name"), doc.get("supplier")),
		},
		"date": rules.date_text(doc.get("transaction_date") if is_order else doc.get("posting_date")),
		"bill_no": doc.get("bill_no") or "",
		"bill_date": rules.date_text(doc.get("bill_date")),
		"due_date": rules.date_text(doc.get("due_date")) if not is_order else "",
		"is_return": is_return,
		"reference": doc.name,
		"return_against": doc.get("return_against") if is_return else None,
		"lines": lines,
		"totals": rules.totals_view(
			abs(calc.num(doc.get("total"))),
			abs(calc.num(doc.get("total_taxes_and_charges"))),
			abs(calc.num(doc.get("grand_total"))),
			None if is_order else doc.get("outstanding_amount"),
		),
		"bills": bills,
		"returns": returns,
		"receipts": receipts,
		"pay": None,
		"can": {
			"receive_goods": bool(is_order and rules.can_receive(doc.docstatus, doc.get("status"), lines)),
			"pay_bill": to_pay is not None,
			"cancel": can_cancel,
			"return": bool(return_against),
			"edit_draft": doc.docstatus == 0,
			"print": True,
		},
		"return_target": return_against,
		"notes": _notes_for(doc.doctype, doc.name),
	}
	if to_pay is not None:
		data["pay"] = {
			"invoice": to_pay["id"],
			"number": to_pay["number"],
			"bill_no": to_pay["bill_no"],
			"outstanding": to_pay["outstanding"],
			"due_date": to_pay["due_date"],
			"modes": _payment_modes(company),
		}
	return data


# --- receive goods: the goods received note, the stock and the bill, together -------------------------


def _stock_codes(codes) -> set:
	"""Which of these items hold stock (a service or other item that does not has no stock to report)."""
	if not codes:
		return set()
	found = frappe.get_all("Item", filters={"name": ["in", list(codes)]}, fields=["name", "is_stock_item"])
	return {r.get("name") for r in found if cint(r.get("is_stock_item"))}


def _stock_in_hand(pairs) -> dict:
	"""{item code: quantity in hand} over the (item code, warehouse) pairs, adding up an item held in two warehouses."""
	rows = []
	for code, warehouse in pairs:
		qty = (
			frappe.db.get_value("Bin", {"item_code": code, "warehouse": warehouse}, "actual_qty")
			if warehouse
			else 0
		)
		rows.append((code, qty or 0))
	return rules.sum_by_item(rows)


def _earlier(request, order, company):
	"""(receipt name, bill name) already made for this request on this order; either may be None."""
	if not request:
		return None, None
	pattern = rules.marker_pattern(request, order)
	receipts = frappe.get_all(
		"Purchase Receipt",
		filters={"docstatus": 1, "company": company, "remarks": ["like", pattern]},
		pluck="name",
		limit_page_length=1,
	)
	if not receipts:
		return None, None
	invoices = frappe.get_all(
		"Purchase Invoice",
		filters={"docstatus": 1, "company": company, "remarks": ["like", pattern]},
		pluck="name",
		limit_page_length=1,
	)
	return receipts[0], (invoices[0] if invoices else None)


def _make_receipt(order, items, warehouse):
	"""
	The goods received note, made by the call that already makes one from an order. Returns (name, None) or
	(None, the reason). That call rolls back by itself when it fails.
	"""
	from murzak_pos.api.stock import create_pr_from_po

	lines = []
	for item in items:
		line = {"item_code": item["item_code"], "qty": item["qty"]}
		if warehouse:
			line["warehouse"] = warehouse
		lines.append(line)
	reply = create_pr_from_po(order, items=lines, submit=1)
	name = ((reply or {}).get("data") or {}).get("purchase_receipt")
	if not (reply or {}).get("success") or not name:
		return None, plain_text((reply or {}).get("message"))
	return name, None


def _make_invoice(receipt, bill_no, bill_date, due_date, order, request):
	"""The supplier bill from the receipt, in ERPNext's own way (the way create_purchase_invoice_from_grn does). Returns the saved, submitted bill."""
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

	invoice = make_purchase_invoice(receipt)
	if bill_no:
		invoice.bill_no = bill_no
	if bill_date:
		invoice.bill_date = bill_date
	if due_date:
		invoice.due_date = due_date
	invoice.remarks = rules.invoice_remark(order, request)
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	return invoice


def _received(receipt, invoice, stock, order, left, repeat=False) -> dict:
	"""The answer for goods that are received and billed."""
	return {
		"success": True,
		"data": {
			"receipt": receipt,
			"receipt_number": rules.short_number(rules.RECEIPT, receipt),
			"invoice": invoice.get("name"),
			"invoice_number": rules.short_number(rules.INVOICE, invoice.get("name")),
			"bill": {
				"bill_no": invoice.get("bill_no") or "",
				"due_date": rules.date_text(invoice.get("due_date")),
				"total": abs(calc.money(invoice.get("grand_total"))),
			},
			"stock": stock,
			"stock_ok": rules.stock_matches(stock),
			"order": {"id": order, "open": bool(left), "still_to_arrive": left},
			"repeat": repeat,
		},
	}


@frappe.whitelist(methods=["POST"])
def receive_goods(
	order,
	items,
	bill_no=None,
	bill_date=None,
	warehouse=None,
	company=None,
	request_id=None,
	due_date=None,
):
	"""
	Receive goods against an order: ONE transaction that makes the goods received note (which raises the stock) and the
	supplier bill from it. If any step fails everything is rolled back and a plain sentence comes back; nothing is left
	half done. A repeat with the same request_id returns the first result and changes nothing.
	"""
	_require_buyer()
	ensure_record_access("Purchase Order", order)
	ensure_record_access("Warehouse", warehouse)
	if company:
		company = resolve_company(company)

	# What was asked, checked before anything is read or written
	if isinstance(items, str):
		items = frappe.parse_json(items)
	request, request_ok = rules.clean_request_id(request_id)
	if not request_ok:
		return _refusal(
			"bad_request_id", _("This delivery could not be checked for a repeat. Please try again.")
		)
	bill_day, bill_ok = rules.clean_day(bill_date)
	due_day, due_ok = rules.clean_day(due_date)
	if not bill_ok:
		return _refusal("bad_bill_date", _("The bill date must look like 2026-10-13."))
	if not due_ok:
		return _refusal("bad_due_date", _("The due date must look like 2026-10-13."))
	if rules.check_dates(bill_day, due_day):
		return _refusal("due_before_bill", _("The due date cannot be before the bill date."))
	bill_no = rules.clean_bill_no(bill_no)

	head = frappe.db.get_value(
		"Purchase Order", order, ["docstatus", "company", "status", "supplier"], as_dict=True
	)
	if not head or (company and head.get("company") != company):
		return _not_found(order)
	company = head.get("company")
	if warehouse and frappe.db.get_value("Warehouse", warehouse, "company") not in (None, company):
		return _refusal("bad_warehouse", _("That store belongs to another business."))

	try:
		return _receive(order, items, head, company, request, bill_no, bill_day, due_day, warehouse)
	except Exception as error:
		# Anything not caught below: put everything back, so no receipt is left without its bill (or the other way round)
		frappe.db.rollback()
		frappe.log_error("Receive goods error", f"{order}\n{frappe.get_traceback()}")
		return _refusal(
			"server_error", _failed(plain_text(error) if isinstance(error, frappe.ValidationError) else "")
		)


def _failed(reason: str = "") -> str:
	"""The sentence for a delivery that could not be saved: nothing was received and nothing was billed."""
	base = _("The goods were not received and no bill was made, so nothing was changed.")
	if reason:
		return f"{base} {reason}"
	return f"{base} {_('Please try again.')}"


def _receive(order, items, head, company, request, bill_no, bill_day, due_day, warehouse):
	"""The work of receive_goods, inside its try."""
	# One request at a time per order, so a double tap cannot slip in between the check and the receipt
	frappe.db.get_value("Purchase Order", order, "name", for_update=True)
	receipt_done, bill_done = _earlier(request, order, company)
	if receipt_done:
		if not bill_done:
			return _refusal(
				"already_received",
				_(
					"These goods were already received on goods received note {0}, but its bill is no longer there."
				).format(rules.short_number(rules.RECEIPT, receipt_done)),
			)
		invoice = frappe.get_doc("Purchase Invoice", bill_done)
		return _received(receipt_done, invoice, [], order, _left_now(order), repeat=True)

	doc = frappe.get_doc("Purchase Order", order)
	if doc.docstatus != 1:
		return _refusal(
			"not_submitted",
			_("This order has not been sent to the supplier yet, so nothing can be received on it."),
		)
	lines = [rules.line_view(row) for row in doc.items]
	if not rules.can_receive(doc.docstatus, doc.get("status"), lines):
		return _refusal("nothing_to_receive", _("Nothing is left to arrive on this order."))
	plan = rules.plan_receipt(lines, items)
	if plan["error"]:
		return _plan_refusal(plan["error"])
	repeated = set(rules.duplicate_items(lines)) & {i["item_code"] for i in plan["items"]}
	if repeated:
		return _refusal(
			"duplicate_lines",
			_(
				"{0} is on this order more than once, so it cannot be received here. Use the current Create GRN screen for this order."
			).format(", ".join(sorted(repeated))),
		)

	codes = [i["item_code"] for i in plan["items"]]
	stock_items = _stock_codes(codes)
	where = {line["item_code"]: (warehouse or line["warehouse"]) for line in lines}
	pairs = [(c, where.get(c)) for c in codes if c in stock_items]
	before = _stock_in_hand(pairs)

	receipt, reason = _make_receipt(order, plan["items"], warehouse)
	if not receipt:
		# create_pr_from_po has already put everything back
		frappe.db.rollback()
		return _refusal("receipt_failed", _failed(reason))
	frappe.db.set_value(
		"Purchase Receipt", receipt, "remarks", rules.receipt_remark(order, request), update_modified=False
	)

	invoice = _make_invoice(receipt, bill_no, bill_day, due_day, order, request)
	after = _stock_in_hand(pairs)
	report = rules.stock_report(plan["items"], before, after, stock_items)
	frappe.db.commit()
	return _received(receipt, invoice, report, order, rules.still_to_arrive(lines, plan["items"]))


def _left_now(order) -> list[dict]:
	"""What is still to arrive on an order as it stands now."""
	doc = frappe.get_doc("Purchase Order", order)
	return [
		{"item_code": line["item_code"], "to_arrive": line["to_arrive"]}
		for line in (rules.line_view(row) for row in doc.items)
		if line["to_arrive"] > 0
	]


def _plan_refusal(error: dict) -> dict:
	"""The plain sentence for each reason the quantities cannot be received."""
	code = error["code"]
	item = error.get("item_code") or ""
	if code == "no_items":
		return _refusal("no_items", _("Tick at least one item that arrived."))
	if code == "no_item_code":
		return _refusal("no_item_code", _("Every line needs an item."))
	if code == "not_on_order":
		return _refusal("not_on_order", _("{0} is not on this order.").format(item))
	if code == "named_twice":
		return _refusal("named_twice", _("{0} is listed more than once.").format(item))
	if code == "too_much":
		return _refusal(
			"too_much",
			_("Only {0} of {1} is still to arrive. Enter that or less.").format(error.get("left"), item),
		)
	return _refusal("bad_qty", _("Enter a quantity above 0 for {0}.").format(item))


# --- a direct bill: the bill is already in hand, and the goods with it --------------------------------


@frappe.whitelist(methods=["POST"])
def create_direct_bill(
	supplier,
	items,
	bill_no=None,
	bill_date=None,
	due_date=None,
	warehouse=None,
	company=None,
	paid_amount=None,
	mode_of_payment=None,
	reference_no=None,
	request_id=None,
	notes=None,
):
	"""
	Record a supplier bill the shop already holds. The bill is submitted with "update stock" on, so the stock goes up at
	once; when paid_amount is given it is also marked as paid, in the same document. A repeat with the same request_id
	returns the first bill and changes nothing.
	"""
	_require_buyer()
	ensure_record_access("Supplier", supplier)
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	if not supplier or not frappe.db.exists("Supplier", supplier):
		return _refusal("no_supplier", _("Choose a supplier."))

	if isinstance(items, str):
		items = frappe.parse_json(items)
	request, request_ok = rules.clean_request_id(request_id)
	if not request_ok:
		return _refusal("bad_request_id", _("This bill could not be checked for a repeat. Please try again."))
	bill_day, bill_ok = rules.clean_day(bill_date)
	due_day, due_ok = rules.clean_day(due_date)
	if not bill_ok:
		return _refusal("bad_bill_date", _("The bill date must look like 2026-10-13."))
	if not due_ok:
		return _refusal("bad_due_date", _("The due date must look like 2026-10-13."))
	if rules.check_dates(bill_day, due_day):
		return _refusal("due_before_bill", _("The due date cannot be before the bill date."))
	plan = rules.plan_direct_bill(items)
	if plan["error"]:
		return _direct_refusal(plan["error"])
	problem = rules.check_payment(rules.lines_total(plan["items"]), paid_amount)
	if problem == "bad_paid":
		return _refusal("bad_paid", _("Enter an amount above 0 for what you paid."))
	if problem == "paid_too_much":
		return _refusal(
			"paid_too_much",
			_("You cannot pay more than the bill total of {0}.").format(
				rules.money_text(rules.lines_total(plan["items"]))
			),
		)

	try:
		return _direct(
			company,
			supplier,
			plan["items"],
			bill_no,
			bill_day,
			due_day,
			warehouse,
			paid_amount,
			mode_of_payment,
			reference_no,
			request,
			rules.clean_note(notes),
		)
	except Exception as error:
		frappe.db.rollback()
		frappe.log_error("Direct bill error", f"{supplier}\n{frappe.get_traceback()}")
		reason = plain_text(error) if isinstance(error, frappe.ValidationError) else ""
		base = _("The bill was not saved and no stock was added, so nothing was changed.")
		return _refusal("server_error", f"{base} {reason or _('Please try again.')}")


def _direct_refusal(error: dict) -> dict:
	code = error["code"]
	item = error.get("item_code") or ""
	if code == "no_items":
		return _refusal("no_items", _("Add at least one item."))
	if code == "no_item_code":
		return _refusal("no_item_code", _("Every line needs an item."))
	if code == "bad_rate":
		return _refusal("bad_rate", _("Enter a cost of 0 or more for {0}.").format(item))
	return _refusal("bad_qty", _("Enter a quantity above 0 for {0}.").format(item))


def _mode_account(mode, company):
	"""The account the company keeps for a way of paying, or None."""
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	info = get_default_bank_cash_account(company, mode_of_payment=mode) or {}
	return info.get("account")


def _direct(
	company,
	supplier,
	items,
	bill_no,
	bill_day,
	due_day,
	warehouse,
	paid_amount,
	mode,
	reference_no,
	request,
	note=None,
):
	"""The work of create_direct_bill, inside its try."""
	frappe.db.get_value("Supplier", supplier, "name", for_update=True)
	marker = rules.direct_marker(request) if request else None
	if marker:
		earlier = frappe.get_all(
			"Purchase Invoice",
			filters={
				"docstatus": 1,
				"company": company,
				"supplier": supplier,
				"remarks": ["like", rules.direct_pattern(request)],
			},
			pluck="name",
			limit_page_length=1,
		)
		if earlier:
			return _direct_reply(frappe.get_doc("Purchase Invoice", earlier[0]), repeat=True)

	today = calc.nairobi_today(_now_utc())
	rows = _order_lines(
		[{"item_code": i["item_code"], "qty": i["qty"], "rate": i["rate"]} for i in items],
		company,
		warehouse,
		today,
	)
	invoice_rows = [{k: v for k, v in row.items() if k != "schedule_date"} for row in rows]
	values = {
		"doctype": "Purchase Invoice",
		"company": company,
		"supplier": supplier,
		"update_stock": 1,
		"items": invoice_rows,
		"remarks": ("Bill already held when entered" + (f" {marker}" if marker else "")),
	}
	if warehouse:
		values["set_warehouse"] = warehouse
	if bill_no:
		values["bill_no"] = rules.clean_bill_no(bill_no)
	if bill_day:
		values["bill_date"] = bill_day
	if due_day:
		values["due_date"] = due_day
	if paid_amount not in (None, ""):
		mode = mode or business_lists.first_mode_of_payment(company)
		if not mode or not business_lists.mode_of_payment_usable(mode, company):
			return _refusal("no_mode", _("Choose how you paid."))
		account = _mode_account(mode, company)
		if not account:
			return _refusal(
				"no_account",
				_("{0} has no account set up for this shop. Ask the owner to add it in Settings.").format(
					mode
				),
			)
		values.update(
			is_paid=1,
			paid_amount=calc.money(paid_amount),
			mode_of_payment=mode,
			cash_bank_account=account,
		)
		if reference_no:
			values["remarks"] += f" Payment reference {rules.clean_bill_no(reference_no)}."
	invoice = frappe.get_doc(values)
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	if note:
		invoice.add_comment("Comment", note)
	frappe.db.commit()
	return _direct_reply(invoice)


def _direct_reply(invoice, repeat=False) -> dict:
	return {
		"success": True,
		"data": {
			"invoice": invoice.get("name"),
			"invoice_number": rules.short_number(rules.INVOICE, invoice.get("name")),
			"total": abs(calc.money(invoice.get("grand_total"))),
			"outstanding": max(calc.money(invoice.get("outstanding_amount")), 0.0),
			"repeat": repeat,
		},
	}


# --- a note on a purchase ----------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def add_purchase_note(id, note, doctype=None):
	"""Write a note on an order or a bill (kept as a comment on it, shown in the panel). Replies with the note as saved."""
	_require_buyer()
	ensure_record_access("Purchase Order", id)
	ensure_record_access("Purchase Invoice", id)
	try:
		doctype = rules.normalize_doctype(doctype)
	except rules.ListError as error:
		_refuse_list(error.code)
	text = rules.clean_note(note)
	if not text:
		return _refusal("no_note", _("Write the note first."))
	kind = _kind_of(id, doctype)
	if kind is None:
		return _not_found(id)
	try:
		doc = frappe.get_doc(kind, id)
		doc.add_comment("Comment", text)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error("Purchase note error", f"{kind} {id}\n{frappe.get_traceback()}")
		return _refusal("server_error", _("The note was not saved. Please try again."))
	return {"success": True, "data": {"note": {"text": text, "date": str(calc.nairobi_today(_now_utc()))}}}
