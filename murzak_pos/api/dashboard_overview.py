"""
The Home overview: one call that gives the Home screens everything they draw (ticket P2-1).

`get_overview` returns the numbers in the contract docs/redesign/P2-1-overview-contract.md of the frontend
repository: today's takings, the revenue, net profit, money owed and low stock cards, the charts, and the few things
that need attention. It is new; no existing call, report or record is changed.

How it is built
- The arithmetic is in overview_calc.py (plain functions, no database). This file reads the database in small
  functions, one job each, and joins the two.
- Sales are counted as everywhere else in the app (see sales_figures.py): submitted Sales Invoices plus submitted
  POS Invoices that were not merged into a Sales Invoice at shift close, so nothing is counted twice.
- Profit is worked out from invoice lines, both kinds together (ticket P1-9). A POS Invoice has not moved stock
  yet, so its cost comes from the product's current valuation rate and is counted as an estimate; a product with no
  cost at all is not counted and is reported in `uncosted_products`.
- Roles are checked here, on the server (`role_tiers`): a cashier gets only their own sales, and none of the
  profit, cost or money-owed parts (they are left out of the answer, not set to null).
- Each card is computed on its own. If one fails it is null, `errors` names it, the real error goes to the Error
  Log, and the others are still returned.
- The answer is kept for 60 seconds, separately for each kind of person, company, store, period, dates and staff.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder import functions as fn
from pypika.terms import Case

from murzak_pos.api import overview_calc as calc
from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.dashboard_api import _get_operating_expenses
from murzak_pos.api.payment_gateway_common import resolve_company
from murzak_pos.api.profit_calc import summarize_profit
from murzak_pos.api.sales_figures import pos_invoice_days

CACHE_SECONDS = 60
CACHE_PREFIX = "murzak_pos:overview"
HERO_LOOKBACK_DAYS = 7  # the hero compares today with the same weekday last week

# Every card that can fail on its own, named as in the answer's `errors`
CARDS = (
	"hero",
	"tiles.revenue",
	"tiles.net_profit",
	"tiles.money_owed",
	"tiles.low_stock",
	"charts.sales",
	"charts.payments",
	"charts.top_products",
	"charts.busiest_hours",
	"attention",
)


# --- the clock and the cache (small on purpose, so tests can replace them) --------------------------


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


def _cache_get(key: str):
	return frappe.cache().get_value(key)


def _cache_set(key: str, value) -> None:
	frappe.cache().set_value(key, value, expires_in_sec=CACHE_SECONDS)


def cache_key(tier, company, store, staff, period: dict) -> str:
	"""One key per kind of person, company, store, period (with its dates) and staff member."""
	parts = [tier, company, store, period["key"], period["from"], period["to"], staff]
	return CACHE_PREFIX + ":" + ":".join(str(p or "-") for p in parts)


# --- the request ------------------------------------------------------------------------------------


class _Scope:
	"""What one request is about, and a memory so a query shared by several cards runs once."""

	def __init__(self, *, tier, user, company, store, owner, period, today, now, profit, owed):
		self.tier = tier
		self.user = user
		self.company = company
		self.store = store
		self.owner = owner
		self.period = period
		self.today = today
		self.now = now
		self.can_profit = profit
		self.can_owed = owed
		self._memo: dict = {}

	def memo(self, name, compute):
		"""Run `compute` once; a failure is remembered too, so every card that needs it fails the same way."""
		if name not in self._memo:
			try:
				self._memo[name] = (True, compute())
			except Exception as error:
				self._memo[name] = (False, error)
		ok, value = self._memo[name]
		if not ok:
			raise value
		return value


def _refuse(code: str):
	"""Refuse a period that cannot be used, in words the person can act on."""
	if code == "need_dates":
		frappe.throw(_("Please give both a start date and an end date."), frappe.ValidationError)
	if code == "bad_date":
		frappe.throw(_("Dates must look like 2026-10-13."), frappe.ValidationError)
	if code == "order":
		frappe.throw(_("The start date must not be after the end date."), frappe.ValidationError)
	if code == "too_long":
		frappe.throw(_("Please choose a period of one year or less."), frappe.ValidationError)
	frappe.throw(_("Choose a period: today, week, 7d, 30d, month or custom."), frappe.ValidationError)


@frappe.whitelist()
def get_overview(period="30d", from_date=None, to_date=None, store=None, staff=None):
	"""The Home overview for a period (see the module notes and the contract for the answer)."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	ensure_record_access("Warehouse", store)

	now = _now_utc()
	today = calc.nairobi_today(now)
	try:
		resolved = calc.resolve_period(period, from_date, to_date, today)
	except calc.PeriodError as error:
		_refuse(error.code)
	company = resolve_company(None)

	user = frappe.session.user
	# A cashier sees only their own sales, whatever was asked for
	owner = user if tier == role_tiers.CASHIER else (staff or None)
	key = cache_key(tier, company, store, owner, resolved)
	cached = _cache_get(key)
	if cached:
		return {"success": True, "data": {**cached, "cached": True}}

	scope = _Scope(
		tier=tier,
		user=user,
		company=company,
		store=store or None,
		owner=owner,
		period=resolved,
		today=today,
		now=now.astimezone(calc.NAIROBI),
		profit=role_tiers.can("profit"),
		owed=role_tiers.can("money_owed"),
	)
	data, errors = _build(scope)
	if data is None:
		return {"success": False, "message": _("Could not load the overview. Please try again.")}
	data["errors"] = errors
	if not errors:
		_cache_set(key, data)
	return {"success": True, "data": data}


