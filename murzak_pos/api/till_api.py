"""
The Till open panel and the count at the end of the shift (ticket P3-6).

Two new calls, in the contract docs/redesign/P3-6-contract.md:

- `get_till_state`: is a till session open for me, what should be in it, how many bills are on hold, the last sales.
- `close_till`: the shift-end count. By default it only shows the count against what should be there. With
  confirm true it closes the session.

Nothing existing is changed. The arithmetic is in till_calc.py (no database). The closing itself is the existing
`sales_api.close_pos_opening_entry`, so a POS Closing Entry is made and submitted exactly as it is today; this file
only adds the counted amounts to that entry before it is submitted.

Which sales belong to a session is the rule the till already uses (`sales_api._check_pos_opening_entry_has_invoices`):
submitted Sales Invoices that are till sales, and POS Invoices not yet merged, made by the cashier who opened the
session, on its till (POS Profile), since it opened.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api import role_tiers, sales_api
from murzak_pos.api import till_calc as calc
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import get_user_companies, resolve_company

# The two kinds of till sale. Names are fixed here; they are never taken from a request.
SALE_DOCTYPES = ("Sales Invoice", "POS Invoice")
SESSION_FIELDS = ["name", "user", "company", "pos_profile", "period_start_date", "posting_date"]
# Errors Frappe raises on purpose, with a message for the person: pass them on as they are
PASS_THROUGH = (frappe.PermissionError, frappe.AuthenticationError, frappe.ValidationError)


def _labels() -> dict:
	return {
		calc.CASH: _("Cash"),
		calc.MPESA: _("M-Pesa"),
		calc.CARD: _("Card"),
		calc.CREDIT: _("On credit"),
		calc.OTHER: _("Other"),
	}


def _label(line: dict) -> str:
	"""M-Pesa, Cash, Card, On credit; Bank Draft, Cheque and Wire Transfer read Other; any other mode keeps its name."""
	labels = _labels()
	if line["key"] == calc.OTHER and not calc.is_grouped_other(line["mode"]):
		return line["mode"]
	return labels[line["key"]]


def _refusal(code: str, detail: str = "", **data) -> dict:
	"""An answer that says no in words. `error_type` is the code; the request itself did not fail."""
	messages = {
		"not_found": _("Till session {0} was not found."),
		"already_closed": _("This shift is already closed."),
		"not_open": _("This shift is not open (status: {0}). Only an open shift can be closed."),
		"cash_count_required": _("Please count the cash in the till first."),
		"bad_counted": _("Counted amounts could not be read. Please try again."),
		"negative_count": _("Counted amounts cannot be negative."),
		"unknown_mode": _("{0} is not one of the payment methods used in this till session."),
		"duplicate_mode": _("{0} was counted twice."),
	}
	message = messages.get(code, detail or _("Could not close the till. Please try again."))
	if "{0}" in message:
		message = message.format(detail)
	result = {"success": False, "message": message, "error_type": code}
	if data:
		result["data"] = data
	return result


# --- reading the database (small functions, one job each, so tests can replace them) ----------------


def _tier() -> str:
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return tier


def _open_sessions(company, caller, tier, pos_profile, warehouse) -> list[dict]:
	"""Open till sessions, newest first. A cashier's are their own; a manager's are those of the business."""
	filters = {"status": "Open", "docstatus": 1}
	if tier == role_tiers.CASHIER:
		filters["user"] = caller
	else:
		filters["company"] = company
	if pos_profile:
		filters["pos_profile"] = pos_profile
	elif warehouse:
		profiles = frappe.get_all("POS Profile", filters={"warehouse": warehouse}, pluck="name")
		filters["pos_profile"] = ["in", profiles or [""]]
	return frappe.get_all(
		"POS Opening Entry", filters=filters, fields=SESSION_FIELDS, order_by="period_start_date desc"
	)


def _person(user) -> str:
	return frappe.db.get_value("User", user, "full_name") or user


def _opening_rows(session_name) -> list[dict]:
	doc = frappe.get_doc("POS Opening Entry", session_name)
	return [
		{"mode_of_payment": r.mode_of_payment, "opening_amount": r.opening_amount}
		for r in doc.balance_details
	]


def _scope(doctype: str) -> str:
	"""The SQL conditions that pick one session's sales out of a kind of sale document (alias d)."""
	conditions = [
		"d.docstatus = 1",
		"d.owner = %(user)s",
		"d.pos_profile = %(profile)s",
		"TIMESTAMP(d.posting_date, d.posting_time) >= %(start)s",
	]
	if doctype == "POS Invoice":
		conditions.append("IFNULL(d.consolidated_invoice, '') = ''")
	else:
		conditions.append("d.is_pos = 1")
		# Only the columns this ERPNext has, as sales_api does
		if frappe.db.has_column("Sales Invoice", "is_created_using_pos"):
			conditions.append("d.is_created_using_pos = 1")
		if frappe.db.has_column("Sales Invoice", "pos_closing_entry"):
			conditions.append("IFNULL(d.pos_closing_entry, '') = ''")
		if frappe.db.has_column("Sales Invoice", "is_consolidated"):
			conditions.append("d.is_consolidated = 0")
	return " AND ".join(conditions)


