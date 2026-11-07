"""
The shop type (what kind of business this is), which decides the starter products a shop is offered.

Reading is for an owner or a manager. Changing is for an owner only, limited in three ways: only a type from the list of
active types can be chosen, a type already set can be changed once in shop_type_calc.COOLDOWN_DAYS, and the call is
rate-limited. Every change is written to the business's record (who and when), which is also what the waiting period reads.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import format_date, get_datetime, now_datetime

from murzak_pos.api import role_tiers
from murzak_pos.api import shop_type_calc as calc
from murzak_pos.api.payment_gateway_common import resolve_company

FIELD = "custom_pos_industry"


def _has_field() -> bool:
	return bool(frappe.db.has_column("User", FIELD))


def _options() -> list[dict]:
	rows = frappe.get_all(
		"POS Industry",
		filters={"is_active": 1},
		fields=["name", "industry_name", "description"],
		order_by="sort_order asc, industry_name asc",
	)
	return [
		{
			"value": r["name"],
			"label": r["industry_name"] or r["name"],
			"description": r.get("description") or "",
		}
		for r in rows
	]


def _current() -> str | None:
	if not _has_field():
		return None
	return frappe.db.get_value("User", frappe.session.user, FIELD) or None


def _last_change(company: str):
	rows = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Company",
			"reference_name": company,
			"comment_type": "Info",
			"content": ["like", f"{calc.AUDIT_PREFIX}%"],
		},
		fields=["creation"],
		order_by="creation desc",
		limit_page_length=1,
	)
	return get_datetime(rows[0]["creation"]) if rows else None


def _payload(company: str) -> dict:
	current = _current()
	last = _last_change(company)
	options = _options()
	known = {o["value"]: o["label"] for o in options}
	when = calc.next_change_on(last, now_datetime()) if current else None
	return {
		"company": company,
		"current": current,
		"current_label": known.get(current) if current else None,
		"options": options,
		"can_change": role_tiers.get_tier() == role_tiers.OWNER and _has_field(),
		"next_change_on": str(when) if when else None,
		"next_change_text": format_date(when) if when else "",
	}


@frappe.whitelist()
def get_shop_type(company: str | None = None) -> dict:
	"""The shop type now, the types to choose from, and whether (and when) it can be changed."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	return {"success": True, "data": _payload(company)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60)
def set_shop_type(industry: str, company: str | None = None) -> dict:
	"""Set the shop type for the business: the owner and everyone on the team, so they all see the same products."""
	role_tiers.require_access("team")
	company = resolve_company(company)
	if not _has_field():
		frappe.throw(_("The shop type cannot be set on this site."), frappe.ValidationError)

	if not industry or not frappe.db.get_value("POS Industry", {"name": industry, "is_active": 1}, "name"):
		frappe.throw(_("Choose a shop type from the list."), frappe.ValidationError)

	decision = calc.change_decision(_current(), industry, _last_change(company), now_datetime())
	if decision["reason"] == "same":
		frappe.throw(_("That is already your shop type."), frappe.ValidationError)
	if decision["reason"] == "cooldown":
		frappe.throw(
			_("The shop type was changed recently. It can be changed again on {0}.").format(
				format_date(decision["next_change_on"])
			),
			frappe.ValidationError,
		)

	people = set(frappe.get_all("User", filters={"custom_company": company, "enabled": 1}, pluck="name"))
	people.add(frappe.session.user)
	for person in people - {"Administrator", "Guest"}:
		frappe.db.set_value("User", person, FIELD, industry, update_modified=False)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Company",
			"reference_name": company,
			"content": f"{calc.AUDIT_PREFIX}{industry}",
		}
	).insert(ignore_permissions=True)
	return {"success": True, "message": _("Shop type saved."), "data": _payload(company)}
