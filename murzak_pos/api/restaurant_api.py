"""
Restaurants and bars: approving a void (an item taken off a bill after the kitchen or bar already had it) and the
end-of-shift cash-up by waiter. The rules are in restaurant_rules.py.

A void by a cashier or waiter needs a manager or the owner of the same business to approve it with their own password,
at the till. A manager or the owner approves their own. Every approved void is kept as a Till Void, never changed or
deleted, and shows on the waiter cash-up.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, flt, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.password import check_password

from murzak_pos.api import restaurant_rules as rules
from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import get_user_companies, resolve_company

VOID = "Till Void"


def _has_void_doctype() -> bool:
	return bool(frappe.db.table_exists(VOID))


def _full_name(user: str) -> str:
	return frappe.db.get_value("User", user, "full_name") or user


def _approvers(company: str) -> list[dict]:
	"""The enabled managers and owners of the business, who may approve a void."""
	users = frappe.get_all(
		"User", filters={"custom_company": company, "enabled": 1}, fields=["name", "full_name"]
	)
	found = []
	for u in users:
		if u.name in ("Administrator", "Guest"):
			continue
		if rules.can_approve(role_tiers.get_tier(u.name)):
			found.append({"user": u.name, "name": u.full_name or u.name})
	found.sort(key=lambda a: a["name"].lower())
	return found


@frappe.whitelist()
def list_void_approvers(company: str | None = None) -> dict:
	"""Who can approve a void at this business, and whether the signed-in person needs one at all."""
	company = resolve_company(company)
	return {
		"success": True,
		"data": {
			"needs_approver": rules.approver_needed(role_tiers.get_tier()),
			"approvers": _approvers(company),
			"reasons": list(rules.QUICK_REASONS),
		},
	}


def _check_approver(company: str, approver: str | None, password: str | None) -> str:
	"""The user who approves this void: the signed-in manager or owner, else a manager or owner who gave their password."""
	me = frappe.session.user
	if not rules.approver_needed(role_tiers.get_tier(me)):
		return me
	approver = str(approver or "").strip()
	if not approver or not password:
		frappe.throw(
			_("A manager must approve removing an item the kitchen already has."), frappe.ValidationError
		)
	if approver == me:
		frappe.throw(
			_("Someone else must approve your void: a manager or the owner."), frappe.ValidationError
		)
	if not frappe.db.get_value("User", approver, "enabled") or company not in get_user_companies(approver):
		frappe.throw(_("That manager does not work for this business."), frappe.PermissionError)
	if not rules.can_approve(role_tiers.get_tier(approver)):
		frappe.throw(_("Only a manager or the owner can approve a void."), frappe.PermissionError)
	try:
		check_password(approver, password)
	except frappe.AuthenticationError:
		frappe.throw(_("The manager's password is not right. Please try again."), frappe.AuthenticationError)
	return approver


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60 * 60)
def approve_void(
	item_code: str,
	qty,
	reason: str,
	rate=None,
	item_name: str | None = None,
	bill_label: str | None = None,
	warehouse: str | None = None,
	approver: str | None = None,
	password: str | None = None,
	company: str | None = None,
) -> dict:
	"""
	Approve taking an item the kitchen or bar already has off a bill, and keep the record. The till removes the item
	only after this answers. Refused without a reason, or (for a cashier or waiter) without a manager's password.
	"""
	company = resolve_company(company)
	if not _has_void_doctype():
		frappe.throw(_("Voids cannot be recorded on this site yet. Please ask for the server to be updated."))
	if warehouse:
		ensure_record_access("Warehouse", warehouse)
	clean_reason = rules.clean_reason(reason)
	if not clean_reason:
		frappe.throw(_("Say why the item is being removed."), frappe.ValidationError)
	quantity = rules.clean_qty(qty)
	if quantity is None:
		frappe.throw(_("The quantity removed is not right."), frappe.ValidationError)
	item_code = str(item_code or "").strip()
	if not item_code or not frappe.db.exists("Item", item_code):
		frappe.throw(_("That item could not be found."), frappe.ValidationError)
	ensure_record_access("Item", item_code)

	approved_by = _check_approver(company, approver, password)
	doc = frappe.get_doc(
		{
			"doctype": VOID,
			"company": company,
			"warehouse": warehouse or None,
			"bill_label": rules.clean_label(bill_label),
			"item_code": item_code,
			"item_name": str(item_name or "")[:140] or frappe.db.get_value("Item", item_code, "item_name"),
			"qty": quantity,
			"amount": round(quantity * flt(rate), 2),
			"reason": clean_reason,
			"requested_by": frappe.session.user,
			"approved_by": approved_by,
			"voided_at": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)
	return {
		"success": True,
		"message": _("Removed. The void is recorded."),
		"data": {"void": doc.name, "approved_by": approved_by, "approved_by_name": _full_name(approved_by)},
	}


# --- the waiter cash-up -------------------------------------------------------------------------------------

SALE_DOCTYPES = ("POS Invoice", "Sales Invoice")


def _sales_of_day(company: str, day, warehouse: str | None) -> tuple[list[dict], dict[str, str]]:
	"""The day's submitted till sales (both kinds) and which doctype each is."""
	invoices, kind = [], {}
	for doctype in SALE_DOCTYPES:
		filters = {"company": company, "docstatus": 1, "posting_date": day}
		if doctype == "Sales Invoice":
			filters.update({"is_pos": 1, "is_consolidated": 0})
		rows = frappe.get_all(
			doctype,
			filters=filters,
			fields=["name", "grand_total", "change_amount", "owner"],
			limit_page_length=0,
		)
		if warehouse and rows:
			at_outlet = set(
				frappe.get_all(
					f"{doctype} Item",
					filters={"parent": ["in", [r.name for r in rows]], "warehouse": warehouse},
					pluck="parent",
					limit_page_length=0,
				)
			)
			rows = [r for r in rows if r.name in at_outlet]
		for r in rows:
			kind[r.name] = doctype
		invoices.extend(rows)
	return invoices, kind


