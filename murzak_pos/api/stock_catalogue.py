"""
The one list of products that can be "out of stock".

The Products page, Stock levels and Home each worked out "out of stock" from a different list (Products from every
product the business sells; Stock levels and Home only from products that have a stock record or sold lately), so the
three showed different numbers. This is Products' own list, so all three start from the same products: enabled, kept in
stock, sold by the business. A product with no stock record counts as having none. Services are never in it.
"""

from __future__ import annotations

import frappe


def tracked_items(company: str) -> list[dict]:
	"""item_code, item_name, item_group, stock_uom of the business's enabled, stock-kept, sellable products."""
	return frappe.db.sql(
		"SELECT i.name AS item_code, i.item_name, i.item_group, i.stock_uom FROM `tabItem` i "
		"WHERE i.custom_company = %(company)s AND i.is_sales_item = 1 AND i.is_stock_item = 1 AND i.disabled = 0",
		{"company": company},
		as_dict=True,
	)
