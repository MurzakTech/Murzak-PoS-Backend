"""
The arithmetic behind the Reports (`reports_api.run_report`), with no database and no Frappe.

Every function here takes plain rows (dicts, dates, numbers) and returns plain results, so each rule can be tested with
a few lines of data. The database work, the role checks and the cache are in reports_api.py.

The Home overview and the Reports must agree for the same period, so this file does not repeat Home's rules. It imports
them from overview_calc.py (periods, daily totals, payments by method, top products, profit rows, money owed, percent
change) and only adds what a report needs on top: rows by day or month, age bands, categories and the lists.

Conventions are the same as overview_calc: money is rounded to 2 decimals; a percentage change is None when there is
nothing to compare with; dates are `datetime.date` inside and `YYYY-MM-DD` text in the answer; "today" is the date in
Africa/Nairobi, which the caller passes in.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

from murzak_pos.api import overview_calc as oc
from murzak_pos.api.profit_calc import summarize_profit

# --- the reports and who may open them --------------------------------------------------------------

SALES = "sales"
PROFIT = "profit"
OWED = "owed"
RUNNING_COSTS = "running-costs"
STOCK_SUMMARY = "stock-summary"
STOCK_VALUE = "stock-value"
AGING_STOCK = "aging-stock"

OWNER, MANAGER, CASHIER = "owner", "manager", "cashier"
EVERYONE = (OWNER, MANAGER, CASHIER)
OWNER_AND_MANAGER = (OWNER, MANAGER)

# capability: the name in role_tiers.CAPABILITIES that decides who may open the report (None: any signed-in person).
# needs_period: False for the reports that describe the shop as it is now (money owed, stock), which ignore the period.
REPORTS = {
	SALES: {"capability": None, "needs_period": True},
	PROFIT: {"capability": "profit", "needs_period": True},
	OWED: {"capability": "money_owed", "needs_period": False},
	RUNNING_COSTS: {"capability": "profit", "needs_period": True},
	STOCK_SUMMARY: {"capability": "cost_of_goods", "needs_period": False},
	STOCK_VALUE: {"capability": "cost_of_goods", "needs_period": False},
	AGING_STOCK: {"capability": "cost_of_goods", "needs_period": False},
}

# Who holds each capability. reports_api checks the real thing (role_tiers); a test keeps this table equal to it.
TIERS_FOR = {
	None: EVERYONE,
	"profit": OWNER_AND_MANAGER,
	"cost_of_goods": OWNER_AND_MANAGER,
	"money_owed": OWNER_AND_MANAGER,
}

# The six questions of the landing page, in order. `opens` is the report the card opens; `reports` are the views that
# belong to the question (the stock question has two: what is in stock, and what it is worth).
QUESTIONS = (
	{"key": "sold", "opens": SALES, "reports": (SALES,)},
	{"key": "profit", "opens": PROFIT, "reports": (PROFIT,)},
	{"key": "owed", "opens": OWED, "reports": (OWED,)},
	{"key": "costs", "opens": RUNNING_COSTS, "reports": (RUNNING_COSTS,)},
	{"key": "stock", "opens": STOCK_SUMMARY, "reports": (STOCK_SUMMARY, STOCK_VALUE)},
	{"key": "not_selling", "opens": AGING_STOCK, "reports": (AGING_STOCK,)},
)

# The reports whose one-line answer on the landing page is cheap enough to work out (the not-selling list scans a month
# of stock movements, so its card shows the question only)
PREVIEWED = (SALES, PROFIT, OWED, RUNNING_COSTS, STOCK_SUMMARY)

DAY, MONTH = "day", "month"
MAX_DAYS_BY_DAY = 62  # a longer period is added up by month unless the caller asks for days
CATEGORIES_SHOWN = 6  # the chart shows this many categories and folds the rest into Other
MAX_ROWS = 500  # the lists (customers, products) are cut here; `rows_total` says how many there were


def allowed(name: str, tier) -> bool:
	"""Whether a kind of person (owner, manager, cashier) may open the report."""
	report = REPORTS.get(name)
	return bool(report) and tier in TIERS_FOR[report["capability"]]


def reports_for(tier) -> list[str]:
	return [name for name in REPORTS if allowed(name, tier)]


def questions_for(tier) -> list[dict]:
	"""The landing page's questions this kind of person may open, each with the reports they may open under it."""
	found = []
	for question in QUESTIONS:
		if not allowed(question["opens"], tier):
			continue
		found.append(
			{
				"key": question["key"],
				"opens": question["opens"],
				"reports": [name for name in question["reports"] if allowed(name, tier)],
				"as_of": not REPORTS[question["opens"]]["needs_period"],
			}
		)
	return found


