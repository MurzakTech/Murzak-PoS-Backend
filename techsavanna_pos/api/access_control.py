"""
Who may do what: shared checks for the calls that manage staff, roles and permissions.

These calls change what people on the site are allowed to do, so being signed in is not enough.
The rules, in plain words:

- only a business owner (a System Manager) or the Administrator may manage staff and roles
- nobody can hand out a role they do not hold themselves, so a manager cannot make a bigger manager
- a standard role is shared by every business on the site, so only the Administrator may change it
- a role a business created can only be changed from inside that business

The frontend already shows the Staff and Roles screens only to these two roles. These checks make
the server enforce the same rule, because anything the browser decides can be bypassed.
"""

from __future__ import annotations

import frappe
from frappe import _

MANAGER_ROLE = "System Manager"


def require_manager() -> None:
	"""Refuse anyone who is not signed in, or is not a business owner (System Manager) or the Administrator."""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if user == "Administrator":
		return
	if MANAGER_ROLE not in frappe.get_roles(user):
		frappe.throw(
			_("Only a business owner or System Manager can do this."),
			frappe.PermissionError,
		)


def require_roles_held(roles) -> None:
	"""Refuse handing out a role the caller does not hold. The Administrator may hand out any role."""
	if frappe.session.user == "Administrator":
		return
	held = set(frappe.get_roles(frappe.session.user))
	missing = sorted(set(roles or []) - held)
	if missing:
		frappe.throw(
			_("You cannot give roles you do not hold yourself: {0}.").format(", ".join(missing)),
			frappe.PermissionError,
		)


def require_role_manageable(role_name: str) -> None:
	"""
	Refuse changing a role that is not the caller's to change.

	A standard role is shared by every business, so only the Administrator may change it. A custom
	role may be changed by the person who made it, or by someone from the same business.
	"""
	user = frappe.session.user
	if user == "Administrator":
		return

	found = frappe.db.get_value("Role", role_name, ["is_custom", "owner"])
	if not found:
		return  # no such role: let the call say so in its own words
	is_custom, creator = found

	if not is_custom:
		frappe.throw(
			_(
				"{0} is a standard role shared by every business. Only the system administrator can change it."
			).format(role_name),
			frappe.PermissionError,
		)

	if creator == user:
		return

	mine = frappe.db.get_value("User", user, "custom_company")
	theirs = frappe.db.get_value("User", creator, "custom_company") if creator else None
	if mine and mine == theirs:
		return

	frappe.throw(_("This role belongs to another business."), frappe.PermissionError)
