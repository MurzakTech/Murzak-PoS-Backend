"""
The rules behind the Customers page (ticket P4-7), with no database and no Frappe.

Every function takes plain rows (dicts, dates, numbers) and returns plain results, so each rule can be tested with a few
lines of data. The database reads, the role checks and the replies are in customers_list_api.py.

What is decided here
- the tabs (All customers, Owing), the filters and how a search box entry is read (a name, or part of a mobile number);
- what each customer owes: the same rule as Home's "Money owed" (the rows come from the same function), so the Owing
  tab, the summary and Home can never disagree;
- the last purchase of each customer, and the short receipt number;
- the paging of a list that is built in memory (the Owing tab);
- the customer statement: one line per sale, return and payment with a running balance, ending at the balance the list
  shows (one function gives that balance to both).

Conventions: money is rounded to 2 decimals; dates are `datetime.date` inside and `YYYY-MM-DD` text in the answer; "today"
is the date in Africa/Nairobi, which the caller passes in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from math import ceil

from murzak_pos.api import overview_calc as calc

POS_INVOICE = "POS Invoice"
SALES_INVOICE = "Sales Invoice"
PAYMENT_ENTRY = "Payment Entry"
KIND_LETTER = {POS_INVOICE: "", SALES_INVOICE: "S", PAYMENT_ENTRY: "P"}

TABS = ("all", "owing")
STATUSES = ("active", "disabled", "all")
CUSTOMER_TYPES = ("Individual", "Company", "Partnership")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 60
MIN_MOBILE_DIGITS = 3
MIN_RECEIPT_DIGITS = 4
WALK_IN = "Walk-in Customer"  # the name the till gives a sale with no named customer

# The statement
STATEMENT_DEFAULT_DAYS = 90  # with no start date, the statement covers this many days up to today
STATEMENT_MAX_DAYS = 731  # two years
STATEMENT_MAX_LINES = 2000  # the most lines read from the database for one statement

# A balance counts as zero within this many shillings
TOLERANCE = 0.005


class ListError(ValueError):
	"""The request cannot be used. `code` says why: bad_tab, bad_status, bad_type."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


class StatementError(ValueError):
	"""The statement period cannot be used. `code` says why: bad_date, order, too_long."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- the request ------------------------------------------------------------------------------------


def normalize_tab(tab) -> str:
	"""`all` or `owing` (case and outer spaces ignored; nothing at all means `all`). Anything else raises ListError."""
	text = str(tab or "all").strip().lower() or "all"
	if text not in TABS:
		raise ListError("bad_tab")
	return text


def normalize_status(status) -> str:
	"""`active` (the default), `disabled` or `all`. Anything else raises ListError."""
	text = str(status or "active").strip().lower() or "active"
	if text not in STATUSES:
		raise ListError("bad_status")
	return text


def normalize_type(customer_type) -> str | None:
	"""Individual, Company or Partnership (case ignored), or None for no filter. Anything else raises ListError."""
	text = str(customer_type or "").strip()
	if not text:
		return None
	for known in CUSTOMER_TYPES:
		if known.lower() == text.lower():
			return known
	raise ListError("bad_type")


def clamp_page(page) -> int:
	try:
		return max(1, int(page))
	except (TypeError, ValueError):
		return 1


def clamp_page_size(size) -> int:
	"""20 when missing or unusable; never above 100, never below 1."""
	try:
		value = int(size)
	except (TypeError, ValueError):
		return DEFAULT_PAGE_SIZE
	if value < 1:
		return DEFAULT_PAGE_SIZE
	return min(value, MAX_PAGE_SIZE)


_PHONE_CHARACTERS = re.compile(r"^[+\d\s\-().]+$")


def mobile_fragment(text) -> str | None:
	"""
	The national digits to look for when the text looks like (part of) a phone number, else None.

	0712 345 gives 712345, +254 712 345 gives 712345 and 254712 gives 712: a number is saved as 0712345678,
	+254712345678 or 254712345678, and the digits after the country code or the leading 0 are in all three.
	"""
	raw = str(text or "").strip()
	if not raw or not _PHONE_CHARACTERS.match(raw):
		return None
	digits = re.sub(r"\D", "", raw)
	if digits.startswith("254"):
		digits = digits[3:]
	elif digits.startswith("0"):
		digits = digits[1:]
	return digits if len(digits) >= MIN_MOBILE_DIGITS else None


def parse_search(text) -> dict | None:
	"""
	What a search box entry means, or None for an empty one.

	text    what to look for in the name, the customer number and the email (lower case, at most 60 characters)
	mobile  the digits to look for in the mobile number, when the entry looks like a phone number (see mobile_fragment)
	"""
	cleaned = " ".join(str(text or "").split())[:MAX_SEARCH_LENGTH].strip()
	if not cleaned:
		return None
	return {"text": cleaned.lower(), "mobile": mobile_fragment(cleaned)}


def escape_like(text: str) -> str:
	"""The text with the characters that mean something in a LIKE pattern made plain."""
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def digits_of(mobile) -> str:
	"""A saved mobile number as digits only."""
	return re.sub(r"\D", "", str(mobile or ""))


def matches_search(row: dict, search: dict | None) -> bool:
	"""The same test the database makes for the All tab, for a list built in memory. No search matches everything."""
	if not search:
		return True
	haystack = f"{row.get('customer_name') or ''} {row.get('name') or ''} {row.get('email_id') or ''}".lower()
	if search["text"] in haystack:
		return True
	return bool(search.get("mobile")) and search["mobile"] in digits_of(row.get("mobile_no"))


def matches_type(row: dict, customer_type: str | None) -> bool:
	return not customer_type or row.get("customer_type") == customer_type


# --- what customers owe -----------------------------------------------------------------------------


def balances_by_customer(rows) -> dict:
	"""
	What each customer owes, as {customer: amount}, only for customers who owe something.

	`rows` are what Home's money-owed read gives (dashboard_overview._money_owed_rows): customer and
	outstanding_amount. This is the rule of overview_calc.money_owed_summary, so the figures add up to Home's.
	"""
	owed: dict = defaultdict(float)
	for row in rows or []:
		amount = calc.num(row.get("outstanding_amount"))
		customer = row.get("customer")
		if amount > 0 and customer:
			owed[customer] += amount
	return {customer: calc.money(total) for customer, total in owed.items() if calc.money(total) > 0}


def owing_summary(rows) -> dict:
	"""The total owed and how many customers owe it: exactly what Home's money-owed tile shows."""
	figures = calc.money_owed_summary(rows)
	return {"owing_total": figures["amount"], "owing_customers": figures["customers"]}


