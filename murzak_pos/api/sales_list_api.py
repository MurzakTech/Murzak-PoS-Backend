"""
The Sales page: one list of every sale, and the receipt panel for one sale (ticket P3-7).

`list_sales` and `get_sale` return what the contract docs/redesign/P3-7-contract.md describes. They are new; no
existing call, report or record is changed.

How it is built
- The rules and the arithmetic are in sales_list_calc.py (plain functions, no database). This file reads the
  database in small functions, one job each, and joins the two.
- A sale is counted as everywhere else in the app (see sales_figures.py): a submitted Sales Invoice, or a submitted POS
  Invoice that was not merged into a Sales Invoice when a till was closed. ERPNext posts POS Invoices to the accounts
  only at till close, so the list reads both kinds, as Home does.
- The list shows the full amount the customer paid (`grand_total`, VAT included), not the net revenue that Home
  reports. The receipt panel reads its total through the same function (`sale_amounts`), so a sale has one amount.
- Roles are checked here, on the server (`role_tiers`): a cashier reads only their own sales and their own till
  sessions, whatever they ask for.
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _

from murzak_pos.api import overview_calc as calc
from murzak_pos.api import role_tiers
from murzak_pos.api import sales_list_calc as rules
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company

# --- the clock (small on purpose, so tests can replace it) ------------------------------------------


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


# --- refusing a request that cannot be used ---------------------------------------------------------


def _refuse_period(code: str):
	"""Refuse a period that cannot be used, in words the person can act on."""
	if code == "need_dates":
		frappe.throw(
			_("Please give both a start date and an end date for the sales list."), frappe.ValidationError
		)
	if code == "bad_date":
		frappe.throw(_("Sales list dates must look like 2026-10-13."), frappe.ValidationError)
	if code == "order":
		frappe.throw(_("The sales list start date must not be after the end date."), frappe.ValidationError)
	if code == "too_long":
		frappe.throw(_("Please choose a sales list period of one year or less."), frappe.ValidationError)
	frappe.throw(
		_("Choose a sales list period: today, week, 7d, 30d, month or custom."), frappe.ValidationError
	)


def _refuse_list(code: str):
	if code == "bad_doctype":
		frappe.throw(_("Choose POS Invoice or Sales Invoice."), frappe.ValidationError)
	frappe.throw(_("Choose a tab: all, unpaid, returns or sessions."), frappe.ValidationError)


# --- the request ------------------------------------------------------------------------------------


class _Scope:
	"""What one list request is about."""

	def __init__(self, *, tab, company, owner, store, period, search, today):
		self.tab = tab
		self.company = company
		self.owner = owner
		self.store = store
		self.period = period
		self.search = search
		self.today = today


@frappe.whitelist()
def list_sales(
	tab="all",
	period="30d",
	from_date=None,
	to_date=None,
	search=None,
	cashier=None,
	store=None,
	page=1,
	page_size=20,
):
	"""The Sales page list for a tab and a period (see the module notes and the contract for the answer)."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	ensure_record_access("Warehouse", store)

	try:
		tab = rules.normalize_tab(tab)
	except rules.ListError as error:
		_refuse_list(error.code)
	today = calc.nairobi_today(_now_utc())
	try:
		resolved = calc.resolve_period(period, from_date, to_date, today)
	except calc.PeriodError as error:
		_refuse_period(error.code)
	company = resolve_company(None)

	scope = _Scope(
		tab=tab,
		company=company,
		# A cashier sees only their own sales, whatever was asked for
		owner=rules.scope_owner(tier, frappe.session.user, cashier),
		store=store or None,
		period=resolved,
		search=rules.parse_search(search),
		today=today,
	)
	try:
		data = (
			_sessions_page(scope, page, page_size)
			if tab == "sessions"
			else _sales_page(scope, page, page_size)
		)
		data["summary"] = _summary(scope)
	except Exception:
		frappe.log_error("Sales list error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the sales. Please try again.")}
	return {"success": True, "data": data}


