"""
The rules behind the Suppliers page (ticket P4-6), with no database and no Frappe.

Every function takes plain rows (dicts, dates, numbers) and returns plain results, so each rule can be tested with a few
lines of data. The database reads, the role checks and the replies are in suppliers_list_api.py.

What is decided here
- the request: the Owed filter, the status, the paging and how a search box entry is read;
- what the shop owes each supplier and when the next bill is due. ONE function (`figures_by_supplier`) works this out
  for the list, the summary row, the Owed filter and the supplier panel, so they can never disagree. It follows the rule of
  Home's "supplier bill due" item (overview_calc.supplier_bill_items): a submitted purchase bill that is not a return,
  with an unpaid amount above zero, and "due soon" means due within overview_calc.BILL_WARNING_DAYS days or already late;
- the rows of the list and the panel, and the paging of a list built in memory.

Conventions: money is rounded to 2 decimals and added up in cents, so a total is always exactly the sum of its rows;
dates are `datetime.date` inside and `YYYY-MM-DD` text in the answer; "today" is the date in Africa/Nairobi, which the
caller passes in.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import ceil

from murzak_pos.api import overview_calc as calc

STATUSES = ("active", "disabled", "all")
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 60
PANEL_BILL_LIMIT = (
	20  # the most unpaid bills listed in the supplier panel (the panel also says how many there are)
)

OVERDUE = "overdue"
SOON = "soon"
LATER = "later"

_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "owed"})
_FALSE_WORDS = frozenset({"", "0", "false", "no", "off", "none", "null", "all"})


class ListError(ValueError):
	"""The request cannot be used. `code` says why: bad_status, bad_owed."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- the request ------------------------------------------------------------------------------------


def normalize_owed(owed) -> bool:
	"""
	Whether the Owed filter is on. Nothing, false, 0 and "all" mean off; true, 1, "true", "yes" and "owed" mean on
	(a web request sends text). Anything else raises ListError.
	"""
	if owed is None or owed is False:
		return False
	if owed is True:
		return True
	if isinstance(owed, int | float):
		return owed != 0
	text = str(owed).strip().lower()
	if text in _TRUE_WORDS:
		return True
	if text in _FALSE_WORDS:
		return False
	raise ListError("bad_owed")


def normalize_status(status, owed: bool = False) -> str:
	"""
	`active`, `disabled` or `all`. With nothing chosen the list shows active suppliers, but the Owed filter shows
	everyone the shop owes, disabled or not: money is still owed to a supplier that is no longer used.
	"""
	text = str(status or "").strip().lower()
	if not text:
		return "all" if owed else "active"
	if text not in STATUSES:
		raise ListError("bad_status")
	return text


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


def parse_search(text) -> dict | None:
	"""What a search box entry means, or None for an empty one: {text} in lower case, at most 60 characters."""
	cleaned = " ".join(str(text or "").split())[:MAX_SEARCH_LENGTH].strip()
	if not cleaned:
		return None
	return {"text": cleaned.lower()}


def clean_group(group) -> str | None:
	"""The supplier group asked for, or None for all groups."""
	text = str(group or "").strip()
	return text or None


def matches_search(record: dict, search: dict | None) -> bool:
	"""A supplier matches when the text is in its name, its number or its tax ID. No search matches everything."""
	if not search:
		return True
	haystack = (
		f"{record.get('supplier_name') or ''} {record.get('name') or ''} {record.get('tax_id') or ''}".lower()
	)
	return search["text"] in haystack


def matches_group(record: dict, group: str | None) -> bool:
	return not group or record.get("supplier_group") == group


def matches_status(record: dict, status: str) -> bool:
	disabled = bool(calc.num(record.get("disabled")))
	if status == "active":
		return not disabled
	if status == "disabled":
		return disabled
	return True


# --- what the shop owes -----------------------------------------------------------------------------


def _cents(value) -> int:
	return round(calc.num(value) * 100)


def _from_cents(cents: int) -> float:
	return calc.money(cents / 100)


def _due(value) -> date | None:
	"""The due date of a bill as a date, or None when the bill has none."""
	if not value:
		return None
	try:
		return calc.parse_date(value)
	except ValueError:
		return None