def _log_card_error(card: str) -> None:
	frappe.log_error("Home overview card error", f"{card}\n{frappe.get_traceback()}")


def _build(scope: _Scope):
	"""(the answer, errors). The answer is None only when every card failed."""
	errors: dict = {}

	def card(name, compute):
		try:
			return compute(scope)
		except Exception:
			_log_card_error(name)
			errors[name] = _("Could not load this.")
			return None

	hero = card("hero", _hero)
	tiles = {"revenue": card("tiles.revenue", _revenue_tile)}
	if scope.can_profit:
		tiles["net_profit"] = card("tiles.net_profit", _net_profit_tile)
	if scope.can_owed:
		tiles["money_owed"] = card("tiles.money_owed", _money_owed_tile)
	tiles["low_stock"] = card("tiles.low_stock", _low_stock_tile)
	charts = {
		"sales": card("charts.sales", _sales_chart),
		"payments": card("charts.payments", _payments_chart),
		"top_products": card("charts.top_products", _top_products_chart),
		"busiest_hours": card("charts.busiest_hours", _busiest_hours_chart),
	}
	attention = card("attention", _attention)

	if len(errors) >= _card_count(scope):
		return None, errors

	try:
		data_days = _days_of_data(scope)
	except Exception:
		data_days = None
	data = {
		"role": scope.tier,
		"period": calc.period_payload(scope.period, data_days),
		"updated_at": scope.now.strftime("%H:%M"),
		"cached": False,
		"hero": hero,
		"tiles": tiles,
		"charts": charts,
		"attention": attention,
	}
	return data, errors


def _card_count(scope: _Scope) -> int:
	return len(CARDS) - (0 if scope.can_profit else 1) - (0 if scope.can_owed else 1)


# --- the cards --------------------------------------------------------------------------------------


def _period_rows(scope: _Scope) -> list[dict]:
	"""Sales rows for the previous period and this one together: one read shared by the cards that need them."""
	period = scope.period
	return scope.memo("period_rows", lambda: _sale_day_rows(scope, period["previous_from"], period["to"]))


def _period_days(scope: _Scope) -> dict:
	"""Daily totals for the previous period and this one together."""
	return calc.daily_totals(_period_rows(scope))


def _days_of_data(scope: _Scope) -> int:
	return calc.days_of_data(calc.first_sale_date(_period_rows(scope)), scope.period)


def _series(scope: _Scope, with_profit: bool = False) -> list[dict]:
	period = scope.period
	totals = _period_days(scope)
	profit_now = profit_before = None
	if with_profit:
		by_day = calc.profit_by_day(calc.for_profit(_profit_lines(scope)[0]))
		profit_now, profit_before = by_day, by_day
	return calc.build_sales_series(totals, totals, period, scope.today, profit_now, profit_before)


def _hero(scope: _Scope) -> dict:
	today, last_week = scope.today, scope.today - timedelta(days=HERO_LOOKBACK_DAYS)
	totals = calc.daily_totals(scope.memo("hero_rows", lambda: _sale_day_rows(scope, last_week, today)))
	now = calc.sum_totals(totals, today, today)
	then = calc.sum_totals(totals, last_week, last_week)
	payments = _payment_rows(scope, today, today)
	types = _mode_types(scope)
	mpesa = calc.payment_summary(payments, calc.MPESA, types)
	return {
		"today_total": now["revenue"],
		"gross_total": now["gross_total"],
		"same_weekday_last_week_total": then["revenue"],
		"change_pct": calc.pct_change(now["revenue"], then["revenue"]),
		"sales_count": now["count"],
		"average_basket": calc.average_basket(now["revenue"], now["count"]),
		"cash_in_till": _cash_in_till(scope),
		"mpesa": mpesa,
		"till": _till(scope),
	}


