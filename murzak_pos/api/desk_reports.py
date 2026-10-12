"""
Script Reports and chart data for the "Murzak POS" workspace in the Frappe desk.

Each builder returns (columns, data, message, chart) the way Frappe's report screen expects, which then
adds filters, Excel and PDF export. Every builder covers only the businesses the signed-in person may see
(desk_scope.py) and uses the same definition of sales as the dashboard cards (sales_figures.py).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, add_months, add_to_date, flt, getdate, now_datetime, nowdate

from murzak_pos.api import sales_figures as figures
from murzak_pos.api.desk_scope import chosen_companies


def _filters(filters) -> dict:
	return frappe._dict(filters or {})


def _period(filters, days=30):
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_days(to_date, -days))
	return from_date, to_date


def _col(label, fieldname, fieldtype="Data", width=140, options=None) -> dict:
	column = {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width}
	if options:
		column["options"] = options
	return column


def _chart(labels, datasets, kind="bar") -> dict:
	return {"data": {"labels": labels, "datasets": datasets}, "type": kind}


# --- charts (each is also a report) ---------------------------------------------------------------


def sales_by_day(filters=None):
	filters = _filters(filters)
	from_date, to_date = _period(filters)
	rows = figures.sales_by_day(chosen_companies(filters.get("company")), from_date, to_date)
	columns = [
		_col("Date", "date", "Date"),
		_col("Sales", "sales", "Currency"),
		_col("Invoices", "invoices", "Int"),
	]
	chart = _chart(
		[str(r.date) for r in rows], [{"name": _("Sales"), "values": [flt(r.sales) for r in rows]}], "line"
	)
	return columns, rows, None, chart


def sales_by_payment_method(filters=None):
	filters = _filters(filters)
	from_date, to_date = _period(filters)
	rows = figures.sales_by_payment_method(chosen_companies(filters.get("company")), from_date, to_date)
	columns = [_col("Payment method", "mode_of_payment", "Data", 200), _col("Amount", "amount", "Currency")]
	chart = _chart(
		[r.mode_of_payment for r in rows],
		[{"name": _("Amount"), "values": [flt(r.amount) for r in rows]}],
		"donut",
	)
	return columns, rows, None, chart


def top_items(filters=None):
	filters = _filters(filters)
	from_date, to_date = _period(filters)
	rows = figures.top_items(chosen_companies(filters.get("company")), from_date, to_date, 10)
	columns = [
		_col("Item", "item_code", "Link", 160, "Item"),
		_col("Item name", "item_name", "Data", 220),
		_col("Quantity", "qty", "Float"),
		_col("Revenue", "amount", "Currency"),
	]
	chart = _chart(
		[r.item_name or r.item_code for r in rows],
		[{"name": _("Revenue"), "values": [flt(r.amount) for r in rows]}],
	)
	return columns, rows, None, chart


def sales_vs_purchases(filters=None):
	filters = _filters(filters)
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date.replace(day=1), -11))
	companies = chosen_companies(filters.get("company"))
	sales = {r.month: flt(r.sales) for r in figures.sales_by_month(companies, from_date, to_date)}
	purchases = {r.month: flt(r.purchases) for r in figures.purchases_by_month(companies, from_date, to_date)}
	months = sorted(set(sales) | set(purchases))
	rows = [{"month": m, "sales": sales.get(m, 0.0), "purchases": purchases.get(m, 0.0)} for m in months]
	columns = [
		_col("Month", "month"),
		_col("Sales", "sales", "Currency"),
		_col("Purchases", "purchases", "Currency"),
	]
	chart = _chart(
		months,
		[
			{"name": _("Sales"), "values": [r["sales"] for r in rows]},
			{"name": _("Purchases"), "values": [r["purchases"] for r in rows]},
		],
	)
	return columns, rows, None, chart


def stock_value_by_warehouse(filters=None):
	filters = _filters(filters)
	rows = figures.stock_value_by_warehouse(chosen_companies(filters.get("company")))
	columns = [
		_col("Business", "company", "Link", 180, "Company"),
		_col("Warehouse", "warehouse", "Link", 200, "Warehouse"),
		_col("Quantity", "qty", "Float"),
		_col("Stock value", "stock_value", "Currency"),
	]
	chart = _chart(
		[r.warehouse for r in rows],
		[{"name": _("Stock value"), "values": [flt(r.stock_value) for r in rows]}],
	)
	return columns, rows, None, chart


# --- the working reports --------------------------------------------------------------------------


def sales_analytics(filters=None):
	filters = _filters(filters)
	from_date, to_date = _period(filters)
	rows = figures.sales_by_item_group(chosen_companies(filters.get("company")), from_date, to_date)
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Date", "posting_date", "Date"),
		_col("Item group", "item_group", "Link", 180, "Item Group"),
		_col("Quantity", "qty", "Float"),
		_col("Revenue", "amount", "Currency"),
	]
	return columns, rows, None, None


def inventory_valuation(filters=None):
	filters = _filters(filters)
	rows = figures.stock_by_item(chosen_companies(filters.get("company")))
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Warehouse", "warehouse", "Link", 180, "Warehouse"),
		_col("Item", "item_code", "Link", 150, "Item"),
		_col("Item name", "item_name", "Data", 200),
		_col("Quantity", "qty", "Float"),
		_col("Valuation rate", "valuation_rate", "Currency"),
		_col("Stock value", "stock_value", "Currency"),
	]
	return columns, rows, None, None


def stock_ageing(filters=None):
	from murzak_pos.api.reports import stock_aging_report
	from murzak_pos.api.sales_api import _as_administrator

	filters = _filters(filters)
	companies = chosen_companies(filters.get("company"))
	rows = []
	# The legacy report checks the caller against their business; the businesses were already limited above
	with _as_administrator():
		for company in companies:
			result = stock_aging_report(company=company, warehouse=filters.get("warehouse")).get("data") or {}
			for bucket, entries in result.items():
				for entry in entries:
					rows.append({"company": company, "age_bucket": bucket, **entry})
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Age (days)", "age_bucket", "Data", 100),
		_col("Item", "item_code", "Link", 150, "Item"),
		_col("Warehouse", "warehouse", "Link", 180, "Warehouse"),
		_col("Quantity", "actual_qty", "Float"),
		_col("Days in stock", "age_days", "Int"),
		_col("Movement rate", "movement_rate", "Float"),
	]
	return columns, rows, None, None


def expiry(filters=None):
	from murzak_pos.api.expiry_api import get_expiry_alerts
	from murzak_pos.api.sales_api import _as_administrator

	filters = _filters(filters)
	days = int(filters.get("days") or 30)
	companies = chosen_companies(filters.get("company"))
	rows = []
	with _as_administrator():
		for company in companies:
			for row in get_expiry_alerts(company=company, days=days, include_expired=1).get("data") or []:
				rows.append({"company": company, **row})
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Status", "status", "Data", 150),
		_col("Item", "item_code", "Link", 150, "Item"),
		_col("Item name", "item_name", "Data", 200),
		_col("Warehouse", "warehouse", "Link", 180, "Warehouse"),
		_col("Batch", "batch_no", "Data", 120),
		_col("Quantity", "qty", "Float"),
		_col("Expiry date", "expiry_date", "Date"),
		_col("Days left", "days_left", "Int"),
		_col("Stock value", "stock_value", "Currency"),
	]
	return columns, rows, None, None


def low_stock(filters=None):
	from murzak_pos.api.alerts import low_stock_rows

	filters = _filters(filters)
	rows = []
	for company in chosen_companies(filters.get("company")):
		rows.extend({"company": company, **row} for row in low_stock_rows(company))
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Item", "item_code", "Link", 150, "Item"),
		_col("Item name", "item_name", "Data", 200),
		_col("Warehouse", "warehouse", "Link", 180, "Warehouse"),
		_col("In stock", "actual_qty", "Float"),
		_col("Reorder level", "reorder_level", "Float"),
	]
	return columns, rows, None, None


def unsent_etims(filters=None):
	filters = _filters(filters)
	cutoff = add_to_date(now_datetime(), hours=-1)
	rows = figures.unsent_etims_rows(chosen_companies(filters.get("company")), cutoff)
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Invoice", "name", "Link", 170, "Sales Invoice"),
		_col("Date", "posting_date", "Date"),
		_col("Customer", "customer", "Link", 180, "Customer"),
		_col("Total", "grand_total", "Currency"),
		_col("Made at", "creation", "Datetime", 160),
	]
	return columns, rows, None, None


def payment_matching_exceptions(filters=None):
	"""M-Pesa payments that do not match the statement, for the businesses the person may see."""
	from frappe.utils import cint

	from murzak_pos.api.payment_matching_api import DEFAULT_DAYS, exception_rows

	filters = _filters(filters)
	days = cint(filters.get("days")) or DEFAULT_DAYS
	rows = []
	for company in chosen_companies(filters.get("company")):
		rows.extend(exception_rows(company, days))
	columns = [
		_col("Business", "company", "Link", 160, "Company"),
		_col("Date", "date", "Date"),
		_col("M-Pesa receipt", "reference", "Data", 140),
		_col("Amount", "amount", "Currency"),
		_col("Problem", "problem", "Data", 300),
		_col("Detail", "detail", "Data", 260),
	]
	return columns, rows, None, None
