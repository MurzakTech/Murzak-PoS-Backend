"""
The rules behind the Sales page calls (`sales_list_api.list_sales` and `sales_list_api.get_sale`), with no database
and no Frappe.

Every function takes plain rows (dicts, dates, numbers) and returns plain results, so each rule can be tested with a
few lines of data. The database work, the role checks and the answers are in sales_list_api.py.

What a "sale" is here: every submitted POS Invoice, merged or not, and every submitted Sales Invoice that is not the
consolidated invoice ERPNext makes when a till is closed. Closing a till merges its POS Invoices into one Sales Invoice
(`is_consolidated`), and each POS Invoice then points at it with `consolidated_invoice`. The receipts stay in the list
because they are what customers hold; the consolidated invoice is hidden because it is the same money again. Nothing
is counted twice. (Home counts the other way round, the consolidated invoice instead of its receipts; the totals agree.)

Money still owed on a merged receipt lives on the consolidated invoice, which a later payment reduces, while the
receipt keeps its old figure. `effective_outstanding` therefore reads a merged receipt as paid once the consolidated
invoice is paid, and as owing no more than the consolidated invoice still owes.

One number for one sale: the list row and the receipt panel both get their money from `sale_amounts`, so they cannot
disagree (acceptance criterion AC-24 at the endpoint level).

The short receipt number. The till has no short number today (its receipt shows the full system reference), so the
number is worked out from the document name, the same way every time:

- take the last run of digits of the name: ACC-PSINV-2026-00005 gives 00005, that is 5
- write it with at least 4 digits and a hash in front: #0005 (a longer number is kept whole, #12345)
- a POS Invoice has no letter after the hash; a Sales Invoice has an S: ACC-SINV-2026-00005 is #S0005. The two kinds
  count separately in ERPNext, so without the letter they would share numbers
- an amended copy (ACC-PSINV-2026-00005-1) keeps the number of its original, because the original is cancelled
- a name with no digits at the end is shown as it is, which is long but never wrong

Known risk: ERPNext's default invoice series starts again each year, so ACC-PSINV-2026-00005 and
ACC-PSINV-2027-00005 both give #0005. The row's `id` is always the key; the number is for people to read. The date is
next to it in the list, which tells the two apart.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from math import ceil

from murzak_pos.api import overview_calc as calc

POS_INVOICE = "POS Invoice"
SALES_INVOICE = "Sales Invoice"
KINDS = (POS_INVOICE, SALES_INVOICE)
KIND_LETTER = {POS_INVOICE: "", SALES_INVOICE: "S"}

TABS = ("all", "unpaid", "returns", "sessions")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 60
MIN_RECEIPT_DIGITS = 4
WALK_IN = "Walk-in Customer"  # the name the till gives a sale with no named customer

# A sale counts as paid, owing or returned within this many shillings of the exact figure
TOLERANCE = 0.005

# Who may record a return today (sales_api.RETURN_ROLES); the receipt only offers "Return items" to them
RETURN_ROLES = frozenset({"Business Owner", "System Manager", "Sales Manager", "Accounts Manager"})

STATUSES = ("paid", "unpaid", "overdue", "part-returned", "returned")


class ListError(ValueError):
	"""The request cannot be used. `code` says why: bad_tab, bad_doctype."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- the short receipt number ------------------------------------------------------------------------

_AMENDED = re.compile(r"^(.*\d{5,})-\d{1,2}$")
_TRAILING_DIGITS = re.compile(r"(\d+)$")


def sequence_of(name) -> int | None:
	"""The number at the end of a document name (5 for ACC-PSINV-2026-00005), None when there is none."""
	text = str(name or "").strip()
	amended = _AMENDED.match(text)
	if amended:
		text = amended.group(1)
	found = _TRAILING_DIGITS.search(text)
	return int(found.group(1)) if found else None


def short_receipt(doctype, name) -> str:
	"""#0033 for a POS Invoice, #S0033 for a Sales Invoice; a name with no number at the end is returned as it is."""
	seq = sequence_of(name)
	if seq is None:
		return str(name or "")
	return f"#{KIND_LETTER.get(doctype, '')}{seq:0{MIN_RECEIPT_DIGITS}d}"


