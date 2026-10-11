"""
Run one of ERPNext's standard financial reports for the till frontend, limited to the caller's businesses.

Only reports on the list below can be run, and the company filter is always forced to a business the
caller may see: a request for another business is refused, and a request with no company is answered for
the caller's only business (or refused when they have several and must choose).
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from murzak_pos.api.desk_scope import chosen_companies

ALLOWED_REPORTS = (
	"Profit and Loss Statement",
	"Balance Sheet",
	"Trial Balance",
	"General Ledger",
	"Accounts Receivable",
	"Accounts Payable",
)


def forced_filters(filters, user: str | None = None) -> dict:
	"""The filters with `company` set to a business the person may see."""
	if isinstance(filters, str):
		filters = json.loads(filters or "{}")
	filters = dict(filters or {})
	requested = filters.get("company")
	companies = chosen_companies(requested, user)
	if requested:
		return filters
	if len(companies) != 1:
		frappe.throw(_("Choose which business this report is for."), frappe.ValidationError)
	filters["company"] = companies[0]
	return filters


@frappe.whitelist()
def run_standard_report(name: str, filters=None) -> dict:
	"""Run an allowed standard report; the result is the same as Frappe's own report screen receives."""
	if name not in ALLOWED_REPORTS:
		frappe.throw(_("This report is not available here."), frappe.PermissionError)
	from frappe.desk.query_report import run

	return run(name, filters=forced_filters(filters))
