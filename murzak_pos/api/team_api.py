"""
The Team list (ticket P5-3): the people of a business, with the kind of person each is (Owner, Manager or
Cashier) and the roles they hold. It is the read half of the new Settings > Team page; adding a person, changing
them and deactivating them use the staff calls that already exist (`staff_api`), which only an owner may use.

This file only reads. Who may read it: an owner or a manager (a cashier is refused before anything is read). The
people shown are those of the caller's own business, the same ones `staff_api.get_staff_users` lists.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.permissions import AUTOMATIC_ROLES

from murzak_pos.api import role_tiers, team_calc
from murzak_pos.api.payment_gateway_common import resolve_company

USER_FIELDS = (
	"name",
	"email",
	"first_name",
	"last_name",
	"full_name",
	"mobile_no",
	"enabled",
	"creation",
	"last_login",
)


def _require_reader() -> None:
	"""An owner or a manager may see the team. Anyone else, and anyone not signed in, is refused."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if tier not in (role_tiers.OWNER, role_tiers.MANAGER):
		frappe.throw(_("Only an owner or a manager can see the team."), frappe.PermissionError)


def _users(company: str, enabled_only: bool) -> list[dict]:
	filters = {"custom_company": company}
	if enabled_only:
		filters["enabled"] = 1
	return frappe.get_all("User", filters=filters, fields=list(USER_FIELDS), order_by="creation asc")


def _roles_by_user(names: list[str]) -> dict:
	"""Every role of each person, read in one query."""
	if not names:
		return {}
	rows = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "parent": ["in", names]},
		fields=["parent", "role"],
	)
	found: dict[str, list[str]] = {}
	for row in rows:
		found.setdefault(row["parent"], []).append(row["role"])
	return found


@frappe.whitelist()
def list_team(company: str | None = None, enabled_only: bool | int | str = 0) -> dict:
	"""
	The people of the business with their kind and roles, owners first. Read only.

	`enabled_only` leaves out people who have been deactivated. `tiers` carries what the page needs to move a
	person between kinds with the existing role calls (see team_calc.tier_setup).
	"""
	_require_reader()
	company = resolve_company(company)
	only_active = str(enabled_only).strip().lower() in ("1", "true", "yes")
	users = _users(company, only_active)
	people = team_calc.build_team(users, _roles_by_user([u["name"] for u in users]), AUTOMATIC_ROLES)
	return {
		"success": True,
		"data": {
			"company": company,
			"members": people,
			"counts": team_calc.counts(people),
			"tiers": team_calc.tier_setup(),
			"me": frappe.session.user,
		},
	}