def choose_group(days: int, asked=None) -> str:
	"""Day or month rows for the profit report: what was asked for if it is valid, else days up to 62 days, months after."""
	if asked in (DAY, MONTH):
		return asked
	return DAY if days <= MAX_DAYS_BY_DAY else MONTH


# --- small helpers ----------------------------------------------------------------------------------


def share(part, whole) -> float:
	"""A share in percent to 1 decimal; 0 when there is no whole."""
	whole = oc.num(whole)
	return round(oc.num(part) / whole * 100, 1) + 0.0 if whole else 0.0


def display_name(name) -> str:
	"""'Rent - DS' (an account or store with the business's short code on the end) reads 'Rent'."""
	text = str(name or "").strip()
	return re.sub(r"\s+-\s+[A-Za-z0-9]{1,8}$", "", text) or text


def _bucket(day: date, group_by: str) -> str:
	return day.strftime("%Y-%m") if group_by == MONTH else str(day)


def bucket_keys(period: dict, group_by: str) -> list[str]:
	"""Every day (or every month) the period touches, in order, each once."""
	keys: list[str] = []
	for day in oc.date_range(period["from"], period["to"]):
		key = _bucket(day, group_by)
		if not keys or keys[-1] != key:
			keys.append(key)
	return keys


def fold_categories(
	items: list[dict], keep: int = CATEGORIES_SHOWN, other_key: str = "category"
) -> list[dict]:
	"""The `keep` biggest items as they are and the rest added up as one "other" item (the chart's fifth colour rule)."""
	items = sorted(items, key=lambda i: -oc.num(i.get("amount")))
	if len(items) <= keep:
		return [dict(i) for i in items]
	head = [dict(i) for i in items[:keep]]
	rest = items[keep:]
	total = oc.money(sum(oc.num(i.get("amount")) for i in rest))
	head.append({other_key: None, "amount": total, "other": True, "count": len(rest)})
	return head


# --- sales ------------------------------------------------------------------------------------------


def sales_rows(totals: dict, period: dict, today: date) -> list[dict]:
	"""
	One row per day of the period, oldest first, from oc.daily_totals output covering the previous period and this one.
	Each row carries the matching day of the previous period (the same position from its start). A day with no
	sales is a row of zeros. `so_far` marks today, because the day is not over.
	"""
	rows = []
	for day in oc.date_range(period["from"], period["to"]):
		before = period["previous_from"] + timedelta(days=(day - period["from"]).days)
		now = totals.get(str(day)) or {}
		then = totals.get(str(before)) or {}
		revenue, count = oc.money(now.get("revenue")), int(now.get("count") or 0)
		rows.append(
			{
				"date": str(day),
				"revenue": revenue,
				"count": count,
				"average_basket": oc.average_basket(revenue, count),
				"previous_revenue": oc.money(then.get("revenue")),
				"previous_count": int(then.get("count") or 0),
				"so_far": day == today,
			}
		)
	return rows


def sales_points(rows: list[dict]) -> list[dict]:
	"""The rows in the shape of the sales chart (`charts.sales` of the Home overview): previous period nested."""
	points = []
	for row in rows:
		point = {
			"date": row["date"],
			"revenue": row["revenue"],
			"count": row["count"],
			"previous": {"revenue": row["previous_revenue"], "count": row["previous_count"]},
		}
		if row["so_far"]:
			point["so_far"] = True
		points.append(point)
	return points