@frappe.whitelist()
def get_sale(id, doctype=None):
	"""The receipt panel for one sale (see the contract for the answer)."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	# A sale of another business is refused whichever kind of document it is; a number that is not a document
	# of that kind has no owner recorded and passes, and the call then says it was not found
	ensure_record_access("POS Invoice", id)
	ensure_record_access("Sales Invoice", id)

	try:
		doctype = rules.normalize_doctype(doctype)
	except rules.ListError as error:
		_refuse_list(error.code)
	kind = _kind_of(id, doctype)
	if kind is None:
		return _not_found(id)

	user = frappe.session.user
	try:
		doc = frappe.get_doc(kind, id)
	except frappe.DoesNotExistError:
		return _not_found(id)
	if doc.docstatus != 1:
		return _not_found(id)
	if not rules.may_read_sale(tier, user, doc.owner):
		frappe.throw(_("You can only see your own sales."), frappe.PermissionError)

	try:
		data = rules.build_receipt(
			_receipt_source(doc),
			calc.nairobi_today(_now_utc()),
			returns=_returns_against(kind, doc.name),
			names=_user_names([doc.owner]),
			can_return=rules.may_return(frappe.get_roles(user), user),
		)
	except Exception:
		frappe.log_error("Sale receipt error", f"{kind} {id}\n{frappe.get_traceback()}")
		return {"success": False, "message": _("Could not load this sale. Please try again.")}
	return {"success": True, "data": data}


def _not_found(id):
	return {
		"success": False,
		"message": _("Sale {0} was not found.").format(id),
		"error_type": "not_found",
	}


def _kind_of(id, doctype):
	"""The kind of document `id` is: the one asked for if it exists, otherwise whichever kind has it. None if neither."""
	for kind in (doctype,) if doctype else rules.KINDS:
		if frappe.db.exists(kind, id):
			return kind
	return None


# --- reading the sales list -------------------------------------------------------------------------


def _filters(scope: _Scope, doctype: str, with_tab: bool):
	"""(SQL conditions, values) over one kind of sale document, alias d."""
	conditions = [
		"d.docstatus = 1",
		"d.company = %(company)s",
		"d.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]
	values: dict = {
		"company": scope.company,
		"from_date": scope.period["from"],
		"to_date": scope.period["to"],
	}
	if doctype == rules.POS_INVOICE:
		# A POS Invoice merged into a Sales Invoice at till close is already inside that Sales Invoice
		conditions.append("IFNULL(d.consolidated_invoice, '') = ''")
	if scope.owner:
		conditions.append("d.owner = %(owner)s")
		values["owner"] = scope.owner
	if scope.store:
		conditions.append(
			f"EXISTS (SELECT 1 FROM `tab{doctype} Item` wi WHERE wi.parent = d.name AND wi.warehouse = %(store)s)"
		)
		values["store"] = scope.store
	search = scope.search
	if search:
		matches = ["d.customer_name LIKE %(like)s", "d.customer LIKE %(like)s"]
		values["like"] = search["like"]
		if search["seq"] is None:
			matches.append("d.name LIKE %(like)s")
		elif search["kind"] in (None, doctype):
			matches.append("d.name REGEXP %(receipt_rx)s")
			values["receipt_rx"] = rules.receipt_regex(search["seq"])
		conditions.append("(" + " OR ".join(matches) + ")")
	if with_tab:
		conditions += rules.tab_conditions(scope.tab)
	return conditions, values


def _sale_union(scope: _Scope, select: str, tables: str, with_tab: bool, end_with: str = ""):
	"""SQL over both kinds of sale document joined with UNION ALL, and its values. `select` and `tables` take {doctype}."""
	parts, values = [], {}
	for doctype in rules.KINDS:
		conditions, values = _filters(scope, doctype, with_tab)
		parts.append(
			select.format(
				doctype=doctype,
				consolidated=_consolidated_column(doctype) if "{consolidated}" in select else "",
			)
			+ " "
			+ tables.format(doctype=doctype)
			+ " WHERE "
			+ " AND ".join(conditions)
			+ " "
			+ end_with
		)
	return " UNION ALL ".join(parts), values


def _row_columns() -> str:
	"""The columns of a list row, the same for both kinds of document so the two can be joined."""
	return (
		"SELECT '{doctype}' AS doctype, d.name AS name, d.posting_date AS posting_date, d.posting_time AS posting_time, "
		"d.customer AS customer, d.customer_name AS customer_name, d.owner AS owner, d.grand_total AS grand_total, "
		"d.outstanding_amount AS outstanding_amount, d.is_return AS is_return, d.due_date AS due_date, "
		"c.payment_terms AS payment_terms, {consolidated} AS is_consolidated"
	)


def _consolidated_column(doctype: str) -> str:
	"""Only a Sales Invoice can be the merged invoice of a till close (is_consolidated); a POS Invoice never is."""
	if doctype == rules.SALES_INVOICE and frappe.db.has_column(rules.SALES_INVOICE, "is_consolidated"):
		return "IFNULL(d.is_consolidated, 0)"
	return "0"


def _sales_page(scope: _Scope, page, page_size) -> dict:
	"""rows, total, page, page_size, pages for the All sales, Unpaid and Returns tabs."""
	union, values = _sale_union(scope, "SELECT d.name", "FROM `tab{doctype}` d", with_tab=True)
	counted = frappe.db.sql(f"SELECT COUNT(*) AS n FROM ({union}) s", values, as_dict=True)
	total = int(counted[0].get("n") or 0) if counted else 0
	numbers = rules.page_numbers(page, page_size, total)

	rows: list[dict] = []
	if total:
		union, values = _sale_union(
			scope,
			_row_columns(),
			"FROM `tab{doctype}` d LEFT JOIN `tabCustomer` c ON c.name = d.customer",
			with_tab=True,
		)
		values.update(limit=numbers["page_size"], offset=numbers["offset"])
		found = frappe.db.sql(
			f"SELECT * FROM ({union}) s ORDER BY posting_date DESC, posting_time DESC, name DESC "
			"LIMIT %(limit)s OFFSET %(offset)s",
			values,
			as_dict=True,
		)
		for row in found:
			row["has_terms"] = bool(row.get("payment_terms"))
		rows = rules.union_sales(found)
	returned = _returned_values(rows)
	methods = _payment_rows(rows)
	names = _user_names([r.get("owner") for r in rows])
	return {
		"rows": [rules.list_row(r, scope.today, returned, methods, names) for r in rows],
		"total": total,
		"page": numbers["page"],
		"page_size": numbers["page_size"],
		"pages": numbers["pages"],
	}


def _summary(scope: _Scope) -> dict:
	"""Sold, unpaid and returned for the same filters, whatever the tab."""
	union, values = _sale_union(
		scope,
		"SELECT d.is_return AS is_return, SUM(d.grand_total) AS total, "
		"SUM(CASE WHEN d.outstanding_amount > 0 THEN d.outstanding_amount ELSE 0 END) AS outstanding",
		"FROM `tab{doctype}` d",
		with_tab=False,
		end_with="GROUP BY d.is_return",
	)
	return rules.build_summary(frappe.db.sql(union, values, as_dict=True))


def _by_kind(rows) -> dict:
	grouped: dict = {}
	for row in rows:
		grouped.setdefault(row.get("doctype"), []).append(row.get("name"))
	return grouped


def _returned_values(rows) -> dict:
	"""{sale name: value of the returns made against it} for the sales on this page."""
	out: dict = {}
	for doctype, names in _by_kind([r for r in rows if not calc.num(r.get("is_return"))]).items():
		found = frappe.db.sql(
			f"SELECT return_against, SUM(ABS(grand_total)) AS returned FROM `tab{doctype}` "
			"WHERE docstatus = 1 AND is_return = 1 AND return_against IN %(names)s GROUP BY return_against",
			{"names": tuple(names)},
			as_dict=True,
		)
		for row in found:
			out[row["return_against"]] = calc.num(row.get("returned"))
	return out


def _payment_rows(rows) -> dict:
	"""{sale name: its payment rows} for the sales on this page."""
	out: dict = {}
	for doctype, names in _by_kind(rows).items():
		found = frappe.db.sql(
			"SELECT parent, mode_of_payment, amount FROM `tabSales Invoice Payment` "
			"WHERE parenttype = %(doctype)s AND parent IN %(names)s",
			{"doctype": doctype, "names": tuple(names)},
			as_dict=True,
		)
		for row in found:
			out.setdefault(row["parent"], []).append(row)
	return out


def _user_names(users) -> dict:
	"""{user: full name}."""
	users = sorted({u for u in users if u})
	if not users:
		return {}
	found = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	return {row["name"]: row.get("full_name") for row in found}


# --- reading the till sessions ----------------------------------------------------------------------


def _session_filters(scope: _Scope):
	conditions = [
		"o.docstatus = 1",
		"o.company = %(company)s",
		"DATE(o.period_start_date) BETWEEN %(from_date)s AND %(to_date)s",
	]
	values: dict = {
		"company": scope.company,
		"from_date": scope.period["from"],
		"to_date": scope.period["to"],
	}
	if scope.owner:
		conditions.append("o.user = %(owner)s")
		values["owner"] = scope.owner
	if scope.store:
		conditions.append(
			"o.pos_profile IN (SELECT p.name FROM `tabPOS Profile` p "
			"WHERE p.company = %(company)s AND p.warehouse = %(store)s)"
		)
		values["store"] = scope.store
	if scope.search:
		conditions.append("(o.name LIKE %(like)s OR o.user LIKE %(like)s)")
		values["like"] = scope.search["like"]
	return conditions, values


def _sessions_page(scope: _Scope, page, page_size) -> dict:
	"""rows, total, page, page_size, pages for the Till sessions tab."""
	conditions, values = _session_filters(scope)
	where = " AND ".join(conditions)
	counted = frappe.db.sql(
		f"SELECT COUNT(*) AS n FROM `tabPOS Opening Entry` o WHERE {where}", values, as_dict=True
	)
	total = int(counted[0].get("n") or 0) if counted else 0
	numbers = rules.page_numbers(page, page_size, total)

	openings: list[dict] = []
	if total:
		values.update(limit=numbers["page_size"], offset=numbers["offset"])
		openings = frappe.db.sql(
			"SELECT o.name, o.user, o.pos_profile, o.status, o.posting_date, o.period_start_date "
			f"FROM `tabPOS Opening Entry` o WHERE {where} "
			"ORDER BY o.period_start_date DESC, o.name DESC LIMIT %(limit)s OFFSET %(offset)s",
			values,
			as_dict=True,
		)
	names = [o["name"] for o in openings]
	closings = _closings(names)
	closing_rows = _closing_rows([c["name"] for c in closings.values()])
	counts = _session_sales(names)
	types = _mode_types()
	users = _user_names([o.get("user") for o in openings])
	return {
		"rows": [
			rules.session_row(
				o,
				closings.get(o["name"]),
				closing_rows.get((closings.get(o["name"]) or {}).get("name"), []),
				counts.get(o["name"], 0),
				users,
				types,
			)
			for o in openings
		],
		"total": total,
		"page": numbers["page"],
		"page_size": numbers["page_size"],
		"pages": numbers["pages"],
	}


def _closings(opening_names) -> dict:
	"""{opening entry: its latest submitted closing entry}."""
	if not opening_names:
		return {}
	found = frappe.db.sql(
		"SELECT name, pos_opening_entry, period_end_date FROM `tabPOS Closing Entry` "
		"WHERE docstatus = 1 AND pos_opening_entry IN %(names)s ORDER BY creation",
		{"names": tuple(opening_names)},
		as_dict=True,
	)
	return {row["pos_opening_entry"]: row for row in found}  # the latest wins


def _closing_rows(closing_names) -> dict:
	"""{closing entry: its payment rows (expected, counted and difference per mode of payment)}."""
	if not closing_names:
		return {}
	found = frappe.db.sql(
		"SELECT parent, mode_of_payment, expected_amount, closing_amount, difference "
		"FROM `tabPOS Closing Entry Detail` WHERE parenttype = 'POS Closing Entry' AND parent IN %(names)s",
		{"names": tuple(closing_names)},
		as_dict=True,
	)
	out: dict = {}
	for row in found:
		out.setdefault(row["parent"], []).append(row)
	return out


def _session_sales(opening_names) -> dict:
	"""{opening entry: receipts made in the session}: the cashier's POS Invoices between opening and closing."""
	if not opening_names:
		return {}
	found = frappe.db.sql(
		"""
		SELECT o.name AS session, COUNT(DISTINCT d.name) AS n
		FROM `tabPOS Opening Entry` o
		LEFT JOIN `tabPOS Closing Entry` c ON c.pos_opening_entry = o.name AND c.docstatus = 1
		LEFT JOIN `tabPOS Invoice` d ON d.docstatus = 1 AND d.is_return = 0 AND d.owner = o.user
			AND d.pos_profile = o.pos_profile
			AND TIMESTAMP(d.posting_date, d.posting_time) >= o.period_start_date
			AND (c.period_end_date IS NULL OR TIMESTAMP(d.posting_date, d.posting_time) <= c.period_end_date)
		WHERE o.name IN %(names)s GROUP BY o.name
		""",
		{"names": tuple(opening_names)},
		as_dict=True,
	)
	return {row["session"]: int(row.get("n") or 0) for row in found}


