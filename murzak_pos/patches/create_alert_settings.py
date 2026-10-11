"""Give every existing business its alert settings, with the defaults and nobody to email yet.

A business owner chooses who is told (Business Alert Settings); until then alerts only appear in the
till's list of recent alerts, and nobody is emailed without asking.
"""

import frappe


def execute():
	from murzak_pos.api.alerts import ensure_alert_settings

	for company in frappe.get_all("Company", pluck="name"):
		ensure_alert_settings(company)
