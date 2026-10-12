"""
Recipes for a restaurant: selling a dish takes its ingredients out of stock, and the dish's cost and margin come
from them.

A recipe is an ERPNext Product Bundle: the dish is a parent item that is not kept in stock, and its ingredients are
the bundle's components. ERPNext takes the ingredients from stock on every sale, on both kinds of invoice, and
puts them back when the sale is cancelled or returned. These calls make that easy to set up and read:

* `create_recipe`, `update_recipe`, `get_recipe`, `list_recipes`, `delete_recipe`
* `replace_dish_item`: ERPNext will not turn an item that already has stock history into a bundle parent, so such a
  dish is replaced by a new item that takes over its barcodes
* `record_wastage`: food that was cooked and thrown away, or spoiled, comes out of stock with a reason; a dish is
  expanded into its ingredients
* `get_wastage_report`: what was wasted, by item and by entry

A recipe's ingredient quantities are kept in each ingredient's stock unit, because ERPNext locks a bundle row to it.
An ingredient given in another unit (grams for an item kept in kilograms) is converted when the recipe is saved.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from murzak_pos.api.access_control import ensure_record_access, require_catalogue_role, require_stock_role
from murzak_pos.api.discount_rules import _selling_price_list
from murzak_pos.api.etims_optional import relax_etims_mandatory
from murzak_pos.api.payment_gateway_common import resolve_company

MAX_INGREDIENTS = 60
MAX_PAGE_SIZE = 100
MAX_REPORT_ENTRIES = 100
MAX_REASON = 140
QUANTITY_DECIMALS = 3
# A recipe quantity that rounds to nothing, or loses more than this share of itself, is refused
LARGEST_ROUNDING_LOSS = 0.05
WASTAGE_TYPE = "Wastage"


def _reply(data=None, message: str | None = None, **extra) -> dict:
	result = {"success": True}
	if message:
		result["message"] = message
	if data is not None:
		result["data"] = data
	result.update(extra)
	return result


def _as_list(value, what: str):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	if not value or not isinstance(value, list | tuple):
		frappe.throw(_("Add at least one {0}.").format(what), frappe.ValidationError)
	return list(value)


# --- units ------------------------------------------------------------------------------------------


def to_stock_quantity(item_code: str, stock_uom: str, quantity: float, uom: str | None) -> float:
	"""
	A quantity given in `uom` as a quantity in the item's stock unit.

	The item's own unit table is used first, then ERPNext's general unit conversions. When neither knows the pair the
	call is refused: ERPNext would quietly treat the units as equal, so 250 grams would become 250 kilograms.
	"""
	if not uom or uom == stock_uom:
		return flt(quantity)
	factor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "uom": uom}, "conversion_factor"
	)
	if not factor:
		from erpnext.stock.doctype.item.item import get_uom_conv_factor

		factor = get_uom_conv_factor(uom, stock_uom)
	if not flt(factor):
		frappe.throw(
			_("There is no conversion between {0} and {1} for {2}. Add it to the item's units first.").format(
				uom, stock_uom, item_code
			),
			frappe.ValidationError,
		)
	return flt(quantity) * flt(factor)


def _checked_quantity(item_code: str, quantity: float) -> float:
	"""A stock quantity held to the decimals ERPNext keeps; one that would be lost in the rounding is refused."""
	rounded = flt(quantity, QUANTITY_DECIMALS)
	if rounded <= 0 or abs(rounded - quantity) / quantity > LARGEST_ROUNDING_LOSS:
		frappe.throw(
			_(
				"The quantity of {0} is too small to keep in its stock unit. Use a smaller stock unit for the item."
			).format(item_code),
			frappe.ValidationError,
		)
	return rounded


def clean_ingredients(ingredients, company: str, dish: str | None = None) -> list[dict]:
	"""
	The ingredient lines of a recipe, checked and in stock units, with a repeated ingredient added together.

	Each line has an item_code, a qty above zero and, optionally, the uom that qty is in.
	"""
	lines = _as_list(ingredients, "ingredient")
	if len(lines) > MAX_INGREDIENTS:
		frappe.throw(
			_("A recipe can have at most {0} ingredients.").format(MAX_INGREDIENTS), frappe.ValidationError
		)

	merged: dict[str, dict] = {}
	for number, line in enumerate(lines, start=1):
		code = (line.get("item_code") or "").strip()
		if not code or not frappe.db.exists("Item", code):
			frappe.throw(
				_("Ingredient {0}: item {1} was not found.").format(number, code), frappe.ValidationError
			)
		if code == dish:
			frappe.throw(_("A dish cannot be an ingredient of itself."), frappe.ValidationError)
		ensure_record_access("Item", code)
		item = frappe.db.get_value(
			"Item", code, ["item_name", "stock_uom", "is_stock_item", "disabled"], as_dict=True
		)
		if not item.is_stock_item or item.disabled:
			frappe.throw(
				_("Ingredient {0}: {1} must be an item kept in stock.").format(number, code),
				frappe.ValidationError,
			)
		quantity = flt(line.get("qty"))
		if quantity <= 0:
			frappe.throw(
				_("Ingredient {0}: the quantity of {1} must be more than zero.").format(number, code),
				frappe.ValidationError,
			)
		stock_quantity = to_stock_quantity(
			code, item.stock_uom, quantity, (line.get("uom") or "").strip() or None
		)
		row = merged.setdefault(
			code, {"item_code": code, "item_name": item.item_name, "uom": item.stock_uom, "qty": 0.0}
		)
		row["qty"] += stock_quantity
	for row in merged.values():
		row["qty"] = _checked_quantity(row["item_code"], row["qty"])
	return list(merged.values())


# --- costs -----------------------------------------------------------------------------------------


def _valuation(item_code: str, warehouse: str | None, company: str | None = None) -> float:
	"""
	What one stock unit of an item costs: the store's valuation; failing a store, the average over the business's
	stock on hand; failing that, the item's own valuation, then its last purchase price.
	"""
	if warehouse:
		rate = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate")
		if flt(rate):
			return flt(rate)
	elif company:
		stores = frappe.get_all("Warehouse", filters={"company": company}, pluck="name", limit_page_length=0)
		held = (
			frappe.get_all(
				"Bin",
				filters={"item_code": item_code, "warehouse": ["in", stores], "actual_qty": [">", 0]},
				fields=["actual_qty", "stock_value"],
			)
			if stores
			else []
		)
		quantity = sum(flt(b.actual_qty) for b in held)
		if quantity:
			return flt(sum(flt(b.stock_value) for b in held) / quantity)
	item = (
		frappe.db.get_value("Item", item_code, ["valuation_rate", "last_purchase_rate"], as_dict=True) or {}
	)
	return flt(item.get("valuation_rate")) or flt(item.get("last_purchase_rate"))


def _selling_rate(dish: str, company: str) -> float:
	price_list = _selling_price_list(company)
	rate = None
	if price_list:
		rate = frappe.db.get_value(
			"Item Price", {"item_code": dish, "price_list": price_list, "selling": 1}, "price_list_rate"
		)
	return flt(rate or frappe.db.get_value("Item", dish, "standard_rate"))


def _recipe_rows(
	dish: str, warehouse: str | None, company: str | None = None
) -> tuple[list[dict], float, bool]:
	"""The ingredients of a dish with their cost, the cost of one portion, and whether every cost is known."""
	rows, total, known = [], 0.0, True
	for part in frappe.get_all(
		"Product Bundle Item",
		filters={"parent": dish, "parenttype": "Product Bundle"},
		fields=["item_code", "description", "qty", "uom"],
		order_by="idx asc",
	):
		rate = _valuation(part.item_code, warehouse, company)
		known = known and bool(rate)
		cost = flt(part.qty) * rate
		total += cost
		rows.append(
			{
				"item_code": part.item_code,
				"item_name": frappe.db.get_value("Item", part.item_code, "item_name"),
				"qty": flt(part.qty),
				"uom": part.uom,
				"unit_cost": rate,
				"cost": flt(cost, 2),
			}
		)
	return rows, flt(total, 2), known


# --- reading ---------------------------------------------------------------------------------------


def _dish_for(dish: str) -> dict:
	"""The dish item, refusing one that is missing or belongs to another business."""
	if not dish or not frappe.db.exists("Item", dish):
		frappe.throw(_("Item {0} was not found.").format(dish), frappe.DoesNotExistError)
	ensure_record_access("Item", dish)
	return frappe.db.get_value(
		"Item",
		dish,
		["name", "item_name", "is_stock_item", "stock_uom", "custom_company", "disabled"],
		as_dict=True,
	)


@frappe.whitelist()
def get_recipe(dish: str, company: str | None = None, warehouse: str | None = None) -> dict:
	"""A dish's recipe: ingredients with their cost, the cost of one portion, the selling price and the margin."""
	company = resolve_company(company)
	item = _dish_for(dish)
	if warehouse:
		ensure_record_access("Warehouse", warehouse)
	if not frappe.db.exists("Product Bundle", dish):
		frappe.throw(_("{0} has no recipe yet.").format(dish), frappe.DoesNotExistError)

	ingredients, cost, costs_known = _recipe_rows(dish, warehouse, company)
	price = _selling_rate(dish, company)
	margin = flt(price - cost, 2)
	data = {
		"dish": item.name,
		"dish_name": item.item_name,
		"ingredients": ingredients,
		"cost_per_portion": cost,
		"costs_known": costs_known,
		"selling_price": flt(price, 2),
		"margin": margin,
		"margin_percent": flt(margin / price * 100, 2) if price else 0.0,
	}
	if warehouse:
		from murzak_pos.api.items import get_product_bundle_availability

		data["availability"] = get_product_bundle_availability(dish, warehouse)
	return _reply(data)


