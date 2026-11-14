"""
Menu options (restaurants and bars): the option groups of each dish, kept on the product (Item.custom_menu_options).
The rules are in menu_options_rules.py.

Reading is for anyone who works for the business, because every till needs the options. Changing a dish's options is
for the roles that may change products.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api import menu_options_rules as rules
from murzak_pos.api.access_control import ensure_record_access, require_catalogue_role
from murzak_pos.api.payment_gateway_common import resolve_company

FIELD = "custom_menu_options"


def _has_field() -> bool:
	try:
		return bool(frappe.get_meta("Item").has_field(FIELD))
	except Exception:
		return False


@frappe.whitelist()
def list_menu_options(company: str | None = None) -> dict:
	"""Every dish of the business that has options: {item_code: [groups]}. Dishes without options are left out."""
	company = resolve_company(company)
	if not _has_field():
		return {"success": True, "data": {"options": {}}}
	rows = frappe.get_all(
		"Item",
		filters={"custom_company": company, "disabled": 0, FIELD: ["is", "set"]},
		fields=["name", FIELD],
		limit_page_length=0,
	)
	options = {}
	for row in rows:
		groups = rules.load_groups(row.get(FIELD))
		if groups:
			options[row.name] = groups
	return {"success": True, "data": {"options": options}}


@frappe.whitelist(methods=["POST"])
def set_menu_options(item_code: str, groups=None, company: str | None = None) -> dict:
	"""Keep a dish's option groups (an empty list removes them). Refused in plain words when they cannot be kept."""
	require_catalogue_role()
	resolve_company(company)
	ensure_record_access("Item", item_code)
	if not _has_field():
		frappe.throw(_("Menu options cannot be kept on this site yet."), frappe.ValidationError)
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("That item could not be found."), frappe.ValidationError)
	try:
		clean = rules.clean_groups(groups)
	except rules.OptionsError as error:
		frappe.throw(str(error), frappe.ValidationError)
	frappe.db.set_value("Item", item_code, FIELD, rules.dump_groups(clean))
	return {"success": True, "message": _("Saved."), "data": {"item_code": item_code, "groups": clean}}