def sales_summary(totals: dict, rows: list[dict], period: dict) -> dict:
	"""Revenue and the number of sales for the period, against the previous one (the Home revenue tile's figures)."""
	now = oc.sum_totals(totals, period["from"], period["to"])
	before = oc.sum_totals(totals, period["previous_from"], period["previous_to"])
	best = None
	for row in rows:
		if row["revenue"] > 0 and (best is None or row["revenue"] > best["revenue"]):
			best = row
	return {
		"revenue": now["revenue"],
		"gross_total": now["gross_total"],
		"previous_revenue": before["revenue"],
		"change_pct": oc.pct_change(now["revenue"], before["revenue"]),
		"count": now["count"],
		"previous_count": before["count"],
		"count_change_pct": oc.pct_change(now["count"], before["count"]),
		"average_basket": oc.average_basket(now["revenue"], now["count"]),
		"days_with_sales": sum(1 for r in rows if r["count"] > 0),
		"best_day": {"date": best["date"], "revenue": best["revenue"]} if best else None,
	}


# --- profit -----------------------------------------------------------------------------------------


def split_expenses(expense_rows, period: dict) -> tuple[dict, float, float]:
	"""
	Expense rows (posting_date, amount, ...) over the previous period and this one ->
	({date text: running costs} for this period, this period's total, the previous period's total).
	"""
	by_day: dict = defaultdict(float)
	before = 0.0
	for row in expense_rows or []:
		day = oc.parse_date(row.get("posting_date"))
		amount = oc.num(row.get("amount"))
		if period["from"] <= day <= period["to"]:
			by_day[str(day)] += amount
		elif period["previous_from"] <= day <= period["previous_to"]:
			before += amount
	return {d: oc.money(a) for d, a in by_day.items()}, oc.money(sum(by_day.values())), oc.money(before)


def profit_summary(
	now: dict, before: dict, running: float, previous_running: float, revenue: dict, uncosted: int
) -> dict:
	"""
	The profit report's headline. `now` and `before` are summarize_profit answers (Home's Net profit tile uses the same
	call), `revenue` is {"now": ..., "before": ...} from sum_totals (Home's Revenue tile uses the same sums).

	Profit is worked out only on products with a cost: the sales of a product with no cost at all are left out
	(`sales_not_counted`). So revenue minus cost of goods minus running costs equals net profit exactly when no
	product is left out, and the difference is shown when some are.
	"""
	revenue_now, revenue_before = oc.money(revenue["now"]["revenue"]), oc.money(revenue["before"]["revenue"])
	counted = oc.money(now["profitSales"])
	net, net_before = oc.money(now["netProfit"]), oc.money(before["netProfit"])
	return {
		"revenue": revenue_now,
		"previous_revenue": revenue_before,
		"revenue_change_pct": oc.pct_change(revenue_now, revenue_before),
		"sales_counted": counted,
		"sales_not_counted": oc.money(max(revenue_now - counted, 0.0)) if uncosted else 0.0,
		"cost_of_goods": oc.money(now["costOfGoodsSold"]),
		"previous_cost_of_goods": oc.money(before["costOfGoodsSold"]),
		"gross_profit": oc.money(now["grossProfit"]),
		"gross_margin_pct": round(oc.num(now["grossMargin"]), 1) + 0.0,
		"running_costs": oc.money(running),
		"previous_running_costs": oc.money(previous_running),
		"net_profit": net,
		"previous_net_profit": net_before,
		"change_pct": oc.pct_change(net, net_before),
		"margin_pct": round(oc.num(now["profitMargin"]), 1) + 0.0,
		"previous_margin_pct": round(oc.num(before["profitMargin"]), 1) + 0.0,
		"uncosted_products": int(uncosted),
		"estimated": bool(now.get("costEstimatedItems")),
	}