@frappe.whitelist()
def list_recipes(
	company: str | None = None, search: str | None = None, page: int = 1, page_size: int = 20
) -> dict:
	"""The business's recipes, a page at a time, each with its ingredient count and the cost of one portion."""
	company = resolve_company(company)
	page = max(cint(page), 1)
	page_size = max(1, min(cint(page_size) or 20, MAX_PAGE_SIZE))
	filters = {"custom_company": company}
	if search:
		filters["item_name"] = ["like", f"%{search.strip()}%"]
	dishes = frappe.get_all("Item", filters=filters, pluck="name", limit_page_length=0)
	recipes = (
		frappe.get_all("Product Bundle", filters={"new_item_code": ["in", dishes]}, pluck="new_item_code")
		if dishes
		else []
	)
	recipes.sort()
	window = recipes[(page - 1) * page_size : page * page_size]

	rows = []
	for code in window:
		ingredients, cost, costs_known = _recipe_rows(code, None, company)
		rows.append(
			{
				"dish": code,
				"dish_name": frappe.db.get_value("Item", code, "item_name"),
				"ingredients_count": len(ingredients),
				"cost_per_portion": cost,
				"costs_known": costs_known,
				"selling_price": flt(_selling_rate(code, company), 2),
			}
		)
	return _reply({"data": rows, "count": len(recipes)})