def _session_values(session) -> dict:
	return {"user": session["user"], "profile": session["pos_profile"], "start": session["period_start_date"]}


def _session_figures(session) -> dict:
	"""The raw rows of one session: opening cash, payments, change, credit and Payment Entries."""
	values = _session_values(session)
	payments, change, credit = [], [], 0.0
	for doctype in SALE_DOCTYPES:
		where = _scope(doctype)
		payments += frappe.db.sql(
			f"SELECT d.is_return, p.mode_of_payment, SUM(p.base_amount) AS amount FROM `tab{doctype}` d "
			f"JOIN `tabSales Invoice Payment` p ON p.parent = d.name AND p.parenttype = '{doctype}' "
			f"WHERE {where} GROUP BY d.is_return, p.mode_of_payment",
			values,
			as_dict=True,
		)
		change += frappe.db.sql(
			f"SELECT d.is_return, SUM(d.base_change_amount) AS `change` FROM `tab{doctype}` d "
			f"WHERE {where} GROUP BY d.is_return",
			values,
			as_dict=True,
		)
		owed = frappe.db.sql(
			f"SELECT SUM(d.outstanding_amount) AS owed FROM `tab{doctype}` d "
			f"WHERE {where} AND d.is_return = 0 AND d.outstanding_amount > 0",
			values,
			as_dict=True,
		)
		credit += calc.num(owed[0].get("owed")) if owed else 0.0
	# Payment Entries by the cashier since the session opened: Receive is money in, Pay is money out
	entries = frappe.db.sql(
		"SELECT payment_type, mode_of_payment, SUM(base_paid_amount) AS amount FROM `tabPayment Entry` "
		"WHERE docstatus = 1 AND owner = %(user)s AND creation >= %(start)s "
		"AND payment_type IN ('Receive', 'Pay') GROUP BY payment_type, mode_of_payment",
		values,
		as_dict=True,
	)
	return {
		"opening": _opening_rows(session["name"]),
		"payments": payments,
		"change": [{"is_return": r.get("is_return"), "change": r.get("change")} for r in change],
		"credit": credit,
		"entries": entries,
	}


def _mode_types() -> dict:
	return {r["name"]: r.get("type") for r in frappe.get_all("Mode of Payment", fields=["name", "type"])}


def _expected_lines(session) -> list[dict]:
	"""The session's lines with their labels."""
	figures = _session_figures(session)
	lines = calc.build_expected(
		figures["opening"],
		figures["payments"],
		figures["change"],
		figures["entries"],
		figures["credit"],
		_mode_types(),
	)
	for line in lines:
		line["label"] = _label(line)
	return lines


