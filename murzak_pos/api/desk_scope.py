"""
Which businesses a person signed in to the Frappe desk may see.

Every dashboard card, chart and report in the "Murzak POS" workspace asks this first, so a manager of one
business never sees another business's figures.

* Administrator sees every business.
* A person bound to a business (their company field, a company permission or a default company) sees only
  those businesses.
* A System Manager bound to no business at all is the platform operator and sees every business. Anyone
  else bound to nothing sees nothing.
"""

from __future__ import annotations

import frappe
from frappe import _

OPERATOR_ROLE = "System Manager"


def visible_companies(user: str | None = None) -> list[str]:
	from murzak_pos.api.payment_gateway_common import get_user_companies

	user = user or frappe.session.user
	if user == "Administrator":
		return sorted(frappe.get_all("Company", pluck="name"))
	bound = get_user_companies(user)
	if bound:
		return sorted(company for company in bound if frappe.db.exists("Company", company))
	if OPERATOR_ROLE in frappe.get_roles(user):
		return sorted(frappe.get_all("Company", pluck="name"))
	return []


def chosen_companies(requested=None, user: str | None = None) -> list[str]:
	"""
	The businesses a report or card covers: the one asked for if the person may see it, otherwise all they may see.
	A business they may not see is refused, never silently dropped.
	"""
	allowed = visible_companies(user)
	if not requested:
		return allowed
	wanted = [requested] if isinstance(requested, str) else list(requested)
	refused = [company for company in wanted if company not in allowed]
	if refused:
		frappe.throw(_("You do not have access to {0}.").format(", ".join(refused)), frappe.PermissionError)
	return wanted
