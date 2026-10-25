"""
The arithmetic of the till panel and the shift-end count (ticket P3-6). Plain functions: no database, no Frappe.

`till_api.py` reads the database and joins these. Everything that decides a number, an order or a word is here so it
can be tested alone.

Words used below
- a "line" is one mode of payment of one till session: its opening cash, what was sold, taken in, refunded and paid
  out, and what should be there at the end (`expected`)
- a "key" is the kind of payment: cash, mpesa, card, credit or other (the same five the screens colour)
- the difference is counted minus expected: below zero is short, above zero is over
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

CASH, MPESA, CARD, CREDIT, OTHER = "cash", "mpesa", "card", "credit", "other"
# Cash and M-Pesa first (the Spec), On credit last because it is not money in the till
KEY_ORDER = {CASH: 0, MPESA: 1, CARD: 2, OTHER: 3, CREDIT: 4}
# Names ERPNext ships that the screens show together as "Other"
GROUPED_OTHER = ("bankdraft", "cheque", "wiretransfer")
RECENT_SALES = 5
MATCHES_WITHIN = 0.005  # a difference smaller than this is no difference


class TillError(Exception):
	"""A request that cannot be used. `code` names the reason; `detail` is what to show in the message."""

	def __init__(self, code: str, detail: str = ""):
		super().__init__(code)
		self.code = code
		self.detail = detail


# --- small readers ----------------------------------------------------------------------------------


def num(value) -> float:
	"""A number from anything the database or a request gives; empty and unreadable values are 0."""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def money(value) -> float:
	"""Rounded to cents. Never returns -0.0."""
	return round(num(value), 2) + 0.0


def as_bool(value) -> bool:
	"""A true or false from a request, where "false", "0", "no" and "" are false (a plain bool() would say true)."""
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "y", "on")
	return bool(value)


def clock(value) -> str | None:
	"""HH:MM from a time, a datetime, a timedelta (how MariaDB gives a time) or text like 14:05:09. None if unreadable."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime):
		return value.strftime("%H:%M")
	if isinstance(value, time):
		return value.strftime("%H:%M")
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds()) % 86400
		return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
	found = re.search(r"(\d{1,2}):(\d{2})", str(value))
	return f"{int(found.group(1)):02d}:{found.group(2)}" if found else None


def day_of(value) -> str | None:
	"""YYYY-MM-DD from a date, a datetime or text that starts with one."""
	if isinstance(value, datetime):
		return str(value.date())
	if isinstance(value, date):
		return str(value)
	found = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
	return found.group(0) if found else None


def short_receipt(name) -> str:
	"""
	The short receipt number: the last run of digits of the invoice name, as a hash sign and at least four digits.
	ACC-PSINV-2026-00033 gives #0033. The till shows the invoice name as the receipt number today (ReceiptDialog:
	"Receipt no." is invoice.name); the Spec wants the short form. A name with no digits is returned as it is.
	"""
	text = str(name or "")
	runs = re.findall(r"\d+", text)
	if not runs:
		return text
	return "#" + str(int(runs[-1])).zfill(4)


# --- kinds of payment -------------------------------------------------------------------------------


def classify_method(name, mode_type=None) -> str:
	"""
	cash, mpesa, card, credit or other for a mode of payment's name (and its ERPNext type, if known).

	The same five as the front end's paymentKey for the standard names (M-Pesa, Cash, Card, Credit Card, On credit),
	and the same reading Home uses for Cash in the till, so the two screens agree: MPESA and Mpesa are M-Pesa; a name
	with "card" in it is Card (checked before credit); Credit, On credit and Customer credit are credit; Bank Draft,
	Cheque, Wire Transfer and anything else are other. A mode of ERPNext type Cash that is not one of those named
	ones (for example "Cash KES") is cash.
	"""
	text = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
	if "mpesa" in text:
		return MPESA
	if "card" in text:
		return CARD
	if text in ("credit", "oncredit", "customercredit", "creditsale", "creditsales"):
		return CREDIT
	if "cash" in text:
		return CASH
	if str(mode_type or "").strip().lower() == "cash" and text not in GROUPED_OTHER:
		return CASH
	return OTHER