def _held_count(company, warehouse) -> int:
	rows = frappe.get_all("POS Held Sale", filters={"company": company}, fields=["warehouse"])
	return calc.count_held(rows, warehouse)


def _recent_sales(session) -> list[dict]:
	values = _session_values(session)
	rows = []
	for doctype in SALE_DOCTYPES:
		rows += frappe.db.sql(
			f"SELECT d.name, d.creation, d.posting_time, d.customer, d.customer_name, d.grand_total, "
			f"d.is_return, d.outstanding_amount FROM `tab{doctype}` d WHERE {_scope(doctype)} "
			f"ORDER BY d.creation DESC LIMIT {int(calc.RECENT_SALES)}",
			values,
			as_dict=True,
		)
	return calc.recent_sales(rows)


def _session_view(session) -> dict:
	opened = session.get("period_start_date")
	return {
		"name": session["name"],
		"opened_on": calc.day_of(opened),
		"opened_at": calc.clock(opened),
		"cashier": session["user"],
		"cashier_name": _person(session["user"]),
		"pos_profile": session["pos_profile"],
		"opening": [
			{"mode": r["mode_of_payment"], "amount": calc.money(r["opening_amount"])}
			for r in _opening_rows(session["name"])
		],
	}


def _fail(title: str, message: str):
	frappe.log_error(title=title, message=frappe.get_traceback())
	frappe.throw(message)


# --- the calls --------------------------------------------------------------------------------------


@frappe.whitelist()
def get_till_state(pos_profile=None, warehouse=None):
	"""The caller's till session and what the Till open panel shows (see the contract)."""
	ensure_record_access("POS Profile", pos_profile)
	ensure_record_access("Warehouse", warehouse)
	tier = _tier()
	caller = frappe.session.user
	company = None if tier == role_tiers.CASHIER else resolve_company()

	try:
		session = calc.pick_session(
			_open_sessions(company, caller, tier, pos_profile, warehouse), caller, tier
		)
		held_company = company or (session or {}).get("company") or resolve_company()
		if session is None:
			data = {
				"state": "closed",
				"session": None,
				"expected": [],
				"cash_in_till": None,
				"held_count": 0,
				"recent_sales": [],
			}
			errors = []
			try:
				data["held_count"] = _held_count(held_company, warehouse)
			except Exception:
				frappe.log_error(title="Till state: held count failed", message=frappe.get_traceback())
				data["held_count"], errors = None, ["held_count"]
			data["errors"] = errors
			return {"success": True, "data": data}

		lines = _expected_lines(session)
		errors, recent, held = [], [], 0
		try:
			recent = _recent_sales(session)
		except Exception:
			frappe.log_error(title="Till state: recent sales failed", message=frappe.get_traceback())
			errors.append("recent_sales")
		try:
			held = _held_count(session["company"], warehouse)
		except Exception:
			frappe.log_error(title="Till state: held count failed", message=frappe.get_traceback())
			held = None
			errors.append("held_count")
		return {
			"success": True,
			"data": {
				"state": "open",
				"session": _session_view(session),
				"expected": lines,
				"cash_in_till": calc.cash_in_till(lines),
				"held_count": held,
				"recent_sales": recent,
				"errors": errors,
			},
		}
	except PASS_THROUGH:
		raise
	except Exception:
		_fail("Till state failed", _("Could not load the till. Please try again."))


