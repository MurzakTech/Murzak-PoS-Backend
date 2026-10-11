"""
Number cards for the "Murzak POS" workspace in the Frappe desk.

Each card is a small function that returns {"value", "fieldtype", "route"...}. All of them cover only the
businesses the signed-in person may see (desk_scope.py), and the sales cards use the same definition of
sales as the dashboard and the reports (sales_figures.py), so the figures agree.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, get_first_day, nowdate

from murzak_pos.api import sales_figures as figures
from murzak_pos.api.desk_scope import visible_companies


def _card(value, fieldtype="Int", route=None, route_options=None) -> dict:
	result = {"value": value, "fieldtype": fieldtype}
	if route:
		result["route"] = route
	if route_options:
		result["route_options"] = route_options
	return result


@frappe.whitelist()
def sales_today(filters=None) -> dict:
	today = nowdate()
	return _card(figures.sales_total(visible_companies(), today, today), "Currency")


@frappe.whitelist()
def sales_this_month(filters=None) -> dict:
	today = nowdate()
	return _card(figures.sales_total(visible_companies(), get_first_day(today), today), "Currency")


@frappe.whitelist()
def open_shifts(filters=None) -> dict:
	return _card(
		figures.open_shift_count(visible_companies()),
		route=["List", "POS Opening Entry"],
		route_options={"status": "Open"},
	)


@frappe.whitelist()
def unpaid_purchase_invoices(filters=None) -> dict:
	_count, amount = figures.unpaid_purchase_invoices(visible_companies())
	return _card(
		amount, "Currency", route=["List", "Purchase Invoice"], route_options={"outstanding_amount": [">", 0]}
	)


@frappe.whitelist()
def items_below_reorder_level(filters=None) -> dict:
	from murzak_pos.api.alerts import low_stock_rows

	return _card(sum(len(low_stock_rows(company)) for company in visible_companies()))


@frappe.whitelist()
def batches_expiring_soon(filters=None) -> dict:
	from murzak_pos.api.expiry_api import get_expiry_alerts
	from murzak_pos.api.sales_api import _as_administrator

	companies = visible_companies()
	total = 0
	# The expiry function checks the caller against their business; the businesses were already limited above
	with _as_administrator():
		for company in companies:
			total += len(get_expiry_alerts(company=company, days=30, include_expired=0).get("data") or [])
	return _card(total)


@frappe.whitelist()
def unsent_etims_invoices(filters=None) -> dict:
	from murzak_pos.api.alerts import unsent_etims

	return _card(sum(unsent_etims(company)[0] for company in visible_companies()))
