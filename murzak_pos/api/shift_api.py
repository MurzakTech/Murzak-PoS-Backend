"""
Cash put into or taken out of the till during a shift (shift management, part 1).

Two calls, in the contract docs/SHIFTS.md:

- `record_cash_movement`: a float topped up (Paid In), money paid out of the drawer (Paid Out: a staff advance, change
  fetched) or notes moved to the safe (Safe Drop), each with a reason. Money spent on the business is an expense
  (expenses_api), and a refund is paid by refund_api; both take their cash out of the drawer by themselves.
- `list_cash_movements`: what was recorded in a shift, newest first, with the totals.

The shift-end count (till_api.close_till) adds these to the cash it expects, so a difference at the end is a real
difference and not money that left the drawer unrecorded. A movement is written once and never changed or removed
(the document type refuses it); a mistake is put right with a movement the other way.

Who may record: the cashier who opened the shift, or a manager or owner of the business. The rules (what is a valid
movement) are in cash_movement_rules.py (no database).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from murzak_pos.api import cash_movement_rules as rules
from murzak_pos.api import shift_report_calc as report
from murzak_pos.api import till_api
from murzak_pos.api import till_calc as calc
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import get_user_companies

MOVEMENT_FIELDS = [
	"name",
	"kind",
	"amount",
	"mode_of_payment",
	"reason",
	"note",
	"recorded_by",
	"recorded_at",
	"client_id",
]


def _refusal(code: str, detail: str = "", **data) -> dict:
	"""An answer that says no in words. `error_type` is the code; the request itself did not fail."""
	messages = {
		"not_found": _("Till session {0} was not found."),
		"not_open": _("This shift is not open, so cash cannot be recorded in it."),
		"no_closing_entry": _(
			"This shift is closed but its closing record is not ready yet. Try again in a minute."
		),
		"bad_kind": _("Choose Paid In, Paid Out or Safe Drop."),
		"bad_amount": _("Enter an amount above zero."),
		"too_large": _("That amount is too large."),
		"bad_reason": _("{0} is not a reason that can be used here."),
		"note_required": _("Please say what the money was for."),
		"bad_client_id": _("The till's reference for this movement could not be read."),
		"more_than_in_till": _("That is more than the till holds. Expected cash in the till is {0}."),
		"no_cash_line": _("This till has no cash to move."),
	}
	message = messages.get(code, detail or _("Could not record the cash. Please try again."))
	if "{0}" in message:
		message = message.format(detail)
	result = {"success": False, "message": message, "error_type": code}
	if data:
		result["data"] = data
	return result


def _movement_view(row) -> dict:
	return {
		"id": row["name"],
		"kind": row["kind"],
		"amount": calc.money(row["amount"]),
		"mode": row["mode_of_payment"],
		"reason": row["reason"],
		"note": row.get("note") or "",
		"recorded_by": row["recorded_by"],
		"recorded_by_name": till_api._person(row["recorded_by"]),
		"recorded_at": calc.clock(row["recorded_at"]),
		"recorded_on": calc.day_of(row["recorded_at"]),
	}


def _session_for(session):
	"""The session as a dict after the checks every call here makes, or a refusal answer."""
	tier = till_api._tier()
	caller = frappe.session.user
	entry = frappe.db.get_value(
		"POS Opening Entry",
		session,
		[*till_api.SESSION_FIELDS, "status", "docstatus", "pos_closing_entry"],
		as_dict=True,
	)
	if not entry:
		return None, _refusal("not_found", str(session))
	if caller != "Administrator" and entry["company"] not in get_user_companies(caller):
		frappe.throw(_("This shift belongs to another business."), frappe.PermissionError)
	if caller != "Administrator" and not calc.may_close(entry["user"], caller, tier):
		frappe.throw(
			_("Only the cashier who opened this shift ({0}) or a manager can record cash in it.").format(
				entry["user"]
			),
			frappe.PermissionError,
		)
	return entry, None


def _totals(rows) -> dict:
	totals = {kind: 0.0 for kind in rules.KINDS}
	for row in rows:
		totals[row["kind"]] = totals.get(row["kind"], 0.0) + calc.num(row["amount"])
	return {kind: calc.money(value) for kind, value in totals.items()}


@frappe.whitelist()
def record_cash_movement(session, kind, amount, reason=None, note=None, client_id=None):
	"""
	Record cash put into or taken out of the till in an open shift (see the contract).

	Sending the same `client_id` again after a dropped connection returns the movement already recorded and records
	nothing new. Money leaving the till cannot be more than the till is expected to hold.
	"""
	ensure_record_access("POS Opening Entry", session)
	return record_movement(session, kind, amount, reason, note, client_id)


def record_movement(session, kind, amount, reason=None, note=None, client_id=None, system=False):
	"""
	The work of `record_cash_movement`. With `system` set it also accepts the reasons the system writes itself (an
	expense, a refund); only the calls that record those (expenses_api, refund_api) set it, never a request.
	"""
	entry, refusal = _session_for(session)
	if refusal:
		return refusal
	if entry["status"] != "Open" or entry["docstatus"] != 1:
		return _refusal("not_open")
	try:
		movement = rules.clean(kind, amount, reason, note, client_id, allow_system=system)
	except rules.MovementError as error:
		return _refusal(error.code, error.detail)

	if movement["client_id"]:
		again = frappe.db.get_value(
			"Till Cash Movement",
			{"company": entry["company"], "client_id": movement["client_id"]},
			[*MOVEMENT_FIELDS, "pos_opening_entry"],
			as_dict=True,
		)
		if again and again["pos_opening_entry"] == entry["name"]:
			return {
				"success": True,
				"message": _("This cash movement was already recorded."),
				"data": {"movement": _movement_view(again), "duplicate": True},
			}

	# Two people recording at once must each see the other's movement before the "more than the till holds" check
	frappe.db.sql("SELECT name FROM `tabPOS Opening Entry` WHERE name = %s FOR UPDATE", entry["name"])
	try:
		lines = till_api._expected_lines(entry)
		cash = next((line for line in lines if line["key"] == calc.CASH), None)
		if cash is None:
			return _refusal("no_cash_line")
		if rules.exceeds_till(movement["kind"], movement["amount"], calc.cash_in_till(lines)):
			return _refusal("more_than_in_till", f"{calc.money(calc.cash_in_till(lines)):,.2f}")
		doc = frappe.get_doc(
			{
				"doctype": "Till Cash Movement",
				"pos_opening_entry": entry["name"],
				"company": entry["company"],
				"pos_profile": entry["pos_profile"],
				"client_id": movement["client_id"],
				"kind": movement["kind"],
				"amount": movement["amount"],
				"mode_of_payment": cash["mode"],
				"reason": movement["reason"],
				"note": movement["note"],
				"recorded_by": frappe.session.user,
				"recorded_at": now_datetime(),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		frappe.db.commit()
		lines = till_api._expected_lines(entry)
	except frappe.ValidationError:
		raise
	except Exception:
		till_api._fail("Cash movement failed", _("Could not record the cash. Please try again."))

	row = {field: doc.get(field) for field in [*MOVEMENT_FIELDS, "pos_opening_entry"]}
	return {
		"success": True,
		"message": _("Cash recorded."),
		"data": {
			"movement": _movement_view(row),
			"duplicate": False,
			"cash_in_till": calc.cash_in_till(lines),
		},
	}


@frappe.whitelist()
def list_cash_movements(session):
	"""What was recorded in a shift (open or closed), oldest first, with the total of each kind."""
	ensure_record_access("POS Opening Entry", session)
	entry, refusal = _session_for(session)
	if refusal:
		return refusal
	rows = frappe.get_all(
		"Till Cash Movement",
		filters={"pos_opening_entry": entry["name"]},
		fields=MOVEMENT_FIELDS,
		order_by="recorded_at asc, creation asc",
	)
	return {
		"success": True,
		"data": {
			"session": entry["name"],
			"movements": [_movement_view(row) for row in rows],
			"totals": _totals(rows),
			"reasons": {kind: list(reasons) for kind, reasons in rules.OFFERED_BY_KIND.items()},
		},
	}


def _closed_scope(doctype: str) -> str:
	"""The SQL conditions for the sales of a shift that has ended: its cashier and till between its start and end,
	merged or not (alias d)."""
	conditions = [
		"d.docstatus = 1",
		"d.owner = %(user)s",
		"d.pos_profile = %(profile)s",
		"TIMESTAMP(d.posting_date, d.posting_time) >= %(start)s",
		"TIMESTAMP(d.posting_date, d.posting_time) <= %(end)s",
	]
	if doctype != "POS Invoice":
		conditions.append("d.is_pos = 1")
		# The invoice made when receipts are merged is not a sale of its own
		if frappe.db.has_column("Sales Invoice", "is_consolidated"):
			conditions.append("d.is_consolidated = 0")
	return " AND ".join(conditions)


def _sales_figures(entry, end=None) -> dict:
	"""
	The sales of a shift from its sale documents: while open (`end` None) the same ones the shift count reads; once
	closed, everything its cashier sold on its till between its start and `end`.
	"""
	values = {**till_api._session_values(entry), "end": end}
	found = {
		"count": 0,
		"gross": 0.0,
		"returns_count": 0,
		"returns_total": 0.0,
		"net": 0.0,
		"tax": 0.0,
		"discounts": 0.0,
	}
	for doctype in till_api.SALE_DOCTYPES:
		where = till_api._scope(doctype) if end is None else _closed_scope(doctype)
		rows = frappe.db.sql(
			f"SELECT SUM(d.is_return = 0) AS sales_count, "
			f"SUM(CASE WHEN d.is_return = 0 THEN d.base_grand_total ELSE 0 END) AS gross, "
			f"SUM(d.is_return = 1) AS returns_count, "
			f"SUM(CASE WHEN d.is_return = 1 THEN -d.base_grand_total ELSE 0 END) AS returns_total, "
			f"SUM(d.base_net_total) AS net, SUM(d.base_total_taxes_and_charges) AS tax, "
			f"SUM(CASE WHEN d.is_return = 0 THEN d.base_discount_amount ELSE 0 END) AS discounts "
			f"FROM `tab{doctype}` d WHERE {where}",
			values,
			as_dict=True,
		)
		row = rows[0] if rows else {}
		found["count"] += calc.num(row.get("sales_count"))
		found["gross"] += calc.num(row.get("gross"))
		found["returns_count"] += calc.num(row.get("returns_count"))
		found["returns_total"] += calc.num(row.get("returns_total"))
		found["net"] += calc.num(row.get("net"))
		found["tax"] += calc.num(row.get("tax"))
		found["discounts"] += calc.num(row.get("discounts"))

	# A return made through the sales return call is not itself a till sale (it has no till); it belongs to the shift
	# of the cashier who made it, against a sale of that till
	until = "AND TIMESTAMP(r.posting_date, r.posting_time) <= %(end)s " if end is not None else ""
	rows = frappe.db.sql(
		"SELECT COUNT(*) AS returns_count, SUM(-r.base_grand_total) AS returns_total, "
		"SUM(r.base_net_total) AS net, SUM(r.base_total_taxes_and_charges) AS tax "
		"FROM `tabSales Invoice` r JOIN `tabSales Invoice` o ON o.name = r.return_against "
		"WHERE r.docstatus = 1 AND r.is_return = 1 AND r.is_pos = 0 AND r.owner = %(user)s "
		f"AND o.pos_profile = %(profile)s AND TIMESTAMP(r.posting_date, r.posting_time) >= %(start)s {until}",
		values,
		as_dict=True,
	)
	row = rows[0] if rows else {}
	found["returns_count"] += calc.num(row.get("returns_count"))
	found["returns_total"] += calc.num(row.get("returns_total"))
	found["net"] += calc.num(row.get("net"))
	found["tax"] += calc.num(row.get("tax"))
	return found


def _closed_lines(closing, movement_totals) -> tuple[list[dict], dict]:
	"""A closed shift's lines (as the shift count makes them) and what was counted, from its closing entry."""
	modes = till_api._mode_types()
	lines, counted = [], {}
	cash_moved = False
	for row in closing.get("payment_reconciliation") or []:
		expected = calc.num(row.get("expected_amount"))
		opening = calc.num(row.get("opening_amount"))
		key = calc.classify_method(row.mode_of_payment, modes.get(row.mode_of_payment))
		line = {
			"mode": row.mode_of_payment,
			"key": key,
			"opening": opening,
			"sales": expected - opening,
			"paid_in": 0.0,
			"refunds": 0.0,
			"paid_out": 0.0,
			"expected": expected,
			"counts_cash": key != calc.CREDIT,
		}
		if key == calc.CASH and not cash_moved:
			# Cash put in and taken out is not sales: move it out of the figure the closing entry calls sales
			cash_moved = True
			line["paid_in"] = calc.num(movement_totals.get(rules.PAID_IN))
			line["paid_out"] = calc.num(movement_totals.get(rules.PAID_OUT)) + calc.num(
				movement_totals.get(rules.SAFE_DROP)
			)
			line["sales"] = expected - opening - line["paid_in"] + line["paid_out"]
		line["label"] = till_api._label(line)
		lines.append(line)
		# A till that does not record a count leaves both the count and the difference at zero
		if calc.num(row.get("closing_amount")) or calc.num(row.get("difference")):
			counted[row.mode_of_payment] = calc.num(row.get("closing_amount"))
	return lines, counted