_RECEIPT_QUERY = re.compile(r"^#?\s*([sS])?\s*(\d{1,9})$")


def parse_receipt_query(text) -> tuple[str | None, int] | None:
	"""
	(kind, number) when the text looks like a receipt number: 33, 0033, #0033, S33, #S0033. The kind is the Sales
	Invoice when there is an S and None otherwise (a plain number matches both kinds, so nobody has to know the
	letter). None when the text is not a receipt number.
	"""
	found = _RECEIPT_QUERY.match(str(text or "").strip())
	if not found:
		return None
	return (SALES_INVOICE if found.group(1) else None), int(found.group(2))


def _escape_like(text: str) -> str:
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_search(text) -> dict | None:
	"""
	What a search box entry means, or None for an empty one.

	text   what to look for in the customer name and, unless it is a receipt number, in the system reference
	like   the same text as a SQL LIKE pattern
	seq    the receipt number it stands for (None when it is not one)
	kind   the kind of document the receipt number is for (None: either)

	A receipt number such as 33 or #0033 does not search the system reference: that holds the year (2026) and many
	other digits, so it would match far too much.
	"""
	cleaned = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_SEARCH_LENGTH]
	if not cleaned:
		return None
	receipt = parse_receipt_query(cleaned)
	return {
		"text": cleaned,
		"like": "%" + _escape_like(cleaned) + "%",
		"seq": receipt[1] if receipt else None,
		"kind": receipt[0] if receipt else None,
	}


def receipt_regex(seq: int) -> str:
	"""The pattern a document name must match to end with the number `seq` (zeros in front and a -1 amendment allowed)."""
	return rf"[^0-9]0*{int(seq)}(-[0-9]{{1,2}})?$"


def search_matches(row, parsed) -> bool:
	"""
	Whether a sale row (doctype, name, customer, customer_name) answers the search. The database applies the same rule
	in SQL (sales_list_api); this is the rule in plain code, for the tests and for anything that filters rows already
	loaded.
	"""
	if not parsed:
		return True
	needle = parsed["text"].lower()
	customer = f"{row.get('customer_name') or ''} {row.get('customer') or ''}".lower()
	if needle in customer:
		return True
	if parsed["seq"] is None:
		return needle in str(row.get("name") or "").lower()
	if parsed["kind"] and parsed["kind"] != row.get("doctype"):
		return False
	return re.search(receipt_regex(parsed["seq"]), str(row.get("name") or "")) is not None


# --- the request -------------------------------------------------------------------------------------


def normalize_tab(tab) -> str:
	"""The tab asked for (all when none), or a ListError."""
	tab = str(tab or "all").strip().lower()
	if tab not in TABS:
		raise ListError("bad_tab")
	return tab


def normalize_doctype(doctype) -> str | None:
	"""POS Invoice, Sales Invoice or None (not given). Any other value is a ListError."""
	if doctype in (None, ""):
		return None
	if doctype not in KINDS:
		raise ListError("bad_doctype")
	return doctype


def tab_conditions(tab: str) -> list[str]:
	"""
	SQL conditions that a sale must meet for the tab, on the combined list of both kinds (alias s, with the
	effective_outstanding column). The sessions tab lists no sales.
	"""
	return {
		"all": [],
		"unpaid": ["s.is_return = 0", "s.effective_outstanding > 0"],
		"returns": ["s.is_return = 1"],
		"sessions": [],
	}[normalize_tab(tab)]


def scope_owner(tier, user, requested) -> str | None:
	"""
	Whose sales to read: a cashier's own, whatever was asked for; an owner or manager's choice (None: everyone's).
	"""
	if tier == "cashier":
		return user
	return requested or None


def may_read_sale(tier, user, sale_owner) -> bool:
	"""A cashier may read only a sale they made; an owner or manager may read any sale of their business."""
	return tier != "cashier" or sale_owner == user