def _till(scope: _Scope) -> dict:
	session = _open_sessions(scope)[:1]
	if not session:
		return {"state": "closed", "session": None, "opened_on": None, "opened_at": None}
	opened = calc.parse_datetime(session[0].get("period_start_date"))
	return {
		"state": "open",
		"session": session[0].get("name"),
		"opened_on": str(opened.date()) if opened else None,
		"opened_at": opened.strftime("%H:%M") if opened else None,
	}


def _cash_in_till(scope: _Scope):
	"""Cash that should be in the open till, or None when no till is open."""
	session = _open_sessions(scope)[:1]
	if not session:
		return None
	session = session[0]
	cash = _session_cash(session)
	return calc.cash_in_till(
		cash["opening"], cash["payments"], cash["change"], cash["entries"], _mode_types(scope)
	)


def _revenue_tile(scope: _Scope) -> dict:
	period = scope.period
	totals = _period_days(scope)
	now = calc.sum_totals(totals, period["from"], period["to"])
	before = calc.sum_totals(totals, period["previous_from"], period["previous_to"])
	return {
		"value": now["revenue"],
		"gross_total": now["gross_total"],
		"previous": before["revenue"],
		"change_pct": calc.pct_change(now["revenue"], before["revenue"]),
		"spark": [point["revenue"] for point in _series(scope)],
		"days_of_data": _days_of_data(scope),
	}


def _net_profit_tile(scope: _Scope) -> dict:
	period = scope.period
	rows, item_rates, bin_rates = _profit_lines(scope)
	now_rows = calc.rows_between(rows, period["from"], period["to"])
	before_rows = calc.rows_between(rows, period["previous_from"], period["previous_to"])
	now = summarize_profit(calc.for_profit(now_rows), _expenses(scope, period["from"], period["to"]))
	before = summarize_profit(
		calc.for_profit(before_rows), _expenses(scope, period["previous_from"], period["previous_to"])
	)
	uncosted = calc.uncosted_products(now_rows, item_rates, bin_rates)
	spark = [point["profit"] for point in _series(scope, with_profit=True)]
	return calc.net_profit_tile(now, before, spark, len(uncosted))


def _money_owed_tile(scope: _Scope) -> dict:
	owed = calc.money_owed_summary(_money_owed_rows(scope))
	# What was owed at the start of the period is not kept anywhere, so there is nothing to compare with
	return {**owed, "previous": None, "change_pct": None, "spark": []}


def _low_stock_tile(scope: _Scope) -> dict:
	running_low, out_of_stock = _stock_status(scope)
	return {
		"count": len(running_low),
		"out_of_stock": len(out_of_stock),
		# Stock levels of the past are not kept, so there is nothing to compare with
		"previous": None,
		"change_pct": None,
		"spark": [],
	}


def _sales_chart(scope: _Scope) -> list[dict]:
	return _series(scope, with_profit=scope.can_profit)


def _payments_chart(scope: _Scope) -> list[dict]:
	period = scope.period
	rows = _payment_rows(scope, period["from"], period["to"])
	change = _change_total(scope, period["from"], period["to"])
	credit = _credit_outstanding(scope, period["from"], period["to"])
	return calc.group_payments(rows, change, credit, _mode_types(scope))


def _top_products_chart(scope: _Scope) -> list[dict]:
	period = scope.period
	rows = calc.rows_between(_profit_lines(scope)[0], period["from"], period["to"])
	return calc.top_products(rows)


def _busiest_hours_chart(scope: _Scope) -> list[dict]:
	period = scope.period
	counts = _hour_counts(scope, period["from"], period["to"])
	try:
		days = _days_of_data(scope)
	except Exception:
		days = period["days"]
	return calc.busiest_hours(counts, days or period["days"])


def _attention(scope: _Scope) -> list[dict]:
	items = calc.open_session_items(_open_sessions(scope), scope.today)
	running_low, out_of_stock = _stock_status(scope)
	items += out_of_stock + running_low
	if scope.can_owed:
		items += calc.overdue_customer_items(_overdue_rows(scope), scope.today)
		items += calc.supplier_bill_items(_supplier_bill_rows(scope), scope.today)
	return calc.rank_attention(items, scope.can_owed)


