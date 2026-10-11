"""
A read-only checklist of what a business still needs before its sales can be sent to KRA eTIMS.

Nothing is created or changed. A business that is not on eTIMS simply sees which pieces are missing,
and keeps selling through the POS Invoice path meanwhile.
"""

from __future__ import annotations

import frappe
from frappe import _

SETTINGS_DOCTYPE = "Navari KRA eTims Settings"


def build_checklist(facts: dict) -> list[dict]:
	"""Turn the facts found on the server into plain checklist rows: {key, label, done, help}."""
	rows = [
		(
			"kenya_app",
			_("Kenya tax app installed"),
			facts["kenya_app"],
			_("Ask your administrator to install the Kenya compliance app on this server."),
		),
		(
			"settings",
			_("eTIMS settings saved and active for this business"),
			facts["active_settings"],
			_("Enter the KRA device details in the eTIMS settings once KRA has certified the system."),
		),
		(
			"branch",
			_("A branch named 00 or 01"),
			facts["branch"],
			_("KRA identifies the head office as branch 00. Create it in the Branch list."),
		),
		(
			"dimension",
			_("Branch accounting dimension"),
			facts["dimension"],
			_("The Kenya app creates it when the eTIMS settings are first activated."),
		),
		(
			"vat",
			_("VAT accounts and templates"),
			facts["vat_setup"],
			_("Run the VAT set-up for this business."),
		),
		(
			"vat_registered",
			_("Marked as registered for VAT"),
			facts["vat_registered"],
			_("Mark the business as VAT-registered once KRA has issued the PIN certificate."),
		),
	]
	return [
		{"key": k, "label": str(label), "done": bool(done), "help": str(hint)}
		for k, label, done, hint in rows
	]


def is_ready(checklist: list[dict]) -> bool:
	return all(row["done"] for row in checklist)


@frappe.whitelist()
def get_etims_readiness(company: str | None = None) -> dict:
	from murzak_pos.api.payment_gateway_common import resolve_company
	from murzak_pos.api.vat_setup import SALES_TEMPLATE_TITLE, is_vat_registered

	company = resolve_company(company)
	kenya_app = bool(frappe.db.exists("DocType", SETTINGS_DOCTYPE))
	abbr = frappe.get_cached_value("Company", company, "abbr")
	facts = {
		"kenya_app": kenya_app,
		"active_settings": kenya_app
		and bool(frappe.db.exists(SETTINGS_DOCTYPE, {"company": company, "is_active": 1})),
		"branch": bool(frappe.db.exists("Branch", {"name": ["in", ["00", "01"]]})),
		"dimension": bool(
			frappe.db.exists("Accounting Dimension", {"document_type": "Branch", "disabled": 0})
		),
		"vat_setup": bool(
			frappe.db.exists("Sales Taxes and Charges Template", f"{SALES_TEMPLATE_TITLE} - {abbr}")
		),
		"vat_registered": is_vat_registered(company),
	}
	checklist = build_checklist(facts)
	return {
		"success": True,
		"data": {
			"company": company,
			"ready": is_ready(checklist),
			"checklist": checklist,
		},
	}