# --- writing ---------------------------------------------------------------------------------------


def _has_stock_history(item_code: str) -> bool:
	return bool(frappe.db.exists("Stock Ledger Entry", {"item_code": item_code, "is_cancelled": 0})) or bool(
		frappe.db.exists("Bin", {"item_code": item_code, "actual_qty": ["!=", 0]})
	)


@frappe.whitelist(methods=["POST"])
def create_recipe(dish: str, ingredients, company: str | None = None) -> dict:
	"""
	Give a menu item a recipe. The item stops being kept in stock, and its ingredients come out of stock when it sells.

	An item that already has stock history cannot be changed this way; `replace_dish_item` makes the new item.
	"""
	require_catalogue_role()
	company = resolve_company(company)
	item = _dish_for(dish)
	if item.custom_company and item.custom_company != company:
		frappe.throw(_("Item {0} was not found.").format(dish), frappe.DoesNotExistError)
	if frappe.db.exists("Product Bundle", dish):
		frappe.throw(
			_("{0} already has a recipe. Change it with update_recipe.").format(dish), frappe.ValidationError
		)
	if item.is_stock_item and _has_stock_history(dish):
		frappe.throw(
			_(
				"{0} has stock history, so ERPNext cannot make it a recipe dish. Use replace_dish_item to make a new item that takes its place."
			).format(dish),
			frappe.ValidationError,
		)
	rows = clean_ingredients(ingredients, company, dish)

	if item.is_stock_item:
		# Only an item with no stock history gets here, so nothing in the books refers to its stock
		frappe.db.set_value("Item", dish, "is_stock_item", 0)
		frappe.clear_document_cache("Item", dish)
	bundle = frappe.get_doc(
		{
			"doctype": "Product Bundle",
			"new_item_code": dish,
			"items": [
				{"item_code": r["item_code"], "qty": r["qty"], "uom": r["uom"], "description": r["item_name"]}
				for r in rows
			],
		}
	)
	bundle.insert(ignore_permissions=True)
	return get_recipe(dish, company)