def _stock_status(scope: _Scope):
	"""(products running low, products out of stock) as attention items."""

	def compute():
		low = calc.running_low_items(_low_stock_rows(scope))
		sold = _sold_recently(scope)
		quantities = _stock_quantities(scope, [row["item_code"] for row in sold])
		return low, calc.out_of_stock_items(sold, quantities)

	return scope.memo("stock", compute)


def _profit_lines(scope: _Scope):
	"""(profit rows for the previous period and this one, product rates, store rates), costs filled in."""

	def compute():
		period = scope.period
		rows = _line_rows(scope, period["previous_from"], period["to"])
		item_rates, bin_rates = _cost_rates(rows)
		return calc.with_fallback_rates(rows, item_rates, bin_rates), item_rates, bin_rates

	return scope.memo("profit_lines", compute)


# --- products without a cost price ------------------------------------------------------------------


@frappe.whitelist()
def get_products_without_cost(from_date=None, to_date=None, company=None, limit=50):
	"""
	The products sold in the period that have no cost price at all, so their profit cannot be counted
	(owner and manager only). Most sold first, each with the units sold.
	"""
	role_tiers.require_access("cost_of_goods")
	company = resolve_company(company)
	now = _now_utc()
	today = calc.nairobi_today(now)
	try:
		period = calc.resolve_period(
			"custom", from_date or str(today - timedelta(days=29)), to_date or str(today), today
		)
	except calc.PeriodError as error:
		_refuse(error.code)
	try:
		limit = max(1, min(int(limit), 200))
	except (TypeError, ValueError):
		limit = 50

	scope = _Scope(
		tier=role_tiers.get_tier(),
		user=frappe.session.user,
		company=company,
		store=None,
		owner=None,
		period=period,
		today=today,
		now=now.astimezone(calc.NAIROBI),
		profit=True,
		owed=True,
	)
	rows = _line_rows(scope, period["from"], period["to"])
	item_rates, bin_rates = _cost_rates(rows)
	found = calc.uncosted_products(rows, item_rates, bin_rates)
	return {
		"success": True,
		"data": {
			"count": len(found),
			"products": found[:limit],
			"company": company,
			"from": str(period["from"]),
			"to": str(period["to"]),
		},
	}


# --- reading the database ---------------------------------------------------------------------------

# The two kinds of sale document; a POS Invoice merged into a Sales Invoice at shift close is already
# inside that Sales Invoice, so only the unmerged ones are added.
SALE_DOCUMENTS = (("Sales Invoice", False), ("POS Invoice", True))


def _document_filters(scope: _Scope, doctype: str, unmerged_only: bool, alias: str = "d"):
	"""(SQL conditions, values) shared by the queries over sales documents."""
	conditions = [
		f"{alias}.docstatus = 1",
		f"{alias}.company = %(company)s",
	]
	values: dict = {"company": scope.company}
	if unmerged_only:
		conditions.append(f"IFNULL({alias}.consolidated_invoice, '') = ''")
	if scope.owner:
		conditions.append(f"{alias}.owner = %(owner)s")
		values["owner"] = scope.owner
	if scope.store:
		conditions.append(
			f"EXISTS (SELECT 1 FROM `tab{doctype} Item` wi WHERE wi.parent = {alias}.name "
			"AND wi.warehouse = %(store)s)"
		)
		values["store"] = scope.store
	return conditions, values


def _sale_day_rows(scope: _Scope, start, end) -> list[dict]:
	"""Net and gross sales, and the number of sales, per day and sale-or-return (both kinds of document)."""
	rows: list[dict] = []
	conditions, values = _document_filters(scope, "Sales Invoice", False)
	conditions.append("d.posting_date BETWEEN %(from_date)s AND %(to_date)s")
	values.update(from_date=start, to_date=end)
	rows += frappe.db.sql(
		f"""
		SELECT d.posting_date, d.is_return, SUM(d.grand_total) AS gross, SUM(d.base_net_total) AS net,
			COUNT(*) AS n
		FROM `tabSales Invoice` d WHERE {" AND ".join(conditions)}
		GROUP BY d.posting_date, d.is_return
		""",
		values,
		as_dict=True,
	)
	rows += pos_invoice_days(scope.company, start, end, scope.owner, scope.store)
	return rows


