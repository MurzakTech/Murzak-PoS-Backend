"""Business owners hold Business Owner instead of System Manager.

System Manager reaches every business on a site and can change who may see what, so on the shared site an owner
holding it could reach other shops. Everyone bound to a business who holds System Manager gets Business Owner and
loses System Manager. Murzak's own accounts are left alone: those bound to no business, and any listed in
site_config.json under "murzak_platform_operators". Every change is written to the patch output.
"""

import frappe


def execute():
	from murzak_pos.api.payment_gateway_common import get_user_companies
	from murzak_pos.api.role_tiers import OWNER_ROLE, ensure_owner_role

	ensure_owner_role()
	operators = set(frappe.conf.get("murzak_platform_operators") or [])
	holders = frappe.get_all(
		"Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent", distinct=True
	)
	for user in sorted(set(holders) - operators - {"Administrator", "Guest"}):
		if not frappe.db.get_value("User", user, "enabled") or not get_user_companies(user):
			continue
		doc = frappe.get_doc("User", user)
		doc.flags.ignore_permissions = True
		if OWNER_ROLE not in {r.role for r in doc.roles}:
			doc.append("roles", {"role": OWNER_ROLE})
		doc.roles = [r for r in doc.roles if r.role != "System Manager"]
		doc.save()
		print(f"{user}: System Manager replaced by {OWNER_ROLE}")
