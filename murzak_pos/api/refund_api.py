"""
Paying back a credit note (a sales return) to the customer.

A sales return (`sales_api.create_sales_return`) makes a credit note: the shop owes the customer that money. Until now
nothing recorded paying it, so cash handed over the counter did not lower the cash the shift expects and the count at
the end read over by the refund.

`pay_refund` settles the credit note and, for cash, takes the money out of a till's drawer in an open shift as a cash
movement (shift_api) so the shift's count agrees:

- cash: needs an open shift (the caller's own, or the one named); the cash movement is recorded with the reason
  "Customer refund" and cannot be more than the drawer holds
- any other way of paying (M-Pesa, bank): the credit note is settled with a Payment Entry in that method

Who may: the people who may record a return (a Sales Manager, Accounts Manager, the owner). A credit note paid in full
cannot be paid again, and a part payment leaves the rest owed.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from murzak_pos.api import cash_movement_rules as movement_rules
from murzak_pos.api import refund_rules as rules
from murzak_pos.api import role_tiers, sales_api, shift_api, till_api
from murzak_pos.api import till_calc as calc
from murzak_pos.api.access_control import ensure_record_access


def _refusal(code: str, detail: str = "", **data) -> dict:
	messages = {
		"not_found": _("Credit note {0} was not found."),
		"not_a_return": _("{0} is not a sales return."),
		"nothing_to_refund": _("There is nothing left to pay back on this return."),
		"bad_amount": _("Enter an amount above zero."),
		"more_than_owed": _("That is more than is owed on this return ({0})."),
		"bad_mode": _("{0} is not a way of paying that this business uses."),
		"open_shift_required": _("Open a shift first, so the cash comes out of a till."),
		"no_account": _("{0} has no account set up in this business, so it cannot be used to pay a refund."),
	}
	message = messages.get(code, detail or _("Could not pay the refund. Please try again."))
	if "{0}" in message:
		message = message.format(detail)
	result = {"success": False, "message": message, "error_type": code}
	if data:
		result["data"] = data
	return result


def _open_shift(company: str, session: str | None):
	"""The shift the cash comes out of: the one named, or else the caller's own open shift."""
	caller = frappe.session.user
	if session:
		ensure_record_access("POS Opening Entry", session)
		return session
	tier = till_api._tier()
	found = till_api._open_sessions(company, caller, tier, None, None)
	mine = [row for row in found if row["user"] == caller] or found
	return mine[0]["name"] if mine else None


def _payment_entry(credit_note, amount: float, mode: str):
	"""Pay the customer back with a Payment Entry against the credit note, in the way of paying named."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	account = get_bank_cash_account(mode, credit_note.company) or {}
	if not account.get("account"):
		raise rules.RefundError("no_account", mode)
	entry = get_payment_entry(
		"Sales Invoice", credit_note.name, party_amount=-amount, bank_account=account["account"]
	)
	entry.mode_of_payment = mode
	entry.paid_from = account["account"]
	entry.reference_no = credit_note.name
	entry.reference_date = nowdate()
	# Without custom_remarks ERPNext writes its own remarks over ours
	entry.custom_remarks = 1
	entry.remarks = rules.remark_for(credit_note.name)
	entry.flags.ignore_permissions = True
	entry.insert()
	entry.submit()
	return entry


def pay_out(credit_note_name: str, mode_of_payment: str | None = None, amount=None, session=None) -> dict:
	"""Pay a credit note back (the work of `pay_refund`, also called when a return is made with a refund)."""
	if not frappe.db.exists("Sales Invoice", credit_note_name):
		return _refusal("not_found", str(credit_note_name))
	credit_note = frappe.get_doc("Sales Invoice", credit_note_name)
	if not credit_note.is_return or credit_note.docstatus != 1:
		return _refusal("not_a_return", str(credit_note_name))
	try:
		pay = rules.amount_to_pay(credit_note.outstanding_amount, amount)
	except rules.RefundError as error:
		return _refusal(error.code, error.detail)

	mode = (mode_of_payment or "Cash").strip()
	mode_type = (
		frappe.db.get_value("Mode of Payment", mode, "type")
		if frappe.db.exists("Mode of Payment", mode)
		else None
	)
	if mode_type is None:
		return _refusal("bad_mode", mode)
	is_cash = calc.classify_method(mode, mode_type) == calc.CASH

	shift = None
	if is_cash:
		shift = _open_shift(credit_note.company, session)
		if not shift:
			return _refusal("open_shift_required")
	try:
		entry = _payment_entry(credit_note, pay, mode)
	except rules.RefundError as error:
		frappe.db.rollback()
		return _refusal(error.code, error.detail)
	except frappe.ValidationError:
		frappe.db.rollback()
		raise

	movement = None
	if shift:
		# The cash leaves the drawer: written down in the shift so its count agrees (refused if the drawer is short)
		movement = shift_api.record_movement(
			shift,
			movement_rules.PAID_OUT,
			pay,
			rules.REASON,
			credit_note.name,
			f"refund-{credit_note.name}-{entry.name}",
			system=True,
		)
		if not movement.get("success"):
			frappe.db.rollback()
			return movement
	frappe.db.commit()
	credit_note.reload()
	return {
		"success": True,
		"message": _("Refund paid."),
		"data": {
			"credit_note": credit_note.name,
			"paid": flt(pay),
			"mode": mode,
			"payment_entry": entry.name,
			"still_owed": rules.owed(credit_note.outstanding_amount),
			"shift": shift,
			"cash_movement": ((movement or {}).get("data") or {}).get("movement"),
		},
	}


@frappe.whitelist()
def pay_refund(credit_note, mode_of_payment=None, amount=None, session=None):
	"""
	Pay a sales return back to the customer (see the module notes). `mode_of_payment` defaults to Cash, `amount` to
	everything still owed, and for cash `session` to the caller's own open shift.
	"""
	invoice_company = frappe.db.get_value("Sales Invoice", credit_note, "company")
	sales_api._check_return_access(invoice_company, credit_note)
	if role_tiers.get_tier() is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	try:
		return pay_out(credit_note, mode_of_payment, amount, session)
	except frappe.ValidationError:
		raise
	except Exception:
		till_api._fail("Refund payment failed", _("Could not pay the refund. Please try again."))