def profit_rows(now_rows, expenses_by_day: dict, period: dict, today: date, group_by: str) -> list[dict]:
	"""
	Sales counted, cost of goods, running costs and net profit for each day (or month) of the period, oldest first.
	`now_rows` are the profit rows of the period before oc.for_profit (this function applies it); running costs are
	{date text: amount}. A day or month with nothing in it is a row of zeros.
	"""
	grouped: dict = defaultdict(list)
	for row in oc.for_profit(now_rows):
		grouped[_bucket(oc.parse_date(row.get("posting_date")), group_by)].append(row)
	costs: dict = defaultdict(float)
	for day, amount in (expenses_by_day or {}).items():
		costs[_bucket(oc.parse_date(day), group_by)] += oc.num(amount)
	current = _bucket(today, group_by) if period["from"] <= today <= period["to"] else None
	rows = []
	for key in bucket_keys(period, group_by):
		figures = summarize_profit(grouped.get(key, []), costs.get(key, 0.0))
		rows.append(
			{
				"period": key,
				"sales": oc.money(figures["profitSales"]),
				"cost_of_goods": oc.money(figures["costOfGoodsSold"]),
				"gross_profit": oc.money(figures["grossProfit"]),
				"running_costs": oc.money(costs.get(key, 0.0)),
				"net_profit": oc.money(figures["netProfit"]),
				"margin_pct": round(oc.num(figures["profitMargin"]), 1) + 0.0,
				"so_far": key == current,
			}
		)
	return rows


def waterfall(summary: dict) -> list[dict]:
	"""The four steps of the profit waterfall: sales counted, minus cost of goods, minus running costs, net profit."""
	return [
		{"key": "sales", "amount": summary["sales_counted"]},
		{"key": "cost_of_goods", "amount": summary["cost_of_goods"]},
		{"key": "running_costs", "amount": summary["running_costs"]},
		{"key": "net_profit", "amount": summary["net_profit"]},
	]


def profit_report(
	now_rows,
	before_rows,
	expense_rows,
	revenue_totals: dict,
	uncosted: int,
	period: dict,
	today: date,
	group_by=None,
) -> dict:
	"""The whole profit report from the profit rows of this period and the one before and the running-cost rows."""
	group = choose_group(period["days"], group_by)
	by_day, running, previous_running = split_expenses(expense_rows, period)
	now = summarize_profit(oc.for_profit(now_rows), running)
	before = summarize_profit(oc.for_profit(before_rows), previous_running)
	revenue = {
		"now": oc.sum_totals(revenue_totals, period["from"], period["to"]),
		"before": oc.sum_totals(revenue_totals, period["previous_from"], period["previous_to"]),
	}
	summary = profit_summary(now, before, running, previous_running, revenue, uncosted)
	rows = profit_rows(now_rows, by_day, period, today, group)
	return {
		"summary": summary,
		"rows": rows,
		"rows_total": len(rows),
		"group_by": group,
		"series": {
			"waterfall": waterfall(summary),
			"over_time": [
				{"period": r["period"], "net_profit": r["net_profit"], "so_far": r["so_far"]} for r in rows
			],
		},
	}


# --- money owed -------------------------------------------------------------------------------------

# The age of a debt is the days since the sale: 0 to 7, 8 to 14, 15 to 30, over 30 (Spec: Data and API needs)
AGE_BANDS = (("0_7", 0, 7), ("8_14", 8, 14), ("15_30", 15, 30), ("over_30", 31, None))


def age_in_days(posted, today: date) -> int:
	"""Days since the sale; a sale dated in the future counts as 0."""
	return max((today - oc.parse_date(posted)).days, 0)


def age_band(days: int) -> str:
	for key, low, high in AGE_BANDS:
		if days >= low and (high is None or days <= high):
			return key
	return AGE_BANDS[-1][0]


