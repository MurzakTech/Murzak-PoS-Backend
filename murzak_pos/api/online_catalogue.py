"""
The online catalogue: the products a business shows on the internet, with their price and live stock.

The WooCommerce connector and the hosted store both read the catalogue from here, so they always agree on
three rules:

- a business shows only its own products (never another business's, which the old sync did not check);
- a product is shown only when it is for sale, switched on and the owner has not hidden it ("Show Online");
- the stock a customer sees is what is really on the shelf, with what other orders have already claimed taken off.

Nothing here needs a signed-in user, so the public store can use it. The callers decide who may ask.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

MAX_PAGE_SIZE = 100


def online_filters(company: str) -> dict:
	"""The filters that pick a business's online products."""
	return {
		"custom_company": company,
		"disabled": 0,
		"is_sales_item": 1,
		"has_variants": 0,
		"custom_show_online": 1,
	}


def default_warehouse(company: str, preferred: str | None = None) -> str:
	"""The warehouse online stock is read from: the one chosen, else the business's oldest shop warehouse."""
	if preferred and frappe.db.exists("Warehouse", {"name": preferred, "company": company}):
		return preferred
	return (
		frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0}, "name", order_by="creation asc"
		)
		or ""
	)


def available_stock(item_codes: list[str], warehouse: str) -> dict[str, float]:
	"""Stock a customer can still buy: on the shelf minus what other orders have reserved."""
	if not item_codes or not warehouse:
		return {}
	rows = frappe.get_all(
		"Bin",
		filters={"warehouse": warehouse, "item_code": ["in", list(item_codes)]},
		fields=["item_code", "actual_qty", "reserved_qty"],
	)
	return {r.item_code: max(0.0, flt(r.actual_qty) - flt(r.reserved_qty)) for r in rows}


def _search_filters(search: str | None) -> list | None:
	text = (search or "").strip()
	if not text:
		return None
	like = f"%{text}%"
	return [["Item", "item_name", "like", like], ["Item", "name", "like", like]]


def count_items(company: str, search: str | None = None, category: str | None = None) -> int:
	filters = online_filters(company)
	if category:
		filters["item_group"] = category
	rows = frappe.get_all(
		"Item", filters=filters, or_filters=_search_filters(search), fields=["count(name) as total"]
	)
	return int(rows[0].total) if rows else 0


def list_items(
	company: str,
	warehouse: str,
	search: str | None = None,
	category: str | None = None,
	page: int = 1,
	page_size: int = 20,
) -> list[dict]:
	"""One page of the online catalogue, in name order, with price and stock."""
	page = max(1, int(page or 1))
	page_size = min(MAX_PAGE_SIZE, max(1, int(page_size or 20)))
	filters = online_filters(company)
	if category:
		filters["item_group"] = category
	items = frappe.get_all(
		"Item",
		filters=filters,
		or_filters=_search_filters(search),
		fields=[
			"name",
			"item_name",
			"item_group",
			"stock_uom",
			"standard_rate",
			"description",
			"image",
			"is_stock_item",
		],
		order_by="item_name asc",
		limit_start=(page - 1) * page_size,
		limit_page_length=page_size,
	)
	return rows_for(items, warehouse)


def rows_for(items: list, warehouse: str) -> list[dict]:
	stock = available_stock([i.name for i in items], warehouse)
	rows = []
	for it in items:
		tracked = bool(it.is_stock_item)
		qty = stock.get(it.name, 0.0) if tracked else None
		rows.append(
			{
				"item_code": it.name,
				"item_name": it.item_name,
				"category": it.item_group,
				"unit": it.stock_uom,
				"price": flt(it.standard_rate),
				"description": it.get("description") or "",
				"image": it.get("image") or "",
				"stock": qty,
				# A service or other untracked product is always available
				"in_stock": True if qty is None else qty > 0,
			}
		)
	return rows


def iter_all_items(company: str, warehouse: str, batch: int = 100):
	"""Every online product, a batch at a time (so a large shop is never cut off)."""
	page = 1
	while True:
		rows = list_items(company, warehouse, page=page, page_size=batch)
		if not rows:
			return
		yield rows
		if len(rows) < batch:
			return
		page += 1


def categories(company: str) -> list[str]:
	"""The product categories that have at least one online product."""
	return sorted(
		{
			g
			for g in frappe.get_all("Item", filters=online_filters(company), pluck="item_group")
			if g
		}
	)
