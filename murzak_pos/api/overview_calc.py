"""
The arithmetic behind the Home overview (`dashboard_overview.get_overview`), with no database and no Frappe.

Every function here takes plain rows (dicts, dates, numbers) and returns plain results, so each rule can be tested
with a few lines of data. The database work, the role checks and the cache are in dashboard_overview.py.

Conventions: money is rounded to 2 decimals; a percentage change is rounded to 1 decimal and is None when there is
nothing to compare with (a previous value of 0), never a division by zero; dates are `datetime.date` inside and
`YYYY-MM-DD` text in the answer; weeks start on Monday; "today" is the date in Africa/Nairobi (UTC+3, no daylight
saving), which the caller passes in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from murzak_pos.api.profit_calc import summarize_profit

# East Africa Time has no daylight saving, so a fixed offset is the same as the Africa/Nairobi zone
NAIROBI = timezone(timedelta(hours=3))

PERIOD_KEYS = ("today", "week", "7d", "30d", "month", "custom")
MAX_CUSTOM_DAYS = 366

CASH, MPESA, CARD, CREDIT, OTHER = "Cash", "M-Pesa", "Card", "On credit", "Other"
METHODS = (MPESA, CASH, CARD, CREDIT, OTHER)

FIRST_HOUR, LAST_HOUR = 8, 19
TOP_PRODUCT_COUNT = 5
MAX_ATTENTION = 4

# How urgent each kind of attention item is: 1 comes first
URGENCY = {
	"till_not_closed": 1,
	"out_of_stock": 2,
	"running_low": 3,
	"customer_overdue": 4,
	"supplier_bill_due": 5,
}
# The items that show money owed to or by the business, which a cashier never sees
MONEY_ITEMS = frozenset({"customer_overdue", "supplier_bill_due"})

DEFAULT_CREDIT_DAYS = 14  # a credit sale is overdue this long after the sale unless the customer has terms
BILL_WARNING_DAYS = 3  # a supplier bill shows up this many days before it is due
SOLD_RECENTLY_DAYS = 30  # "out of stock" matters for products that sold in this many days


class PeriodError(ValueError):
	"""The period asked for cannot be used. `code` says why: bad_period, need_dates, bad_date, order, too_long."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- numbers ----------------------------------------------------------------------------------------


def num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def money(value) -> float:
	"""A money amount with at most 2 decimals (and never -0.0)."""
	return round(num(value), 2) + 0.0


def pct_change(current, previous) -> float | None:
	"""How much `current` differs from `previous`, in percent to 1 decimal. None when `previous` is 0 or missing."""
	if previous is None or current is None:
		return None
	previous = num(previous)
	if previous == 0:
		return None
	return round((num(current) - previous) / abs(previous) * 100, 1) + 0.0


def average_basket(revenue, sales_count) -> float:
	"""Revenue per sale; 0 when there were no sales."""
	count = int(num(sales_count))
	if count <= 0:
		return 0.0
	return money(num(revenue) / count)


# --- dates and periods ------------------------------------------------------------------------------


def nairobi_today(now_utc: datetime) -> date:
	"""The date in Nairobi at the given moment (a timezone-aware time, or a naive one taken as UTC)."""
	if now_utc.tzinfo is None:
		now_utc = now_utc.replace(tzinfo=timezone.utc)
	return now_utc.astimezone(NAIROBI).date()


def parse_date(value) -> date:
	"""A date from a `date`, a `datetime` or text that starts with YYYY-MM-DD. Raises ValueError otherwise."""
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = str(value or "").strip()
	if not re.match(r"^\d{4}-\d{2}-\d{2}", text):
		raise ValueError("not a date")
	return date.fromisoformat(text[:10])


def parse_datetime(value) -> datetime | None:
	"""A datetime from a datetime or text like '2026-10-13 08:15:00'; None when it cannot be read."""
	if isinstance(value, datetime):
		return value
	if isinstance(value, date):
		return datetime(value.year, value.month, value.day)
	try:
		return datetime.fromisoformat(str(value).strip().replace("T", " "))
	except (TypeError, ValueError):
		return None


def week_start(day: date) -> date:
	"""The Monday of the week that `day` is in."""
	return day - timedelta(days=day.weekday())