def owed_report(rows, today: date, limit: int = MAX_ROWS) -> dict:
	"""
	Who owes the shop money, by customer and by age. Rows (one per customer and sale date): customer, customer_name,
	mobile_no, posting_date, outstanding_amount. A customer's row holds what they owe in each age band and the age of
	their oldest unpaid sale; customers are listed with the biggest debt first. The total and the number of customers
	come from oc.money_owed_summary, the function behind Home's Money owed tile.
	"""
	people: dict = {}
	bands = {key: {"amount": 0.0, "customers": set()} for key, _low, _high in AGE_BANDS}
	for row in rows or []:
		owed = oc.num(row.get("outstanding_amount"))
		if owed <= 0 or not row.get("posting_date"):
			continue
		customer = row.get("customer")
		days = age_in_days(row["posting_date"], today)
		band = age_band(days)
		person = people.setdefault(
			customer,
			{
				"customer": customer,
				"name": row.get("customer_name") or customer,
				"mobile": row.get("mobile_no") or None,
				"amount": 0.0,
				"oldest_days": 0,
				"oldest_sale": None,
				"bands": {key: 0.0 for key, _low, _high in AGE_BANDS},
			},
		)
		person["amount"] += owed
		person["bands"][band] += owed
		if days >= person["oldest_days"]:
			person["oldest_days"] = days
			person["oldest_sale"] = str(oc.parse_date(row["posting_date"]))
		if row.get("mobile_no") and not person["mobile"]:
			person["mobile"] = row["mobile_no"]
		bands[band]["amount"] += owed
		bands[band]["customers"].add(customer)

	ordered = sorted(people.values(), key=lambda p: (-p["amount"], str(p["name"]).lower()))
	for person in ordered:
		person["amount"] = oc.money(person["amount"])
		person["bands"] = {k: oc.money(v) for k, v in person["bands"].items()}
	total = oc.money_owed_summary(rows)
	age_series = [
		{
			"key": key,
			"from": low,
			"to": high,
			"amount": oc.money(bands[key]["amount"]),
			"customers": len(bands[key]["customers"]),
		}
		for key, low, high in AGE_BANDS
	]
	over_30 = bands["over_30"]["amount"]
	return {
		"summary": {
			"amount": total["amount"],
			"customers": total["customers"],
			"oldest_days": max((p["oldest_days"] for p in ordered), default=0),
			"over_30_amount": oc.money(over_30),
			"over_30_share_pct": share(over_30, total["amount"]),
			"bands": age_series,
		},
		"rows": ordered[:limit],
		"rows_total": len(ordered),
		"series": {"age_bands": age_series},
	}


# --- running costs ----------------------------------------------------------------------------------


def category_name(row) -> str:
	"""The name a shop owner knows an expense account by: its own name, else the account without the business code."""
	return str(row.get("account_name") or "").strip() or display_name(row.get("account"))


def running_costs_report(rows, period: dict, today: date) -> dict:
	"""
	Where the money went. Rows (one per day and expense account): posting_date, account, account_name, amount, n.
	The total is the sum of the categories, and it is the figure that comes off Home's Net profit.
	"""
	by_category: dict = {}
	by_day: dict = defaultdict(float)
	before = 0.0
	for row in rows or []:
		day = oc.parse_date(row.get("posting_date"))
		amount = oc.num(row.get("amount"))
		if day < period["from"] or day > period["to"]:
			if period["previous_from"] <= day <= period["previous_to"]:
				before += amount
			continue
		by_day[str(day)] += amount
		entry = by_category.setdefault(
			row.get("account"),
			{"category": category_name(row), "account": row.get("account"), "amount": 0.0, "entries": 0},
		)
		entry["amount"] += amount
		entry["entries"] += int(oc.num(row.get("n")))
	total = oc.money(sum(by_day.values()))
	categories = []
	for entry in by_category.values():
		if abs(entry["amount"]) < 0.005:
			continue
		categories.append(
			{**entry, "amount": oc.money(entry["amount"]), "share_pct": share(entry["amount"], total)}
		)
	categories.sort(key=lambda c: (-c["amount"], c["category"].lower()))
	daily = [
		{"date": str(day), "amount": oc.money(by_day.get(str(day))), "so_far": day == today}
		for day in oc.date_range(period["from"], period["to"])
	]
	biggest = categories[0] if categories and categories[0]["amount"] > 0 else None
	return {
		"summary": {
			"total": total,
			"previous_total": oc.money(before),
			"change_pct": oc.pct_change(total, before),
			"categories": len(categories),
			"daily_average": oc.money(total / period["days"]) if period["days"] else 0.0,
			"biggest": {
				"category": biggest["category"],
				"amount": biggest["amount"],
				"share_pct": biggest["share_pct"],
			}
			if biggest
			else None,
		},
		"rows": categories,
		"rows_total": len(categories),
		"series": {"categories": fold_categories(categories), "by_day": daily},
	}


