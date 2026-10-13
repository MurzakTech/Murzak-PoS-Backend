"""
Profit for a period, for owners and managers only.

This is the first call protected with the redesign's role check (`role_tiers.require_access`): a cashier is
refused before anything is read. It returns the same figures the dashboard's profit tile uses today, worked out
by the same code (`dashboard_api._get_profit_stats`), so the two always agree. The Home page's own endpoint
(`dashboard.overview`, a later ticket) will include these figures and use the same check.

Note: like the dashboard today, this reads Sales Invoices. Whether sales made as POS Invoices must be added is
the question ticket P1-9 answers; this call changes with it.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, getdate, nowdate

from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.dashboard_api import (
	_build_base_filters,
	_get_operating_expenses,
	_get_profit_stats,
	_get_warehouse_filter,
)
from murzak_pos.api.role_tiers import require_access


@frappe.whitelist()
def get_profit_summary(
	from_date: str | None = None,
	to_date: str | None = None,
	company: str | None = None,
	warehouse: str | None = None,
) -> dict:
	"""Sales, cost of goods, gross and net profit for the period (the last 30 days if no dates are given)."""
	require_access("profit")
	ensure_record_access("Warehouse", warehouse)

	to_date = to_date or nowdate()
	from_date = from_date or str(add_days(getdate(to_date), -30))
	filters, company = _build_base_filters(company, warehouse, None, from_date, to_date)
	warehouse_filter = _get_warehouse_filter(warehouse)
	expenses = _get_operating_expenses(filters, company)
	return {
		"success": True,
		"data": _get_profit_stats(filters, warehouse_filter, expenses),
		"filters": {"company": company, "warehouse": warehouse, "from_date": from_date, "to_date": to_date},
	}
