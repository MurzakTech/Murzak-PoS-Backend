"""
The arithmetic behind the Team list (ticket P5-3): which of Owner, Manager and Cashier each person is, and how a
person is shown. Plain functions, no database, so they are tested with plain rows.

A person's kind is worked out from the roles they already hold, by the same rule as `role_tiers.get_tier`
(this file reads the same two sets of roles from there, so the two cannot drift apart). Nothing here reads or
changes a role.
"""

from __future__ import annotations

from murzak_pos.api.role_tiers import (
	CASHIER,
	MANAGER,
	MANAGER_ROLES,
	OWNER,
	OWNER_ROLES,
	ROLES_TO_GRANT,
)

# The order the Team list shows people in: owners first, then managers, then cashiers
TIER_ORDER = (OWNER, MANAGER, CASHIER)


def tier_for(roles, user: str | None = None) -> str:
	"""Owner, manager or cashier for a person who holds `roles` (the Administrator is always an owner)."""
	held = set(roles or [])
	if user == "Administrator" or held & OWNER_ROLES:
		return OWNER
	if held & MANAGER_ROLES:
		return MANAGER
	return CASHIER


def shown_roles(roles, automatic) -> list[str]:
	"""The roles worth showing: those a person was given, without the ones Frappe gives everybody."""
	skip = set(automatic or [])
	return sorted({role for role in (roles or []) if role and role not in skip})


def member(row: dict, roles, automatic=()) -> dict:
	"""One person as the Team page draws them. `row` is a User row; `roles` is every role they hold."""
	given = shown_roles(roles, automatic)
	first = (row.get("first_name") or "").strip()
	last = (row.get("last_name") or "").strip()
	name = (row.get("full_name") or "").strip() or f"{first} {last}".strip() or row.get("name")
	return {
		"email": row.get("email") or row.get("name"),
		"first_name": first,
		"last_name": last,
		"full_name": name,
		"phone": row.get("mobile_no") or "",
		"enabled": bool(row.get("enabled")),
		"tier": tier_for(given, row.get("name")),
		"roles": given,
		"created": str(row["creation"]) if row.get("creation") else None,
		"last_sign_in": str(row["last_login"]) if row.get("last_login") else None,
	}


def sort_members(members: list[dict]) -> list[dict]:
	"""Owners, then managers, then cashiers; active people before deactivated ones; then by name."""
	order = {tier: index for index, tier in enumerate(TIER_ORDER)}
	return sorted(
		members,
		key=lambda m: (order.get(m["tier"], len(order)), not m["enabled"], m["full_name"].lower()),
	)


def build_team(users: list[dict], roles_by_user: dict, automatic=()) -> list[dict]:
	"""Every person, placed and sorted. `roles_by_user` maps a user's name to the roles they hold."""
	return sort_members([member(row, roles_by_user.get(row.get("name"), []), automatic) for row in users])


def counts(members: list[dict]) -> dict:
	"""How many people there are, how many are active and how many of each kind."""
	active = [m for m in members if m["enabled"]]
	return {
		"total": len(members),
		"active": len(active),
		"owners": sum(1 for m in active if m["tier"] == OWNER),
		"managers": sum(1 for m in active if m["tier"] == MANAGER),
		"cashiers": sum(1 for m in active if m["tier"] == CASHIER),
	}


def tier_setup() -> dict:
	"""What the Team page needs to change a person's kind with the existing role calls: the roles to give for each
	kind, and which roles make an owner or a manager (so they can be taken away when someone moves down)."""
	return {
		"grant": {tier: list(ROLES_TO_GRANT[tier]) for tier in TIER_ORDER},
		"owner_roles": sorted(OWNER_ROLES),
		"manager_roles": sorted(MANAGER_ROLES),
	}
