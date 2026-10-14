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

from murzak_pos.api import business_lists, role_tiers
from murzak_pos.api import overview_calc as calc
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
		data = _receipt(doc, user)
	except Exception:
		frappe.log_error("Sale receipt error", f"{kind} {id}\n{frappe.get_traceback()}")
		return {"success": False, "message": _("Could not load this sale. Please try again.")}
	return {"success": True, "data": data}


def _receipt(doc, user) -> dict:
	"""The receipt panel data for a sale document that has been read."""
	return rules.build_receipt(
		_receipt_source(doc),
		calc.nairobi_today(_now_utc()),
		returns=_returns_against(doc.doctype, doc.name),
		names=_user_names([doc.owner]),
		can_return=rules.may_return(frappe.get_roles(user), user),
	)


def _refusal(error_type: str, message: str) -> dict:
	"""An expected refusal: a plain sentence and a short code the page can act on (the reply is still HTTP 200)."""
	return {"success": False, "message": message, "error_type": error_type}


@frappe.whitelist()
def take_payment(id, doctype, amount, mode, reference=None, request_id=None, company=None):
	"""
	Record money received on an unpaid sale (see the contract for the cases that are supported and refused).

	A Sales Invoice takes a Payment Entry on itself. A POS Invoice that was merged into a Sales Invoice when its till
	was closed takes the Payment Entry on that Sales Invoice. A POS Invoice still on an open shift cannot (ERPNext
	cannot point a Payment Entry at it) and is refused, so money is never recorded without reducing what is owed.
	"""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	# Who may touch this sale, before any money is read: another business's sale is refused, and a cashier may
	# take payment only on a sale of their own
	ensure_record_access("POS Invoice", id)
	ensure_record_access("Sales Invoice", id)
	try:
		doctype = rules.normalize_doctype(doctype)
	except rules.ListError as error:
		_refuse_list(error.code)
	if doctype is None:
		_refuse_list("bad_doctype")
	if company:
		company = resolve_company(company)
	user = frappe.session.user
	head = frappe.db.get_value(doctype, id, ["owner", "docstatus", "company"], as_dict=True)
	if not head:
		return _not_found(id)
	if not rules.may_read_sale(tier, user, head.get("owner")):
		frappe.throw(_("You can only see your own sales."), frappe.PermissionError)
	if company and head.get("company") != company:
		return _not_found(id)

	# What was asked, checked before anything is read or written
	value = rules.parse_amount(amount)
	if value is None:
		return _refusal("bad_amount", _("Enter an amount above 0, such as 500 or 250.50."))
	key = rules.normalize_mode_key(mode)
	if key is None:
		return _refusal("bad_mode", _("Choose how the customer paid: cash, M-Pesa or card."))
	code, problem = rules.clean_reference(key, reference)
	if problem == "missing":
		return _refusal("reference_required", _("Enter the M-Pesa code from the customer's message."))
	if problem:
		return _refusal(
			"bad_reference",
			_("The M-Pesa code must be 8 to 12 letters and numbers, with no spaces or dashes."),
		)
	request, request_ok = rules.clean_request_id(request_id)
	if not request_ok:
		return _refusal(
			"bad_request_id", _("This payment could not be checked for a repeat. Please try again.")
		)

	try:
		entry, refusal = _take(doctype, id, head.get("company"), value, key, code, request)
	except Exception:
		frappe.db.rollback()
		frappe.log_error("Take payment error", f"{doctype} {id}\n{frappe.get_traceback()}")
		return _refusal(
			"server_error", _("The payment could not be recorded, so nothing was taken. Please try again.")
		)
	if refusal:
		return refusal
	try:
		return _paid(entry, doctype, id, user)
	except Exception:
		# The money is recorded; only the picture of the sale could not be drawn
		frappe.log_error("Take payment receipt error", f"{doctype} {id}\n{frappe.get_traceback()}")
		return {"success": True, "data": {"payment": entry, "sale": None}}


def _take(doctype, id, company, value, key, code, request):
	"""Check the sale and record the payment. Returns (payment name, None) or (None, the refusal)."""
	# One request at a time per sale, so a double tap cannot slip in between the check and the payment
	frappe.db.get_value(doctype, id, "name", for_update=True)
	earlier = _earlier_payment(request, id, company)
	if earlier:
		return earlier, None

	doc = frappe.get_doc(doctype, id)
	source = _receipt_source(doc)
	owed = rules.sale_amounts(source)["outstanding"]
	plan = rules.payment_plan(
		doctype,
		id,
		doc.docstatus,
		doc.get("is_return"),
		owed,
		source.get("merged_into"),
		_is_consolidated_invoice(doc),
	)
	if plan.get("error"):
		return None, _plan_refusal(plan["error"], id)
	target = plan["target"]
	target_info = _payment_target(target, doc, owed)
	if target_info.get("error"):
		return None, _plan_refusal(target_info["error"], id)
	owed = target_info["owed"]
	if rules.check_amount(value, owed):
		return None, _refusal(
			"too_much",
			_("The amount is more than the {0} still owed on this sale.").format(rules.money_text(owed)),
		)
	modes = _shop_modes(doc.company)
	picked = rules.pick_mode(key, modes, _mode_types() if modes else {})
	if not picked:
		return None, _refusal(
			"bad_mode",
			_("This shop does not have {0} set up as a way to pay.").format(rules.MODE_KEYS[key]),
		)
	account = _mode_account(picked, doc.company)
	if not account:
		return None, _refusal(
			"no_account",
			_("{0} has no account set up for this shop. Ask the owner to add it in Settings.").format(picked),
		)
	entry = _record_payment(
		target, value, picked, account, code or _fallback_reference(key, id), source, request, id
	)
	return entry, None


