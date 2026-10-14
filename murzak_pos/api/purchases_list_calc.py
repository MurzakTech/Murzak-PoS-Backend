"""
The rules and the arithmetic behind the Purchases page and the guided Receive goods step (ticket P4-5).

Plain functions that read nothing and write nothing, so each rule can be tested without a database. The file
purchases_list_api.py reads the database and joins these rules to it.

What a "purchase" is on the Purchases page
- An order: a Purchase Order. The goods are asked for and may arrive in parts.
- A bill: a Purchase Invoice that is not tied to an order (a bill the shop already holds, which raised the stock at
  once). A bill made from an order is shown inside that order's panel, not as a second row, so one purchase is one row.
- A return: a Purchase Invoice that is a return (a debit note). Returns are always listed, whatever they came from.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from math import ceil

from murzak_pos.api import overview_calc as calc

ORDER = "Purchase Order"
INVOICE = "Purchase Invoice"
RECEIPT = "Purchase Receipt"
KINDS = (ORDER, INVOICE)
KIND_LETTER = {ORDER: "O", INVOICE: "B", RECEIPT: "G"}
RETURN_LETTER = "R"

TABS = ("all", "orders", "returns")
STATUSES = ("draft", "ordered", "received", "billed", "paid", "returned", "cancelled")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 60
MIN_NUMBER_DIGITS = 4
TOLERANCE = 0.005
MAX_NAME_LENGTH = 140
MAX_BILL_NO_LENGTH = 60

# An order the supplier no longer owes goods on, whatever the quantities say
FINISHED_ORDER_STATUSES = frozenset({"Closed", "Completed", "Cancelled"})
# An order that cannot be received or billed for now
HELD_ORDER_STATUSES = frozenset({"On Hold", "Draft", "Cancelled", "Closed", "Completed"})


class ListError(ValueError):
	"""The request cannot be used. `code` says why: bad_tab, bad_doctype."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- the short number --------------------------------------------------------------------------------

_AMENDED = re.compile(r"^(.*\d{5,})-\d{1,2}$")
_TRAILING_DIGITS = re.compile(r"(\d+)$")


def sequence_of(name) -> int | None:
	"""The number at the end of a document name (12 for PUR-ORD-2026-00012), None when there is none."""
	text = str(name or "").strip()
	amended = _AMENDED.match(text)
	if amended:
		text = amended.group(1)
	found = _TRAILING_DIGITS.search(text)
	return int(found.group(1)) if found else None


def short_number(doctype, name, is_return=False) -> str:
	"""#O0012 for an order, #B0012 for a bill, #R0012 for a return, #G0012 for goods received; a name with no number is returned as it is."""
	seq = sequence_of(name)
	if seq is None:
		return str(name or "")
	letter = RETURN_LETTER if is_return and doctype == INVOICE else KIND_LETTER.get(doctype, "")
	return f"#{letter}{seq:0{MIN_NUMBER_DIGITS}d}"


_NUMBER_QUERY = re.compile(r"^#?\s*([oObBrRgG])?\s*(\d{1,9})$")


def parse_number_query(text) -> tuple[str | None, int] | None:
	"""
	(letter, number) when the text looks like a short number: 12, 0012, #0012, O12, #B0012. The letter is None for a
	plain number (which matches every kind). None when the text is not a short number.
	"""
	found = _NUMBER_QUERY.match(str(text or "").strip())
	if not found:
		return None
	return (found.group(1).upper() if found.group(1) else None), int(found.group(2))


def _escape_like(text: str) -> str:
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_search(text) -> dict | None:
	"""
	What a search box entry means, or None for an empty one.

	text    what to look for in the supplier name, the bill number and (unless it is a short number) the reference
	like    the same text as a SQL LIKE pattern
	seq     the number it stands for when it looks like a short number (None otherwise)
	letter  the kind letter in a short number, if one was typed
	"""
	cleaned = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_SEARCH_LENGTH]
	if not cleaned:
		return None
	number = parse_number_query(cleaned)
	return {
		"text": cleaned,
		"like": "%" + _escape_like(cleaned) + "%",
		"seq": number[1] if number else None,
		"letter": number[0] if number else None,
	}


def number_regex(seq: int) -> str:
	"""The pattern a document name must match to end with the number `seq` (zeros in front and a -1 amendment allowed)."""
	return rf"[^0-9]0*{int(seq)}(-[0-9]{{1,2}})?$"


