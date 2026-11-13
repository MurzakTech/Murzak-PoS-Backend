"""
A restaurant or bar's own settings: happy hours, the service charge, delivery platforms and tables. One record per
business (kept as a site default, like the set-up answers). The rules are in restaurant_settings_rules.py.

Reading is for anyone who works for the business, because every till needs them. Saving is for the owner and managers.
A service charge above 0 makes sure the business has its "Service charge" item, which the till adds as a line.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _

from murzak_pos.api import restaurant_settings_rules as rules
from murzak_pos.api import role_tiers
from murzak_pos.api.payment_gateway_common import resolve_company

PREFIX = "murzak_restaurant"
MAX_KEY_LENGTH = 140
SERVICE_ITEM_PREFIX = "SERVICE-CHARGE"


def _key(company: str) -> str:
	key = f"{PREFIX}:{company}"
	return (
		key if len(key) <= MAX_KEY_LENGTH else f"{PREFIX}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"
	)


def read_settings(company: str) -> dict:
	return rules.load_settings(frappe.db.get_default(_key(company)))


def _service_item_code(company: str) -> str:
	abbr = frappe.db.get_value("Company", company, "abbr") or company[:10]
	return f"{SERVICE_ITEM_PREFIX}-{abbr}"[:140]


def ensure_service_item(company: str) -> str:
	"""The business's "Service charge" item: a service (no stock) sold at whatever the till charges. Made on first use."""
	code = _service_item_code(company)
	if frappe.db.exists("Item", code):
		return code
	from murzak_pos.api.etims_optional import relax_etims_mandatory

	group = "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": "Service charge",
			"item_group": group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"custom_company": company,
			"custom_prevent_etims_registration": 1,
		}
	)
	relax_etims_mandatory(item)
	item.insert(ignore_permissions=True)
	return code


def _payload(company: str) -> dict:
	data = read_settings(company)
	data["company"] = company
	data["service_charge_item"] = _service_item_code(company) if data["service_charge_percent"] > 0 else None
	data["can_change"] = role_tiers.can("profit")
	return data


@frappe.whitelist()
def get_restaurant_settings(company: str | None = None) -> dict:
	"""The business's happy hours, service charge, delivery platforms and tables."""
	company = resolve_company(company)
	return {"success": True, "data": _payload(company)}


@frappe.whitelist(methods=["POST"])
def save_restaurant_settings(settings, company: str | None = None) -> dict:
	"""Keep the settings (owner and managers). Refused in plain words when something cannot be kept."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	try:
		clean = rules.clean_settings(settings)
	except rules.SettingsError as error:
		frappe.throw(str(error), frappe.ValidationError)
	if clean["service_charge_percent"] > 0:
		ensure_service_item(company)
	frappe.db.set_default(_key(company), json.dumps(clean, separators=(",", ":")))
	return {"success": True, "message": _("Saved."), "data": _payload(company)}