def _plan_refusal(code: str, id) -> dict:
	"""The plain sentence for each reason a payment cannot be taken on a sale."""
	if code == "pay_after_close":
		return _refusal(
			"pay_after_close",
			_("This sale is still on the till's open shift. Close the shift, then take the payment."),
		)
	if code == "consolidated":
		return _refusal(
			"consolidated",
			_("This is the invoice made when a till was closed. Take the payment on the customer's receipt."),
		)
	if code == "other_customer":
		return _refusal(
			"other_customer",
			_(
				"This sale was merged into an invoice for another customer, so the payment cannot be taken here."
			),
		)
	if code == "is_return":
		return _refusal("not_payable", _("A return cannot take a payment."))
	if code == "nothing_owed":
		return _refusal("not_payable", _("Nothing is owed on this sale."))
	return _not_found(id)


def _is_consolidated_invoice(doc) -> bool:
	"""A Sales Invoice made when a till was closed: flagged by ERPNext, or named by the receipts merged into it."""
	if doc.doctype != rules.SALES_INVOICE:
		return False
	return bool(doc.get("is_consolidated")) or bool(
		frappe.db.exists(rules.POS_INVOICE, {"consolidated_invoice": doc.name})
	)


def _payment_target(target, doc, owed) -> dict:
	"""
	The Sales Invoice that takes the payment and the most it can take. A Sales Invoice is its own target. For a merged
	POS Invoice the target is the consolidated invoice: it must still be submitted and be the same customer's.
	"""
	if target == (doc.doctype, doc.name):
		return {"owed": owed}
	info = frappe.db.get_value(
		target[0], target[1], ["docstatus", "customer", "outstanding_amount"], as_dict=True, for_update=True
	)
	if not info or info.get("docstatus") != 1:
		return {"error": "pay_after_close"}
	if info.get("customer") != doc.get("customer"):
		return {"error": "other_customer"}
	return {"owed": min(owed, calc.money(info.get("outstanding_amount")))}


def _shop_modes(company) -> list[str]:
	return business_lists.modes_of_payment_for(company) or []


def _mode_account(mode, company):
	"""The company's account for a mode of payment, or None. Only the company's own currency is accepted."""
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	info = get_default_bank_cash_account(company, mode_of_payment=mode) or {}
	account = info.get("account")
	if not account:
		return None
	currency = info.get("account_currency")
	if currency and currency != frappe.get_cached_value("Company", company, "default_currency"):
		return None
	return {"account": account, "account_currency": currency}


def _fallback_reference(key, sale_id):
	"""A card payment into a bank account must carry a reference for ERPNext; the sale id stands in when none is given."""
	return sale_id if key == "card" else None


def _earlier_payment(request, sale_id, company):
	"""The submitted payment already taken for this request on this sale, or None."""
	if not request:
		return None
	found = frappe.get_all(
		"Payment Entry",
		filters={
			"docstatus": 1,
			"company": company,
			"remarks": ["like", rules.marker_pattern(request, sale_id)],
		},
		pluck="name",
		limit_page_length=1,
	)
	return found[0] if found else None