def search_matches(row, parsed) -> bool:
	"""
	Whether a row (doctype, name, is_return, supplier, supplier_name, bill_no) answers the search. The database applies
	the same rule in SQL; this is the rule in plain code, for the tests and for anything filtering rows already loaded.
	"""
	if not parsed:
		return True
	needle = parsed["text"].lower()
	people = (
		f"{row.get('supplier_name') or ''} {row.get('supplier') or ''} {row.get('bill_no') or ''}".lower()
	)
	if needle in people:
		return True
	if parsed["seq"] is None:
		return needle in str(row.get("name") or "").lower()
	if parsed["letter"]:
		letter = (
			RETURN_LETTER
			if row.get("is_return") and row.get("doctype") == INVOICE
			else KIND_LETTER.get(row.get("doctype"))
		)
		if parsed["letter"] != letter:
			return False
	return re.search(number_regex(parsed["seq"]), str(row.get("name") or "")) is not None


# --- the request -------------------------------------------------------------------------------------


def normalize_tab(tab) -> str:
	"""The tab asked for (all when none), or a ListError."""
	tab = str(tab or "all").strip().lower()
	if tab not in TABS:
		raise ListError("bad_tab")
	return tab


def normalize_doctype(doctype) -> str | None:
	"""Purchase Order, Purchase Invoice or None (not given). Any other value is a ListError."""
	if doctype in (None, ""):
		return None
	if doctype not in KINDS:
		raise ListError("bad_doctype")
	return doctype


def tab_parts(tab: str) -> dict:
	"""
	Which parts of the combined list a tab holds: orders, bills (not tied to an order) and returns.
	All holds the three; Orders only orders; Returns only returns.
	"""
	tab = normalize_tab(tab)
	return {
		"all": {"orders": True, "bills": True, "returns": True},
		"orders": {"orders": True, "bills": False, "returns": False},
		"returns": {"orders": False, "bills": False, "returns": True},
	}[tab]


def page_numbers(page, page_size, total) -> dict:
	"""
	The page to read: `page` (from 1), `page_size`, `pages` and `offset`. A size that is missing or wrong is 20, one
	above 100 is 100; a page below 1 is the first and one past the end is the last, so a list is never blank because
	of a stale page number. An empty list has 1 page.
	"""
	size = _whole(page_size, DEFAULT_PAGE_SIZE)
	if size < 1:
		size = DEFAULT_PAGE_SIZE
	size = min(size, MAX_PAGE_SIZE)
	count = max(_whole(total, 0), 0)
	pages = max(ceil(count / size), 1)
	number = min(max(_whole(page, 1), 1), pages)
	return {"page": number, "page_size": size, "pages": pages, "offset": (number - 1) * size}


def _whole(value, default: int) -> int:
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return default


def date_text(value) -> str:
	"""YYYY-MM-DD for a date, a datetime or text that starts with one; empty for nothing."""
	if value in (None, ""):
		return ""
	if isinstance(value, datetime):
		return str(value.date())
	if isinstance(value, date):
		return str(value)
	return str(calc.parse_date(value))


# --- status ------------------------------------------------------------------------------------------


def derive_status(
	doctype,
	docstatus,
	is_return=False,
	order_status=None,
	per_received=0,
	per_billed=0,
	outstanding=0.0,
) -> str:
	"""
	One of draft, ordered, received, billed, paid, returned, cancelled.

	Any document: cancelled when docstatus is 2, draft when it is 0.
	A bill: returned when it is a return, paid when nothing is owed on it, otherwise billed.
	An order: ordered while goods are still to arrive; received once every item has arrived (or the order was
	closed by hand); billed once everything has also been billed.
	"""
	if calc.num(docstatus) == 2:
		return "cancelled"
	if calc.num(docstatus) == 0:
		return "draft"
	if doctype == INVOICE:
		if is_return:
			return "returned"
		return "billed" if calc.num(outstanding) > TOLERANCE else "paid"
	if order_status == "Cancelled":
		return "cancelled"
	if order_status == "Closed":
		return "received"
	if calc.num(per_received) < 100 - TOLERANCE:
		return "ordered"
	return "billed" if calc.num(per_billed) >= 100 - TOLERANCE else "received"


def part_received(doctype, docstatus, per_received) -> bool:
	"""An order that has some of its goods but not all (it stays open for the rest)."""
	received = calc.num(per_received)
	return doctype == ORDER and calc.num(docstatus) == 1 and TOLERANCE < received < 100 - TOLERANCE