def balance_of(balances: dict, customer) -> float:
	"""One customer's balance from balances_by_customer; 0 when they owe nothing."""
	return calc.money(balances.get(customer))


# --- the short receipt number -----------------------------------------------------------------------

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
	"""
	#0033 for a POS Invoice, #S0033 for a Sales Invoice, #P0033 for a Payment Entry; a name with no number at the end
	is returned as it is. This is the rule of the Sales list (the last digits of the name, at least four, and S for a
	Sales Invoice); a payment gets a P so it never looks like a sale.
	"""
	seq = sequence_of(name)
	if seq is None:
		return str(name or "")
	return f"#{KIND_LETTER.get(doctype, '')}{seq:0{MIN_RECEIPT_DIGITS}d}"


# --- the last purchase ------------------------------------------------------------------------------


def _seconds(value) -> int:
	"""Seconds since midnight of a time given as text ('14:05:09'), a timedelta or a time; 0 when unreadable."""
	if value is None or value == "":
		return 0
	if isinstance(value, timedelta):
		return int(value.total_seconds())
	if hasattr(value, "hour"):
		return value.hour * 3600 + value.minute * 60 + value.second
	parts = str(value).split(".")[0].split(":")
	try:
		numbers = [int(p) for p in parts[:3]]
	except ValueError:
		return 0
	while len(numbers) < 3:
		numbers.append(0)
	return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def _day(value) -> str:
	try:
		return str(calc.parse_date(value))
	except ValueError:
		return ""


def sale_sort_key(row) -> tuple:
	"""Oldest first when sorted as it is: posting date, posting time, then the document name."""
	return (_day(row.get("posting_date")), _seconds(row.get("posting_time")), str(row.get("name") or ""))


def last_purchases(rows) -> dict:
	"""
	The latest sale of each customer: {customer: {date, amount, receipt, id}}.

	`rows` are sales of both kinds (customer, name, doctype, posting_date, posting_time, grand_total). A return is not
	a purchase and is ignored here even if it was passed in; a row without a customer or a readable date is ignored.
	"""
	best: dict = {}
	for row in rows or []:
		customer = row.get("customer")
		if not customer or calc.num(row.get("is_return")) or not _day(row.get("posting_date")):
			continue
		if customer not in best or sale_sort_key(row) > sale_sort_key(best[customer]):
			best[customer] = row
	return {
		customer: {
			"date": _day(row.get("posting_date")),
			"amount": calc.money(row.get("grand_total")),
			"receipt": short_receipt(row.get("doctype"), row.get("name")),
			"id": row.get("name"),
		}
		for customer, row in best.items()
	}