def is_grouped_other(name) -> bool:
	return re.sub(r"[^a-z0-9]", "", str(name or "").lower()) in GROUPED_OTHER


def line_order(line) -> tuple:
	return (KEY_ORDER.get(line["key"], 3), str(line["mode"]).lower())


# --- what should be in the till ---------------------------------------------------------------------


def build_expected(
	opening_rows, payment_rows, change_rows, entry_rows, credit_total=0.0, mode_types=None, movement_rows=None
) -> list[dict]:
	"""
	The lines of one session, in display order.

	opening_rows  [{mode_of_payment, opening_amount}] counted when the till was opened
	payment_rows  [{is_return, mode_of_payment, amount}] payments on the session's sales; a refund is a return
	              and carries a negative amount
	change_rows   [{is_return, change}] change handed back on those sales; it comes out of cash
	entry_rows    [{payment_type, mode_of_payment, amount}] Payment Entries the cashier made in the session:
	              Receive is money in (a customer settling a debt), Pay is money out
	credit_total  what was sold on credit (the unpaid part of the session's sales)
	movement_rows [{payment_type, mode_of_payment, amount}] cash put in or taken out of the drawer during the
	              shift (floats, petty cash, safe drops), read the same way as entry_rows

	For each mode: expected = opening + sales + paid_in - refunds - paid_out. For cash, `sales` is the cash taken
	minus the change given back. A credit sale is not money in the till, so On credit is listed last with
	expected 0 and counts_cash false.
	"""
	mode_types = mode_types or {}
	lines: dict[str, dict] = {}
	credit_sales = 0.0
	credit_mode = None

	def key_of(name):
		return classify_method(name, mode_types.get(name))

	def line_for(name):
		if name not in lines:
			lines[name] = {
				"mode": name,
				"key": key_of(name),
				"opening": 0.0,
				"sales": 0.0,
				"paid_in": 0.0,
				"refunds": 0.0,
				"paid_out": 0.0,
			}
		return lines[name]

	for row in opening_rows or []:
		name = row.get("mode_of_payment")
		if name:
			line_for(name)["opening"] += num(row.get("opening_amount"))
	for row in payment_rows or []:
		name = row.get("mode_of_payment")
		if not name:
			continue
		line = line_for(name)
		amount = num(row.get("amount"))
		if row.get("is_return"):
			line["refunds"] += -amount
		else:
			line["sales"] += amount
	for row in [*(entry_rows or []), *(movement_rows or [])]:
		name = row.get("mode_of_payment")
		if not name:
			continue
		line = line_for(name)
		if row.get("payment_type") == "Receive":
			line["paid_in"] += num(row.get("amount"))
		elif row.get("payment_type") == "Pay":
			line["paid_out"] += num(row.get("amount"))

	# Credit-kind modes are not money in the till: fold them into the one On credit line
	for name in [n for n, line in lines.items() if line["key"] == CREDIT]:
		line = lines.pop(name)
		credit_sales += line["sales"]
		credit_mode = credit_mode or name

	ordered = sorted(lines.values(), key=line_order)

	change = sum(num(r.get("change")) for r in change_rows or [] if not r.get("is_return"))
	first_cash = next((line for line in ordered if line["key"] == CASH), None)
	if first_cash is not None:
		first_cash["sales"] -= change

	result = []
	for line in ordered:
		refunds = max(line["refunds"], 0.0)
		expected = line["opening"] + line["sales"] + line["paid_in"] - refunds - line["paid_out"]
		result.append(
			{
				"mode": line["mode"],
				"key": line["key"],
				"opening": money(line["opening"]),
				"sales": money(line["sales"]),
				"paid_in": money(line["paid_in"]),
				"refunds": money(refunds),
				"paid_out": money(line["paid_out"]),
				"expected": money(expected),
				"counts_cash": True,
			}
		)

	credit_given = max(credit_sales, num(credit_total))
	if credit_given > 0 or credit_mode:
		result.append(
			{
				"mode": credit_mode or "On credit",
				"key": CREDIT,
				"opening": 0.0,
				"sales": money(credit_given),
				"paid_in": 0.0,
				"refunds": 0.0,
				"paid_out": 0.0,
				"expected": 0.0,
				"counts_cash": False,
			}
		)
	return result