def bill_state(due: date | None, today: date) -> str | None:
	"""
	`overdue` (due before today), `soon` (due today up to 3 days ahead), `later`, or None for a bill with no due date.
	Overdue and soon together are Home's "due soon" rule.
	"""
	if due is None:
		return None
	if due < today:
		return OVERDUE
	if due <= today + timedelta(days=calc.BILL_WARNING_DAYS):
		return SOON
	return LATER


def is_due_soon(due: date | None, today: date) -> bool:
	"""Due within 3 days or already late: the rule of Home's supplier bill item."""
	return bill_state(due, today) in (OVERDUE, SOON)


def clean_bills(rows, today: date) -> list[dict]:
	"""
	The unpaid bills in `rows` (name, supplier, bill_no, due_date, outstanding_amount), each as
	{invoice, supplier, bill_no, due (date or None), cents, state, days}. A row with nothing left to pay, or no
	supplier, is dropped. `days` is the days until the due date (below 0 when late).
	"""
	bills = []
	for row in rows or []:
		cents = _cents(row.get("outstanding_amount"))
		supplier = row.get("supplier")
		if cents <= 0 or not supplier:
			continue
		due = _due(row.get("due_date"))
		bills.append(
			{
				"invoice": row.get("name"),
				"supplier": supplier,
				"bill_no": row.get("bill_no") or "",
				"due": due,
				"cents": cents,
				"state": bill_state(due, today),
				"days": (due - today).days if due else None,
			}
		)
	return bills


def _bill_order(bill: dict) -> tuple:
	"""The soonest due date first (late bills therefore come first), then the bigger bill; bills with no date last."""
	due = bill["due"]
	return (due is None, due or date.max, -bill["cents"], str(bill["invoice"] or ""))


def bill_line(bill: dict) -> dict:
	"""One unpaid bill as it is sent to the screen."""
	return {
		"invoice": bill["invoice"],
		"bill_no": bill["bill_no"],
		"due_date": str(bill["due"]) if bill["due"] else None,
		"outstanding": _from_cents(bill["cents"]),
		"state": bill["state"],
		"days": bill["days"],
	}


def next_due(bills: list[dict]) -> dict | None:
	"""
	The bill to pay next: the soonest due date among the unpaid bills, so a late bill comes before any other. Bills
	with no due date only count when no bill has one. None when there are no unpaid bills.
	"""
	if not bills:
		return None
	chosen = sorted(bills, key=_bill_order)[0]
	if chosen["due"] is None:
		return None
	return {
		"date": str(chosen["due"]),
		"amount": _from_cents(chosen["cents"]),
		"invoice": chosen["invoice"],
		"bill_no": chosen["bill_no"],
		"state": chosen["state"],
		"days": chosen["days"],
	}


def figures_by_supplier(rows, today: date, only=None) -> dict:
	"""
	THE one place that works out what the shop owes. Returns {supplier: {owed_cents, bills, bill_count, due_soon}} for
	each supplier that has an unpaid bill, `bills` sorted soonest due first.

	`rows` are unpaid submitted purchase bills (the rows `suppliers_list_api` reads with Home's conditions). `only`
	limits the answer to some suppliers (a set of names) when given; the list and its summary pass the same set.
	"""
	grouped: dict = {}
	for bill in clean_bills(rows, today):
		if only is not None and bill["supplier"] not in only:
			continue
		grouped.setdefault(bill["supplier"], []).append(bill)
	figures = {}
	for supplier, bills in grouped.items():
		bills.sort(key=_bill_order)
		figures[supplier] = {
			"owed_cents": sum(b["cents"] for b in bills),
			"bills": bills,
			"bill_count": len(bills),
			"due_soon": sum(1 for b in bills if b["state"] in (OVERDUE, SOON)),
		}
	return figures


def owed_of(figures: dict, supplier) -> float:
	"""What the shop owes one supplier (0.0 when nothing)."""
	found = figures.get(supplier)
	return _from_cents(found["owed_cents"]) if found else 0.0


