"""
Owner, Manager and Cashier: who may see the figures that only the people running the business should see.

The redesign names three kinds of person. This file decides which one a signed-in user is, and holds the single
check that every protected call makes (`require_access`). The server enforces this; hiding a number in the browser
is not enough, because anything the browser decides can be bypassed.

Who is who. The three are worked out from the roles a person already has, so every existing user is placed at once
and no role record is created or changed on any business:

- Owner:   a System Manager, or the Administrator
- Manager: holds one of the manager roles the business uses today (Sales, Purchase, Stock, Accounts or Item
           Manager, or Accounts User). These are the roles the current screens already let into Reports
- Cashier: any other signed-in user (Sales User, Desk User, Employee and so on). Frappe's "All" role is ignored,
           because it is given to every user

A person with roles of several kinds takes the highest.

Who may see what ("Who sees what" in the redesign's Data and API needs):

- profit, cost of goods and money owed: Owner and Manager
- every sale, not only their own till's: Owner and Manager
- the team and who holds which role: Owner

How to protect a call: put `require_access("profit")` (or another capability below) first in it, before anything is
read. To change who may see something, change CAPABILITIES in this one place.
"""

from __future__ import annotations

import frappe
from frappe import _

OWNER = "owner"
MANAGER = "manager"
CASHIER = "cashier"

# A business owner holds Business Owner. System Manager is for Murzak's own staff; it still counts as an owner here so
# their accounts, and owners not yet moved to Business Owner, keep working.
OWNER_ROLE = "Business Owner"
OWNER_ROLES = frozenset({OWNER_ROLE, "System Manager"})
# Copied from the roles the frontend's role table (src/config/roleAccessConfig.js) lets into Reports today,
# minus "All", which Frappe gives to every user.
MANAGER_ROLES = frozenset(
	{
		"Sales Manager",
		"Purchase Manager",
		"Stock Manager",
		"Accounts Manager",
		"Accounts User",
		"Item Manager",
	}
)

# What each kind of person may see or do. The one place to change it.
CAPABILITIES = {
	"profit": (OWNER, MANAGER),
	"cost_of_goods": (OWNER, MANAGER),
	"money_owed": (OWNER, MANAGER),
	"all_sales": (OWNER, MANAGER),
	"team": (OWNER,),
}

# What to give a person so they become each kind. Used by the Team page when it assigns a role; nothing here
# changes anyone's roles by itself.
ROLES_TO_GRANT = {
	OWNER: ("Business Owner",),
	MANAGER: ("Sales Manager", "Purchase Manager", "Stock Manager", "Accounts Manager", "Item Manager"),
	CASHIER: ("Sales User",),
}

# Capabilities only an owner holds read differently to the person who is refused
_OWNER_ONLY = frozenset(name for name, who in CAPABILITIES.items() if who == (OWNER,))


def get_tier(user: str | None = None) -> str | None:
	"""Owner, manager or cashier for `user` (the signed-in user by default). A guest is none of them."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return None
	if user == "Administrator":
		return OWNER
	held = set(frappe.get_roles(user))
	if held & OWNER_ROLES:
		return OWNER
	if held & MANAGER_ROLES:
		return MANAGER
	return CASHIER


def can(capability: str, user: str | None = None) -> bool:
	"""Whether `user` may use `capability`. An unknown capability is a mistake in the code and is refused."""
	if capability not in CAPABILITIES:
		raise ValueError(f"Unknown capability: {capability}")
	return get_tier(user) in CAPABILITIES[capability]


def require_access(capability: str) -> None:
	"""The one check. Refuses anyone who may not use `capability`; call it first in a protected call."""
	if capability not in CAPABILITIES:
		raise ValueError(f"Unknown capability: {capability}")
	tier = get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if tier in CAPABILITIES[capability]:
		return
	if capability in _OWNER_ONLY:
		frappe.throw(_("Only a business owner or System Manager can do this."), frappe.PermissionError)
	frappe.throw(_("Only an owner or a manager can see this."), frappe.PermissionError)


@frappe.whitelist()
def get_my_access() -> dict:
	"""Which kind of person the caller is, and what they may see. The screens use it to decide what to draw; the
	server still checks every protected call itself."""
	tier = get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return {
		"success": True,
		"data": {"tier": tier, "can": {name: tier in who for name, who in CAPABILITIES.items()}},
	}


def ensure_owner_role() -> None:
	"""Create the Business Owner role if the site does not have it. Safe to run again (after_install, after_migrate)."""
	if frappe.db.exists("Role", OWNER_ROLE):
		return
	role = frappe.new_doc("Role")
	role.role_name = OWNER_ROLE
	role.desk_access = 1
	role.insert(ignore_permissions=True)