def cash_in_till(lines) -> float | None:
	"""Expected cash: the expected of every cash line, or None when the session has no cash line."""
	cash = [line for line in lines or [] if line["key"] == CASH]
	return money(sum(line["expected"] for line in cash)) if cash else None


# --- the count at the end of the shift --------------------------------------------------------------


def parse_counted(value) -> list[dict]:
	"""
	The counted amounts as [{"mode", "amount"}] from a list, or the same list as JSON text. Nothing, None or an
	empty list gives []. Raises TillError: bad_counted (cannot be read), negative_count, duplicate_mode.
	"""
	import json

	if value is None or value == "":
		return []
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			raise TillError("bad_counted")
	if isinstance(value, dict):
		value = [{"mode": mode, "amount": amount} for mode, amount in value.items()]
	if not isinstance(value, list):
		raise TillError("bad_counted")
	rows, seen = [], set()
	for item in value:
		if not isinstance(item, dict) or not str(item.get("mode") or "").strip():
			raise TillError("bad_counted")
		raw = item.get("amount")
		if isinstance(raw, bool) or raw is None or raw == "":
			raise TillError("bad_counted")
		try:
			amount = float(raw)
		except (TypeError, ValueError):
			raise TillError("bad_counted")
		if amount != amount or amount in (float("inf"), float("-inf")):
			raise TillError("bad_counted")
		if amount < 0:
			raise TillError("negative_count")
		mode = str(item["mode"]).strip()
		if mode.lower() in seen:
			raise TillError("duplicate_mode", mode)
		seen.add(mode.lower())
		rows.append({"mode": mode, "amount": money(amount)})
	return rows


def match_counted(lines, counted) -> dict[str, float]:
	"""
	Counted amounts by the session's mode name. A counted mode matches a line by its name (any capitals) or, when
	exactly one line has that kind, by the kind (cash, mpesa, card, other). Raises TillError unknown_mode for a mode
	that is not one of the session's money lines (On credit is not counted).
	"""
	countable = [line for line in lines or [] if line["counts_cash"]]
	by_name = {line["mode"].lower(): line["mode"] for line in countable}
	result: dict[str, float] = {}
	for row in counted or []:
		wanted = row["mode"].lower()
		mode = by_name.get(wanted)
		if mode is None:
			same_kind = [line["mode"] for line in countable if line["key"] == wanted]
			mode = same_kind[0] if len(same_kind) == 1 else None
		if mode is None:
			raise TillError("unknown_mode", row["mode"])
		if mode in result:
			raise TillError("duplicate_mode", row["mode"])
		result[mode] = row["amount"]
	return result


def difference_word(difference) -> str:
	"""short, over or matches."""
	value = num(difference)
	if abs(value) < MATCHES_WITHIN:
		return "matches"
	return "short" if value < 0 else "over"