def _sale_union(scope: _Scope, select: str, tables: str, extra: str = "", end_with: str = ""):
	"""SQL over both kinds of sale document, joined with UNION ALL, and its values."""
	parts, values = [], {}
	for doctype, unmerged in SALE_DOCUMENTS:
		conditions, values = _document_filters(scope, doctype, unmerged)
		conditions.append("d.posting_date BETWEEN %(from_date)s AND %(to_date)s")
		if extra:
			conditions.append(extra)
		parts.append(
			select.format(doctype=doctype)
			+ " "
			+ tables.format(doctype=doctype)
			+ " WHERE "
			+ " AND ".join(conditions)
			+ " "
			+ end_with
		)
	return " UNION ALL ".join(parts), values


def _hour_counts(scope: _Scope, start, end) -> dict:
	"""How many sales were made in each hour of the day over the period."""
	union, values = _sale_union(
		scope,
		"SELECT HOUR(d.posting_time) AS hr, COUNT(*) AS n",
		"FROM `tab{doctype}` d",
		extra="d.is_return = 0",
		end_with="GROUP BY HOUR(d.posting_time)",
	)
	values.update(from_date=start, to_date=end)
	counts: dict = {}
	for row in frappe.db.sql(f"SELECT hr, SUM(n) AS n FROM ({union}) s GROUP BY hr", values, as_dict=True):
		if row.get("hr") is not None:
			counts[int(row["hr"])] = counts.get(int(row["hr"]), 0) + int(row["n"] or 0)
	return counts