def resolve_period(key, from_date, to_date, today: date) -> dict:
	"""
	The dates behind a period key, and the period of the same length right before it.

	today   today only
	week    this week so far: Monday to today
	7d      the last 7 days, today included
	30d     the last 30 days, today included
	month   this calendar month so far
	custom  from_date to to_date (both needed; a future end is cut back to today; at most a year)

	The answer holds `from`, `to`, `previous_from`, `previous_to` (dates), `days` and `key`. The previous period
	ends the day before this one starts and has as many days (for `month`: the same number of days before the
	first of the month).
	"""
	key = key or "30d"
	if key not in PERIOD_KEYS:
		raise PeriodError("bad_period")
	if key == "today":
		start, end = today, today
	elif key == "week":
		start, end = week_start(today), today
	elif key == "7d":
		start, end = today - timedelta(days=6), today
	elif key == "30d":
		start, end = today - timedelta(days=29), today
	elif key == "month":
		start, end = today.replace(day=1), today
	else:
		if not from_date or not to_date:
			raise PeriodError("need_dates")
		try:
			start, end = parse_date(from_date), parse_date(to_date)
		except ValueError:
			raise PeriodError("bad_date")
		if start > end:
			raise PeriodError("order")
		end = min(end, today)
		if start > end:
			raise PeriodError("order")
	days = (end - start).days + 1
	if days > MAX_CUSTOM_DAYS:
		raise PeriodError("too_long")
	previous_to = start - timedelta(days=1)
	previous_from = previous_to - timedelta(days=days - 1)
	return {
		"key": key,
		"from": start,
		"to": end,
		"days": days,
		"previous_from": previous_from,
		"previous_to": previous_to,
	}


def days_of_data(first_sale, period: dict) -> int:
	"""
	How many days of the period there is history for: all of them once the business has sold before the period
	starts, fewer for a new business, 0 when there has been no sale at all. `first_sale` is the date of the earliest
	sale seen in the period or the one before it (None when there is none).
	"""
	if first_sale is None:
		return 0
	first = parse_date(first_sale)
	if first <= period["from"]:
		return period["days"]
	if first > period["to"]:
		return 0
	return (period["to"] - first).days + 1


def period_payload(period: dict, data_days: int | None) -> dict:
	"""The `period` part of the answer."""
	return {
		"key": period["key"],
		"from": str(period["from"]),
		"to": str(period["to"]),
		"days": period["days"],
		"previous_from": str(period["previous_from"]),
		"previous_to": str(period["previous_to"]),
		"days_of_data": data_days,
	}


def date_range(start: date, end: date) -> list[date]:
	"""Every date from start to end, both included (empty when end is before start)."""
	return [start + timedelta(days=i) for i in range(max((end - start).days + 1, 0))]


# --- sales by day -----------------------------------------------------------------------------------


def daily_totals(rows) -> dict:
	"""
	Sales rows (posting_date, is_return, net, gross, n: one per day and sale-or-return) -> {date text: totals}.
	Revenue and gross_total add up every row, so returns (negative amounts) take their value off; the count
	is the number of sales, not of returns.
	"""
	out: dict = defaultdict(lambda: {"revenue": 0.0, "gross_total": 0.0, "count": 0})
	for row in rows or []:
		day = str(parse_date(row.get("posting_date") or row.get("date")))
		entry = out[day]
		entry["revenue"] += num(row.get("net"))
		entry["gross_total"] += num(row.get("gross"))
		if not row.get("is_return"):
			entry["count"] += int(num(row.get("n")))
	return {
		day: {"revenue": money(v["revenue"]), "gross_total": money(v["gross_total"]), "count": v["count"]}
		for day, v in out.items()
	}


def sum_totals(by_day: dict, start: date, end: date) -> dict:
	"""Revenue, gross total and sale count over start..end from daily_totals output."""
	revenue = gross = 0.0
	count = 0
	for day in date_range(start, end):
		entry = by_day.get(str(day))
		if entry:
			revenue += entry["revenue"]
			gross += entry["gross_total"]
			count += entry["count"]
	return {"revenue": money(revenue), "gross_total": money(gross), "count": count}


def first_sale_date(rows) -> date | None:
	"""The earliest date in sales rows (None when there are none)."""
	days = [parse_date(r.get("posting_date") or r.get("date")) for r in rows or []]
	return min(days) if days else None