def _record_payment(target, amount, mode, account, reference, source, request, sale_id) -> str:
	"""Make, save and submit one Payment Entry against the target Sales Invoice. Returns its name."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	today = calc.nairobi_today(_now_utc())
	entry = get_payment_entry(target[0], target[1], party_amount=amount)
	entry.posting_date = today
	entry.mode_of_payment = mode
	entry.paid_to = account["account"]
	if account.get("account_currency"):
		entry.paid_to_account_currency = account["account_currency"]
	entry.paid_amount = amount
	entry.received_amount = amount
	for row in entry.references:
		row.allocated_amount = amount
	if reference:
		entry.reference_no = reference
	entry.reference_date = today
	entry.remarks = rules.payment_remark(
		rules.short_receipt(source.get("doctype"), source.get("name")), request, sale_id
	)
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry.name


def _paid(entry, doctype, id, user) -> dict:
	"""The answer for a payment that is recorded: its name and the sale as the receipt panel shows it now."""
	return {
		"success": True,
		"data": {"payment": entry, "sale": _receipt(frappe.get_doc(doctype, id), user)},
	}


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


def _filters(scope: _Scope, doctype: str):
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
	if doctype == rules.SALES_INVOICE:
		# The invoice ERPNext makes when a till is closed holds that shift's receipts, which are listed themselves.
		# It is recognised the way the rest of the app does (sales_api: is_consolidated) and by the receipts that
		# point at it (consolidated_invoice, as sales_figures.py does), so it is hidden either way
		if frappe.db.has_column(rules.SALES_INVOICE, "is_consolidated"):
			conditions.append("IFNULL(d.is_consolidated, 0) = 0")
		conditions.append(
			"NOT EXISTS (SELECT 1 FROM `tabPOS Invoice` mp WHERE mp.consolidated_invoice = d.name)"
		)
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
	return conditions, values


# The columns of a list row, the same for both kinds of document so the two can be joined. The POS Invoice part
# also reads the consolidated invoice it was merged into, for `effective_outstanding` (see sales_list_calc).
_COLUMNS = (
	"SELECT '{doctype}' AS doctype, d.name AS name, d.posting_date AS posting_date, d.posting_time AS posting_time, "
	"d.customer AS customer, d.customer_name AS customer_name, d.owner AS owner, d.grand_total AS grand_total, "
	"d.outstanding_amount AS outstanding_amount, {effective} AS effective_outstanding, d.is_return AS is_return, "
	"d.due_date AS due_date, c.payment_terms AS payment_terms, {merged_into} AS merged_into, "
	"{merged_outstanding} AS merged_outstanding "
	"FROM `tab{doctype}` d LEFT JOIN `tabCustomer` c ON c.name = d.customer {merged_join}"
)

# The same rule as sales_list_calc.effective_outstanding, for the filters and the summary that run in SQL
_EFFECTIVE_POS = (
	"CASE WHEN d.is_return = 1 OR IFNULL(d.consolidated_invoice, '') = '' OR m.outstanding_amount IS NULL "
	"THEN d.outstanding_amount WHEN m.outstanding_amount <= 0 THEN 0 "
	"WHEN d.outstanding_amount > 0 THEN LEAST(d.outstanding_amount, m.outstanding_amount) "
	"ELSE d.outstanding_amount END"
)


def _sale_part(doctype: str) -> str:
	"""The SELECT ... FROM part for one kind of document."""
	if doctype == rules.POS_INVOICE:
		return _COLUMNS.format(
			doctype=doctype,
			effective=_EFFECTIVE_POS,
			merged_into="IFNULL(d.consolidated_invoice, '')",
			merged_outstanding="m.outstanding_amount",
			merged_join="LEFT JOIN `tabSales Invoice` m ON m.name = d.consolidated_invoice",
		)
	return _COLUMNS.format(
		doctype=doctype,
		effective="d.outstanding_amount",
		merged_into="''",
		merged_outstanding="NULL",
		merged_join="",
	)


def _sale_union(scope: _Scope):
	"""SQL listing every sale of both kinds (see the module notes), and its values. Alias it `s`."""
	parts, values = [], {}
	for doctype in rules.KINDS:
		conditions, values = _filters(scope, doctype)
		parts.append(_sale_part(doctype) + " WHERE " + " AND ".join(conditions))
	return " UNION ALL ".join(parts), values


def _tab_where(scope: _Scope) -> str:
	conditions = rules.tab_conditions(scope.tab)
	return (" WHERE " + " AND ".join(conditions)) if conditions else ""


def _sales_page(scope: _Scope, page, page_size) -> dict:
	"""rows, total, page, page_size, pages for the All sales, Unpaid and Returns tabs."""
	union, values = _sale_union(scope)
	where = _tab_where(scope)
	counted = frappe.db.sql(f"SELECT COUNT(*) AS n FROM ({union}) s{where}", values, as_dict=True)
	total = int(counted[0].get("n") or 0) if counted else 0
	numbers = rules.page_numbers(page, page_size, total)

	rows: list[dict] = []
	if total:
		values.update(limit=numbers["page_size"], offset=numbers["offset"])
		found = frappe.db.sql(
			f"SELECT * FROM ({union}) s{where} ORDER BY s.posting_date DESC, s.posting_time DESC, s.name DESC "
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
	union, values = _sale_union(scope)
	return rules.build_summary(
		frappe.db.sql(
			"SELECT s.is_return AS is_return, SUM(s.grand_total) AS total, "
			"SUM(CASE WHEN s.effective_outstanding > 0 THEN s.effective_outstanding ELSE 0 END) AS outstanding "
			f"FROM ({union}) s GROUP BY s.is_return",
			values,
			as_dict=True,
		)
	)


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
	merged_into = (doc.get("consolidated_invoice") or None) if doc.doctype == rules.POS_INVOICE else None
	merged_outstanding = None
	if merged_into:
		merged_outstanding = frappe.db.get_value(rules.SALES_INVOICE, merged_into, "outstanding_amount")
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"merged_into": merged_into,
		"merged_outstanding": merged_outstanding,
		"is_consolidated": doc.get("is_consolidated") if doc.doctype == rules.SALES_INVOICE else None,
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
