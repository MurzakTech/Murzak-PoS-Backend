"""
Handing a till over to the next cashier (docs/SHIFTS.md, part 4).

`hand_over_till` counts and closes the outgoing shift through the existing closing path (`till_api`), then opens the
next person's shift on the same till with the counted cash as the float. Without confirm it only shows the count and
who would take over, and changes nothing.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api import handover_rules as rules
from murzak_pos.api import role_tiers, sales_api, till_api
from murzak_pos.api import till_calc as calc
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import get_user_companies

PASS_THROUGH = till_api.PASS_THROUGH


def _refusal_text(code: str) -> str:
	if code == "same_person":
		return _("Choose the person taking over; it cannot be the same cashier.")
	if code == "person_disabled":
		return _("That person's account is switched off.")
	if code == "other_business":
		return _("That person does not work for this business.")
	return _("That person was not found.")


def _people_of(company, leaving) -> list[dict]:
	"""Active people of a business who could take a till, oldest account first. The leaving cashier is left out."""
	users = set(
		frappe.get_all("User Permission", filters={"allow": "Company", "for_value": company}, pluck="user")
	)
	if frappe.db.has_column("User", "custom_company"):
		users.update(frappe.get_all("User", filters={"custom_company": company}, pluck="name"))
	users.discard(leaving)
	users.discard("Administrator")
	users.discard("Guest")
	if not users:
		return []
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", sorted(users)], "enabled": 1, "user_type": "System User"},
		fields=["name", "full_name"],
		order_by="creation asc",
	)
	return [{"user": r["name"], "name": r.get("full_name") or r["name"]} for r in rows]


@frappe.whitelist()
def list_handover_people(session):
	"""The people the till in `session` can be handed to."""
	ensure_record_access("POS Opening Entry", session)
	tier = till_api._tier()
	entry, refusal = till_api.closable_entry(session, frappe.session.user, tier)
	if refusal:
		return refusal
	return {"success": True, "data": {"people": _people_of(entry["company"], entry["user"])}}


def _recipient_problem(entry, to_user):
	exists = bool(to_user) and bool(frappe.db.exists("User", to_user))
	enabled = exists and bool(frappe.db.get_value("User", to_user, "enabled"))
	same_business = exists and entry["company"] in get_user_companies(to_user)
	return rules.refuse_recipient(entry["user"], to_user, exists, enabled, same_business)


def _keep_note(entry_names, text) -> None:
	"""The handover line goes on both shifts as a comment, so either report can show it."""
	for name in entry_names:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "POS Opening Entry",
				"reference_name": name,
				"content": text,
			}
		).insert(ignore_permissions=True)


@frappe.whitelist()
def hand_over_till(session, to_user, counted=None, confirm=False, note=None):
	"""
	Close `session` with the counted cash and open the next shift for `to_user` with that cash as the float.

	Without confirm nothing changes: the answer shows the count against what should be there and who takes over.
	"""
	ensure_record_access("POS Opening Entry", session)
	tier = till_api._tier()
	caller = frappe.session.user
	confirm = calc.as_bool(confirm)
	try:
		counted_rows = calc.parse_counted(counted)
	except calc.TillError as error:
		return till_api._refusal(error.code, error.detail)

	entry, refusal = till_api.closable_entry(session, caller, tier)
	if refusal:
		return refusal
	problem = _recipient_problem(entry, to_user)
	if problem:
		return till_api._refusal(problem, _refusal_text(problem))

	try:
		lines = till_api._expected_lines(entry)
		figures = calc.close_figures(lines, calc.match_counted(lines, counted_rows))
	except calc.TillError as error:
		return till_api._refusal(error.code, error.detail)
	except PASS_THROUGH:
		raise
	except Exception:
		till_api._fail("Till count failed", _("Could not load the till. Please try again."))

	incoming = {"user": to_user, "name": till_api._person(to_user)}
	if not confirm:
		return {"success": True, "data": {"closed": False, "handover_to": incoming, **figures}}
	if figures["needs_cash_count"]:
		return till_api._refusal("cash_count_required")

	closed = till_api._close_session(entry, figures)
	if not closed.get("success"):
		return closed
	data = closed["data"]
	data["handover_to"] = incoming
	if data.get("outcome") != "closed":
		# Closing carries on in the background; the next shift cannot open until it is done
		data["next_shift"] = None
		closed["message"] = _("The shift is closing. Open {0}'s shift once it has closed.").format(
			incoming["name"]
		)
		return closed
	return _open_next(entry, to_user, incoming, figures, closed, rules.clean_note(note))


def _open_next(entry, to_user, incoming, figures, closed, note) -> dict:
	mode, amount = rules.cash_float(figures["lines"])
	balances = [{"mode_of_payment": mode, "opening_amount": amount}] if mode else None
	data = closed["data"]
	try:
		with sales_api._as_administrator():
			name = sales_api._get_or_create_pos_opening_entry(
				pos_profile=entry["pos_profile"],
				company=entry["company"],
				user=to_user,
				balance_details=balances,
			)
		_keep_note(
			[entry["name"], name],
			rules.handover_text(
				till_api._person(entry["user"]),
				incoming["name"],
				amount,
				figures["difference"],
				note,
			),
		)
		frappe.db.commit()
	except PASS_THROUGH:
		raise
	except Exception:
		frappe.log_error(title="Till handover: next shift not opened", message=frappe.get_traceback())
		data["next_shift"] = None
		closed["message"] = _(
			"The shift is closed, but {0}'s shift could not be opened. Open it with a float of {1}."
		).format(incoming["name"], f"{amount:,.2f}")
		data["float"] = amount
		return closed
	data["next_shift"] = {"name": name, "user": to_user, "person": incoming["name"], "float": amount}
	closed["message"] = _("Till handed over to {0}.").format(incoming["name"])
	return closed
