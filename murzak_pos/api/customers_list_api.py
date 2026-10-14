"""
The Customers page calls (ticket P4-7): the list with its Owing tab, the customer panel and the statement.

`list_customers_overview` gives one page of customers with an exact count, what each one owes, their last purchase
and the three numbers of the summary row. `get_customer_overview` gives the panel (facts, balance, last purchase).
`get_customer_statement` gives a statement with a running balance. `get_customer_form_options` tells the New customer
form whether to show the group and area. The contract is docs/redesign/P4-7-contract.md of this repository. These calls are
new; no existing call, report or record is changed.

How it is built
- The rules are in customers_list_calc.py (plain functions, no database). This file reads the database in small
  functions, one job each, and joins the two.
- What a customer owes is read with the code Home's "Money owed" uses (dashboard_overview): the same rows, the same
  conditions (submitted Sales Invoices and POS Invoices not yet merged into a Sales Invoice, returns left out, only an
  amount above zero). The Owing tab, the summary, the panel, the statement and Home therefore always agree.
- Roles are checked here, on the server (`role_tiers`): a cashier may list and open customers but never receives a
  balance, the Owing tab or a statement (the figures are left out of the answer, not set to null).
- Customers belong to a business through `custom_company`, as in the existing customer calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _

from murzak_pos.api import customers_list_calc as rules
from murzak_pos.api import dashboard_overview, role_tiers
from murzak_pos.api import overview_calc as calc
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company

CUSTOMER_FIELDS = ("name", "customer_name", "customer_type", "mobile_no", "email_id", "disabled")
PANEL_FIELDS = (*CUSTOMER_FIELDS, "customer_group", "territory", "tax_id", "loyalty_program")
OPTION_LIMIT = 200  # the most customer groups or areas offered in the form


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


def _signed_in_tier() -> str:
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return tier


def _refuse_list(code: str):
	if code == "bad_status":
		frappe.throw(_("Choose active, disabled or all customers."), frappe.ValidationError)
	if code == "bad_type":
		frappe.throw(_("Choose Individual, Company or Partnership."), frappe.ValidationError)
	frappe.throw(_("Choose a tab: all or owing."), frappe.ValidationError)


def _refuse_period(code: str):
	if code == "order":
		frappe.throw(_("The statement start date must not be after the end date."), frappe.ValidationError)
	if code == "too_long":
		frappe.throw(_("Please choose a statement period of two years or less."), frappe.ValidationError)
	frappe.throw(_("Statement dates must look like 2026-10-13."), frappe.ValidationError)


def _has_owner_column() -> bool:
	return bool(frappe.db.has_column("Customer", "custom_company"))


# --- the reads --------------------------------------------------------------------------------------


def _owed_scope(company: str) -> dashboard_overview._Scope:
	"""
	The request scope Home's money-owed read needs, with nothing narrowed (not by cashier, not by store): what a
	customer owes does not depend on who is asking, and only a person allowed to see it ever gets here.
	"""
	return dashboard_overview._Scope(
		tier=role_tiers.get_tier(),
		user=frappe.session.user,
		company=company,
		store=None,
		owner=None,
		period=None,
		today=None,
		now=None,
		profit=False,
		owed=True,
	)


def _balance_rows(company: str) -> list[dict]:
	"""Every customer's unpaid amount, from the read that feeds Home's Money owed tile."""
	return dashboard_overview._money_owed_rows(_owed_scope(company))


def _customer_balance_rows(company: str, customer: str) -> list[dict]:
	"""The same read (same conditions) narrowed to one customer."""
	scope = _owed_scope(company)
	union, values = dashboard_overview._owed_union(
		scope,
		"SELECT d.customer AS customer, d.outstanding_amount AS outstanding_amount",
		end_with="AND d.customer = %(customer)s",
	)
	values["customer"] = customer
	return frappe.db.sql(
		f"SELECT customer, SUM(outstanding_amount) AS outstanding_amount FROM ({union}) s GROUP BY customer",
		values,
		as_dict=True,
	)


def _customer_conditions(company: str, status: str, customer_type, search) -> tuple[list[str], dict]:
	"""(SQL conditions, values) for the All customers tab."""
	conditions = ["c.name != %(walk_in)s"]
	values: dict = {"walk_in": rules.WALK_IN}
	if _has_owner_column():
		conditions.append("c.custom_company = %(company)s")
		values["company"] = company
	if status == "active":
		conditions.append("c.disabled = 0")
	elif status == "disabled":
		conditions.append("c.disabled = 1")
	if customer_type:
		conditions.append("c.customer_type = %(customer_type)s")
		values["customer_type"] = customer_type
	if search:
		text = "(c.customer_name LIKE %(like)s OR c.name LIKE %(like)s"
		values["like"] = f"%{rules.escape_like(search['text'])}%"
		if search.get("mobile"):
			text += " OR REPLACE(REPLACE(REPLACE(IFNULL(c.mobile_no, ''), ' ', ''), '-', ''), '+', '') LIKE %(mobile)s"
			values["mobile"] = f"%{search['mobile']}%"
		conditions.append(text + ")")
	return conditions, values


def _count_customers(company: str, status: str, customer_type, search) -> int:
	conditions, values = _customer_conditions(company, status, customer_type, search)
	found = frappe.db.sql(
		f"SELECT COUNT(*) AS n FROM `tabCustomer` c WHERE {' AND '.join(conditions)}", values, as_dict=True
	)
	return int(found[0]["n"]) if found else 0


def _customer_page(company: str, status: str, customer_type, search, offset: int, limit: int) -> list[dict]:
	conditions, values = _customer_conditions(company, status, customer_type, search)
	values.update(limit=limit, offset=offset)
	fields = ", ".join(f"c.{f}" for f in CUSTOMER_FIELDS)
	return frappe.db.sql(
		f"SELECT {fields} FROM `tabCustomer` c WHERE {' AND '.join(conditions)} "
		"ORDER BY c.customer_name, c.name LIMIT %(limit)s OFFSET %(offset)s",
		values,
		as_dict=True,
	)


def _customer_details(customers) -> dict:
	"""{customer: Customer record} for the given customer numbers."""
	if not customers:
		return {}
	fields = ", ".join(f"c.{f}" for f in CUSTOMER_FIELDS)
	found = frappe.db.sql(
		f"SELECT {fields} FROM `tabCustomer` c WHERE c.name IN %(names)s",
		{"names": tuple(customers)},
		as_dict=True,
	)
	return {r["name"]: r for r in found}


def _last_purchase_rows(company: str, customers) -> list[dict]:
	"""
	The sales made on each customer's latest day of buying (a few rows each; the rules pick the latest). Both kinds of
	sale count, and a POS invoice merged at till close still is a purchase; the Sales Invoice made by the merge is not.
	"""
	if not customers:
		return []
	rows: list[dict] = []
	for doctype in (rules.SALES_INVOICE, rules.POS_INVOICE):
		extra = " AND IFNULL({alias}.is_consolidated, 0) = 0" if doctype == rules.SALES_INVOICE else ""
		inner = (
			f"SELECT customer, MAX(posting_date) AS last_day FROM `tab{doctype}` i WHERE i.docstatus = 1 "
			"AND i.is_return = 0 AND i.company = %(company)s AND i.customer IN %(customers)s"
			+ extra.format(alias="i")
			+ " GROUP BY customer"
		)
		rows += frappe.db.sql(
			f"SELECT d.customer, d.name, '{doctype}' AS doctype, d.posting_date, d.posting_time, d.grand_total "
			f"FROM `tab{doctype}` d JOIN ({inner}) m ON m.customer = d.customer AND m.last_day = d.posting_date "
			"WHERE d.docstatus = 1 AND d.is_return = 0 AND d.company = %(company)s" + extra.format(alias="d"),
			{"company": company, "customers": tuple(customers)},
			as_dict=True,
		)
	return rows


def _features(company: str) -> dict:
	"""Whether this business uses customer groups and areas (its customers have more than one of each)."""
	where, values = (
		("WHERE custom_company = %(company)s", {"company": company}) if _has_owner_column() else ("", {})
	)
	groups = frappe.db.sql(
		f"SELECT DISTINCT customer_group AS v FROM `tabCustomer` {where} LIMIT 3", values, as_dict=True
	)
	areas = frappe.db.sql(
		f"SELECT DISTINCT territory AS v FROM `tabCustomer` {where} LIMIT 3", values, as_dict=True
	)
	return {
		"groups": rules.uses_values([r["v"] for r in groups]),
		"territories": rules.uses_values([r["v"] for r in areas]),
	}


# --- the list ---------------------------------------------------------------------------------------


@frappe.whitelist()
def list_customers_overview(
	tab="all", search=None, customer_type=None, page=1, page_size=20, company=None, status="active"
):
	"""One page of the Customers list, its exact count and the summary row (see the contract for the answer)."""
	tier = _signed_in_tier()
	try:
		tab = rules.normalize_tab(tab)
		status = rules.normalize_status(status)
		customer_type = rules.normalize_type(customer_type)
	except rules.ListError as error:
		_refuse_list(error.code)
	may_see_money = role_tiers.can("money_owed")
	if tab == "owing":
		# The Owing tab is the list of balances: a cashier is refused, not shown an empty list
		role_tiers.require_access("money_owed")
	company = resolve_company(company)
	query = rules.parse_search(search)
	size = rules.clamp_page_size(page_size)

	try:
		data = _list_page(
			tab, company, status, customer_type, query, rules.clamp_page(page), size, may_see_money
		)
	except Exception:
		frappe.log_error("Customers list error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the customers. Please try again.")}
	data["role"] = tier
	return {"success": True, "data": data}


def _list_page(tab, company, status, customer_type, query, page, size, may_see_money) -> dict:
	owed_rows = _balance_rows(company) if may_see_money else []
	balances = rules.balances_by_customer(owed_rows)
	if tab == "owing":
		details = _customer_details(list(balances))
		records = rules.owing_records(balances, details, query, customer_type)
		chosen, info = rules.page_slice(records, page, size)
	else:
		total = _count_customers(company, status, customer_type, query)
		info = rules.page_info(total, page, size)
		chosen = _customer_page(company, status, customer_type, query, (info["page"] - 1) * size, size)
	purchases = rules.last_purchases(_last_purchase_rows(company, [r["name"] for r in chosen]))
	rows = [rules.customer_row(record, balances, purchases, may_see_money) for record in chosen]

	summary = {"customers": _count_customers(company, "active", None, None)}
	if may_see_money:
		# Straight from the rows Home's tile reads, so the two totals cannot differ
		summary.update(rules.owing_summary(owed_rows))
	return {"rows": rows, "summary": summary, **info}


# --- the panel --------------------------------------------------------------------------------------


def _customer_record(customer: str, company: str) -> dict | None:
	"""The customer, or None when there is no such customer or it belongs to another business."""
	fields = list(PANEL_FIELDS)
	if _has_owner_column():
		fields.append("custom_company")
	record = frappe.db.get_value("Customer", customer, fields, as_dict=True)
	if not record:
		return None
	if _has_owner_column() and not rules.customer_in_company(record.get("custom_company"), company):
		return None
	return record


def _balance_of(company: str, customer: str) -> float:
	"""What one customer owes: the one function the list and the statement both read."""
	return rules.balance_of(rules.balances_by_customer(_customer_balance_rows(company, customer)), customer)


@frappe.whitelist()
def get_customer_overview(customer, company=None):
	"""The customer panel: facts, last purchase and (for an owner or manager) the balance owed."""
	_signed_in_tier()
	ensure_record_access("Customer", customer)
	company = resolve_company(company)
	may_see_money = role_tiers.can("money_owed")

	try:
		record = _customer_record(customer, company)
		if record is None:
			return {"success": False, "message": _("Customer {0} was not found.").format(customer)}
		purchases = rules.last_purchases(_last_purchase_rows(company, [customer]))
		data = {
			"id": record["name"],
			"name": record.get("customer_name") or record["name"],
			"type": record.get("customer_type") or "",
			"mobile": record.get("mobile_no") or "",
			"email": record.get("email_id") or "",
			"tax_id": record.get("tax_id") or "",
			"group": record.get("customer_group") or "",
			"territory": record.get("territory") or "",
			"loyalty_program": record.get("loyalty_program") or "",
			"disabled": bool(calc.num(record.get("disabled"))),
			"last_purchase": rules.last_purchase_for(purchases, customer, may_see_money),
			"features": _features(company),
		}
		if may_see_money:
			data["balance"] = _balance_of(company, customer)
	except Exception:
		frappe.log_error("Customer overview error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load this customer. Please try again.")}
	return {"success": True, "data": data}


# --- the statement ----------------------------------------------------------------------------------


def _statement_documents(company: str, customer: str, first) -> dict:
	"""The newest sales and returns of the customer from `first` up to today, one read per kind of invoice."""
	reads = {}
	for doctype in (rules.SALES_INVOICE, rules.POS_INVOICE):
		# The same documents Home counts: a POS invoice merged into a Sales Invoice is inside that invoice
		merged = " AND IFNULL(d.consolidated_invoice, '') = ''" if doctype == rules.POS_INVOICE else ""
		reads[doctype] = frappe.db.sql(
			f"SELECT d.name, '{doctype}' AS doctype, d.posting_date, d.posting_time, d.grand_total, "
			"IFNULL(d.paid_amount, 0) - IFNULL(d.change_amount, 0) AS paid, d.is_return "
			f"FROM `tab{doctype}` d WHERE d.docstatus = 1 AND d.customer = %(customer)s "
			"AND d.company = %(company)s AND d.posting_date >= %(first)s"
			+ merged
			+ " ORDER BY d.posting_date DESC, d.posting_time DESC, d.name DESC LIMIT %(cap)s",
			{"customer": customer, "company": company, "first": first, "cap": rules.STATEMENT_MAX_LINES},
			as_dict=True,
		)
	return reads


def _statement_payments(company: str, customer: str, first) -> list[dict]:
	"""The newest payments received from the customer against their sales, from `first` up to today."""
	return frappe.db.sql(
		"SELECT pe.name, pe.posting_date, TIME(pe.creation) AS posting_time, SUM(r.allocated_amount) AS amount "
		"FROM `tabPayment Entry` pe JOIN `tabPayment Entry Reference` r ON r.parent = pe.name "
		"WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND pe.party_type = 'Customer' "
		"AND pe.party = %(customer)s AND pe.company = %(company)s "
		"AND r.reference_doctype IN ('Sales Invoice', 'POS Invoice') AND pe.posting_date >= %(first)s "
		"GROUP BY pe.name, pe.posting_date, pe.creation ORDER BY pe.posting_date DESC, pe.creation DESC LIMIT %(cap)s",
		{"customer": customer, "company": company, "first": first, "cap": rules.STATEMENT_MAX_LINES},
		as_dict=True,
	)


@frappe.whitelist()
def get_customer_statement(customer, from_date=None, to_date=None, company=None):
	"""A customer's statement for a period, ending at the balance the list shows (owner and manager only)."""
	tier = _signed_in_tier()
	ensure_record_access("Customer", customer)
	role_tiers.require_access("money_owed")
	company = resolve_company(company)

	today = calc.nairobi_today(_now_utc())
	try:
		first, last = rules.resolve_statement_period(from_date, to_date, today)
	except rules.StatementError as error:
		_refuse_period(error.code)

	try:
		record = _customer_record(customer, company)
		if record is None:
			return {"success": False, "message": _("Customer {0} was not found.").format(customer)}
		reads = _statement_documents(company, customer, first)
		payments = _statement_payments(company, customer, first)
		complete = rules.complete_from(first, *reads.values(), payments)
		entries = rules.entries_from(
			rules.statement_entries([d for rows in reads.values() for d in rows], payments), complete
		)
		statement = rules.build_statement(
			entries, _balance_of(company, customer), complete, max(last, complete)
		)
		purchases = rules.last_purchases(_last_purchase_rows(company, [customer]))
	except Exception:
		frappe.log_error("Customer statement error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load this statement. Please try again.")}

	statement["customer"] = {
		"id": record["name"],
		"name": record.get("customer_name") or record["name"],
		"mobile": record.get("mobile_no") or "",
	}
	statement["last_purchase"] = rules.last_purchase_for(purchases, customer, True)
	# The period asked for starts earlier than the first line when the oldest lines could not all be read
	statement["truncated"] = complete > first
	statement["requested_from"] = str(first)
	statement["role"] = tier
	return {"success": True, "data": statement}


# --- the form ---------------------------------------------------------------------------------------


@frappe.whitelist()
def get_customer_form_options(company=None):
	"""What the New and Edit customer forms need: whether to show the group and area, and their choices."""
	_signed_in_tier()
	company = resolve_company(company)
	try:
		features = _features(company)
		groups = frappe.get_all(
			"Customer Group",
			filters={"is_group": 0},
			pluck="name",
			order_by="name",
			limit_page_length=OPTION_LIMIT,
		)
		areas = frappe.get_all(
			"Territory",
			filters={"is_group": 0},
			pluck="name",
			order_by="name",
			limit_page_length=OPTION_LIMIT,
		)
	except Exception:
		frappe.log_error("Customer form options error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the form options. Please try again.")}
	return {"success": True, "data": {"features": features, "groups": groups, "territories": areas}}
