"""
The Suppliers page calls (ticket P4-6): the list with its Owed filter, and the supplier panel.

`list_suppliers_overview` gives one page of suppliers with an exact count, what the shop owes each one, the next bill
due and the four numbers of the summary row. `get_supplier_overview` gives the panel (facts, what is owed, the next bill
due and the unpaid bills). The contract is docs/redesign/P4-6-contract.md of this repository. These calls are new; no
existing call, report or record is changed.

How it is built
- The rules are in suppliers_list_calc.py (plain functions, no database). This file reads the database in small
  functions, one job each, and joins the two.
- What the shop owes a supplier is read with the conditions Home's "supplier bill due" item uses
  (dashboard_overview._supplier_bill_rows): submitted Purchase Invoices that are not returns, with an unpaid amount above
  zero, of the business. The list, the Owed filter, the summary and the panel all take their figures from the one rule
  `figures_by_supplier`, so they always agree with each other and with Home's rule for what is due soon.
- Which suppliers a business sees is decided by the existing supplier calls (`supplier_api._visible_supplier_names`),
  which this file reuses and does not change.
- Roles are checked here, on the server (`role_tiers`): a cashier may list and open suppliers but never receives what is
  owed, the next bill due, the bills or the Owed filter (the figures are left out of the answer, not set to null).
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _

from murzak_pos.api import overview_calc as calc
from murzak_pos.api import role_tiers, supplier_api
from murzak_pos.api import suppliers_list_calc as rules
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company

SUPPLIER_FIELDS = (
	"name",
	"supplier_name",
	"supplier_type",
	"supplier_group",
	"tax_id",
	"country",
	"default_currency",
	"disabled",
	"is_internal_supplier",
)
SUPPLIER_LIMIT = 5000  # the most suppliers one business is read for (far above any shop's list)
BILL_LIMIT = 5000  # the most unpaid bills read for one business


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


def _signed_in_tier() -> str:
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return tier


def _refuse_list(code: str):
	if code == "bad_status":
		frappe.throw(_("Choose active, disabled or all suppliers."), frappe.ValidationError)
	frappe.throw(_("Choose All or Owed."), frappe.ValidationError)


# --- the reads --------------------------------------------------------------------------------------


def _bill_rows(company: str, supplier: str | None = None) -> list[dict]:
	"""
	The unpaid supplier bills of the business: the same conditions as Home's supplier bill item (submitted, not a
	return, an unpaid amount above zero) with no due-date cut-off, soonest due first. One supplier's when named.
	"""
	extra = " AND supplier = %(supplier)s" if supplier else ""
	values = {"company": company, "cap": BILL_LIMIT}
	if supplier:
		values["supplier"] = supplier
	return frappe.db.sql(
		"SELECT name, supplier, bill_no, outstanding_amount, due_date FROM `tabPurchase Invoice` "
		"WHERE docstatus = 1 AND is_return = 0 AND outstanding_amount > 0 AND company = %(company)s"
		+ extra
		+ " ORDER BY due_date, name LIMIT %(cap)s",
		values,
		as_dict=True,
	)


def _supplier_records(company: str) -> list[dict]:
	"""The suppliers the business sees (all of them, disabled too), as Supplier records."""
	visible = supplier_api._visible_supplier_names(company)
	if visible is not None and not visible:
		return []
	filters = {"name": ["in", list(visible)]} if visible is not None else {}
	return frappe.get_all(
		"Supplier", filters=filters, fields=list(SUPPLIER_FIELDS), limit_page_length=SUPPLIER_LIMIT
	)


# --- the list ---------------------------------------------------------------------------------------


@frappe.whitelist()
def list_suppliers_overview(
	search=None, owed=None, supplier_group=None, status=None, page=1, page_size=20, company=None
):
	"""One page of the Suppliers list, its exact count and the summary row (see the contract for the answer)."""
	tier = _signed_in_tier()
	try:
		owed_only = rules.normalize_owed(owed)
		status = rules.normalize_status(status, owed_only)
	except rules.ListError as error:
		_refuse_list(error.code)
	may_see_money = role_tiers.can("money_owed")
	if owed_only:
		# The Owed filter is a list of what the shop owes: a cashier is refused, not shown an empty list
		role_tiers.require_access("money_owed")
	company = resolve_company(company)
	query = rules.parse_search(search)
	group = rules.clean_group(supplier_group)
	size = rules.clamp_page_size(page_size)

	try:
		data = _list_page(
			company, query, group, status, owed_only, rules.clamp_page(page), size, may_see_money
		)
	except Exception:
		frappe.log_error("Suppliers list error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the suppliers. Please try again.")}
	data["role"] = tier
	return {"success": True, "data": data}


def _list_page(company, query, group, status, owed_only, page, size, may_see_money) -> dict:
	records = _supplier_records(company)
	today = calc.nairobi_today(_now_utc())
	# Only suppliers the business sees count, in the rows and in the summary alike
	known = {r["name"] for r in records}
	figures = rules.figures_by_supplier(_bill_rows(company), today, only=known) if may_see_money else {}
	chosen = rules.select_records(records, query, group, status, owed_only, figures)
	rows_on_page, info = rules.page_slice(chosen, page, size)
	rows = [rules.supplier_row(r, figures, may_see_money) for r in rows_on_page]
	active = sum(1 for r in records if rules.matches_status(r, "active"))
	return {"rows": rows, "summary": rules.summary(figures, active, may_see_money), **info}


# --- the panel --------------------------------------------------------------------------------------


@frappe.whitelist()
def get_supplier_overview(supplier, company=None):
	"""The supplier panel: facts and (for an owner or manager) what is owed, the next bill due and the unpaid bills."""
	_signed_in_tier()
	ensure_record_access("Supplier", supplier)
	company = resolve_company(company)
	may_see_money = role_tiers.can("money_owed")

	try:
		visible = supplier_api._visible_supplier_names(company)
		record = None
		if visible is None or supplier in visible:
			record = frappe.db.get_value("Supplier", supplier, list(SUPPLIER_FIELDS), as_dict=True)
		if not record:
			return {"success": False, "message": _("Supplier {0} was not found.").format(supplier)}
		figures = {}
		if may_see_money:
			today = calc.nairobi_today(_now_utc())
			figures = rules.figures_by_supplier(_bill_rows(company, supplier), today)
		data = rules.panel(record, figures, may_see_money)
	except Exception:
		frappe.log_error("Supplier overview error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load this supplier. Please try again.")}
	return {"success": True, "data": data}