def may_return(roles, user=None) -> bool:
	"""Whether the person may record a return (the roles sales_api allows, or the Administrator)."""
	return user == "Administrator" or bool(RETURN_ROLES.intersection(roles or ()))


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


# --- the sale documents ------------------------------------------------------------------------------


def date_text(value) -> str:
	"""YYYY-MM-DD for a date, a datetime or text that starts with one."""
	return str(calc.parse_date(value))


def time_text(value) -> str:
	"""HH:MM for a time, a datetime, a timedelta (how the database driver returns a time) or text like 08:15:03."""
	if isinstance(value, datetime):
		return value.strftime("%H:%M")
	if isinstance(value, time):
		return value.strftime("%H:%M")
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds()) % 86400
		return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
	found = re.match(r"^\s*(\d{1,2}):(\d{2})", str(value or ""))
	return f"{int(found.group(1)):02d}:{found.group(2)}" if found else "00:00"


def _seconds(value) -> int:
	"""The time of day in seconds, for ordering (the seconds count when two sales are in the same minute)."""
	if isinstance(value, timedelta):
		return int(value.total_seconds()) % 86400
	if isinstance(value, datetime | time):
		return value.hour * 3600 + value.minute * 60 + value.second
	found = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", str(value or ""))
	if not found:
		return 0
	return int(found.group(1)) * 3600 + int(found.group(2)) * 60 + int(found.group(3) or 0)


def sale_sort_key(row) -> tuple:
	"""Newest first when sorted in reverse: posting date, then posting time, then name."""
	return (date_text(row.get("posting_date")), _seconds(row.get("posting_time")), str(row.get("name") or ""))


def is_consolidated_invoice(row) -> bool:
	"""The Sales Invoice ERPNext made when a till was closed (it holds the shift's receipts, which are listed instead)."""
	return row.get("doctype") == SALES_INVOICE and bool(calc.num(row.get("is_consolidated")))


def union_sales(*groups) -> list[dict]:
	"""
	Every sale from the groups of rows (one per kind of document) as one list, newest first, without the consolidated
	Sales Invoices made at till close. A POS Invoice merged into one stays: it is the receipt. The database does the
	same in SQL; this is the rule in plain code.
	"""
	rows = [row for group in groups for row in group or [] if not is_consolidated_invoice(row)]
	return sorted(rows, key=sale_sort_key, reverse=True)


def effective_outstanding(own, is_return=False, merged_into=None, merged_outstanding=None) -> float:
	"""
	What is still owed on a sale, read the same way in the list, the summary, the Unpaid tab and the receipt.

	A sale that was not merged owes its own `outstanding_amount`. A POS Invoice merged into a consolidated Sales
	Invoice owes nothing once that invoice is paid, and never more than that invoice still owes (a payment is taken
	on the consolidated invoice, not on the receipt). When the consolidated invoice cannot be read, the receipt's own
	figure is used. A return owes what it says.
	"""
	own = calc.num(own)
	if is_return or not merged_into or merged_outstanding is None:
		return own
	remaining = calc.num(merged_outstanding)
	if remaining <= 0:
		return 0.0
	return min(own, remaining) if own > 0 else own


def sale_amounts(src) -> dict:
	"""
	The money of one sale, the single place the list row and the receipt both read it from.

	total        the full amount the customer was charged (grand_total), VAT included. Negative on a return
	paid         total minus outstanding: what has been settled, change excluded
	outstanding  what is still owed (see effective_outstanding: a merged receipt reads its consolidated invoice)
	change       the change handed back (receipt only)
	subtotal     the lines added up, before the extra discount and the tax
	discount     the extra discount on the whole sale (positive)
	tax          the tax and charges added
	"""
	total = calc.money(src.get("grand_total"))
	outstanding = calc.money(
		effective_outstanding(
			src.get("outstanding_amount"),
			bool(calc.num(src.get("is_return"))),
			src.get("merged_into"),
			src.get("merged_outstanding"),
		)
	)
	return {
		"total": total,
		"paid": calc.money(total - outstanding),
		"outstanding": outstanding,
		"change": calc.money(src.get("change_amount")),
		"subtotal": calc.money(src.get("total")),
		"discount": calc.money(abs(calc.num(src.get("discount_amount")))),
		"tax": calc.money(src.get("total_taxes_and_charges")),
	}