def last_purchase_for(purchases: dict, customer, may_see_money: bool) -> dict | None:
	"""
	The last purchase as the screen gets it. A cashier gets the date and the receipt number but not the amount: the
	amount is another person's sale.
	"""
	found = purchases.get(customer)
	if not found:
		return None
	shown = {"date": found["date"], "receipt": found["receipt"], "id": found["id"]}
	if may_see_money:
		shown["amount"] = found["amount"]
	return shown


# --- the list rows ----------------------------------------------------------------------------------


def customer_row(record: dict, balances: dict, purchases: dict, may_see_money: bool) -> dict:
	"""
	One row of the list.

	`record` is a Customer (name, customer_name, customer_type, mobile_no, email_id, disabled). The balance is left
	out of the row altogether (not set to null) for a person who may not see money owed.
	"""
	row = {
		"id": record.get("name"),
		"name": record.get("customer_name") or record.get("name"),
		"type": record.get("customer_type") or "",
		"mobile": record.get("mobile_no") or "",
		"email": record.get("email_id") or "",
		"disabled": bool(calc.num(record.get("disabled"))),
		"last_purchase": last_purchase_for(purchases, record.get("name"), may_see_money),
	}
	if may_see_money:
		row["balance"] = balance_of(balances, record.get("name"))
	return row


def owing_records(
	balances: dict, details: dict, search: dict | None, customer_type: str | None
) -> list[dict]:
	"""
	The customers on the Owing tab, most owed first (then by name), as Customer-like records with a `balance`.

	Everyone in `balances` is listed, so the tab always adds up to Home's money owed, even when a customer record
	cannot be read (it is shown by its number). `details` is {customer: Customer record}. The search and the type are
	applied after the balances, in memory.
	"""
	records = []
	for customer, amount in balances.items():
		record = dict(details.get(customer) or {"name": customer, "customer_name": customer})
		record.setdefault("name", customer)
		if not matches_search(record, search) or not matches_type(record, customer_type):
			continue
		record["balance"] = amount
		records.append(record)
	records.sort(
		key=lambda r: (-calc.money(r["balance"]), str(r.get("customer_name") or r.get("name") or "").lower())
	)
	return records


def page_info(total: int, page: int, page_size: int) -> dict:
	"""{page, page_size, pages, total}; there is always at least one page, and a page past the end is the last page."""
	pages = max(1, ceil(total / page_size)) if page_size else 1
	return {"page": min(clamp_page(page), pages), "page_size": page_size, "pages": pages, "total": total}


def page_slice(items: list, page: int, page_size: int) -> tuple[list, dict]:
	"""(the items of the page, page_info). A page past the end gives the last page."""
	info = page_info(len(items), page, page_size)
	start = (info["page"] - 1) * page_size
	return items[start : start + page_size], info


def uses_values(values) -> bool:
	"""
	Whether a shop really uses a setting such as the customer group or the area: its customers have more than one
	different value. A shop whose customers all share the one default value does not, so the forms hide it.
	"""
	seen = {str(v).strip() for v in values or [] if v and str(v).strip()}
	return len(seen) > 1


def customer_in_company(customer_company, company) -> bool:
	"""A customer belongs to the business when no business is recorded on it (an old record) or it is this one."""
	return not customer_company or customer_company == company


# --- the statement ----------------------------------------------------------------------------------


def resolve_statement_period(from_date, to_date, today: date) -> tuple[date, date]:
	"""
	(first day, last day) of a statement. No end date means today and an end date in the future is cut back to today; no
	start date means the last 90 days. Raises StatementError: bad_date, order or too_long (more than two years).
	"""
	try:
		last = calc.parse_date(to_date) if to_date not in (None, "") else today
		first = (
			calc.parse_date(from_date)
			if from_date not in (None, "")
			else last - timedelta(days=STATEMENT_DEFAULT_DAYS - 1)
		)
	except ValueError:
		raise StatementError("bad_date")
	last = min(last, today)
	if first > last:
		raise StatementError("order")
	if (last - first).days + 1 > STATEMENT_MAX_DAYS:
		raise StatementError("too_long")
	return first, last


def _cents(value) -> int:
	return int(round(calc.num(value) * 100))


def _from_cents(cents: int) -> float:
	return calc.money(cents / 100)