def supplier_label(supplier_name, supplier) -> str:
	return str(supplier_name or supplier or "")


def list_row(raw) -> dict:
	"""A row of the list from a database row (doctype, name, supplier, supplier_name, posting_date, grand_total, docstatus ...)."""
	doctype = raw.get("doctype")
	is_return = bool(calc.num(raw.get("is_return")))
	status = derive_status(
		doctype,
		raw.get("docstatus"),
		is_return,
		raw.get("status"),
		raw.get("per_received"),
		raw.get("per_billed"),
		raw.get("outstanding_amount"),
	)
	outstanding = 0.0
	if doctype == INVOICE and not is_return and status in ("billed", "paid"):
		outstanding = max(calc.money(raw.get("outstanding_amount")), 0.0)
	return {
		"id": raw.get("name"),
		"doctype": doctype,
		"number": short_number(doctype, raw.get("name"), is_return),
		"supplier": supplier_label(raw.get("supplier_name"), raw.get("supplier")),
		"supplier_id": raw.get("supplier"),
		"date": date_text(raw.get("posting_date")),
		"total": abs(calc.money(raw.get("grand_total"))),
		"status": status,
		"outstanding": outstanding,
		"due_date": date_text(raw.get("due_date")) if doctype == INVOICE and not is_return else "",
		"bill_no": raw.get("bill_no") or "",
		"is_return": is_return,
		"part_received": part_received(doctype, raw.get("docstatus"), raw.get("per_received")),
	}


def build_summary(open_orders, unpaid_total, returned_total) -> dict:
	"""The three numbers of the summary row."""
	return {
		"orders_open": int(calc.num(open_orders)),
		"bills_unpaid": calc.money(unpaid_total),
		"returned": abs(calc.money(returned_total)),
	}


# --- the lines of an order ---------------------------------------------------------------------------


def quantity(value) -> float:
	"""A quantity as a number, rounded to 3 places (the most ERPNext keeps)."""
	return round(calc.num(value), 3)


def line_view(row) -> dict:
	"""
	One order line for the panel and the Receive goods table: how many were ordered, how many have arrived and how many
	are still to arrive (never below 0).
	"""
	ordered = quantity(row.get("qty"))
	received = quantity(row.get("received_qty"))
	return {
		"item_code": row.get("item_code"),
		"item_name": row.get("item_name") or row.get("item_code"),
		"unit": row.get("uom") or row.get("stock_uom") or "",
		"ordered": ordered,
		"received": received,
		"to_arrive": max(quantity(ordered - received), 0.0),
		"rate": calc.money(row.get("rate")),
		"amount": calc.money(row.get("amount")),
		"warehouse": row.get("warehouse") or "",
	}


def bill_line_view(row) -> dict:
	"""One bill line: the whole quantity was billed (and, for a direct bill, stocked)."""
	qty = quantity(row.get("qty"))
	return {
		"item_code": row.get("item_code"),
		"item_name": row.get("item_name") or row.get("item_code"),
		"unit": row.get("uom") or row.get("stock_uom") or "",
		"ordered": qty,
		"received": qty,
		"to_arrive": 0.0,
		"rate": calc.money(row.get("rate")),
		"amount": calc.money(row.get("amount")),
		"warehouse": row.get("warehouse") or "",
	}


def to_arrive_total(lines) -> float:
	return round(sum(line["to_arrive"] for line in lines), 3)


def can_receive(docstatus, order_status, lines) -> bool:
	"""An order that is submitted, not held, closed or cancelled, with something still to arrive."""
	return calc.num(docstatus) == 1 and order_status not in HELD_ORDER_STATUSES and to_arrive_total(lines) > 0


def first_unpaid_bill(bills) -> dict | None:
	"""The bill to pay first: the unpaid one due soonest (a bill with no due date last), then the oldest."""
	unpaid = [
		b
		for b in bills or []
		if calc.num(b.get("outstanding")) > TOLERANCE
		and b.get("docstatus", 1) == 1
		and not b.get("is_return")
	]
	if not unpaid:
		return None
	return sorted(
		unpaid, key=lambda b: (b.get("due_date") or "9999-12-31", b.get("date") or "", str(b.get("id") or ""))
	)[0]


def totals_view(subtotal, tax, total, outstanding=None) -> dict:
	view = {"subtotal": calc.money(subtotal), "tax": calc.money(tax), "total": calc.money(total)}
	if outstanding is not None:
		view["outstanding"] = max(calc.money(outstanding), 0.0)
	return view


