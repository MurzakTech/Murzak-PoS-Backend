"""Undo the links that put people inside a business that is not theirs.

1. A default company that is not one of the person's own businesses is cleared, or replaced by their only one.
   Such defaults came from Frappe's "session defaults" call, which accepted any company, and from the profile call
   that picked the first company on the site for someone who had none (both closed now).
2. On a shop's own site (one company), anyone with roles who is bound to no business used to count as a member
   of that company by default. That fallback is gone, so they are bound to it with a company permission instead,
   and keep exactly the access they had. Sign-up is closed on shop sites, so these are people Murzak or the owner
   put there.
3. On the shared site (several companies), people who signed up but never created a business got every business
   role at sign-up, System Manager among them. Bound to no business, those roles reach every shop on the site, so
   they are removed; creating a business gives them back. Murzak's own operator accounts can be kept by listing
   them in site_config.json under "murzak_platform_operators".

Every change is written to the patch output so it can be checked afterwards.
"""

import frappe

COMPANY_KEYS = ("Company", "company")
# Roles Frappe gives everyone; holding only these is holding no role
AUTOMATIC_ROLES = {"All", "Guest", "Desk User"}


def execute():
	from murzak_pos.api.payment_gateway_common import get_user_companies

	users = set(
		frappe.get_all("User", filters={"name": ["not in", ["Administrator", "Guest"]]}, pluck="name")
	)
	companies = frappe.get_all("Company", pluck="name")

	# First, so the default company of someone on a shop's own site is not cleared below
	if len(companies) == 1:
		bind_to_the_only_company(users, companies[0], get_user_companies)

	clear_defaults_that_are_not_theirs(users, get_user_companies)

	if len(companies) > 1:
		remove_sign_up_roles_from_the_unbound(users, get_user_companies)


def bind_to_the_only_company(users, company, get_user_companies):
	from murzak_pos.api.staff_api import set_company_permission

	for user in sorted(users):
		if not set(frappe.get_roles(user)) - AUTOMATIC_ROLES or get_user_companies(user):
			continue
		set_company_permission(user, company, "Administrator")
		print(f"Bound {user} to {company}, the only business on this site")


def clear_defaults_that_are_not_theirs(users, get_user_companies):
	for row in frappe.get_all(
		"DefaultValue",
		filters={"defkey": ["in", COMPANY_KEYS], "parent": ["in", list(users) or [""]]},
		fields=["parent", "defkey", "defvalue"],
	):
		allowed = get_user_companies(row.parent)
		if row.defvalue in allowed:
			continue
		frappe.defaults.clear_user_default(row.defkey, row.parent)
		if len(allowed) == 1:
			frappe.defaults.set_user_default(row.defkey, next(iter(allowed)), row.parent)
		print(
			f"Cleared default company {row.defvalue!r} of {row.parent} (their businesses: {sorted(allowed)})"
		)


def remove_sign_up_roles_from_the_unbound(users, get_user_companies):
	from murzak_pos.api.auth_api import STANDARD_BUSINESS_ROLES

	operators = set(frappe.conf.get("murzak_platform_operators") or [])
	signup_roles = {role for role in STANDARD_BUSINESS_ROLES if frappe.db.exists("Role", role)}
	for user in sorted(users - operators):
		held = set(frappe.get_roles(user))
		if not signup_roles or not signup_roles <= held or get_user_companies(user):
			continue
		doc = frappe.get_doc("User", user)
		doc.flags.ignore_permissions = True
		doc.remove_roles(*sorted(signup_roles))
		print(f"Removed sign-up business roles from {user}, who belongs to no business")
