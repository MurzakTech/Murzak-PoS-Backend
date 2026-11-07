"""
Download how this shop is set up (Settings > Business > Setup health): the till switches, the ways of paying that are on, the
cashier discount limit and the defaults for new customers, as one secret-free document. Read only; owners only.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

from murzak_pos.api import role_tiers
from murzak_pos.api import settings_export_calc as calc
from murzak_pos.api.payment_gateway_common import resolve_company


def _safely(read, fallback):
	try:
		return read()
	except Exception:
		frappe.log_error("Settings export: a part could not be read", frappe.get_traceback())
		return fallback


def _till(company) -> dict:
	rows = frappe.get_all(
		"POS Profile",
		filters={"company": company, "disabled": 0},
		fields=["update_stock", "allow_discount_change", "allow_rate_change", "allow_partial_payment"],
		limit_page_length=1,
	)
	if not rows:
		return {}
	return {key: bool(value) for key, value in rows[0].items()}


def _payment_methods(company) -> list[str]:
	profiles = frappe.get_all("POS Profile", filters={"company": company, "disabled": 0}, pluck="name")
	if not profiles:
		return []
	return frappe.get_all(
		"POS Payment Method", filters={"parent": ["in", profiles]}, pluck="mode_of_payment", distinct=True
	)


def _customer_defaults() -> dict:
	from murzak_pos.api.customer_api import _default_group_or_territory

	return {
		"group": _default_group_or_territory("Customer Group", "customer_group", "Individual"),
		"territory": _default_group_or_territory("Territory", "territory", "Kenya"),
	}


@frappe.whitelist()
def export_settings(company: str | None = None) -> dict:
	"""The shop's settings as one document with no secrets in it."""
	role_tiers.require_access("team")  # owners only
	company = resolve_company(company)
	from murzak_pos.api.cashier_limits import discount_limit

	facts = {
		"till": _safely(lambda: _till(company), {}),
		"payment_methods": _safely(lambda: _payment_methods(company), []),
		"cashier_discount_limit": _safely(lambda: discount_limit(company), None),
		"customer_defaults": _safely(_customer_defaults, {}),
		"currency": _safely(lambda: frappe.get_cached_value("Company", company, "default_currency"), None),
		"time_zone": _safely(lambda: frappe.db.get_single_value("System Settings", "time_zone"), None),
	}
	return {"success": True, "data": calc.build_export(company, facts, nowdate())}
