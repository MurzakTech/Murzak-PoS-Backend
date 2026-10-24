"""
When a refund or a purchase order needs a second person's approval, and who may give it.

Kept free of Frappe imports so it can be tested on its own. The database work lives in approvals_api.py.
"""

from __future__ import annotations

REFUND = "Refund"
PURCHASE_ORDER = "Purchase order"

PENDING = "Pending"
APPROVED = "Approved"
REJECTED = "Rejected"

OWNER_ROLES = frozenset({"Business Owner", "System Manager"})

# Who may approve what. A cashier or a sales manager who records a refund cannot approve it.
APPROVER_ROLES = {
	REFUND: frozenset({"Business Owner", "System Manager", "Accounts Manager"}),
	PURCHASE_ORDER: frozenset({"Business Owner", "System Manager", "Accounts Manager", "Purchase Manager"}),
}


def needs_approval(amount, limit) -> bool:
	"""True when an amount is above a limit. A limit of 0 (or none) means the check is off."""
	limit = float(limit or 0)
	return limit > 0 and abs(float(amount or 0)) > limit


def approver_roles(kind: str) -> frozenset:
	return APPROVER_ROLES.get(kind, OWNER_ROLES)


def decision_problem(kind: str, requested_by: str, decided_by: str, roles) -> str | None:
	"""
	Why this person may not decide a request, or None when they may.

	Administrator may always decide. Anyone else needs an approver role, and may not decide their own request unless they
	are the owner (System Manager), who has nobody above them.
	"""
	if decided_by == "Administrator":
		return None
	roles = set(roles)
	if not roles & approver_roles(kind):
		return "Only a manager with approval rights can approve or reject this."
	if decided_by == requested_by and not roles & OWNER_ROLES:
		return "You cannot approve your own request. Ask another manager."
	return None
