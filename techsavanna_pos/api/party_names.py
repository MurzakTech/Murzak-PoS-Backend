"""
Names for new suppliers, customers and price lists when each business keeps its own records.

Frappe names a supplier or customer by its own name ("Supplier Name" in Buying Settings, "Customer Name"
in Selling Settings), so a name can exist only once on the whole site, whichever business made it.
Two businesses that both deal with "Bidco" would clash: the second could not create their own record,
and could not use the first's either, since each business sees only its own.

The rule: when another business already uses the name, the new record carries the creating business's
company code, "Bidco - SHB", the same convention Frappe uses for warehouses and accounts. Nothing
changes when the name is free, or when the site names these records by number series (the name is then
a code, and the display name can repeat).
"""

from __future__ import annotations

import frappe

# The setting that says how each kind of record is named
NAMING_SETTING = {"Supplier": "supp_master_name", "Customer": "cust_master_name"}


def names_must_be_unique(doctype: str) -> bool:
	"""False when the site names these records by number series, so the same display name may repeat."""
	setting = NAMING_SETTING.get(doctype)
	return setting is None or frappe.defaults.get_global_default(setting) != "Naming Series"


def name_for_new_record(doctype: str, wanted: str, company: str | None) -> str:
	"""
	The name a new supplier or customer should get: the one asked for, or with the company code added.

	The caller has already ruled out a duplicate belonging to the same business, so a name that is taken
	here is taken by another business. When the company has no code (the Administrator naming none), the
	name is returned as it is and the caller's own duplicate handling applies.
	"""
	if not names_must_be_unique(doctype) or not frappe.db.exists(doctype, wanted):
		return wanted
	code = frappe.db.get_value("Company", company, "abbr") if company else None
	return f"{wanted} - {code}" if code else wanted