@frappe.whitelist(methods=["POST"])
def update_recipe(dish: str, ingredients, company: str | None = None) -> dict:
	"""Replace a dish's ingredients."""
	require_catalogue_role()
	company = resolve_company(company)
	item = _dish_for(dish)
	if item.custom_company and item.custom_company != company:
		frappe.throw(_("Item {0} was not found.").format(dish), frappe.DoesNotExistError)
	if not frappe.db.exists("Product Bundle", dish):
		frappe.throw(
			_("{0} has no recipe yet. Make one with create_recipe.").format(dish), frappe.DoesNotExistError
		)
	rows = clean_ingredients(ingredients, company, dish)
	bundle = frappe.get_doc("Product Bundle", dish)
	bundle.set(
		"items",
		[
			{"item_code": r["item_code"], "qty": r["qty"], "uom": r["uom"], "description": r["item_name"]}
			for r in rows
		],
	)
	bundle.save(ignore_permissions=True)
	return get_recipe(dish, company)


@frappe.whitelist(methods=["POST"])
def delete_recipe(dish: str, company: str | None = None) -> dict:
	"""Remove a dish's recipe. The item stays, still not kept in stock."""
	require_catalogue_role()
	company = resolve_company(company)
	item = _dish_for(dish)
	if item.custom_company and item.custom_company != company:
		frappe.throw(_("Item {0} was not found.").format(dish), frappe.DoesNotExistError)
	if not frappe.db.exists("Product Bundle", dish):
		frappe.throw(_("{0} has no recipe.").format(dish), frappe.DoesNotExistError)
	frappe.delete_doc("Product Bundle", dish, ignore_permissions=True)
	return _reply({"dish": dish}, _("Recipe removed"))


@frappe.whitelist(methods=["POST"])
def replace_dish_item(item_code: str, new_item_code: str | None = None, company: str | None = None) -> dict:
	"""
	Make a new item to take the place of a dish that has stock history, ready to be given a recipe.

	The new item is a copy that is not kept in stock; it takes the old item's barcodes, and the old item is switched off
	so the till stops offering it. Its stock history stays with it.
	"""
	require_catalogue_role()
	company = resolve_company(company)
	old = _dish_for(item_code)
	if old.custom_company and old.custom_company != company:
		frappe.throw(_("Item {0} was not found.").format(item_code), frappe.DoesNotExistError)
	if not old.is_stock_item or not _has_stock_history(item_code):
		frappe.throw(
			_("{0} has no stock history, so it can be given a recipe as it is.").format(item_code),
			frappe.ValidationError,
		)
	new_code = (new_item_code or f"{item_code}-RECIPE").strip()
	if frappe.db.exists("Item", new_code):
		frappe.throw(
			_("Item {0} already exists. Choose another code.").format(new_code), frappe.ValidationError
		)

	source = frappe.get_doc("Item", item_code)
	barcodes = [row.barcode for row in source.get("barcodes")]
	prices = frappe.get_all(
		"Item Price",
		filters={"item_code": item_code},
		fields=["price_list", "price_list_rate", "currency", "uom", "selling", "buying", "brand"],
	)
	new = frappe.copy_doc(source)
	new.item_code = new_code
	new.is_stock_item = 0
	new.disabled = 0
	new.opening_stock = 0
	new.set("barcodes", [])
	# ERPNext adds a price when an item with a standard rate is inserted, using the caller's own rights; the prices
	# are copied below instead
	standard_rate = new.standard_rate
	new.standard_rate = 0
	# A business that is not on eTIMS saves items without KRA classification, and the copy must not demand it either
	relax_etims_mandatory(new)
	new.insert(ignore_permissions=True)
	if standard_rate:
		new.db_set("standard_rate", standard_rate)
	for price in prices:
		copy = frappe.new_doc("Item Price")
		copy.update({**price, "item_code": new_code})
		copy.insert(ignore_permissions=True)

	# Barcodes are unique, so they move: the old item lets go before the new one takes them
	source.set("barcodes", [])
	source.disabled = 1
	relax_etims_mandatory(source)
	source.save(ignore_permissions=True)
	for barcode in barcodes:
		new.append("barcodes", {"barcode": barcode})
	if barcodes:
		relax_etims_mandatory(new)
		new.save(ignore_permissions=True)
	return _reply(
		{"old_item": item_code, "new_item": new.name, "barcodes_moved": len(barcodes)},
		_("{0} now takes the place of {1}. Give it a recipe with create_recipe.").format(new.name, item_code),
	)


