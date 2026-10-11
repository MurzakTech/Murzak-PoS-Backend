"""
Price lists and modes of payment, kept apart for each business.

Price lists
    A price list belongs to the company in its `custom_company` field (added by this app). A business
    sees and changes only its own lists, plus the shared ones that belong to nobody, such as "Standard
    Selling". The shared ones are changed only by the Administrator, since every business uses them.

Modes of payment
    A mode of payment ("Cash", "M-Pesa") is a name that many businesses can share, because Frappe keeps
    each company's account in a table on the mode. A business therefore sees a mode when it has an
    account row for that company, or when the mode has no account rows at all (a plain "Cash" that
    nobody has set up yet). A mode set up only for other businesses stays hidden.
"""

from __future__ import annotations

import frappe
from frappe import _

from techsavanna_pos.api.payment_gateway_common import resolve_company

OWNER_FIELD = "custom_company"


def caller_company(company: str | None = None) -> str | None:
	"""
	The company the caller acts for: the one named (it must be theirs) or their own.

	The Administrator, who belongs to no business, may have none; that means "no isolation".
	"""
	if company:
		return resolve_company(company)
	if frappe.session.user == "Administrator":
		return None
	return resolve_company(None)


def visible_price_list_names(company: str | None):
	"""The price lists a company may see (its own and the shared ones), or None for no limit."""
	if not company or not frappe.db.has_column("Price List", OWNER_FIELD):
		return None
	own = frappe.get_all("Price List", filters={OWNER_FIELD: company}, pluck="name")
	shared = frappe.get_all("Price List", filters={OWNER_FIELD: ["is", "not set"]}, pluck="name")
	return list(set(own) | set(shared))


def ensure_price_list_changeable(name: str) -> None:
	"""
	Refuse changing or deleting a price list the caller may not change.

	Another business's list is refused by `ensure_record_access`; this covers the shared lists that
	belong to nobody, which only the Administrator may change.
	"""
	if frappe.session.user == "Administrator" or not frappe.db.has_column("Price List", OWNER_FIELD):
		return
	if frappe.db.exists("Price List", name) and not frappe.db.get_value("Price List", name, OWNER_FIELD):
		frappe.throw(
			_(
				"{0} is a price list shared by every business. Only the system administrator can change it."
			).format(name),
			frappe.PermissionError,
		)


def modes_of_payment_for(company: str | None, only_enabled: bool = True) -> list[str] | None:
	"""
	The modes of payment a company may use, or None for no limit (the Administrator with no company).

	The names come back in alphabetical order.
	"""
	if not company:
		return None
	filters = {"enabled": 1} if only_enabled else {}
	names = frappe.get_all("Mode of Payment", filters=filters, pluck="name", order_by="name asc")
	rows = frappe.get_all("Mode of Payment Account", fields=["parent", "company"])
	set_up_anywhere = {r.parent for r in rows}
	set_up_here = {r.parent for r in rows if r.company == company}
	return [n for n in names if n in set_up_here or n not in set_up_anywhere]


def first_mode_of_payment(company: str | None) -> str | None:
	"""The first enabled mode of payment the company may use, in alphabetical order."""
	allowed = modes_of_payment_for(company)
	if allowed is None:
		return frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name", order_by="name")
	return allowed[0] if allowed else None


def mode_of_payment_usable(mode: str, company: str | None) -> bool:
	"""False when the mode of payment is set up only for other businesses."""
	allowed = modes_of_payment_for(company, only_enabled=False)
	return allowed is None or mode in allowed