def derive_status(total, outstanding, due_date, today, is_return=False, returned_total=0.0) -> str:
	"""
	paid, unpaid, overdue, part-returned or returned for one sale.

	- a return document is `returned`
	- a sale whose returns add up to all of it is `returned`
	- a sale with an amount still owed is `overdue` once the due date has passed, otherwise `unpaid`. Money owed
	  comes before a part return, because it is what the owner has to act on
	- a settled sale with some of it returned is `part-returned`
	- anything else is `paid`
	`returned_total` is the value of the returns made against the sale, as a positive number.
	"""
	if is_return:
		return "returned"
	total, owed = abs(calc.num(total)), calc.num(outstanding)
	returned = abs(calc.num(returned_total))
	if total > TOLERANCE and returned >= total - TOLERANCE:
		return "returned"
	if owed > TOLERANCE:
		if due_date is not None and calc.parse_date(due_date) < calc.parse_date(today):
			return "overdue"
		return "unpaid"
	if returned > TOLERANCE:
		return "part-returned"
	return "paid"


def customer_label(customer_name, customer) -> str:
	"""The customer's name; a sale with none is a walk-in."""
	return str(customer_name or customer or WALK_IN)


def method_labels(payment_rows, outstanding=0.0, is_return=False) -> list[str]:
	"""
	The ways the sale was paid, in the order the till shows them (M-Pesa, Cash, Card, On credit, Other), each once.
	A payment row with 0 is left out. A sale with money still owed also shows On credit.
	"""
	found = set()
	for row in payment_rows or []:
		if abs(calc.num(row.get("amount"))) < TOLERANCE:
			continue
		found.add(calc.classify_method(row.get("mode_of_payment")))
	if not is_return and calc.num(outstanding) > TOLERANCE:
		found.add(calc.CREDIT)
	return [m for m in calc.METHODS if m in found]


def list_row(raw, today, returned=None, methods=None, names=None) -> dict:
	"""
	One row of the All sales, Unpaid and Returns tabs.

	raw       the document as read: doctype, name, posting_date, posting_time, customer, customer_name, owner,
	          grand_total, outstanding_amount, is_return, due_date, has_terms, merged_into, merged_outstanding
	returned  {document name: value of the returns made against it}
	methods   {document name: [payment rows]}
	names     {user: full name}
	"""
	name, doctype = raw.get("name"), raw.get("doctype")
	amounts = sale_amounts(raw)
	is_return = bool(calc.num(raw.get("is_return")))
	due = calc.due_date_of(
		{
			"posting_date": raw.get("posting_date"),
			"due_date": raw.get("due_date"),
			"has_terms": raw.get("has_terms"),
		}
	)
	return {
		"id": name,
		"doctype": doctype,
		"receipt": short_receipt(doctype, name),
		"reference": name,
		"date": date_text(raw.get("posting_date")),
		"time": time_text(raw.get("posting_time")),
		"customer": customer_label(raw.get("customer_name"), raw.get("customer")),
		"cashier": cashier_label(raw.get("owner"), names),
		"total": amounts["total"],
		"paid": amounts["paid"],
		"outstanding": amounts["outstanding"],
		"status": derive_status(
			amounts["total"], amounts["outstanding"], due, today, is_return, (returned or {}).get(name, 0.0)
		),
		"methods": method_labels((methods or {}).get(name), amounts["outstanding"], is_return),
		"is_return": is_return,
	}


def cashier_label(user, names=None) -> str:
	"""The person's full name; their user name when no full name is known."""
	return str((names or {}).get(user) or user or "")