def _payment_rows(scope: _Scope, start, end) -> list[dict]:
	"""
	How sales were paid, per mode of payment: the amount (returns take it off) and the number of payments.
	The payments recorded on sales, plus money received on Payment Entries (customers settling invoices).
	"""
	union, values = _sale_union(
		scope,
		"SELECT p.mode_of_payment AS mode_of_payment, SUM(p.base_amount) AS amount, "
		"SUM(CASE WHEN d.is_return = 0 THEN 1 ELSE 0 END) AS n",
		"FROM `tab{doctype}` d JOIN `tabSales Invoice Payment` p "
		"ON p.parent = d.name AND p.parenttype = '{doctype}'",
		extra="p.base_amount <> 0",
		end_with="GROUP BY p.mode_of_payment",
	)
	values.update(from_date=start, to_date=end)
	rows = frappe.db.sql(
		f"SELECT mode_of_payment, SUM(amount) AS amount, SUM(n) AS n FROM ({union}) s GROUP BY mode_of_payment",
		values,
		as_dict=True,
	)
	if scope.store:
		return list(rows)  # a Payment Entry belongs to no store
	conditions = [
		"docstatus = 1",
		"company = %(company)s",
		"payment_type = 'Receive'",
		"party_type = 'Customer'",
		"posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]
	entry_values = {"company": scope.company, "from_date": start, "to_date": end}
	if scope.owner:
		conditions.append("owner = %(owner)s")
		entry_values["owner"] = scope.owner
	entries = frappe.db.sql(
		"SELECT mode_of_payment, SUM(base_paid_amount) AS amount, COUNT(*) AS n FROM `tabPayment Entry` "
		f"WHERE {' AND '.join(conditions)} GROUP BY mode_of_payment",
		entry_values,
		as_dict=True,
	)
	return list(rows) + list(entries)


def _change_total(scope: _Scope, start, end) -> float:
	"""The change handed back to customers on sales in the period."""
	union, values = _sale_union(
		scope,
		"SELECT SUM(d.base_change_amount) AS change_given",
		"FROM `tab{doctype}` d",
		extra="d.is_return = 0",
	)
	values.update(from_date=start, to_date=end)
	rows = frappe.db.sql(f"SELECT SUM(change_given) AS total FROM ({union}) s", values, as_dict=True)
	return calc.num(rows[0].get("total")) if rows else 0.0


def _credit_outstanding(scope: _Scope, start, end) -> float:
	"""The unpaid part of the sales made in the period."""
	union, values = _sale_union(
		scope,
		"SELECT SUM(d.outstanding_amount) AS owed",
		"FROM `tab{doctype}` d",
		extra="d.is_return = 0 AND d.outstanding_amount > 0",
	)
	values.update(from_date=start, to_date=end)
	rows = frappe.db.sql(f"SELECT SUM(owed) AS total FROM ({union}) s", values, as_dict=True)
	return calc.num(rows[0].get("total")) if rows else 0.0


def _mode_types(scope: _Scope) -> dict:
	"""Each mode of payment's type in ERPNext (Cash, Bank, Phone, General), to place a mode with an odd name."""

	def compute():
		return {
			row["name"]: row.get("type") for row in frappe.get_all("Mode of Payment", fields=["name", "type"])
		}

	return scope.memo("mode_types", compute)


def _line_rows(scope: _Scope, start, end) -> list[dict]:
	"""
	The invoice lines of the period, one row per day, product and store, in the shape profit_calc reads:
	net_amount, qty, is_stock_item, recorded_cost (quantity x the cost ERPNext recorded when the stock left) and
	uncosted_qty (quantity on lines with no recorded cost).

	Sales Invoice lines carry the cost recorded when the stock left. POS Invoice lines (submitted, not merged) have
	none yet, so all of their quantity is uncosted and is costed from the current valuation. A dish sold from a
	recipe has its cost in the ingredients on the invoice (Packed Items), read the same way for both.
	"""
	rows = list(_invoice_lines(scope, "Sales Invoice", start, end))
	rows += _invoice_lines(scope, "POS Invoice", start, end)
	rows += _packed_lines(scope, "Sales Invoice", start, end)
	rows += _packed_lines(scope, "POS Invoice", start, end)
	return rows


def _invoice_lines(scope: _Scope, doctype: str, start, end) -> list[dict]:
	Invoice, Line, Item = DocType(doctype), DocType(f"{doctype} Item"), DocType("Item")
	query = (
		frappe.qb.from_(Line)
		.join(Invoice)
		.on((Invoice.name == Line.parent) & (Line.parenttype == doctype))
		.left_join(Item)
		.on(Item.name == Line.item_code)
		.where(Invoice.docstatus == 1)
		.where(Invoice.company == scope.company)
		.where(Invoice.posting_date.between(start, end))
	)
	if doctype == "POS Invoice":
		query = query.where(fn.Coalesce(Invoice.consolidated_invoice, "") == "")
	if scope.owner:
		query = query.where(Invoice.owner == scope.owner)
	if scope.store:
		query = query.where(Line.warehouse == scope.store)
	if doctype == "POS Invoice":
		recorded = fn.Sum(Line.stock_qty * 0)
		uncosted = fn.Sum(Line.stock_qty)
	else:
		recorded = fn.Sum(Line.stock_qty * Line.incoming_rate)
		uncosted = fn.Sum(Case().when(fn.Coalesce(Line.incoming_rate, 0) == 0, Line.stock_qty).else_(0))
	rows = (
		query.select(
			Invoice.posting_date,
			Line.item_code,
			fn.Max(Line.item_name).as_("item_name"),
			Line.warehouse,
			fn.Max(Item.is_stock_item).as_("is_stock_item"),
			fn.Sum(Line.base_net_amount).as_("net_amount"),
			fn.Sum(Line.stock_qty).as_("qty"),
			recorded.as_("recorded_cost"),
			uncosted.as_("uncosted_qty"),
		)
		.groupby(Invoice.posting_date, Line.item_code, Line.warehouse)
		.run(as_dict=True)
	)
	return rows


def _packed_lines(scope: _Scope, doctype: str, start, end) -> list[dict]:
	Invoice, Packed = DocType(doctype), DocType("Packed Item")
	query = (
		frappe.qb.from_(Packed)
		.join(Invoice)
		.on((Invoice.name == Packed.parent) & (Packed.parenttype == doctype))
		.where(Invoice.docstatus == 1)
		.where(Invoice.company == scope.company)
		.where(Invoice.posting_date.between(start, end))
	)
	if doctype == "POS Invoice":
		query = query.where(fn.Coalesce(Invoice.consolidated_invoice, "") == "")
	if scope.owner:
		query = query.where(Invoice.owner == scope.owner)
	if scope.store:
		query = query.where(Packed.warehouse == scope.store)
	if doctype == "POS Invoice":
		recorded = fn.Sum(Packed.qty * 0)
		uncosted = fn.Sum(Packed.qty)
	else:
		recorded = fn.Sum(Packed.qty * Packed.incoming_rate)
		uncosted = fn.Sum(Case().when(fn.Coalesce(Packed.incoming_rate, 0) == 0, Packed.qty).else_(0))
	rows = (
		query.select(
			Invoice.posting_date,
			Packed.item_code,
			fn.Max(Packed.item_name).as_("item_name"),
			Packed.warehouse,
			fn.Sum(Packed.qty).as_("qty"),
			recorded.as_("recorded_cost"),
			uncosted.as_("uncosted_qty"),
		)
		.groupby(Invoice.posting_date, Packed.item_code, Packed.warehouse)
		.run(as_dict=True)
	)
	for row in rows:
		row["is_stock_item"] = 1
		row["net_amount"] = 0.0
	return rows


def _cost_rates(rows) -> tuple[dict, dict]:
	"""(product -> valuation rate, (product, store) -> valuation rate) for the products whose lines have no cost."""
	codes = sorted(
		{r["item_code"] for r in rows if r.get("is_stock_item") and calc.num(r.get("uncosted_qty"))}
	)
	if not codes:
		return {}, {}
	bin_rates = {
		(b.item_code, b.warehouse): calc.num(b.valuation_rate)
		for b in frappe.get_all(
			"Bin", filters={"item_code": ["in", codes]}, fields=["item_code", "warehouse", "valuation_rate"]
		)
	}
	item_rates = {
		i.name: calc.num(i.valuation_rate)
		for i in frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "valuation_rate"])
	}
	return item_rates, bin_rates


