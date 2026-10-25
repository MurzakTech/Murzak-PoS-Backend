"""
Cash put into or taken out of the till during a shift (shift management, part 1).

Two calls, in the contract docs/SHIFTS.md:

- `record_cash_movement`: a float topped up (Paid In), money paid out of the drawer (Paid Out: petty cash, a supplier,
  a staff advance) or notes moved to the safe (Safe Drop), each with a reason.
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
		"POS Opening Entry", session, [*till_api.SESSION_FIELDS, "status", "docstatus"], as_dict=True
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
	entry, refusal = _session_for(session)
	if refusal:
		return refusal
	if entry["status"] != "Open" or entry["docstatus"] != 1:
		return _refusal("not_open")
	try:
		movement = rules.clean(kind, amount, reason, note, client_id)
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
			"reasons": {kind: list(reasons) for kind, reasons in rules.REASONS_BY_KIND.items()},
		},
	}