def build_summary(rows) -> dict:
	"""
	The summary row from totals per kind of document and sale-or-return. Rows: is_return, total (sum of
	grand_total), outstanding (sum of the amounts still owed on sales, never below 0).

	sold      sales that are not returns, full total
	unpaid    what is still owed on those sales
	returned  the value of the returns, as a positive number
	"""
	sold = unpaid = returned = 0.0
	for row in rows or []:
		if calc.num(row.get("is_return")):
			returned += abs(calc.num(row.get("total")))
		else:
			sold += calc.num(row.get("total"))
			unpaid += max(calc.num(row.get("outstanding")), 0.0)
	return {"sold": calc.money(sold), "unpaid": calc.money(unpaid), "returned": calc.money(returned)}


# --- the receipt panel -------------------------------------------------------------------------------


def build_receipt(doc, today, returns=None, names=None, can_return=False) -> dict:
	"""
	The receipt panel for one sale.

	doc      the document: doctype, name, posting_date, posting_time, customer, customer_name, mobile, owner,
	         grand_total, outstanding_amount, change_amount, total, discount_amount, total_taxes_and_charges,
	         is_return, return_against, due_date, has_terms, items (item_code, item_name, qty, rate, amount),
	         payments (mode_of_payment, amount, reference_no)
	returns  the returns made against it: doctype, name, grand_total
	"""
	name, doctype = doc.get("name"), doc.get("doctype")
	amounts = sale_amounts(doc)
	is_return = bool(calc.num(doc.get("is_return")))
	returns = [r for r in returns or []]
	returned_total = sum(abs(calc.num(r.get("grand_total"))) for r in returns)
	due = calc.due_date_of(
		{
			"posting_date": doc.get("posting_date"),
			"due_date": doc.get("due_date"),
			"has_terms": doc.get("has_terms"),
		}
	)
	mobile = str(doc.get("mobile") or "").strip()
	plan = payment_plan(
		doctype,
		name,
		1,
		is_return,
		amounts["outstanding"],
		doc.get("merged_into"),
		bool(calc.num(doc.get("is_consolidated"))),
	)
	return {
		"id": name,
		"doctype": doctype,
		"receipt": short_receipt(doctype, name),
		"reference": name,
		"date": date_text(doc.get("posting_date")),
		"time": time_text(doc.get("posting_time")),
		"customer": {
			"name": customer_label(doc.get("customer_name"), doc.get("customer")),
			"mobile": mobile or None,
		},
		"cashier": cashier_label(doc.get("owner"), names),
		"status": derive_status(
			amounts["total"], amounts["outstanding"], due, today, is_return, returned_total
		),
		"is_return": is_return,
		"return_against": (doc.get("return_against") or None) if is_return else None,
		"merged_into": doc.get("merged_into") or None,
		"lines": [
			{
				"item_code": line.get("item_code"),
				"item_name": line.get("item_name") or line.get("item_code"),
				"qty": calc.num(line.get("qty")),
				"rate": calc.money(line.get("rate")),
				"amount": calc.money(line.get("amount")),
			}
			for line in doc.get("items") or []
		],
		"totals": {
			"subtotal": amounts["subtotal"],
			"discount": amounts["discount"],
			"tax": amounts["tax"],
			"total": amounts["total"],
			"paid": amounts["paid"],
			"outstanding": amounts["outstanding"],
			"change": amounts["change"],
		},
		"payments": [
			{
				"mode": row.get("mode_of_payment"),
				"label": calc.classify_method(row.get("mode_of_payment")),
				"amount": calc.money(row.get("amount")),
				"reference": (str(row.get("reference_no")).strip() or None)
				if row.get("reference_no")
				else None,
			}
			for row in doc.get("payments") or []
			if abs(calc.num(row.get("amount"))) >= TOLERANCE
		],
		"returns": [
			{
				"id": r.get("name"),
				"receipt": short_receipt(r.get("doctype") or doctype, r.get("name")),
				"total": calc.money(r.get("grand_total")),
			}
			for r in returns
		],
		"can": {
			"take_payment": "target" in plan,
			"take_payment_blocked": plan.get("error")
			if plan.get("error") in ("pay_after_close", "consolidated")
			else None,
			"return_items": bool(
				can_return
				and not is_return
				and abs(amounts["total"]) > TOLERANCE
				and returned_total < abs(amounts["total"]) - TOLERANCE
			),
			"print": True,
			"whatsapp": bool(mobile),
		},
	}