def owed_suppliers(figures: dict) -> set:
	"""The suppliers the shop owes something above zero: exactly what the Owed filter lists."""
	return {supplier for supplier, found in figures.items() if found["owed_cents"] > 0}


def summary(figures: dict, supplier_count: int, may_see_money: bool) -> dict:
	"""
	The summary row: {suppliers} and, for a person who may see money owed, {owed_total, suppliers_owed,
	bills_due_soon}. The total is the sum of the same per-supplier figures the rows show.
	"""
	result = {"suppliers": supplier_count}
	if may_see_money:
		result["owed_total"] = _from_cents(sum(f["owed_cents"] for f in figures.values()))
		result["suppliers_owed"] = len(owed_suppliers(figures))
		result["bills_due_soon"] = sum(f["due_soon"] for f in figures.values())
	return result


# --- the rows ---------------------------------------------------------------------------------------


def supplier_facts(record: dict) -> dict:
	"""The facts of a Supplier record that the list and the panel share."""
	return {
		"id": record.get("name"),
		"name": record.get("supplier_name") or record.get("name"),
		"type": record.get("supplier_type") or "",
		"group": record.get("supplier_group") or "",
		"tax_id": record.get("tax_id") or "",
		"country": record.get("country") or "",
		"currency": record.get("default_currency") or "",
		"disabled": bool(calc.num(record.get("disabled"))),
	}


def supplier_row(record: dict, figures: dict, may_see_money: bool) -> dict:
	"""
	One row of the list. `owed`, `next_due` and `bills_due_soon` are left out of the row altogether (not set to
	null) for a person who may not see money owed.
	"""
	row = supplier_facts(record)
	if may_see_money:
		found = figures.get(record.get("name"))
		row["owed"] = owed_of(figures, record.get("name"))
		row["next_due"] = next_due(found["bills"]) if found else None
		row["bills_due_soon"] = found["due_soon"] if found else 0
	return row


def panel(record: dict, figures: dict, may_see_money: bool, limit: int = PANEL_BILL_LIMIT) -> dict:
	"""
	The supplier panel: the facts, and for a person who may see money owed what is owed, the next bill due and the
	unpaid bills (at most `limit`, soonest due first, with `bill_count` saying how many there are).
	"""
	data = supplier_facts(record)
	data["internal"] = bool(calc.num(record.get("is_internal_supplier")))
	if may_see_money:
		found = figures.get(record.get("name"))
		bills = found["bills"] if found else []
		data["owed"] = owed_of(figures, record.get("name"))
		data["next_due"] = next_due(bills)
		data["bills_due_soon"] = found["due_soon"] if found else 0
		data["bill_count"] = len(bills)
		data["bills"] = [bill_line(b) for b in bills[: max(0, limit)]]
	return data


def select_records(records, search, group, status, owed: bool, figures: dict) -> list[dict]:
	"""
	The suppliers for the list: those that pass the search, group and status, and with the Owed filter on only those the
	shop owes. Most owed first when the Owed filter is on (then by name), otherwise by name.
	"""
	owing = owed_suppliers(figures) if owed else None
	chosen = [
		r
		for r in records or []
		if matches_search(r, search)
		and matches_group(r, group)
		and matches_status(r, status)
		and (owing is None or r.get("name") in owing)
	]

	def name_key(record):
		return (
			str(record.get("supplier_name") or record.get("name") or "").lower(),
			str(record.get("name") or ""),
		)

	if owed:
		chosen.sort(key=lambda r: ((-figures[r["name"]]["owed_cents"]), *name_key(r)))
	else:
		chosen.sort(key=name_key)
	return chosen


def page_info(total: int, page: int, page_size: int) -> dict:
	"""{page, page_size, pages, total}; there is always at least one page, and a page past the end is the last page."""
	pages = max(1, ceil(total / page_size)) if page_size else 1
	return {"page": min(clamp_page(page), pages), "page_size": page_size, "pages": pages, "total": total}


def page_slice(items: list, page: int, page_size: int) -> tuple[list, dict]:
	"""(the items of the page, page_info). A page past the end gives the last page."""
	info = page_info(len(items), page, page_size)
	start = (info["page"] - 1) * page_size
	return items[start : start + page_size], info