def statement_entries(documents, payments) -> list[dict]:
	"""
	The raw lines of a statement, oldest first, before any running balance.

	documents  sales and returns: name, doctype, posting_date, posting_time, grand_total, paid, is_return
	           `grand_total` is signed (negative on a return); `paid` is what was settled when it was made (negative when
	           money was handed back), change excluded
	payments   money received later: name, posting_date, amount (positive)

	A sale is one line: the whole amount as debit, and what was paid on the spot as credit on the same line, so a cash
	sale reads "debit 500, credit 500" and nets to nothing. A return is one line: the credited amount as credit (and any
	cash handed back as debit). A payment is one line with a credit.
	"""
	entries = []
	for doc in documents or []:
		if not _day(doc.get("posting_date")):
			continue
		total = _cents(doc.get("grand_total"))
		paid = _cents(doc.get("paid"))
		is_return = bool(calc.num(doc.get("is_return")))
		debit = max(total, 0) + max(-paid, 0)
		credit = max(-total, 0) + max(paid, 0)
		entries.append(
			{
				"date": _day(doc.get("posting_date")),
				"time": _seconds(doc.get("posting_time")),
				"kind": "return" if is_return else "sale",
				"id": doc.get("name"),
				"doctype": doc.get("doctype") or SALES_INVOICE,
				"debit": debit,
				"credit": credit,
			}
		)
	for payment in payments or []:
		if not _day(payment.get("posting_date")):
			continue
		amount = _cents(payment.get("amount"))
		entries.append(
			{
				"date": _day(payment.get("posting_date")),
				"time": _seconds(payment.get("posting_time")),
				"kind": "payment",
				"id": payment.get("name"),
				"doctype": PAYMENT_ENTRY,
				"debit": max(-amount, 0),
				"credit": max(amount, 0),
			}
		)
	entries.sort(key=lambda e: (e["date"], e["time"], e["id"] or ""))
	return entries


def build_statement(entries, current_balance, first: date, last: date) -> dict:
	"""
	The statement for first..last, ending at the balance the customer list shows.

	`entries` are statement_entries from `first` up to today (nothing before `first`); `current_balance` is what the
	customer owes now, from the one function that also feeds the list. Working back from it:

	  opening balance = current balance - everything that happened from `first` to today

	so the opening balance is the balance at the start of `first` however the customer's older history looks, and a
	statement that runs to today ends at exactly the current balance. A statement that stops earlier than today
	ends at the balance on its last day.

	Returns {opening_balance, closing_balance, current_balance, total_debit, total_credit, lines}. Each line is
	{date, kind, id, doctype, receipt, debit, credit, balance}; debit and credit are 0.0 when not used.
	"""
	current = _cents(current_balance)
	movement = sum(e["debit"] - e["credit"] for e in entries)
	opening = current - movement
	running = opening
	lines = []
	total_debit = total_credit = 0
	for entry in entries:
		if entry["date"] > str(last):
			break
		running += entry["debit"] - entry["credit"]
		total_debit += entry["debit"]
		total_credit += entry["credit"]
		lines.append(
			{
				"date": entry["date"],
				"kind": entry["kind"],
				"id": entry["id"],
				"doctype": entry["doctype"],
				"receipt": short_receipt(entry["doctype"], entry["id"]),
				"debit": _from_cents(entry["debit"]),
				"credit": _from_cents(entry["credit"]),
				"balance": _from_cents(running),
			}
		)
	return {
		"opening_balance": _from_cents(opening),
		"closing_balance": _from_cents(running),
		"current_balance": _from_cents(current),
		"total_debit": _from_cents(total_debit),
		"total_credit": _from_cents(total_credit),
		"lines": lines,
		"from": str(first),
		"to": str(last),
	}


def complete_from(first: date, *reads, cap: int = STATEMENT_MAX_LINES) -> date:
	"""
	The first day for which the lines read from the database are complete.

	The database gives the newest `cap` rows of each kind (POS invoices, sales invoices, payments) of the period, one list
	per kind in `reads`. When no read reached the cap that is every line, and this is `first`. When one did, its oldest
	lines may be missing, so the statement starts the day after the oldest day that read reached: the lines before it are
	left out for every kind, rather than showing half of a day.
	"""
	start = first
	for rows in reads:
		if rows and len(rows) >= cap:
			days = [_day(r.get("posting_date")) for r in rows if _day(r.get("posting_date"))]
			if days:
				start = max(start, date.fromisoformat(min(days)) + timedelta(days=1))
	return start


def entries_from(entries, first: date) -> list[dict]:
	"""The entries on or after `first`."""
	return [e for e in entries if e["date"] >= str(first)]
