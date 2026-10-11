"""
Give every supplier an owning company, so each business sees only its own suppliers.

Until now suppliers belonged to nobody, so every business could list every other business's suppliers
(with their tax numbers). This adds a `custom_company` field to Supplier and fills it in for the
suppliers that already exist, using evidence rather than guesses:

1. a supplier that has only ever been bought from by one company belongs to that company;
2. a supplier with no purchases belongs to the company of the person who created it;
3. on a site with a single company, anything left belongs to that company.

A supplier bought from by several companies, or made by someone with no company (for example the
Administrator in the desk), is left untagged. It stays visible to each company that has bought from
it, and the Administrator can set its company by hand.

The field is created here as well as shipped in custom/supplier.json: on `bench migrate` the patches
run before the custom files are applied, and this patch needs the field to exist.
"""

from collections import defaultdict

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELD = {
	"fieldname": "custom_company",
	"label": "Company",
	"fieldtype": "Link",
	"options": "Company",
	"insert_after": "supplier_name",
	"search_index": 1,
	"in_standard_filter": 1,
	"description": (
		"Company that owns this supplier. Suppliers are isolated per company so one business "
		"never sees another's supplier list."
	),
}

# Every kind of purchase document that names a supplier and a company
PURCHASE_DOCUMENTS = ("Purchase Order", "Purchase Receipt", "Purchase Invoice")


def execute():
	create_custom_fields({"Supplier": [FIELD]}, update=True)

	untagged = frappe.get_all("Supplier", filters={"custom_company": ["is", "not set"]}, pluck="name")
	if not untagged:
		return

	bought_by = companies_that_bought_from()
	only_company = frappe.get_all("Company", pluck="name")
	only_company = only_company[0] if len(only_company) == 1 else None

	tagged = 0
	for name in untagged:
		company = decide_company(name, bought_by.get(name, set()), only_company)
		if company:
			frappe.db.set_value("Supplier", name, "custom_company", company, update_modified=False)
			tagged += 1

	print(
		f"Supplier company: {tagged} of {len(untagged)} existing suppliers tagged, {len(untagged) - tagged} left untagged"
	)


def companies_that_bought_from() -> dict[str, set[str]]:
	"""For each supplier, the set of companies with a live (not cancelled) purchase document naming it."""
	found: dict[str, set[str]] = defaultdict(set)
	for doctype in PURCHASE_DOCUMENTS:
		for row in frappe.db.sql(
			f"select distinct supplier, company from `tab{doctype}` where docstatus < 2", as_dict=True
		):
			if row.supplier and row.company:
				found[row.supplier].add(row.company)
	return found


def decide_company(supplier: str, bought_by: set[str], only_company: str | None) -> str | None:
	"""The company a supplier belongs to, or None when the evidence does not settle it."""
	if len(bought_by) == 1:
		return next(iter(bought_by))
	if len(bought_by) > 1:
		return None  # shared between businesses: stays visible to each that bought from it

	owner = frappe.db.get_value("Supplier", supplier, "owner")
	if owner and owner != "Administrator":
		company = frappe.db.get_value("User", owner, "custom_company") or frappe.defaults.get_user_default(
			"Company", user=owner
		)
		if company:
			return company

	return only_company