# --- wastage ---------------------------------------------------------------------------------------


def _wastage_type() -> str:
	"""The Stock Entry Type used for wastage, made on first use."""
	if not frappe.db.exists("Stock Entry Type", WASTAGE_TYPE):
		frappe.get_doc(
			{"doctype": "Stock Entry Type", "name": WASTAGE_TYPE, "purpose": "Material Issue"}
		).insert(ignore_permissions=True)
	return WASTAGE_TYPE


def _default_branch(company: str) -> str | None:
	"""
	The branch a business's entries are booked to, when its books ask for one.

	A business on eTIMS has a Branch accounting dimension that every profit and loss posting must carry; it is the
	branch named in its active eTIMS settings. Without eTIMS settings, the dimension's default for the business.
	"""
	if frappe.db.exists("DocType", "Navari KRA eTims Settings"):
		branch = frappe.db.get_value(
			"Navari KRA eTims Settings", {"company": company, "is_active": 1}, "bhfid"
		)
		if branch:
			return branch
	dimension = frappe.db.get_value(
		"Accounting Dimension", {"document_type": "Branch", "disabled": 0}, "name"
	)
	if not dimension:
		return None
	return frappe.db.get_value(
		"Accounting Dimension Detail", {"parent": dimension, "company": company}, "default_dimension"
	)


def expand_wastage(items) -> dict[str, float]:
	"""
	The stock to take out for the items wasted: a dish is its ingredients, an item kept in stock is itself.

	Returns item code -> quantity in the item's stock unit.
	"""
	wanted: dict[str, float] = {}
	for number, line in enumerate(_as_list(items, "item"), start=1):
		code = (line.get("item_code") or "").strip()
		if not code or not frappe.db.exists("Item", code):
			frappe.throw(_("Row {0}: item {1} was not found.").format(number, code), frappe.ValidationError)
		ensure_record_access("Item", code)
		quantity = flt(line.get("qty"))
		if quantity <= 0:
			frappe.throw(
				_("Row {0}: the quantity of {1} must be more than zero.").format(number, code),
				frappe.ValidationError,
			)
		if frappe.db.exists("Product Bundle", code):
			parts = frappe.get_all(
				"Product Bundle Item",
				filters={"parent": code, "parenttype": "Product Bundle"},
				fields=["item_code", "qty"],
			)
			for part in parts:
				wanted[part.item_code] = wanted.get(part.item_code, 0.0) + flt(part.qty) * quantity
		elif frappe.db.get_value("Item", code, "is_stock_item"):
			wanted[code] = wanted.get(code, 0.0) + quantity
		else:
			frappe.throw(
				_(
					"Row {0}: {1} is not kept in stock and has no recipe, so there is no stock to take out."
				).format(number, code),
				frappe.ValidationError,
			)
	return wanted