def _movement_rows(entry) -> list[dict]:
	return frappe.get_all(
		"Till Cash Movement",
		filters={"pos_opening_entry": entry["name"]},
		fields=MOVEMENT_FIELDS,
		order_by="recorded_at asc, creation asc",
	)


@frappe.whitelist()
def get_shift_report(session):
	"""
	The report of a shift. While the shift is open it is the X report (the figures so far, nothing is changed or
	closed); once it is closed it is the Z report (with what was counted and any difference). Anyone who may record
	cash in the shift may read it.
	"""
	ensure_record_access("POS Opening Entry", session)
	entry, refusal = _session_for(session)
	if refusal:
		return refusal
	moves = _movement_rows(entry)
	totals = _totals(moves)
	closing = None
	try:
		if entry["status"] == "Open" and entry["docstatus"] == 1:
			kind = report.X
			lines = till_api._expected_lines(entry)
			counted_by_mode = {}
			sales = _sales_figures(entry)
		else:
			kind = report.Z
			closing = (
				frappe.get_doc("POS Closing Entry", entry["pos_closing_entry"])
				if entry.get("pos_closing_entry")
				else None
			)
			if closing is None or closing.docstatus != 1:
				return _refusal("no_closing_entry")
			lines, counted_by_mode = _closed_lines(closing, totals)
			sales = _sales_figures(entry, closing.period_end_date)
	except frappe.ValidationError:
		raise
	except Exception:
		till_api._fail("Shift report failed", _("Could not load the shift report. Please try again."))

	methods = report.method_rows(lines, counted_by_mode)
	cash_counted = next(
		(
			counted_by_mode[line["mode"]]
			for line in lines
			if line["key"] == calc.CASH and line["mode"] in counted_by_mode
		),
		None,
	)
	return {
		"success": True,
		"data": {
			"kind": kind,
			"session": entry["name"],
			"company": entry["company"],
			"pos_profile": entry["pos_profile"],
			"cashier": entry["user"],
			"cashier_name": till_api._person(entry["user"]),
			"opened_on": calc.day_of(entry["period_start_date"]),
			"opened_at": calc.clock(entry["period_start_date"]),
			"closed_on": calc.day_of(closing.period_end_date) if closing else None,
			"closed_at": calc.clock(closing.period_end_date) if closing else None,
			"closing_entry": closing.name if closing else None,
			"sales": report.sales_block(
				sales["count"], sales["gross"], sales["net"], sales["tax"], sales["discounts"]
			),
			"returns": report.returns_block(sales["returns_count"], sales["returns_total"]),
			"credit_sales": report.credit_total(lines),
			"by_method": methods,
			"cash": report.cash_block(lines, totals, cash_counted),
			"totals": report.totals(methods),
			"movements": [_movement_view(row) for row in moves],
			"movement_totals": totals,
		},
	}