# --- stock ------------------------------------------------------------------------------------------


def stock_summary_report(rows) -> dict:
	"""
	What is in stock, by store, from the current Inventory Summary report. Rows: warehouse, total_qty, total_value.
	"""
	stores = []
	for row in rows or []:
		quantity, value = oc.num(row.get("total_qty")), oc.num(row.get("total_value"))
		if quantity <= 0 and value <= 0:
			continue
		stores.append(
			{
				"store": display_name(row.get("warehouse")),
				"warehouse": row.get("warehouse"),
				"quantity": round(quantity, 3),
				"value": oc.money(value),
			}
		)
	stores.sort(key=lambda s: (-s["value"], s["store"].lower()))
	total_value = oc.money(sum(s["value"] for s in stores))
	for store in stores:
		store["share_pct"] = share(store["value"], total_value)
	return {
		"summary": {
			"stores": len(stores),
			"quantity": round(sum(s["quantity"] for s in stores), 3),
			"value": total_value,
		},
		"rows": stores,
		"rows_total": len(stores),
		"series": {"by_store": [{"store": s["store"], "amount": s["value"]} for s in stores]},
	}


def stock_value_report(rows) -> dict:
	"""
	What the stock is worth, by category, from the current Inventory Valuation (value by category) report.
	Rows: item_group, total_value, total_qty, item_count.
	"""
	categories = []
	for row in rows or []:
		value = oc.num(row.get("total_value"))
		if value <= 0 and oc.num(row.get("total_qty")) <= 0:
			continue
		categories.append(
			{
				"category": str(row.get("item_group") or "Unknown"),
				"value": oc.money(value),
				"quantity": round(oc.num(row.get("total_qty")), 3),
				"items": int(oc.num(row.get("item_count"))),
			}
		)
	categories.sort(key=lambda c: (-c["value"], c["category"].lower()))
	total = oc.money(sum(c["value"] for c in categories))
	for category in categories:
		category["share_pct"] = share(category["value"], total)
	biggest = categories[0] if categories else None
	return {
		"summary": {
			"value": total,
			"categories": len(categories),
			"items": sum(c["items"] for c in categories),
			"biggest": {
				"category": biggest["category"],
				"value": biggest["value"],
				"share_pct": biggest["share_pct"],
			}
			if biggest
			else None,
		},
		"rows": categories,
		"rows_total": len(categories),
		"series": {
			"by_category": fold_categories(
				[{"category": c["category"], "amount": c["value"]} for c in categories]
			)
		},
	}


# Age of the stock still on the shelf that has not sold, in days: 0 to 30, 31 to 60, 61 to 90, over 90
STOCK_AGE_BANDS = (("0_30", 0, 30), ("31_60", 31, 60), ("61_90", 61, 90), ("over_90", 91, None))


def stock_age_band(days: int) -> str:
	for key, low, high in STOCK_AGE_BANDS:
		if days >= low and (high is None or days <= high):
			return key
	return STOCK_AGE_BANDS[-1][0]


