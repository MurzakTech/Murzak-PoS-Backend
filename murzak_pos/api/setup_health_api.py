"""
The call behind Settings > Setup health (and a badge on Home): gathers a few facts about the site and the business and hands
them to setup_health_calc, which turns them into a green, amber or red list. It only reads; it changes nothing.

Who may: an owner or a manager. Each fact is read on its own, so one that cannot be read (a site without the tax app, an
older ERPNext) shows as "unknown" or "not set up" instead of failing the whole page.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

from murzak_pos.api import role_tiers
from murzak_pos.api import setup_health_calc as calc
from murzak_pos.api.payment_gateway_common import resolve_company


def _safely(read, fallback):
	"""A fact that could not be read is the fallback, never an error for the person looking at the page."""
	try:
		return read()
	except Exception:
		frappe.log_error("Setup health: a check could not be read", frappe.get_traceback())
		return fallback


def _scheduler() -> str:
	from frappe.utils.scheduler import is_scheduler_inactive

	return "off" if is_scheduler_inactive() else "on"


def _pos_profile(company):
	profiles = frappe.get_all(
		"POS Profile",
		filters={"company": company, "disabled": 0},
		fields=["name", "update_stock"],
		limit_page_length=1,
	)
	if not profiles:
		return None
	return {"update_stock": bool(profiles[0].get("update_stock"))}


def _stores(company) -> int:
	return frappe.db.count("Warehouse", {"company": company, "is_group": 0, "disabled": 0})


def _cash(company) -> bool:
	cash_modes = frappe.get_all("Mode of Payment", filters={"type": "Cash", "enabled": 1}, pluck="name")
	return bool(
		cash_modes
		and frappe.db.exists("Mode of Payment Account", {"parent": ["in", cash_modes], "company": company})
	)


def _mpesa(company):
	from murzak_pos.api.payment_gateway_api import _gateway_modes

	for info in _gateway_modes(company).values():
		if info.get("gateway") == "mpesa":
			return {
				"prompt_ready": bool(info.get("prompt_ready")),
				"problem": info.get("prompt_problem"),
				"code_entry": bool(info.get("allow_manual_code") or info.get("c2b_enabled")),
			}
	return None


def _etims(company) -> str:
	from murzak_pos.api.etims_optional import company_uses_etims

	if company_uses_etims(company):
		return "active"
	if frappe.db.exists("DocType", "Navari KRA eTims Settings") and frappe.db.exists(
		"Navari KRA eTims Settings", {"company": company}
	):
		return "inactive"
	return "absent"


def _customer_defaults() -> bool:
	from murzak_pos.api.customer_api import _default_group_or_territory

	group = _default_group_or_territory("Customer Group", "customer_group", "Individual")
	territory = _default_group_or_territory("Territory", "territory", "Kenya")
	odd = {None, "", "Government", "Rest Of The World", "All Customer Groups", "All Territories"}
	return group not in odd and territory not in odd


def _shop_type() -> bool:
	if not frappe.db.has_column("User", "custom_pos_industry"):
		return False
	return bool(frappe.db.get_value("User", frappe.session.user, "custom_pos_industry"))


def _fiscal_year(company) -> bool:
	from erpnext.accounts.utils import get_fiscal_year

	return bool(get_fiscal_year(nowdate(), company=company))


@frappe.whitelist()
def get_setup_health(company: str | None = None) -> dict:
	"""Green, amber and red checks of whether this business is ready to sell, receive stock and take payments."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	facts = {
		"scheduler": _safely(_scheduler, "unknown"),
		"stores": _safely(lambda: _stores(company), 0),
		"pos_profile": _safely(lambda: _pos_profile(company), None),
		"cash": _safely(lambda: _cash(company), False),
		"mpesa": _safely(lambda: _mpesa(company), None),
		"etims": _safely(lambda: _etims(company), "absent"),
		"customer_defaults": _safely(_customer_defaults, True),
		"shop_type": _safely(_shop_type, False),
		"fiscal_year": _safely(lambda: _fiscal_year(company), False),
	}
	return {"success": True, "data": {"company": company, **calc.build_report(facts)}}
