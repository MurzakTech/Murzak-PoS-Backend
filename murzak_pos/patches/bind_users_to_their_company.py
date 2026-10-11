"""Give each user who belongs to a business the matching company permission.

The Frappe desk dashboards and reports limit what a person sees by their company permissions. Staff
created through the app already have one; this adds it for anyone who has a company on their profile
(custom_company) but no company permission yet, such as business owners who registered themselves.
Users with no company on their profile (platform operators) are left alone.
"""

import frappe


def execute():
	from murzak_pos.api.staff_api import set_company_permission

	if not frappe.db.has_column("User", "custom_company"):
		return
	users = frappe.get_all(
		"User",
		filters={"custom_company": ["is", "set"], "name": ["not in", ["Administrator", "Guest"]]},
		fields=["name", "custom_company"],
	)
	for user in users:
		if not frappe.db.exists("Company", user.custom_company):
			continue
		if frappe.db.exists("User Permission", {"user": user.name, "allow": "Company"}):
			continue
		set_company_permission(user.name, user.custom_company, "Administrator")