# --- receiving goods ---------------------------------------------------------------------------------


def clean_qty(value) -> float | None:
	"""A quantity typed or sent: a number above 0 (up to 3 decimal places), or None when it cannot be used."""
	if isinstance(value, bool):
		return None
	if isinstance(value, str):
		text = value.strip().replace(",", "")
		if not re.match(r"^\d+(\.\d+)?$", text):
			return None
		value = text
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	if number != number or number in (float("inf"), float("-inf")) or number <= 0:
		return None
	return quantity(number) if quantity(number) > 0 else None


_REQUEST_ID = re.compile(r"^[A-Za-z0-9.:-]{8,64}$")


def clean_request_id(request_id) -> tuple[str | None, bool]:
	"""(request id, valid). No id is allowed (the call then cannot recognise a retry); a bad one is not."""
	text = str(request_id or "").strip()
	if not text:
		return None, True
	return (text, True) if _REQUEST_ID.match(text) else (None, False)


def request_marker(request_id, order) -> str:
	"""The text kept in the remarks of the receipt and the bill, tying them to the request and the order, so a retry finds them."""
	return f"[recv-req:{request_id}:{order}]"


def marker_pattern(request_id, order) -> str:
	"""A SQL LIKE pattern that finds request_marker in remarks."""
	return "%" + _escape_like(request_marker(request_id, order)) + "%"


def direct_marker(request_id) -> str:
	"""The text kept in the remarks of a direct bill that ties it to the request, so a retry finds it."""
	return f"[bill-req:{request_id}]"


def direct_pattern(request_id) -> str:
	"""A SQL LIKE pattern that finds direct_marker in remarks."""
	return "%" + _escape_like(direct_marker(request_id)) + "%"


def receipt_remark(order, request_id) -> str:
	remark = f"Goods received for {order}"
	return f"{remark} {request_marker(request_id, order)}" if request_id else remark


def invoice_remark(order, request_id) -> str:
	remark = f"Bill for goods received on {order}"
	return f"{remark} {request_marker(request_id, order)}" if request_id else remark


def clean_bill_no(value) -> str:
	return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_BILL_NO_LENGTH]


def clean_day(value) -> tuple[date | None, bool]:
	"""(date, valid). Nothing given is allowed (None, True); text that is not a date is not."""
	if value in (None, ""):
		return None, True
	try:
		return calc.parse_date(value), True
	except (ValueError, TypeError):
		return None, False


def check_dates(bill_date, due_date) -> str | None:
	"""A due date before the bill date makes no sense (ERPNext refuses it)."""
	if bill_date and due_date and due_date < bill_date:
		return "due_before_bill"
	return None


def duplicate_items(lines) -> list[str]:
	"""The item codes that more than one line of the order carries, in order of first appearance."""
	seen: dict[str, int] = {}
	for line in lines:
		seen[line["item_code"]] = seen.get(line["item_code"], 0) + 1
	return [code for code, count in seen.items() if count > 1]


def plan_receipt(lines, requested) -> dict:
	"""
	Checks what was asked against the order and says what to receive.

	lines      the order's lines as line_view gives them
	requested  [{item_code, qty_received}] as sent

	Returns {"items": [{item_code, qty}], "error": None} or {"items": [], "error": {code, item_code, left}}.
	The rules: at least one line; every item must be on the order; an item may be named once; every quantity must
	be above 0 and not above what is still to arrive.
	"""
	if isinstance(requested, dict) or not isinstance(requested, list) or not requested:
		return {"items": [], "error": {"code": "no_items"}}
	by_code = {line["item_code"]: line for line in lines}
	seen: set[str] = set()
	items = []
	for entry in requested:
		code = str((entry or {}).get("item_code") or "").strip() if isinstance(entry, dict) else ""
		if not code:
			return {"items": [], "error": {"code": "no_item_code"}}
		if code not in by_code:
			return {"items": [], "error": {"code": "not_on_order", "item_code": code}}
		if code in seen:
			return {"items": [], "error": {"code": "named_twice", "item_code": code}}
		seen.add(code)
		qty = clean_qty(entry.get("qty_received"))
		if qty is None:
			return {"items": [], "error": {"code": "bad_qty", "item_code": code}}
		left = by_code[code]["to_arrive"]
		if qty > left + 1e-9:
			return {"items": [], "error": {"code": "too_much", "item_code": code, "left": left}}
		items.append({"item_code": code, "qty": qty})
	return {"items": items, "error": None}


