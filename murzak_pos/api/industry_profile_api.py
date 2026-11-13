"""
The industry profile of an outlet: which industry features are on, which words the till uses and the look it starts
with. The rules are in industry_profile.py; this file reads and writes the outlet's industry.

An outlet is a store (an ERPNext warehouse). Its industry is kept on the warehouse (custom_pos_industry, a POS
Industry). An outlet with none takes the business's: the shop type, else the answer to "What do you sell?".

Reading is for anyone who works for the business, because every till needs its outlet's profile. Choosing an
outlet's industry is for the owner only, and every change is written to the outlet's record (who and when).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from murzak_pos.api import industry_profile as calc
from murzak_pos.api import role_tiers
from murzak_pos.api.payment_gateway_common import resolve_company

FIELD = "custom_pos_industry"
SHOP_TYPE_FIELD = "custom_pos_industry"  # on User, set by shop_type_api for the whole team
AUDIT_PREFIX = "Outlet industry set to "


def _outlet_has_field() -> bool:
	try:
		return bool(frappe.get_meta("Warehouse").has_field(FIELD))
	except Exception:
		return False


def _industry_code(name) -> str | None:
	"""The code of a POS Industry record (REST, PHARMACY...), or the name itself when the record has no code."""
	if not name:
		return None
	return frappe.db.get_value("POS Industry", name, "industry_code") or name


def _shop_type() -> str | None:
	if not frappe.db.has_column("User", SHOP_TYPE_FIELD):
		return None
	return _industry_code(frappe.db.get_value("User", frappe.session.user, SHOP_TYPE_FIELD))


def _business_kind(company: str) -> str | None:
	from murzak_pos.api.setup_api import _read_kind

	return _read_kind(company)


def _own_outlet(company: str, warehouse: str) -> dict:
	"""The outlet's company, group flag and industry, refused when it belongs to another business."""
	fields = ["name", "company", "is_group"] + ([FIELD] if _outlet_has_field() else [])
	row = frappe.db.get_value("Warehouse", warehouse, fields, as_dict=True)
	if not row:
		frappe.throw(_("That store could not be found."), frappe.DoesNotExistError)
	if row.company != company:
		frappe.throw(_("You do not have access to this store."), frappe.PermissionError)
	return row


def profile_for(company: str, warehouse: str | None = None) -> dict:
	"""The profile of an outlet (or of the business, with no outlet), with where its industry came from."""
	outlet_industry = None
	if warehouse:
		outlet_industry = _industry_code(_own_outlet(company, warehouse).get(FIELD))
	key, source = calc.resolve(outlet_industry, _shop_type(), _business_kind(company))
	data = calc.profile(key)
	data.update(
		{
			"company": company,
			"outlet": warehouse or None,
			"source": source,
			"can_change": role_tiers.get_tier() == role_tiers.OWNER and _outlet_has_field(),
		}
	)
	return data


def business_keys(company: str) -> set[str]:
	"""Every profile key used anywhere in the business: its own, and each of its outlets' own."""
	keys = {calc.resolve(None, _shop_type(), _business_kind(company))[0]}
	if _outlet_has_field():
		for code in frappe.get_all(
			"Warehouse",
			filters={"company": company, "is_group": 0, FIELD: ["is", "set"]},
			pluck=FIELD,
		):
			keys.add(calc.resolve(_industry_code(code))[0])
	return keys


def business_has(company: str, feature: str) -> bool:
	"""Whether the feature is on for the business or any of its outlets (say, a pharmacy counter in a hotel)."""
	try:
		return calc.any_has(business_keys(company), feature)
	except Exception:
		# Never let a missing setting stop a product being saved or a sale being made
		frappe.log_error("Industry profile", frappe.get_traceback())
		return False


@frappe.whitelist()
def get_industry_profile(warehouse: str | None = None, company: str | None = None) -> dict:
	"""How the till behaves and speaks at this outlet: features, words, look and home cards."""
	company = resolve_company(company)
	return {"success": True, "data": profile_for(company, warehouse or None)}


@frappe.whitelist()
def list_outlet_industries(company: str | None = None) -> dict:
	"""Each store of the business with its own industry (empty means it follows the business), and the choices."""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	has_field = _outlet_has_field()
	rows = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0},
		fields=["name", "warehouse_name"] + ([FIELD] if has_field else []),
		order_by="warehouse_name asc",
	)
	business_key = calc.resolve(None, _shop_type(), _business_kind(company))[0]
	outlets = []
	for row in rows:
		own = row.get(FIELD) if has_field else None
		key = calc.resolve(_industry_code(own))[0] if own else business_key
		outlets.append(
			{
				"warehouse": row.name,
				"name": row.warehouse_name or row.name,
				"industry": own or "",
				"profile": key,
				"label": calc.profile(key)["label"],
			}
		)
	options = frappe.get_all(
		"POS Industry",
		filters={"is_active": 1},
		fields=["name", "industry_name"],
		order_by="sort_order asc, industry_name asc",
	)
	return {
		"success": True,
		"data": {
			"company": company,
			"business_profile": business_key,
			"outlets": outlets,
			"options": [{"value": o.name, "label": o.industry_name or o.name} for o in options],
			"can_change": role_tiers.get_tier() == role_tiers.OWNER and has_field,
		},
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def set_outlet_industry(warehouse: str, industry: str | None = None, company: str | None = None) -> dict:
	"""Choose an outlet's industry (owner only). An empty industry makes the outlet follow the business again."""
	role_tiers.require_access("team")
	company = resolve_company(company)
	if not _outlet_has_field():
		frappe.throw(_("The kind of shop cannot be set per store on this site yet."), frappe.ValidationError)
	row = _own_outlet(company, warehouse)
	if row.is_group:
		frappe.throw(_("Choose a store, not a group of stores."), frappe.ValidationError)

	value = str(industry or "").strip() or None
	if value and not frappe.db.get_value("POS Industry", {"name": value, "is_active": 1}, "name"):
		frappe.throw(_("Choose a kind of shop from the list."), frappe.ValidationError)

	frappe.db.set_value("Warehouse", warehouse, FIELD, value)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Warehouse",
			"reference_name": warehouse,
			"content": f"{AUDIT_PREFIX}{value or 'the business default'}",
		}
	).insert(ignore_permissions=True)
	return {"success": True, "message": _("Saved."), "data": profile_for(company, warehouse)}
