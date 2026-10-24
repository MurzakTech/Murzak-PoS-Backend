"""
Frappe's "Session Defaults" call, with the company checked.

Frappe lets any signed-in person set their own defaults with
`frappe.core.doctype.session_default_settings.session_default_settings.set_session_default_values`, without
checking the values. Much of this app reads the default company as "the business this person works for", so
unchecked, a cashier of one shop (or anyone who just signed up) could name another shop and work inside it.
hooks.py points Frappe's call here instead: a company is accepted only when the person belongs to it.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from murzak_pos.api.payment_gateway_common import get_user_companies

COMPANY_KEYS = ("company",)


def refused_companies(default_values: dict, user: str, allowed: set[str]) -> list[str]:
	"""The company values in `default_values` that `user` may not choose. Blank values clear and are fine."""
	if user == "Administrator":
		return []
	return [
		str(value)
		for key, value in (default_values or {}).items()
		if str(key).lower() in COMPANY_KEYS and value and value not in allowed
	]


@frappe.whitelist()
def set_session_default_values(default_values: str | dict[str, Any]):
	default_values = frappe.parse_json(default_values) or {}
	user = frappe.session.user
	refused = refused_companies(default_values, user, get_user_companies(user))
	if refused:
		frappe.throw(
			_("You do not have access to company {0}.").format(", ".join(refused)), frappe.PermissionError
		)

	from frappe.core.doctype.session_default_settings.session_default_settings import (
		set_session_default_values as frappe_set_session_default_values,
	)

	return frappe_set_session_default_values(default_values)
