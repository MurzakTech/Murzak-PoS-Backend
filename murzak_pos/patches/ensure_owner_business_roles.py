"""
Give every business owner all the business roles.

An owner who registered was given all ten business roles, but an owner added later on the Team page was given
only "Business Owner", and an older account may have lost some. ERPNext decides what a person may do inside a
purchase, a stock entry or a payment from the roles they hold, so an owner short of them is refused in the middle of
ordinary work (a bill made from a receipt, a supplier payment), with a message that does not say why.

This adds the missing business roles to every enabled person who holds Business Owner. It adds only: nothing is
taken from anyone, and a role the site does not have is skipped. System Manager is never given. Every change is
written to the patch output.
"""

import frappe


def execute():
	from murzak_pos.api.role_tiers import BUSINESS_ROLES, OWNER_ROLE

	existing = {r for r in BUSINESS_ROLES if frappe.db.exists("Role", r)}
	owners = frappe.get_all(
		"Has Role",
		filters={"role": OWNER_ROLE, "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	changed = 0
	for user in sorted(owners):
		if user in ("Administrator", "Guest") or not frappe.db.get_value("User", user, "enabled"):
			continue
		doc = frappe.get_doc("User", user)
		held = {r.role for r in doc.roles}
		missing = sorted(existing - held)
		if not missing:
			continue
		for role in missing:
			doc.append("roles", {"role": role})
		doc.flags.ignore_permissions = True
		doc.save()
		changed += 1
		print(f"{user}: given {', '.join(missing)}")
	print(f"Owner roles: {changed} of {len(owners)} owners were missing business roles")