def close_figures(lines, counted_by_mode) -> dict:
	"""
	The count against what should be there.

	Only money lines count (On credit is left out). A cash line with no counted amount stays uncounted (counted and
	difference null) and `needs_cash_count` is true. Any other line the cashier did not count is taken to be exactly
	what was expected, so nobody is forced to count M-Pesa.
	"""
	counted_by_mode = counted_by_mode or {}
	out, missing_cash = [], False
	for line in lines or []:
		if not line["counts_cash"]:
			continue
		expected = money(line["expected"])
		if line["mode"] in counted_by_mode:
			counted = money(counted_by_mode[line["mode"]])
		elif line["key"] == CASH:
			counted = None
			missing_cash = True
		else:
			counted = expected
		out.append(
			{
				"mode": line["mode"],
				"key": line["key"],
				"label": line.get("label", line["mode"]),
				"expected": expected,
				"counted": counted,
				"difference": None if counted is None else money(counted - expected),
			}
		)
	total_expected = money(sum(item["expected"] for item in out))
	if missing_cash:
		return {
			"lines": out,
			"total_expected": total_expected,
			"total_counted": None,
			"difference": None,
			"word": None,
			"by": None,
			"needs_cash_count": True,
		}
	total_counted = money(sum(item["counted"] for item in out))
	difference = money(total_counted - total_expected)
	return {
		"lines": out,
		"total_expected": total_expected,
		"total_counted": total_counted,
		"difference": difference,
		"word": difference_word(difference),
		"by": abs(difference),
		"needs_cash_count": False,
	}


def reconciliation_changes(existing_modes, figures_lines) -> tuple[dict, list[dict]]:
	"""
	What to write into the closing entry's payment table: (changes for rows that are there by mode, rows to add for
	counted modes ERPNext did not list). Each is {closing_amount, difference, expected_amount} (a new row also has
	mode_of_payment and opening_amount 0).
	"""
	existing = set(existing_modes or [])
	changes, additions = {}, []
	for item in figures_lines or []:
		if item["counted"] is None:
			continue
		values = {
			"closing_amount": item["counted"],
			"difference": item["difference"],
			"expected_amount": item["expected"],
		}
		if item["mode"] in existing:
			changes[item["mode"]] = values
		else:
			additions.append({"mode_of_payment": item["mode"], "opening_amount": 0, **values})
	return changes, additions


# --- who, which session, what is shown --------------------------------------------------------------


def may_close(session_user, caller, tier) -> bool:
	"""The cashier who opened the session may close it; so may a manager or an owner. Nobody else."""
	if not caller or tier is None:
		return False
	if session_user == caller:
		return True
	return tier in ("owner", "manager")


def pick_session(sessions, caller, tier):
	"""
	The session the till panel is about. `sessions` are open sessions, newest first. A cashier gets only their own;
	a manager or owner gets their own if they have one, else the newest of the business.
	"""
	sessions = list(sessions or [])
	own = [s for s in sessions if s.get("user") == caller]
	if tier == "cashier" or tier is None:
		return own[0] if own else None
	if own:
		return own[0]
	return sessions[0] if sessions else None


def count_held(rows, warehouse=None) -> int:
	"""Held bills of the business; with a store, that store's and those held with no store (as the held list does)."""
	rows = list(rows or [])
	if not warehouse:
		return len(rows)
	return sum(1 for r in rows if not r.get("warehouse") or r.get("warehouse") == warehouse)


def sale_status(is_return, outstanding) -> str:
	"""returned, unpaid or paid."""
	if is_return:
		return "returned"
	return "unpaid" if num(outstanding) > MATCHES_WITHIN else "paid"


def recent_sales(rows, limit=RECENT_SALES) -> list[dict]:
	"""The newest `limit` sales as the panel lists them. Rows: name, creation, posting_time, customer(_name), total."""
	ranked = sorted(
		rows or [], key=lambda r: (str(r.get("creation") or ""), str(r.get("name") or "")), reverse=True
	)
	return [
		{
			"id": r.get("name"),
			"receipt": short_receipt(r.get("name")),
			"time": clock(r.get("posting_time")) or clock(r.get("creation")),
			"customer": r.get("customer_name") or r.get("customer") or "",
			"total": money(r.get("grand_total")),
			"status": sale_status(r.get("is_return"), r.get("outstanding_amount")),
		}
		for r in ranked[: max(int(limit), 0)]
	]
