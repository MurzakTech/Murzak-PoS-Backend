"""
The Reports: `run_report` gives the rows, chart series and summary of one report, and `list_reports` gives the Reports
landing page its questions (ticket P5-2). Both are new; no existing call, report or record is changed.

The contract is docs/redesign/P5-2-contract.md. How it is built:

- The arithmetic is in reports_calc.py (plain functions, no database). This file reads the database in small functions,
  one job each, and joins the two.
- Reports must agree with the Home overview for the same period, so a report does not repeat Home's work. It calls
  Home's own readers (`dashboard_overview`: sales by day, payments, invoice lines with their costs, money owed) and
  Home's pure functions (`overview_calc`: periods, totals, payments by method, top products, profit rows). Nothing
  in those two files is changed.
- The stock reports are the current reports (`reports.inventory_summary_report`,
  `reports.inventory_value_by_category_report`, `reports.stock_aging_report`), called as they are.
- Roles are checked here, on the server (`role_tiers`), before anything is read or looked up in the cache: a cashier
  may open only the sales report, and then sees only their own sales. Profit, cost, running costs, money owed and the
  value of stock are for owners and managers.
- The answer is kept for 60 seconds, separately for each kind of person, report, company, store, period and (for a
  cashier) person, in the same way as the Home overview.
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder import functions as fn

from murzak_pos.api import dashboard_overview as overview
from murzak_pos.api import overview_calc as oc
from murzak_pos.api import reports_calc as calc
from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.dashboard_api import NON_OPERATING_EXPENSE_TYPES
from murzak_pos.api.payment_gateway_common import resolve_company

CACHE_SECONDS = 60
CACHE_PREFIX = "murzak_pos:reports"
DEFAULT_PERIOD = "30d"


# --- the clock and the cache (small on purpose, so tests can replace them) --------------------------


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


def _cache_get(key: str):
	return frappe.cache().get_value(key)


def _cache_set(key: str, value) -> None:
	frappe.cache().set_value(key, value, expires_in_sec=CACHE_SECONDS)


def cache_key(name, tier, company, store, owner, period: dict, group_by=None) -> str:
	"""
	One key per report, kind of person, company, store, period (with its dates) and, for a cashier, person. A report
	about the shop as it is now (money owed, stock) has no period in its key.
	"""
	if calc.REPORTS[name]["needs_period"]:
		span = [period["key"], period["from"], period["to"]]
	else:
		span = ["now", "-", "-"]
	parts = [name, tier, company, store, owner, *span, group_by]
	return CACHE_PREFIX + ":" + ":".join(str(p or "-") for p in parts)


# --- the request ------------------------------------------------------------------------------------


def _scope(tier, company, store, period: dict, today, now) -> overview._Scope:
	"""What one request is about. Home's own scope object, so Home's readers can be used as they are."""
	user = frappe.session.user
	return overview._Scope(
		tier=tier,
		user=user,
		company=company,
		store=store or None,
		# A cashier sees only their own sales, whatever was asked for
		owner=user if tier == role_tiers.CASHIER else None,
		period=period,
		today=today,
		now=now.astimezone(oc.NAIROBI),
		profit=role_tiers.can("profit"),
		owed=role_tiers.can("money_owed"),
	)


def _require(name: str) -> None:
	"""Refuse a report the signed-in person may not open (the one check, before anything is read)."""
	capability = calc.REPORTS[name]["capability"]
	if capability:
		role_tiers.require_access(capability)


@frappe.whitelist()
def run_report(
	name, period=DEFAULT_PERIOD, from_date=None, to_date=None, store=None, company=None, group_by=None
):
	"""
	One report: its summary, chart series and table rows, for a period (the last 30 days by default) with the period
	before it for comparison. `name` is sales, profit, owed, running-costs, stock-summary, stock-value or aging-stock.
	The reports about the shop as it is now (owed, stock-summary, stock-value, aging-stock) ignore the period.
	`group_by` (day or month) applies to the profit report only.
	"""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	ensure_record_access("Warehouse", store)
	name = str(name or "").strip().lower()
	if name not in calc.REPORTS:
		frappe.throw(_("That report does not exist."), frappe.DoesNotExistError)
	_require(name)

	now = _now_utc()
	today = oc.nairobi_today(now)
	try:
		resolved = oc.resolve_period(period, from_date, to_date, today)
	except oc.PeriodError as error:
		overview._refuse(error.code)
	company = resolve_company(company)
	try:
		data = _answer(name, tier, company, store, resolved, today, now, group_by)
	except Exception:
		_log_error(name)
		return {"success": False, "message": _("Could not load this report. Please try again.")}
	return {"success": True, "data": data}


@frappe.whitelist()
def list_reports(preview=0):
	"""
	The questions on the Reports landing page that the signed-in person may open, each with the reports under it.
	With `preview`, each question that can be answered cheaply also carries `preview`: the few numbers for a one-line
	answer over the last 30 days (null when they could not be worked out; the others are still returned).
	"""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	questions = calc.questions_for(tier)
	data = {"role": tier, "questions": questions}
	if str(preview).lower() in ("1", "true", "yes"):
		now = _now_utc()
		today = oc.nairobi_today(now)
		resolved = oc.resolve_period(DEFAULT_PERIOD, None, None, today)
		company = resolve_company(None)
		for question in questions:
			question["preview"] = None
			if question["opens"] not in calc.PREVIEWED:
				continue
			try:
				answer = _answer(question["opens"], tier, company, None, resolved, today, now, None)
				question["preview"] = calc.preview_of(question["opens"], answer["summary"])
			except Exception:
				_log_error(question["opens"])
	return {"success": True, "data": data}


def _log_error(name: str) -> None:
	frappe.log_error("Reports error", f"{name}\n{frappe.get_traceback()}")


def _answer(name, tier, company, store, period: dict, today, now, group_by) -> dict:
	"""The report from the cache, or worked out now and kept for 60 seconds (only when every part loaded)."""
	scope = _scope(tier, company, store, period, today, now)
	group = group_by if name == calc.PROFIT else None
	key = cache_key(name, tier, company, store, scope.owner, period, group)
	cached = _cache_get(key)
	if cached:
		return {**cached, "cached": True}
	data = _BUILDERS[name](scope, group)
	errors = data.pop("errors", {})
	needs_period = calc.REPORTS[name]["needs_period"]
	data = {
		"name": name,
		"role": tier,
		"period": oc.period_payload(period, data.get("series", {}).get("days_of_data"))
		if needs_period
		else None,
		"as_of": str(today),
		"updated_at": scope.now.strftime("%H:%M"),
		"cached": False,
		**data,
		"errors": errors,
	}
	if not errors:
		_cache_set(key, data)
	return data


def _part(errors: dict, label: str, compute):
	"""One part of a report on its own: if it fails it is null and named in `errors`, and the rest is still returned."""
	try:
		return compute()
	except Exception:
		frappe.log_error("Reports part error", f"{label}\n{frappe.get_traceback()}")
		errors[label] = _("Could not load this.")
		return None


# --- the reports ------------------------------------------------------------------------------------


def _sales(scope, group_by=None) -> dict:
	"""How much did I sell: sales by day, how customers paid, top products (Home's figures, read the same way)."""
	period = scope.period
	raw = overview._sale_day_rows(scope, period["previous_from"], period["to"])
	totals = oc.daily_totals(raw)
	rows = calc.sales_rows(totals, period, scope.today)
	errors: dict = {}
	payments = _part(
		errors,
		"payments",
		lambda: oc.group_payments(
			overview._payment_rows(scope, period["from"], period["to"]),
			overview._change_total(scope, period["from"], period["to"]),
			overview._credit_outstanding(scope, period["from"], period["to"]),
			overview._mode_types(scope),
		),
	)
	top = _part(
		errors,
		"top_products",
		lambda: oc.top_products(
			oc.rows_between(
				overview._line_rows(scope, period["from"], period["to"]), period["from"], period["to"]
			)
		),
	)
	return {
		"summary": calc.sales_summary(totals, rows, period),
		"rows": rows,
		"rows_total": len(rows),
		"series": {
			"sales": calc.sales_points(rows),
			"days_of_data": oc.days_of_data(oc.first_sale_date(raw), period),
			"payments": payments,
			"top_products": top,
		},
		"errors": errors,
	}


def _profit(scope, group_by=None) -> dict:
	"""What is my profit: Home's Net profit worked out on the same rows, with its parts and a row for each day or month."""
	period = scope.period
	rows, item_rates, bin_rates = overview._profit_lines(scope)
	now_rows = oc.rows_between(rows, period["from"], period["to"])
	before_rows = oc.rows_between(rows, period["previous_from"], period["previous_to"])
	revenue = oc.daily_totals(overview._sale_day_rows(scope, period["previous_from"], period["to"]))
	expenses = _expense_rows(scope, period["previous_from"], period["to"])
	uncosted = oc.uncosted_products(now_rows, item_rates, bin_rates)
	return {
		**calc.profit_report(
			now_rows, before_rows, expenses, revenue, len(uncosted), period, scope.today, group_by
		),
		"errors": {},
	}


def _owed(scope, group_by=None) -> dict:
	"""Who owes me money: unpaid sales of both kinds, by customer and by age."""
	return {**calc.owed_report(_owed_rows(scope), scope.today), "errors": {}}


def _running_costs(scope, group_by=None) -> dict:
	"""Where did my money go: the expenses that come off Net profit, by category and by day."""
	period = scope.period
	return {
		**calc.running_costs_report(
			_expense_rows(scope, period["previous_from"], period["to"]), period, scope.today
		),
		"errors": {},
	}


def _stock_summary(scope, group_by=None) -> dict:
	"""What is in stock: the current Inventory Summary report, by store."""
	reply = _legacy().inventory_summary_report(company=scope.company, warehouse=scope.store)
	return {**calc.stock_summary_report(_legacy_data(reply)), "errors": {}}


def _stock_value(scope, group_by=None) -> dict:
	"""What the stock is worth: the current Inventory Valuation report, value by category."""
	reply = _legacy().inventory_value_by_category_report(company=scope.company, warehouse=scope.store)
	return {**calc.stock_value_report(_legacy_data(reply)), "errors": {}}


def _aging_stock(scope, group_by=None) -> dict:
	"""What is not selling: the current Stock Aging report, with the till sales that have no stock movement yet."""
	reply = _legacy().stock_aging_report(company=scope.company, warehouse=scope.store)
	buckets = _legacy_data(reply)
	codes = sorted({e["item_code"] for group in buckets.values() for e in group})
	names = _item_names(codes)
	rates = _valuation_rates(codes)
	sold = [row["item_code"] for row in overview._sold_recently(scope)]
	return {**calc.aging_stock_report(buckets, names, rates, sold), "errors": {}}


_BUILDERS = {
	calc.SALES: _sales,
	calc.PROFIT: _profit,
	calc.OWED: _owed,
	calc.RUNNING_COSTS: _running_costs,
	calc.STOCK_SUMMARY: _stock_summary,
	calc.STOCK_VALUE: _stock_value,
	calc.AGING_STOCK: _aging_stock,
}


# --- reading the database ---------------------------------------------------------------------------


def _legacy():
	"""The current reports module. It needs ERPNext, so it is loaded only when a stock report is run."""
	from murzak_pos.api import reports

	return reports


def _legacy_data(reply):
	"""The data of a current report's answer; a report that says it failed is a failure here too."""
	if not isinstance(reply, dict) or not reply.get("success"):
		raise RuntimeError(f"stock report failed: {(reply or {}).get('message')}")
	return reply.get("data")


def _expense_rows(scope, start, end) -> list[dict]:
	"""
	Running costs per day and expense account: everything booked to an expense account in the general ledger except
	the cost of stock sold and stock valuation entries, company-wide. It is the rule of
	`dashboard_api._get_operating_expenses`, which takes Net profit down on Home, with the account and the day kept.
	"""
	GLEntry, Account = DocType("GL Entry"), DocType("Account")
	excluded = [
		a
		for a in frappe.get_cached_value(
			"Company", scope.company, ["default_expense_account", "stock_adjustment_account"]
		)
		or []
		if a
	]
	query = (
		frappe.qb.from_(GLEntry)
		.join(Account)
		.on(Account.name == GLEntry.account)
		.where(GLEntry.is_cancelled == 0)
		.where(Account.root_type == "Expense")
		.where((Account.account_type.isnull()) | (Account.account_type.notin(NON_OPERATING_EXPENSE_TYPES)))
		.where(GLEntry.company == scope.company)
		.where(GLEntry.posting_date.between(str(start), str(end)))
	)
	if excluded:
		query = query.where(GLEntry.account.notin(excluded))
	return (
		query.select(
			GLEntry.posting_date,
			GLEntry.account,
			fn.Max(Account.account_name).as_("account_name"),
			fn.Sum(GLEntry.debit - GLEntry.credit).as_("amount"),
			fn.Count("*").as_("n"),
		)
		.groupby(GLEntry.posting_date, GLEntry.account)
		.run(as_dict=True)
	)


def _owed_rows(scope) -> list[dict]:
	"""What customers owe now: unpaid sales of both kinds, one row per customer and sale date, with the phone number."""
	union, values = overview._owed_union(
		scope,
		"SELECT d.customer AS customer, d.customer_name AS customer_name, c.mobile_no AS mobile_no, "
		"d.posting_date AS posting_date, d.outstanding_amount AS outstanding_amount",
		"FROM `tab{doctype}` d LEFT JOIN `tabCustomer` c ON c.name = d.customer",
	)
	return frappe.db.sql(
		"SELECT customer, MAX(customer_name) AS customer_name, MAX(mobile_no) AS mobile_no, posting_date, "
		f"SUM(outstanding_amount) AS outstanding_amount FROM ({union}) s GROUP BY customer, posting_date",
		values,
		as_dict=True,
	)


def _item_names(codes) -> dict:
	if not codes:
		return {}
	return {
		row["name"]: row.get("item_name")
		for row in frappe.get_all("Item", filters={"name": ["in", list(codes)]}, fields=["name", "item_name"])
	}


def _valuation_rates(codes) -> dict:
	"""(product, store) -> valuation rate."""
	if not codes:
		return {}
	return {
		(row["item_code"], row["warehouse"]): oc.num(row.get("valuation_rate"))
		for row in frappe.get_all(
			"Bin",
			filters={"item_code": ["in", list(codes)]},
			fields=["item_code", "warehouse", "valuation_rate"],
		)
	}