def aging_stock_report(
	buckets: dict, names: dict, rates: dict, sold_recently=(), limit: int = MAX_ROWS
) -> dict:
	"""
	What is not selling, from the current Stock Aging report. `buckets` is that report's answer: age groups holding
	entries (item_code, warehouse, actual_qty, age_days, movement_rate, is_slow_moving), where movement_rate is the
	quantity that left the store in the last 30 days. A product is "not selling" when nothing of it left the stores in
	those 30 days AND it is not among `sold_recently`, the products Home counts as sold in the last 30 days (that read
	includes the till sales that have not yet been merged into a Sales Invoice, which leave no stock movement yet).
	`names` maps item_code to its name; `rates` maps (item_code, warehouse) to the valuation rate.

	One row per product, across its stores: quantity in stock, what it is worth, how many days the stock has been there
	(the oldest store) and the units sold in 30 days (always 0 for the products listed).
	"""
	entries = [e for group in (buckets or {}).values() for e in group or []]
	sold_codes = set(sold_recently or ())
	in_stock = {e.get("item_code") for e in entries if oc.num(e.get("actual_qty")) > 0}
	by_product: dict = {}
	selling: dict = defaultdict(float)
	for entry in entries:
		selling[entry.get("item_code")] += oc.num(entry.get("movement_rate"))
	for entry in entries:
		code = entry.get("item_code")
		quantity = oc.num(entry.get("actual_qty"))
		if quantity <= 0 or selling[code] > 0 or code in sold_codes:
			continue
		row = by_product.setdefault(
			code,
			{
				"item_code": code,
				"name": names.get(code) or code,
				"quantity": 0.0,
				"value": 0.0,
				"age_days": 0,
				"sold_30_days": 0,
				"stores": 0,
			},
		)
		row["quantity"] += quantity
		row["value"] += quantity * oc.num(rates.get((code, entry.get("warehouse"))))
		row["age_days"] = max(row["age_days"], int(oc.num(entry.get("age_days"))))
		row["stores"] += 1
	rows = sorted(by_product.values(), key=lambda r: (-r["value"], -r["age_days"], str(r["name"]).lower()))
	for row in rows:
		row["quantity"] = round(row["quantity"], 3)
		row["value"] = oc.money(row["value"])
		row["age_band"] = stock_age_band(row["age_days"])
	bands = {key: {"value": 0.0, "products": 0} for key, _low, _high in STOCK_AGE_BANDS}
	for row in rows:
		bands[row["age_band"]]["value"] += row["value"]
		bands[row["age_band"]]["products"] += 1
	total = oc.money(sum(r["value"] for r in rows))
	return {
		"summary": {
			"products": len(rows),
			"products_in_stock": len(in_stock),
			"value": total,
			"oldest_days": max((r["age_days"] for r in rows), default=0),
			"share_of_products_pct": share(len(rows), len(in_stock)),
		},
		"rows": rows[:limit],
		"rows_total": len(rows),
		"series": {
			"age_bands": [
				{
					"key": key,
					"from": low,
					"to": high,
					"value": oc.money(bands[key]["value"]),
					"products": bands[key]["products"],
				}
				for key, low, high in STOCK_AGE_BANDS
			]
		},
	}


# --- the landing page's one-line answers ------------------------------------------------------------


def preview_of(name: str, summary: dict) -> dict | None:
	"""The few numbers a question card shows as its one-line answer (the wording is the browser's)."""
	if not summary:
		return None
	if name == SALES:
		return {"revenue": summary["revenue"], "count": summary["count"], "change_pct": summary["change_pct"]}
	if name == PROFIT:
		return {
			"net_profit": summary["net_profit"],
			"margin_pct": summary["margin_pct"],
			"change_pct": summary["change_pct"],
		}
	if name == OWED:
		return {"amount": summary["amount"], "customers": summary["customers"]}
	if name == RUNNING_COSTS:
		return {"total": summary["total"], "change_pct": summary["change_pct"]}
	if name == STOCK_SUMMARY:
		return {"value": summary["value"], "quantity": summary["quantity"], "stores": summary["stores"]}
	return None