def build_sales_series(
	current: dict,
	previous: dict,
	period: dict,
	today: date,
	profit_current: dict | None = None,
	profit_previous: dict | None = None,
) -> list[dict]:
	"""
	The day-by-day points for `charts.sales`, completed days only.

	`current` and `previous` are daily_totals outputs. Today is left out of the line (the day is not over), except
	when the period is `today`, when it is the one point and carries so_far: true. Days with no sales are
	present with zeros. Each point's `previous` is the matching day of the previous period (the same position from
	its start). The profit fields exist only when the profit dicts ({date text: profit}) are given.
	"""
	with_profit = profit_current is not None
	days = date_range(period["from"], period["to"])
	if period["key"] != "today":
		days = [d for d in days if d < today]
	points = []
	for day in days:
		offset = (day - period["from"]).days
		before = period["previous_from"] + timedelta(days=offset)
		now = current.get(str(day)) or {}
		then = previous.get(str(before)) or {}
		point = {
			"date": str(day),
			"revenue": money(now.get("revenue")),
			"gross_total": money(now.get("gross_total")),
			"count": int(now.get("count") or 0),
		}
		previous_point = {"revenue": money(then.get("revenue")), "count": int(then.get("count") or 0)}
		if with_profit:
			point["profit"] = money((profit_current or {}).get(str(day)))
			previous_point["profit"] = money((profit_previous or {}).get(str(before)))
		point["previous"] = previous_point
		if period["key"] == "today":
			point["so_far"] = True
		points.append(point)
	return points


# --- how people paid --------------------------------------------------------------------------------