def _mode_types() -> dict:
	"""Each mode of payment's type in ERPNext (Cash, Bank, Phone, General), to place a mode with an odd name."""
	return {
		row["name"]: row.get("type") for row in frappe.get_all("Mode of Payment", fields=["name", "type"])
	}


# --- reading one sale -------------------------------------------------------------------------------


def _receipt_source(doc) -> dict:
	"""The fields of a sale document that the receipt panel reads, as plain data."""
	customer = doc.get("customer")
	mobile = None
	has_terms = False
	if customer:
		found = frappe.db.get_value("Customer", customer, ["mobile_no", "payment_terms"], as_dict=True)
		if found:
			mobile = found.get("mobile_no")
			has_terms = bool(found.get("payment_terms"))
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"posting_date": doc.get("posting_date"),
		"posting_time": doc.get("posting_time"),
		"customer": customer,
		"customer_name": doc.get("customer_name"),
		"mobile": mobile or doc.get("contact_mobile"),
		"has_terms": has_terms,
		"owner": doc.get("owner"),
		"grand_total": doc.get("grand_total"),
		"outstanding_amount": doc.get("outstanding_amount"),
		"change_amount": doc.get("change_amount"),
		"total": doc.get("total"),
		"discount_amount": doc.get("discount_amount"),
		"total_taxes_and_charges": doc.get("total_taxes_and_charges"),
		"is_return": doc.get("is_return"),
		"return_against": doc.get("return_against"),
		"due_date": doc.get("due_date"),
		"items": [
			{
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
				"qty": row.get("qty"),
				"rate": row.get("rate"),
				"amount": row.get("amount"),
			}
			for row in doc.get("items") or []
		],
		"payments": [
			{
				"mode_of_payment": row.get("mode_of_payment"),
				"amount": row.get("amount"),
				"reference_no": row.get("reference_no"),
			}
			for row in doc.get("payments") or []
		],
	}


def _returns_against(doctype: str, name: str) -> list[dict]:
	"""The submitted returns made against a sale, oldest first."""
	found = frappe.get_all(
		doctype,
		filters={"return_against": name, "is_return": 1, "docstatus": 1},
		fields=["name", "grand_total"],
		order_by="posting_date asc, posting_time asc, name asc",
	)
	return [{"doctype": doctype, "name": r["name"], "grand_total": r.get("grand_total")} for r in found]