@frappe.whitelist(methods=["POST"])
def record_wastage(
	items,
	warehouse: str,
	reason: str,
	company: str | None = None,
	posting_date: str | None = None,
	ticket: str | None = None,
) -> dict:
	"""
	Take wasted or spoiled stock out, with the reason. A dish is taken out as its ingredients.

	A refund puts stock back, which is wrong for a dish already cooked; this is what a kitchen uses instead.
	`ticket` is the kitchen ticket it came from, if any, and is kept in the entry's remarks.
	"""
	require_stock_role()
	company = resolve_company(company)
	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Say why the stock is being written off."), frappe.ValidationError)
	if len(reason) > MAX_REASON:
		frappe.throw(
			_("The reason can be at most {0} characters.").format(MAX_REASON), frappe.ValidationError
		)
	if not warehouse or not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("Choose a warehouse."), frappe.ValidationError)
	ensure_record_access("Warehouse", warehouse)
	if frappe.db.get_value("Warehouse", warehouse, "company") != company:
		frappe.throw(
			_("Warehouse {0} belongs to another business.").format(warehouse), frappe.PermissionError
		)

	wanted = expand_wastage(items)
	remarks = f"{WASTAGE_TYPE}: {reason}" + (f" (kitchen ticket {ticket})" if ticket else "")
	entry = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": _wastage_type(),
			"purpose": "Material Issue",
			"company": company,
			"from_warehouse": warehouse,
			"posting_date": getdate(posting_date) if posting_date else nowdate(),
			"remarks": remarks,
			"items": [
				{
					"item_code": code,
					"qty": flt(qty, QUANTITY_DECIMALS),
					"s_warehouse": warehouse,
					"uom": frappe.db.get_value("Item", code, "stock_uom"),
					"stock_uom": frappe.db.get_value("Item", code, "stock_uom"),
					"conversion_factor": 1,
				}
				for code, qty in wanted.items()
			],
		}
	)
	branch = _default_branch(company) if entry.meta.has_field("branch") else None
	if branch:
		entry.branch = branch
		for row in entry.items:
			if row.meta.has_field("branch"):
				row.branch = branch
	entry.insert(ignore_permissions=True)
	entry.submit()
	return _reply(
		{
			"name": entry.name,
			"reason": reason,
			"items": [
				{"item_code": code, "qty": flt(qty, QUANTITY_DECIMALS)} for code, qty in wanted.items()
			],
			"total_value": flt(entry.total_amount, 2),
		},
		_("Wastage recorded"),
	)


@frappe.whitelist()
def get_wastage_report(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	warehouse: str | None = None,
) -> dict:
	"""What was written off as wastage in a period: totals by item, and the latest entries with their reasons."""
	company = resolve_company(company)
	filters = {"company": company, "stock_entry_type": WASTAGE_TYPE, "docstatus": 1}
	if warehouse:
		ensure_record_access("Warehouse", warehouse)
		filters["from_warehouse"] = warehouse
	if from_date and to_date:
		filters["posting_date"] = ["between", [getdate(from_date), getdate(to_date)]]
	elif from_date:
		filters["posting_date"] = [">=", getdate(from_date)]
	elif to_date:
		filters["posting_date"] = ["<=", getdate(to_date)]

	entries = frappe.get_all(
		"Stock Entry",
		filters=filters,
		fields=["name", "posting_date", "remarks", "total_amount", "from_warehouse"],
		order_by="posting_date desc, creation desc",
		limit_page_length=0,
	)
	by_item: dict[str, dict] = {}
	if entries:
		for line in frappe.get_all(
			"Stock Entry Detail",
			filters={"parent": ["in", [e.name for e in entries]], "parenttype": "Stock Entry"},
			fields=["item_code", "item_name", "qty", "uom", "basic_amount"],
		):
			row = by_item.setdefault(
				line.item_code,
				{
					"item_code": line.item_code,
					"item_name": line.item_name,
					"uom": line.uom,
					"qty": 0.0,
					"value": 0.0,
				},
			)
			row["qty"] += flt(line.qty)
			row["value"] += flt(line.basic_amount)
	items = sorted(by_item.values(), key=lambda r: -r["value"])
	for row in items:
		row["qty"] = flt(row["qty"], QUANTITY_DECIMALS)
		row["value"] = flt(row["value"], 2)
	return _reply(
		{
			"total_value": flt(sum(flt(e.total_amount) for e in entries), 2),
			"entries_count": len(entries),
			"items": items,
			"entries": [
				{
					"name": e.name,
					"date": str(e.posting_date),
					"warehouse": e.from_warehouse,
					"reason": (e.remarks or "").removeprefix(f"{WASTAGE_TYPE}: "),
					"value": flt(e.total_amount, 2),
				}
				for e in entries[:MAX_REPORT_ENTRIES]
			],
		}
	)
