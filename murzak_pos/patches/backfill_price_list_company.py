"""
Give every price list an owning company, so each business sees and changes only its own.

Until now price lists belonged to nobody, so every business could list, rename and delete every other
business's price lists. This adds a `custom_company` field to Price List and fills it in for the lists
that already exist, using evidence rather than guesses:

1. a list that only one company's till profiles, invoices, orders or priced items use belongs to it;
2. a list nobody uses belongs to the company of the person who created it;
3. on a site with a single company, anything left belongs to that company.

The two standard lists ("Standard Selling", "Standard Buying") stay shared, since every business uses
them, and so does a list used by several companies or made by someone with no company (for example the
Administrator in the desk). The Administrator can set the company of any list by hand.

The field is created here as well as shipped in custom/price_list.json: on `bench migrate` the patches
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
	"insert_after": "price_list_name",
	"search_index": 1,
	"in_standard_filter": 1,
	"description": (
		"Company that owns this price list. Price lists are kept apart per company so one business "
		"never sees or changes another's. Leave empty for a list shared by every business."
	),
}

# Lists every business uses
SHARED = ("Standard Selling", "Standard Buying")

# (document type, field holding the price list, field holding the company)
USES = (
	("POS Profile", "selling_price_list", "company"),
	("Sales Invoice", "selling_price_list", "company"),
	("Purchase Invoice", "buying_price_list", "company"),
	("Purchase Order", "buying_price_list", "company"),
)


def execute():
	create_custom_fields({"Price List": [FIELD]}, update=True)

	untagged = [
		name
		for name in frappe.get_all("Price List", filters={"custom_company": ["is", "not set"]}, pluck="name")
		if name not in SHARED
	]
	if not untagged:
		return

	used_by = companies_that_use_lists()
	companies = frappe.get_all("Company", pluck="name")
	only_company = companies[0] if len(companies) == 1 else None

	tagged = 0
	for name in untagged:
		company = decide_company(name, used_by.get(name, set()), only_company)
		if company:
			frappe.db.set_value("Price List", name, "custom_company", company, update_modified=False)
			tagged += 1

	print(
		f"Price list company: {tagged} of {len(untagged)} existing price lists tagged, {len(untagged) - tagged} left shared"
	)


def companies_that_use_lists() -> dict[str, set[str]]:
	"""For each price list, the companies whose profiles, documents or items use it."""
	found: dict[str, set[str]] = defaultdict(set)
	for doctype, list_field, company_field in USES:
		extra = " where docstatus < 2" if doctype != "POS Profile" else ""
		for row in frappe.db.sql(
			f"select distinct `{list_field}` as price_list, `{company_field}` as company from `tab{doctype}`{extra}",
			as_dict=True,
		):
			if row.price_list and row.company:
				found[row.price_list].add(row.company)

	# A price list that prices a company's items is that company's
	if frappe.db.has_column("Item", "custom_company"):
		for row in frappe.db.sql(
			"""select distinct ip.price_list as price_list, i.custom_company as company
			from `tabItem Price` ip join `tabItem` i on i.name = ip.item_code
			where i.custom_company is not null and i.custom_company != ''""",
			as_dict=True,
		):
			if row.price_list and row.company:
				found[row.price_list].add(row.company)
	return found


def decide_company(price_list: str, used_by: set[str], only_company: str | None) -> str | None:
	"""The company a price list belongs to, or None when the evidence does not settle it."""
	if len(used_by) == 1:
		return next(iter(used_by))
	if len(used_by) > 1:
		return None  # used by several businesses: stays shared

	owner = frappe.db.get_value("Price List", price_list, "owner")
	if owner and owner != "Administrator":
		company = frappe.db.get_value("User", owner, "custom_company") or frappe.defaults.get_user_default(
			"Company", user=owner
		)
		if company:
			return company

	return only_company