# --- till sessions -----------------------------------------------------------------------------------


def session_cash(rows, mode_types=None) -> dict | None:
	"""
	Expected cash, counted cash and the difference of a closed session, from its closing entry's payment rows
	(mode_of_payment, expected_amount, closing_amount, difference). Only the Cash rows count: the difference is the
	cash difference. Counted and difference are None when the cashier recorded no count (a till that does not record
	one leaves both at 0); the whole answer is None when there is no cash row.
	"""
	mode_types = mode_types or {}
	cash = [
		r
		for r in rows or []
		if calc.classify_method(r.get("mode_of_payment"), mode_types.get(r.get("mode_of_payment")))
		== calc.CASH
	]
	if not cash:
		return None
	expected = sum(calc.num(r.get("expected_amount")) for r in cash)
	counted = sum(calc.num(r.get("closing_amount")) for r in cash)
	difference = sum(calc.num(r.get("difference")) for r in cash)
	recorded = abs(counted) >= TOLERANCE or abs(difference) >= TOLERANCE
	return {
		"expected": calc.money(expected),
		"counted": calc.money(counted) if recorded else None,
		"difference": calc.money(difference) if recorded else None,
	}


def session_row(opening, closing=None, closing_rows=None, sales_count=0, names=None, mode_types=None) -> dict:
	"""
	One row of the Till sessions tab.

	opening       POS Opening Entry: name, user, period_start_date, status
	closing       its submitted POS Closing Entry (name, period_end_date), or None
	closing_rows  the closing entry's payment rows
	sales_count   receipts made in the session
	"""
	started = calc.parse_datetime(opening.get("period_start_date")) or calc.parse_datetime(
		opening.get("posting_date")
	)
	ended = calc.parse_datetime((closing or {}).get("period_end_date")) if closing else None
	is_open = str(opening.get("status") or "").lower() == "open"
	cash = None if is_open or not closing else session_cash(closing_rows, mode_types)
	return {
		"id": opening.get("name"),
		"cashier": opening.get("user"),
		"cashier_name": cashier_label(opening.get("user"), names),
		"opened_on": str(started.date()) if started else None,
		"opened_at": started.strftime("%H:%M") if started else None,
		"closed_on": str(ended.date()) if ended and not is_open else None,
		"closed_at": ended.strftime("%H:%M") if ended and not is_open else None,
		"status": "open" if is_open else "closed",
		"sales_count": int(calc.num(sales_count)),
		"expected": cash["expected"] if cash else None,
		"counted": cash["counted"] if cash else None,
		"difference": cash["difference"] if cash else None,
		"closing_entry": (closing or {}).get("name") if closing and not is_open else None,
	}


# --- taking payment on a sale -------------------------------------------------------------------------

# The kinds of payment the Sales page offers, by the key the page sends. A key stands for the shop's own mode of
# payment of that kind (see pick_mode), found with the same naming rule Home uses (overview_calc.classify_method).
MODE_KEYS = {"cash": calc.CASH, "mpesa": calc.MPESA, "card": calc.CARD}

_REFERENCE = re.compile(r"^[A-Za-z0-9]{8,12}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9.:-]{1,64}$")
MAX_REFERENCE_LENGTH = 40


def parse_amount(value) -> float | None:
	"""
	The amount to take as a number of shillings with at most 2 decimals, or None when it cannot be used: not a number
	(or text, NaN, infinity, a yes/no value), zero or negative.
	"""
	if isinstance(value, bool) or value is None:
		return None
	try:
		number = float(str(value).strip().replace(",", "")) if isinstance(value, str) else float(value)
	except (TypeError, ValueError):
		return None
	if number != number or number in (float("inf"), float("-inf")):
		return None
	number = round(number, 2) + 0.0
	return number if number > 0 else None


def normalize_mode_key(value) -> str | None:
	"""cash, mpesa or card for what the page sent (M-Pesa, m-pesa and MPESA all mean mpesa); None for anything else."""
	key = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
	return key if key in MODE_KEYS else None