def _expenses(scope: _Scope, start, end) -> float:
	"""Running costs for a stretch of days (company-wide, as the rest of the dashboard)."""
	filters = {"company": scope.company, "posting_date": ["between", [str(start), str(end)]]}
	return _get_operating_expenses(filters, scope.company)


def _money_owed_rows(scope: _Scope) -> list[dict]:
	"""What customers owe now, per customer, on unpaid sales of both kinds."""
	union, values = _owed_union(
		scope, "SELECT d.customer AS customer, d.outstanding_amount AS outstanding_amount"
	)
	return frappe.db.sql(
		f"SELECT customer, SUM(outstanding_amount) AS outstanding_amount FROM ({union}) s GROUP BY customer",
		values,
		as_dict=True,
	)


def _owed_union(scope: _Scope, select: str, tables: str = "FROM `tab{doctype}` d", end_with: str = ""):
	parts, values = [], {}
	for doctype, unmerged in SALE_DOCUMENTS:
		conditions, values = _document_filters(scope, doctype, unmerged)
		conditions += ["d.is_return = 0", "d.outstanding_amount > 0"]
		parts.append(f"{select} {tables.format(doctype=doctype)} WHERE {' AND '.join(conditions)} {end_with}")
	return " UNION ALL ".join(parts), values


def _overdue_rows(scope: _Scope) -> list[dict]:
	"""Unpaid sales with what is needed to say whether they are late: dates, customer terms and phone."""
	union, values = _owed_union(
		scope,
		"SELECT d.customer AS customer, d.customer_name AS customer_name, c.mobile_no AS mobile_no, "
		"d.posting_date AS posting_date, d.due_date AS due_date, d.outstanding_amount AS outstanding_amount, "
		"c.payment_terms AS payment_terms",
		"FROM `tab{doctype}` d LEFT JOIN `tabCustomer` c ON c.name = d.customer",
	)
	rows = frappe.db.sql(f"SELECT * FROM ({union}) s ORDER BY posting_date LIMIT 500", values, as_dict=True)
	for row in rows:
		row["has_terms"] = bool(row.get("payment_terms"))
	return rows


def _supplier_bill_rows(scope: _Scope) -> list[dict]:
	horizon = scope.today + timedelta(days=calc.BILL_WARNING_DAYS)
	return frappe.db.sql(
		"""
		SELECT name, supplier, supplier_name, outstanding_amount, due_date FROM `tabPurchase Invoice`
		WHERE docstatus = 1 AND is_return = 0 AND outstanding_amount > 0 AND company = %(company)s
			AND due_date <= %(horizon)s
		ORDER BY due_date LIMIT 50
		""",
		{"company": scope.company, "horizon": horizon},
		as_dict=True,
	)


def _open_sessions(scope: _Scope) -> list[dict]:
	"""Till sessions that are open (newest first), for the caller if a cashier, limited to the store if one is chosen."""

	def compute():
		filters = {"company": scope.company, "status": "Open", "docstatus": 1}
		if scope.owner:
			filters["user"] = scope.owner
		if scope.store:
			filters["pos_profile"] = [
				"in",
				frappe.get_all(
					"POS Profile", filters={"company": scope.company, "warehouse": scope.store}, pluck="name"
				)
				or [""],
			]
		return frappe.get_all(
			"POS Opening Entry",
			filters=filters,
			fields=["name", "user", "pos_profile", "period_start_date", "posting_date"],
			order_by="period_start_date desc",
		)

	return scope.memo("sessions", compute)