def classify_method(name, mode_type=None) -> str:
	"""
	M-Pesa, Cash, Card, On credit or Other for a mode of payment's name (and its type in ERPNext, if known).

	MPESA, Mpesa and M-Pesa are M-Pesa; anything with "card" in it (Credit Card, Debit Card) is Card, checked
	before "credit" so a card is never on credit; Credit, On credit and Customer credit are On credit; Cash is Cash.
	Bank Draft, Cheque, Wire Transfer and everything else is Other.
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
	if str(mode_type or "").strip().lower() == "cash" and text not in ("bankdraft", "cheque", "wiretransfer"):
		return CASH
	return OTHER


def group_payments(rows, change_given=0.0, on_credit=0.0, mode_types=None) -> list[dict]:
	"""
	Payment rows (mode_of_payment, amount) -> [{"method", "amount"}], biggest first, nothing that adds up to 0.

	The change handed back to customers comes out of Cash (they paid more cash than the sale). `on_credit` is
	the unpaid part of the period's sales. `mode_types` maps a mode's name to its ERPNext type.
	"""
	mode_types = mode_types or {}
	totals = defaultdict(float)
	for row in rows or []:
		name = row.get("mode_of_payment")
		totals[classify_method(name, mode_types.get(name))] += num(row.get("amount"))
	totals[CASH] -= num(change_given)
	totals[CREDIT] += num(on_credit)
	order = {method: i for i, method in enumerate(METHODS)}
	grouped = [{"method": m, "amount": money(a)} for m, a in totals.items() if abs(a) >= 0.005]
	return sorted(grouped, key=lambda g: (-g["amount"], order[g["method"]]))


def payment_summary(rows, method: str, mode_types=None) -> dict:
	"""Amount and number of payments for one method (rows: mode_of_payment, amount, n)."""
	mode_types = mode_types or {}
	amount, count = 0.0, 0
	for row in rows or []:
		name = row.get("mode_of_payment")
		if classify_method(name, mode_types.get(name)) == method:
			amount += num(row.get("amount"))
			count += int(num(row.get("n")))
	return {"amount": money(amount), "payments": count}


def cash_in_till(opening_rows, payment_rows, change_rows, entry_rows, mode_types=None) -> dict:
	"""
	The cash that should be in the till: the opening cash of the session plus cash payments, minus cash refunds
	and cash paid out.

	opening_rows  [{mode_of_payment, opening_amount}] counted when the till was opened
	payment_rows  [{is_return, mode_of_payment, amount}] till payments in the session (refunds are returns)
	change_rows   [{is_return, change}] change handed back to customers on those sales (taken out of cash)
	entry_rows    [{payment_type, mode_of_payment, amount}]: Payment Entries by the cashier in the session,
	              "Receive" money in (a customer settling a debt) and "Pay" money out
	"""
	mode_types = mode_types or {}

	def is_cash(row):
		name = row.get("mode_of_payment")
		return classify_method(name, mode_types.get(name)) == CASH

	opening = sum(num(r.get("opening_amount")) for r in opening_rows or [] if is_cash(r))
	received = sum(num(r.get("amount")) for r in payment_rows or [] if is_cash(r) and not r.get("is_return"))
	refunds = -sum(num(r.get("amount")) for r in payment_rows or [] if is_cash(r) and r.get("is_return"))
	change = sum(num(r.get("change")) for r in change_rows or [] if not r.get("is_return"))
	entries_in = sum(
		num(r.get("amount")) for r in entry_rows or [] if is_cash(r) and r.get("payment_type") == "Receive"
	)
	paid_out = sum(
		num(r.get("amount")) for r in entry_rows or [] if is_cash(r) and r.get("payment_type") == "Pay"
	)
	amount = opening + received - change + entries_in - max(refunds, 0.0) - paid_out
	return {"amount": money(amount), "opening_float": money(opening)}


# --- products and hours -----------------------------------------------------------------------------


def top_products(rows, limit: int = TOP_PRODUCT_COUNT) -> list[dict]:
	"""The `limit` products with the most revenue. Rows: item_code, item_name, net_amount (several per product)."""
	revenue: dict = defaultdict(float)
	names: dict = {}
	for row in rows or []:
		code = row.get("item_code")
		if not code:
			continue
		revenue[code] += num(row.get("net_amount"))
		if row.get("item_name") and code not in names:
			names[code] = row["item_name"]
	ranked = sorted(((c, r) for c, r in revenue.items() if r > 0), key=lambda cr: (-cr[1], cr[0]))
	return [{"item_code": c, "name": names.get(c) or c, "revenue": money(r)} for c, r in ranked[:limit]]


def busiest_hours(hour_counts: dict, days: int) -> list[dict]:
	"""
	The average number of sales in each hour of the day, 8 to 19. `hour_counts` maps an hour (0 to 23) to how many
	sales were made in that hour over the whole period; `days` is how many days that covers.
	"""
	days = max(int(num(days)), 1)
	return [
		{"hour": hour, "average_sales": round(num(hour_counts.get(hour)) / days, 2)}
		for hour in range(FIRST_HOUR, LAST_HOUR + 1)
	]


# --- needs attention --------------------------------------------------------------------------------


def open_session_items(sessions, today: date) -> list[dict]:
	"""A till session opened on an earlier day and still open. Sessions: name, period_start_date (or posting_date)."""
	items = []
	for session in sessions or []:
		opened = parse_datetime(session.get("period_start_date")) or parse_datetime(
			session.get("posting_date")
		)
		if opened is None or opened.date() >= today:
			continue
		items.append(
			{"type": "till_not_closed", "session": session.get("name"), "opened_on": str(opened.date())}
		)
	return items


def out_of_stock_items(sold_items, qty_by_item: dict) -> list[dict]:
	"""
	Products that sold recently and have 0 or less in stock. `sold_items`: item_code, item_name;
	`qty_by_item`: item_code -> quantity in stock across the stores looked at (a product with no stock record is 0).
	"""
	items = []
	for row in sold_items or []:
		code = row.get("item_code")
		if num(qty_by_item.get(code)) <= 0:
			items.append({"type": "out_of_stock", "item_code": code, "name": row.get("item_name") or code})
	return items


def running_low_items(rows) -> list[dict]:
	"""
	Products with some stock left but at or below their warning level. Rows: item_code, item_name, actual_qty,
	reorder_level (one per store); a product low in several stores shows once, with its lowest stock.
	"""
	lowest: dict = {}
	for row in rows or []:
		qty, level = num(row.get("actual_qty")), num(row.get("reorder_level"))
		if qty <= 0 or level <= 0 or qty > level:
			continue
		code = row.get("item_code")
		if code not in lowest or qty < lowest[code]["qty"]:
			lowest[code] = {
				"type": "running_low",
				"item_code": code,
				"name": row.get("item_name") or code,
				"qty": qty,
				"warning_level": level,
			}
	return list(lowest.values())


def due_date_of(row) -> date | None:
	"""
	When a credit sale is due: the invoice's own due date if the customer has payment terms, otherwise 14 days after
	the sale (an invoice made without terms carries a due date equal to its own day, which would be wrong).
	"""
	posted = parse_date(row.get("posting_date")) if row.get("posting_date") else None
	if row.get("has_terms") and row.get("due_date"):
		return parse_date(row["due_date"])
	if posted is None:
		return None
	return posted + timedelta(days=DEFAULT_CREDIT_DAYS)


def overdue_customer_items(rows, today: date) -> list[dict]:
	"""
	Customers with a credit sale past its due date, one item per customer: all they owe on overdue sales and how
	many days late the oldest is. Rows: customer, customer_name, mobile_no, posting_date, due_date,
	outstanding_amount, has_terms.
	"""
	by_customer: dict = {}
	for row in rows or []:
		owed = num(row.get("outstanding_amount"))
		due = due_date_of(row)
		if owed <= 0 or due is None or due >= today:
			continue
		late = (today - due).days
		entry = by_customer.setdefault(
			row.get("customer"),
			{
				"type": "customer_overdue",
				"customer": row.get("customer"),
				"name": row.get("customer_name") or row.get("customer"),
				"mobile": row.get("mobile_no") or None,
				"amount": 0.0,
				"days_overdue": 0,
			},
		)
		entry["amount"] += owed
		entry["days_overdue"] = max(entry["days_overdue"], late)
	for entry in by_customer.values():
		entry["amount"] = money(entry["amount"])
	return list(by_customer.values())


def supplier_bill_items(rows, today: date) -> list[dict]:
	"""Unpaid supplier bills due within 3 days (or already late). Rows: name, supplier, supplier_name, outstanding_amount, due_date."""
	horizon = today + timedelta(days=BILL_WARNING_DAYS)
	items = []
	for row in rows or []:
		if num(row.get("outstanding_amount")) <= 0 or not row.get("due_date"):
			continue
		due = parse_date(row["due_date"])
		if due > horizon:
			continue
		items.append(
			{
				"type": "supplier_bill_due",
				"invoice": row.get("name"),
				"supplier": row.get("supplier_name") or row.get("supplier"),
				"amount": money(row.get("outstanding_amount")),
				"due_on": str(due),
			}
		)
	return items


def _attention_order(item: dict):
	"""Within one kind, the worst first: lowest stock, biggest debt, oldest session, soonest bill."""
	kind = item["type"]
	if kind == "till_not_closed":
		return (item.get("opened_on") or "", item.get("session") or "")
	if kind == "out_of_stock":
		return (str(item.get("name") or "").lower(), item.get("item_code") or "")
	if kind == "running_low":
		return (num(item.get("qty")), str(item.get("name") or "").lower())
	if kind == "customer_overdue":
		return (-num(item.get("amount")), str(item.get("name") or "").lower())
	return (item.get("due_on") or "", -num(item.get("amount")))


def rank_attention(items, may_see_money: bool, limit: int = MAX_ATTENTION) -> list[dict]:
	"""
	At most `limit` items, most urgent first (urgency 1 is the most urgent), each with its `urgency` filled in.
	Without `may_see_money` the customer and supplier items are dropped (a cashier never sees them).
	"""
	kept = [dict(i) for i in items or [] if i.get("type") in URGENCY]
	if not may_see_money:
		kept = [i for i in kept if i["type"] not in MONEY_ITEMS]
	for item in kept:
		item["urgency"] = URGENCY[item["type"]]
	kept.sort(key=lambda i: (i["urgency"], _attention_order(i)))
	return kept[:limit]


def money_owed_summary(rows) -> dict:
	"""What customers owe: the total and how many customers. Rows: customer, outstanding_amount."""
	owed: dict = defaultdict(float)
	for row in rows or []:
		amount = num(row.get("outstanding_amount"))
		if amount > 0:
			owed[row.get("customer")] += amount
	return {"amount": money(sum(owed.values())), "customers": len(owed)}


# --- profit -----------------------------------------------------------------------------------------


def day_key(row) -> str:
	return str(parse_date(row.get("posting_date")))


def rows_between(rows, start: date, end: date) -> list[dict]:
	"""The profit rows whose posting_date is in start..end."""
	low, high = str(start), str(end)
	return [r for r in rows or [] if low <= day_key(r) <= high]


def with_fallback_rates(rows, item_rates: dict, bin_rates: dict) -> list[dict]:
	"""
	Fill in `fallback_rate` on the rows that have uncosted stock: the store's own valuation rate first, then the
	product's, then the highest rate in any store. A product with none stays at 0 (it is not counted in profit).
	`item_rates`: item_code -> rate; `bin_rates`: (item_code, warehouse) -> rate.
	"""
	best_bin: dict = {}
	for (code, _warehouse), rate in bin_rates.items():
		best_bin[code] = max(best_bin.get(code, 0.0), num(rate))
	for row in rows or []:
		if not row.get("is_stock_item") or not num(row.get("uncosted_qty")):
			continue
		code = row.get("item_code")
		row["fallback_rate"] = (
			num(bin_rates.get((code, row.get("warehouse"))))
			or num(item_rates.get(code))
			or best_bin.get(code, 0.0)
		)
	return rows


def for_profit(rows) -> list[dict]:
	"""
	Copies of the profit rows with the sales of products that have no cost at all taken out, so profit is only
	worked out where a cost is known ("not counted", not shown as 100% margin). When only part of a row's quantity
	has no cost, only that share of its sales is taken out. Revenue figures keep using the original rows.
	"""
	counted = []
	for row in rows or []:
		row = dict(row)
		missing = num(row.get("uncosted_qty"))
		if row.get("is_stock_item") and missing and not num(row.get("fallback_rate")):
			qty = num(row.get("qty"))
			share = min(max(missing / qty, 0.0), 1.0) if qty else 1.0
			row["net_amount"] = num(row.get("net_amount")) * (1 - share)
		counted.append(row)
	return counted


def profit_by_day(rows) -> dict:
	"""{date text: profit before running costs (sales minus cost of goods)} for profit rows."""
	grouped: dict = defaultdict(list)
	for row in rows or []:
		grouped[day_key(row)].append(row)
	return {day: money(summarize_profit(day_rows)["grossProfit"]) for day, day_rows in grouped.items()}


def uncosted_products(rows, item_rates: dict, bin_rates: dict) -> list[dict]:
	"""
	The products sold that have no cost price at all: nothing recorded when the stock left, no valuation rate on
	the product and none in any store. Rows are the profit rows (item_code, item_name, is_stock_item, qty,
	recorded_cost, uncosted_qty). Services and other non-stock products have no cost of goods and are left out.
	Returns [{"item_code", "item_name", "units_sold"}], the most sold first.
	"""
	any_bin: dict = defaultdict(float)
	for (code, _warehouse), rate in (bin_rates or {}).items():
		any_bin[code] = max(any_bin[code], num(rate))
	sold: dict = {}
	for row in rows or []:
		if not row.get("is_stock_item"):
			continue
		code = row.get("item_code")
		entry = sold.setdefault(code, {"item_name": None, "qty": 0.0, "recorded": 0.0, "uncosted": 0.0})
		entry["item_name"] = entry["item_name"] or row.get("item_name")
		entry["qty"] += num(row.get("qty"))
		entry["recorded"] += abs(num(row.get("recorded_cost")))
		entry["uncosted"] += abs(num(row.get("uncosted_qty")))
	found = []
	for code, entry in sold.items():
		if entry["qty"] <= 0 or not entry["uncosted"]:
			continue
		if entry["recorded"] or num((item_rates or {}).get(code)) or any_bin.get(code):
			continue
		found.append(
			{"item_code": code, "item_name": entry["item_name"] or code, "units_sold": round(entry["qty"], 3)}
		)
	return sorted(found, key=lambda p: (-p["units_sold"], p["item_name"].lower()))


def net_profit_tile(current: dict, previous: dict, spark, uncosted_count: int) -> dict:
	"""The `tiles.net_profit` card from two summarize_profit answers (this period and the one before)."""
	value = money(current["netProfit"])
	before = money(previous["netProfit"])
	return {
		"value": value,
		"margin_pct": round(num(current["profitMargin"]), 1),
		"previous": before,
		"change_pct": pct_change(value, before),
		"spark": list(spark or []),
		"estimated": bool(current.get("costEstimatedItems")),
		"uncosted_products": int(uncosted_count),
	}