def still_to_arrive(lines, items) -> list[dict]:
	"""What is left to arrive on each line after `items` (from plan_receipt) are received: [{item_code, to_arrive}], only those above 0."""
	got = {i["item_code"]: i["qty"] for i in items}
	left = []
	for line in lines:
		remaining = quantity(line["to_arrive"] - got.get(line["item_code"], 0.0))
		if remaining > 0:
			left.append({"item_code": line["item_code"], "to_arrive": remaining})
	return left


def stock_report(items, before, after, stock_items=None) -> list[dict]:
	"""
	[{item_code, before, after, received}] for each item received that is a stock item.

	before and after are {item code: quantity in stock}; `stock_items` (a set of item codes) leaves out services and
	other items that hold no stock (None: every item counts).
	"""
	report = []
	for item in items:
		code = item["item_code"]
		if stock_items is not None and code not in stock_items:
			continue
		report.append(
			{
				"item_code": code,
				"received": item["qty"],
				"before": quantity(before.get(code, 0.0)),
				"after": quantity(after.get(code, 0.0)),
			}
		)
	return report


def stock_matches(report) -> bool:
	"""Stock afterwards equals stock before plus the quantity received (AC-29), for every item in the report."""
	return all(abs(r["after"] - (r["before"] + r["received"])) < 1e-6 for r in report)


def sum_by_item(rows) -> dict:
	"""{item code: total} from rows of (item_code, qty); an item in two warehouses adds up."""
	totals: dict[str, float] = {}
	for code, qty in rows:
		totals[code] = quantity(totals.get(code, 0.0) + calc.num(qty))
	return totals


def default_due_date(bill_date: date | None, days: int = 30) -> date | None:
	"""A due date `days` after the bill date (the shop's habit when the supplier has no payment terms), None without a bill date."""
	return bill_date + timedelta(days=days) if bill_date else None


# --- a direct bill (the bill is already in hand, and the goods with it) ----------------------------------


def plan_direct_bill(lines) -> dict:
	"""
	Checks the lines of a direct bill: {"items": [{item_code, qty, rate}], "error": None} or an error
	{code, item_code}. Every line needs an item, a quantity above 0 and a cost of 0 or more; an item named twice is
	added up (the cost of the last line stands).
	"""
	if isinstance(lines, dict) or not isinstance(lines, list) or not lines:
		return {"items": [], "error": {"code": "no_items"}}
	merged: dict[str, dict] = {}
	for entry in lines:
		code = str((entry or {}).get("item_code") or "").strip() if isinstance(entry, dict) else ""
		if not code:
			return {"items": [], "error": {"code": "no_item_code"}}
		qty = clean_qty(entry.get("qty"))
		if qty is None:
			return {"items": [], "error": {"code": "bad_qty", "item_code": code}}
		rate = entry.get("rate")
		rate = 0.0 if rate in (None, "") else _cost(rate)
		if rate is None:
			return {"items": [], "error": {"code": "bad_rate", "item_code": code}}
		if code in merged:
			merged[code]["qty"] = quantity(merged[code]["qty"] + qty)
			merged[code]["rate"] = rate
		else:
			merged[code] = {"item_code": code, "qty": qty, "rate": rate}
	return {"items": list(merged.values()), "error": None}


def _cost(value) -> float | None:
	if isinstance(value, bool):
		return None
	try:
		number = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
	except (TypeError, ValueError):
		return None
	if number != number or number < 0 or number == float("inf"):
		return None
	return round(number, 2)


def lines_total(items) -> float:
	return calc.money(sum(i["qty"] * i["rate"] for i in items))


def check_payment(total, paid_amount) -> str | None:
	"""A payment taken with a direct bill: above 0 and not above the bill. Returns an error code or None."""
	if paid_amount in (None, ""):
		return None
	amount = _cost(paid_amount)
	if amount is None or amount <= 0:
		return "bad_paid"
	if amount > calc.money(total) + TOLERANCE:
		return "paid_too_much"
	return None


def money_text(value) -> str:
	"""KES 1,200 (whole shillings) or KES 1,200.50."""
	amount = calc.money(value)
	text = f"{amount:,.0f}" if abs(amount - round(amount)) < 0.005 else f"{amount:,.2f}"
	return f"KES {text}"