def pick_mode(key, modes, mode_types=None) -> str | None:
	"""
	The shop's mode of payment for a key, from the names it has enabled (`modes`), or None when it has none of that
	kind. A mode named exactly like the kind (Cash, M-Pesa, Mpesa, Card) wins; otherwise the first in alphabetical order.
	"""
	label = MODE_KEYS.get(key)
	if not label:
		return None
	mode_types = mode_types or {}
	found = sorted(m for m in modes or [] if calc.classify_method(m, mode_types.get(m)) == label)
	for name in found:
		if re.sub(r"[^a-z0-9]", "", name.lower()) == key:
			return name
	return found[0] if found else None


def clean_reference(key, reference) -> tuple[str | None, str | None]:
	"""
	(reference, problem). An M-Pesa payment needs its code: 8 to 12 letters and digits, saved in capitals; the problem
	is `missing` or `bad`. For cash and card a reference is optional; if given it is kept (control characters taken
	out, at most 40 characters).
	"""
	text = re.sub(r"\s+", " ", str(reference or "")).strip()
	if key == "mpesa":
		if not text:
			return None, "missing"
		if not _REFERENCE.match(text):
			return None, "bad"
		return text.upper(), None
	text = "".join(ch for ch in text if ch.isprintable())[:MAX_REFERENCE_LENGTH]
	return (text or None), None


def clean_request_id(request_id) -> tuple[str | None, bool]:
	"""(request id, valid). No id is allowed (the call then cannot recognise a retry); a bad one is not."""
	text = str(request_id or "").strip()
	if not text:
		return None, True
	return (text, True) if _REQUEST_ID.match(text) else (None, False)


def request_marker(request_id, sale_id) -> str:
	"""The text kept in the payment's remarks that ties it to the request and the sale, so a retry finds it."""
	return f"[pay-req:{request_id}:{sale_id}]"


def marker_pattern(request_id, sale_id) -> str:
	"""A SQL LIKE pattern that finds request_marker in remarks."""
	return "%" + _escape_like(request_marker(request_id, sale_id)) + "%"


def payment_remark(receipt, request_id, sale_id) -> str:
	"""The remarks written on the payment."""
	remark = f"Payment taken at the till for {receipt}"
	return f"{remark} {request_marker(request_id, sale_id)}" if request_id else remark


def payment_plan(
	doctype, name, docstatus, is_return, outstanding, merged_into=None, is_consolidated=False
) -> dict:
	"""
	Where a payment on a sale can be recorded, or why it cannot. Returns {"target": (doctype, name)} or {"error": code}.

	not_found        the sale is a draft or cancelled
	is_return        a return cannot take a payment
	nothing_owed     the sale owes nothing
	consolidated     the invoice made at till close, which holds many receipts: the payment goes on a receipt
	pay_after_close  a POS Invoice that is not merged yet. ERPNext cannot take a Payment Entry against a POS Invoice, so
	                 the payment waits for the shift to be closed
	A Sales Invoice takes the payment itself; a merged POS Invoice takes it on its consolidated Sales Invoice.
	"""
	if int(calc.num(docstatus)) != 1:
		return {"error": "not_found"}
	if calc.num(is_return):
		return {"error": "is_return"}
	if calc.num(outstanding) < TOLERANCE:
		return {"error": "nothing_owed"}
	if doctype == SALES_INVOICE:
		if is_consolidated:
			return {"error": "consolidated"}
		return {"target": (SALES_INVOICE, name)}
	if merged_into:
		return {"target": (SALES_INVOICE, merged_into)}
	return {"error": "pay_after_close"}


def money_text(value) -> str:
	"""KES 350 or KES 350.50, for a message."""
	amount = calc.money(value)
	return f"KES {amount:,.0f}" if amount == int(amount) else f"KES {amount:,.2f}"


def check_amount(amount, outstanding) -> str | None:
	"""None when the amount can be taken: more than 0, at most 2 decimals, not more than is owed."""
	if amount is None:
		return "bad_amount"
	if amount > round(calc.num(outstanding), 2) + TOLERANCE:
		return "too_much"
	return None