@frappe.whitelist()
def get_waiter_cashup(
	company: str | None = None, date: str | None = None, warehouse: str | None = None
) -> dict:
	"""
	The day's cash-up by waiter: bills, sales, the amount by payment mode, the cash each must hand over (cash taken less
	change given) and their voids. A sale is the waiter's when it is credited to their sales person, else it is the
	person who rang it up. For the owner and managers.
	"""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	if warehouse:
		ensure_record_access("Warehouse", warehouse)
	day = getdate(date) if date else getdate(nowdate())

	invoices, kind = _sales_of_day(company, day, warehouse or None)
	names = [inv.name for inv in invoices]

	waiter_of, payments = {}, []
	if names:
		team = frappe.get_all(
			"Sales Team",
			filters={"parent": ["in", names], "parenttype": ["in", list(SALE_DOCTYPES)]},
			fields=["parent", "sales_person"],
			limit_page_length=0,
		)
		person_names = dict(
			frappe.get_all(
				"Sales Person",
				filters={"name": ["in", list({t.sales_person for t in team})]},
				fields=["name", "sales_person_name"],
				as_list=True,
			)
		)
		waiter_of = {t.parent: person_names.get(t.sales_person) or t.sales_person for t in team}
		payments = frappe.get_all(
			"Sales Invoice Payment",
			filters={"parent": ["in", names], "parenttype": ["in", list(SALE_DOCTYPES)]},
			fields=["parent", "parenttype", "mode_of_payment", "amount"],
			limit_page_length=0,
		)
		# Only the payments of the sales listed (an invoice name could repeat across the two kinds)
		payments = [p for p in payments if kind.get(p.parent) == p.parenttype]

	voids = []
	if _has_void_doctype():
		filters = {
			"company": company,
			"voided_at": ["between", [get_datetime(day), get_datetime(add_days(day, 1))]],
		}
		if warehouse:
			filters["warehouse"] = warehouse
		voids = frappe.get_all(VOID, filters=filters, fields=["requested_by", "amount"], limit_page_length=0)

	waiter_of_user = {}
	for person in frappe.get_all(
		"Sales Person",
		filters={"custom_company": company, "custom_user": ["is", "set"]},
		fields=["custom_user", "sales_person_name"],
	):
		waiter_of_user[person.custom_user] = person.sales_person_name
	for user in {inv.owner for inv in invoices} | {v.requested_by for v in voids}:
		if user and user not in waiter_of_user:
			waiter_of_user[user] = _full_name(user)

	cash_modes = set(frappe.get_all("Mode of Payment", filters={"type": "Cash"}, pluck="name"))
	result = rules.cashup(invoices, waiter_of, payments, cash_modes, voids, waiter_of_user)
	result.update({"company": company, "date": str(day), "outlet": warehouse or None})
	return {"success": True, "data": result}