def _session_cash(session) -> dict:
	"""Opening cash, till payments, change and Payment Entries of one open session."""
	start = session.get("period_start_date")
	opening = [
		{"mode_of_payment": r.mode_of_payment, "opening_amount": r.opening_amount}
		for r in frappe.get_doc("POS Opening Entry", session["name"]).balance_details
	]
	values = {"user": session.get("user"), "profile": session.get("pos_profile"), "start": start}
	where = (
		"d.docstatus = 1 AND IFNULL(d.consolidated_invoice, '') = '' AND d.owner = %(user)s "
		"AND d.pos_profile = %(profile)s AND TIMESTAMP(d.posting_date, d.posting_time) >= %(start)s"
	)
	payments = frappe.db.sql(
		"SELECT d.is_return, p.mode_of_payment, SUM(p.base_amount) AS amount FROM `tabPOS Invoice` d "
		"JOIN `tabSales Invoice Payment` p ON p.parent = d.name AND p.parenttype = 'POS Invoice' "
		f"WHERE {where} GROUP BY d.is_return, p.mode_of_payment",
		values,
		as_dict=True,
	)
	change = frappe.db.sql(
		f"SELECT d.is_return, SUM(d.base_change_amount) AS change_given FROM `tabPOS Invoice` d "
		f"WHERE {where} GROUP BY d.is_return",
		values,
		as_dict=True,
	)
	entries = frappe.db.sql(
		"SELECT payment_type, mode_of_payment, SUM(base_paid_amount) AS amount FROM `tabPayment Entry` "
		"WHERE docstatus = 1 AND owner = %(user)s AND creation >= %(start)s AND payment_type IN ('Receive', 'Pay') "
		"GROUP BY payment_type, mode_of_payment",
		values,
		as_dict=True,
	)
	return {
		"opening": opening,
		"payments": payments,
		"change": [{"is_return": r.get("is_return"), "change": r.get("change_given")} for r in change],
		"entries": entries,
	}


def _low_stock_rows(scope: _Scope) -> list[dict]:
	"""Products at or below their warning level (the same rule as the low-stock email alert)."""
	from murzak_pos.api.alerts import low_stock_rows

	rows = low_stock_rows(scope.company)
	if scope.store:
		rows = [r for r in rows if r.get("warehouse") == scope.store]
	return rows


def _sold_recently(scope: _Scope) -> list[dict]:
	"""The stock products that sold in the last 30 days (by anyone: stock is the shop's, not a cashier's)."""
	since = scope.today - timedelta(days=calc.SOLD_RECENTLY_DAYS)
	parts, values = [], {}
	for doctype, unmerged in SALE_DOCUMENTS:
		conditions = [
			"d.docstatus = 1",
			"d.is_return = 0",
			"d.company = %(company)s",
			"d.posting_date BETWEEN %(since)s AND %(today)s",
			"it.is_stock_item = 1",
			"it.disabled = 0",
		]
		if unmerged:
			conditions.append("IFNULL(d.consolidated_invoice, '') = ''")
		if scope.store:
			conditions.append("i.warehouse = %(store)s")
			values["store"] = scope.store
		parts.append(
			f"SELECT i.item_code AS item_code, MAX(it.item_name) AS item_name FROM `tab{doctype}` d "
			f"JOIN `tab{doctype} Item` i ON i.parent = d.name JOIN `tabItem` it ON it.name = i.item_code "
			f"WHERE {' AND '.join(conditions)} GROUP BY i.item_code"
		)
	values.update(company=scope.company, since=since, today=scope.today)
	return frappe.db.sql(" UNION ".join(parts), values, as_dict=True)


def _stock_quantities(scope: _Scope, item_codes) -> dict:
	"""Quantity in stock per product across the business's stores (or the chosen store)."""
	if not item_codes:
		return {}
	conditions = ["b.item_code IN %(items)s", "w.company = %(company)s"]
	values = {"items": tuple(item_codes), "company": scope.company}
	if scope.store:
		conditions.append("b.warehouse = %(store)s")
		values["store"] = scope.store
	rows = frappe.db.sql(
		"SELECT b.item_code, SUM(b.actual_qty) AS qty FROM `tabBin` b JOIN `tabWarehouse` w ON w.name = b.warehouse "
		f"WHERE {' AND '.join(conditions)} GROUP BY b.item_code",
		values,
		as_dict=True,
	)
	return {r["item_code"]: calc.num(r["qty"]) for r in rows}
