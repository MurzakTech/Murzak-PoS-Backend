"""
Give every product that holds stock an owning business, so it shows at the till.

The till and the Products list show only the products tagged with the business (`custom_company`). A product
made some other way (in the ERPNext desk, by an import, or before the tag existed) has no tag, so it holds stock in
a store but never appears at the till. Suppliers and price lists were tagged the same way earlier. This fills in the
tag for products that have none, using evidence rather than guesses:

1. a product whose stock sits only in the stores of one business belongs to that business;
2. a product with no stock records belongs to the business of the person who made it;
3. on a site with a single business, anything left belongs to that business.

A product with stock in the stores of several businesses, or made by someone with no business (the Administrator in
the desk), is left untagged for someone to set by hand. Nothing else about a product is changed.
"""

from collections import defaultdict

import frappe


def execute():
	if not frappe.db.has_column("Item", "custom_company"):
		return

	untagged = frappe.get_all(
		"Item", filters={"custom_company": ["is", "not set"]}, fields=["name", "owner"], limit_page_length=0
	)
	if not untagged:
		return

	stocked_in = companies_holding_stock()
	companies = frappe.get_all("Company", pluck="name")
	only_company = companies[0] if len(companies) == 1 else None

	tagged = 0
	for item in untagged:
		company = decide_company(stocked_in.get(item.name, set()), creator_company(item.owner), only_company)
		if company:
			frappe.db.set_value("Item", item.name, "custom_company", company, update_modified=False)
			tagged += 1
	print(
		f"Item company: {tagged} of {len(untagged)} untagged products tagged, {len(untagged) - tagged} left untagged"
	)


def companies_holding_stock() -> dict[str, set[str]]:
	"""For each product, the businesses whose stores have a stock record (a bin) for it."""
	found: dict[str, set[str]] = defaultdict(set)
	for row in frappe.db.sql(
		"select distinct b.item_code, w.company from `tabBin` b join `tabWarehouse` w on w.name = b.warehouse",
		as_dict=True,
	):
		if row.item_code and row.company:
			found[row.item_code].add(row.company)
	return found


def creator_company(owner: str | None) -> str | None:
	"""The business of the person who made a product (None for the Administrator or someone with no business)."""
	if not owner or owner == "Administrator":
		return None
	return frappe.db.get_value("User", owner, "custom_company") or None


def decide_company(stocked_in: set[str], creator: str | None, only_company: str | None) -> str | None:
	"""The business a product belongs to, or None when the evidence does not settle it."""
	if len(stocked_in) == 1:
		return next(iter(stocked_in))
	if len(stocked_in) > 1:
		return None  # stock in several businesses: someone must say whose it is
	return creator or only_company