@frappe.whitelist()
def close_till(session, counted=None, confirm=False):
	"""
	The shift-end count for a till session (see the contract).

	Without confirm it only shows the count against what should be there and changes nothing. With confirm true it
	closes the session through the existing closing path, saving the counted amounts on the closing entry.
	"""
	ensure_record_access("POS Opening Entry", session)
	tier = _tier()
	caller = frappe.session.user
	confirm = calc.as_bool(confirm)
	try:
		counted_rows = calc.parse_counted(counted)
	except calc.TillError as error:
		return _refusal(error.code, error.detail)

	entry = frappe.db.get_value(
		"POS Opening Entry",
		session,
		[*SESSION_FIELDS, "status", "docstatus", "pos_closing_entry"],
		as_dict=True,
	)
	if not entry:
		return _refusal("not_found", str(session))
	if caller != "Administrator" and entry["company"] not in get_user_companies(caller):
		frappe.throw(_("This shift belongs to another business."), frappe.PermissionError)
	if caller != "Administrator" and not calc.may_close(entry["user"], caller, tier):
		frappe.throw(
			_("Only the cashier who opened this shift ({0}) or a manager can close it.").format(
				entry["user"]
			),
			frappe.PermissionError,
		)
	if entry["status"] == "Closed":
		return _refusal("already_closed", closing_entry=entry.get("pos_closing_entry"))
	if entry["status"] != "Open" or entry["docstatus"] != 1:
		return _refusal("not_open", str(entry["status"]))

	try:
		lines = _expected_lines(entry)
		figures = calc.close_figures(lines, calc.match_counted(lines, counted_rows))
	except calc.TillError as error:
		return _refusal(error.code, error.detail)
	except PASS_THROUGH:
		raise
	except Exception:
		_fail("Till count failed", _("Could not load the till. Please try again."))

	if not confirm:
		return {"success": True, "data": {"closed": False, **figures}}
	if figures["needs_cash_count"]:
		return _refusal("cash_count_required")
	return _close_session(entry, figures)


def _close_session(entry, figures) -> dict:
	"""
	Close through the existing path, with the counts.

	`sales_api.close_pos_opening_entry(do_not_submit=True)` makes the POS Closing Entry exactly as it does today (and
	deals with a close that is already running, failed or finished). If it made a new draft, the counted amounts are
	written into the draft's payment table and the entry is then submitted the way sales_api submits it.
	"""
	result = sales_api.close_pos_opening_entry(entry["name"], do_not_submit=True)
	if not result.get("success"):
		return {
			"success": False,
			"message": result.get("message") or _("Could not close the till. Please try again."),
			"error_type": result.get("error_type") or "close_failed",
		}
	closing = (result.get("data") or {}).get("closing_entry") or {}
	outcome = (result.get("data") or {}).get("outcome")
	if closing.get("docstatus") != 0:
		# Nothing new was made: this shift was already closed, or is closing, in the background
		return {
			"success": True,
			"message": result.get("message"),
			"data": {
				"closed": outcome in ("closed", "closing", "already_closed"),
				"outcome": outcome,
				"closing_entry": closing.get("name"),
				**figures,
			},
		}
	try:
		doc = frappe.get_doc("POS Closing Entry", closing["name"])
		_write_counts(doc, figures["lines"])
		doc.flags.ignore_permissions = True
		doc.save()
		frappe.db.commit()
		# Submitting merges the shift's receipts, which needs full rights (same as sales_api)
		with sales_api._as_administrator():
			doc.submit()
		frappe.db.commit()
		doc.reload()
	except Exception as error:
		return sales_api._shift_error("close", entry["name"], error)
	queued = doc.status == "Queued"
	return {
		"success": True,
		"message": _("This shift is being closed in the background. It can take a minute.")
		if queued
		else _("Till closed. The shift has ended."),
		"data": {
			"closed": True,
			"outcome": "closing" if queued else "closed",
			"closing_entry": doc.name,
			**figures,
		},
	}


def _write_counts(doc, figure_lines) -> None:
	"""Put the counted amounts and differences in the closing entry's payment table."""
	changes, additions = calc.reconciliation_changes(
		[row.mode_of_payment for row in doc.get("payment_reconciliation") or []], figure_lines
	)
	for row in doc.get("payment_reconciliation") or []:
		values = changes.get(row.mode_of_payment)
		if values:
			row.closing_amount = values["closing_amount"]
			row.difference = values["difference"]
			row.expected_amount = values["expected_amount"]
	for values in additions:
		doc.append("payment_reconciliation", values)
