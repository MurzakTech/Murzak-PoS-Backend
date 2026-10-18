"""
The Products page: the list with a correct count, the categories, and the one-screen New product (tickets P4-1, P4-2).

`list_products`, `list_categories` and `create_product_simple` return what the contract
docs/redesign/P4-1-contract.md describes. They are new; no existing call, report or record is changed. The old
`get_products` and `create_product` are left exactly as they are, and `create_product_simple` calls
`create_product` for the product itself.

How it is built
- The rules and the arithmetic are in products_list_calc.py (plain functions, no database). This file reads the
  database in small functions, one job each, and joins the two.
- The count is the number of products that match the SAME filters as the rows: both are made from one set of
  query parts (`products_list_calc.build_query`), so they cannot disagree. (The old `get_products` counted without
  the search text, and with an industry set it also counted without the category.)
- Stock is the sum over the business's stores, or one store when the caller names it. The answer says which.
- Roles are checked here, on the server (`role_tiers`): a cashier gets the list but never the cost price or the
  profit per item (the keys are left out of the answer, not set to null).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from murzak_pos.api import products_list_calc as rules
from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access, require_catalogue_role
from murzak_pos.api.payment_gateway_common import resolve_company

STANDARD_SELLING = "Standard Selling"


# --- the existing calls this file uses (small on purpose, so tests can replace them) ----------------


def _create_product(**values):
	"""The existing product creation, unchanged."""
	from murzak_pos.api.product_api import create_product

	return create_product(**values)


def _stock_entry(**values):
	"""The existing stock entry the Stock in screen uses (a Material Receipt)."""
	from murzak_pos.api.inventory_api import create_stock_entry

	return create_stock_entry(**values)


def _save_buying_price(item_code, warehouse, company, buying_price):
	"""The existing helper the old product screen uses to keep a product's buying price for a store."""
	from murzak_pos.api.inventory_api import _create_or_update_inventory_item_details

	return _create_or_update_inventory_item_details(
		item_code=item_code, warehouse=warehouse, company=company, buying_price=buying_price
	)


def _default_warehouse(company):
	from murzak_pos.api.warehouse_api import get_default_warehouse_for_company

	return get_default_warehouse_for_company(company)


def _next_code():
	"""The next automatic product code (PRD-00001 ...) that no product uses yet."""
	from frappe.model.naming import make_autoname

	for _attempt in range(50):
		code = make_autoname(rules.CODE_SERIES)
		if not frappe.db.exists("Item", code):
			return code
	frappe.throw(_("Could not make a product code. Please try again."), frappe.ValidationError)


def _relax_etims(doc):
	from murzak_pos.api.etims_optional import relax_etims_mandatory

	return relax_etims_mandatory(doc)


# --- small reads ------------------------------------------------------------------------------------


def _industry():
	"""(the person's industry, whether products carry one) for the same filter the old list applied."""
	if not frappe.db.has_column("Item", "custom_pos_industry"):
		return None, False
	if not frappe.db.has_column("User", "custom_pos_industry"):
		return None, True
	try:
		value = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
	except Exception:
		value = None
	return value or None, True


def _selling_price_list():
	"""The price list the product form saves prices in (the same choice `create_product` makes)."""
	name = None
	try:
		name = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	except Exception:
		name = None
	if name and frappe.db.exists("Price List", name):
		return name
	return STANDARD_SELLING if frappe.db.exists("Price List", STANDARD_SELLING) else None


def _prices(codes, price_list):
	"""item_code -> its prices in the price list."""
	if not codes or not price_list:
		return {}
	found: dict = {}
	for row in frappe.get_all(
		"Item Price",
		filters={"item_code": ["in", codes], "price_list": price_list},
		fields=["item_code", "uom", "price_list_rate"],
	):
		found.setdefault(row["item_code"], []).append(row)
	return found


def _barcodes(codes):
	"""item_code -> its barcodes, first one first."""
	if not codes:
		return {}
	found: dict = {}
	for row in frappe.get_all(
		"Item Barcode", filters={"parent": ["in", codes]}, fields=["parent", "barcode"], order_by="idx"
	):
		found.setdefault(row["parent"], []).append(row["barcode"])
	return found


def _costs(codes, company, preferred_store):
	"""
	item_code -> cost price, for the products on this page (only read for a person who may see cost):
	the buying price recorded for the store, else the product's valuation rate, else a store's valuation rate.
	"""
	if not codes:
		return {}
	buying: dict = {}
	if frappe.db.exists("DocType", "Inventory Item Details"):
		for row in frappe.get_all(
			"Inventory Item Details",
			filters={"item_code": ["in", codes], "company": company},
			fields=["item_code", "warehouse", "buying_price"],
		):
			buying.setdefault(row["item_code"], []).append(row)
	item_rates = {
		row["name"]: row["valuation_rate"]
		for row in frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "valuation_rate"])
	}
	stores = frappe.get_all("Warehouse", filters={"company": company, "is_group": 0}, pluck="name")
	bin_rates: dict = {}
	if stores:
		for row in frappe.get_all(
			"Bin",
			filters={"item_code": ["in", codes], "warehouse": ["in", stores]},
			fields=["item_code", "valuation_rate"],
		):
			bin_rates.setdefault(row["item_code"], []).append(row["valuation_rate"])
	return {
		code: rules.cost_price(
			rules.choose_buying_price(buying.get(code), preferred_store),
			item_rates.get(code),
			bin_rates.get(code),
		)
		for code in codes
	}


def _check_store(warehouse, company):
	"""A store named in the request must be one of this business's."""
	if warehouse and frappe.db.get_value("Warehouse", warehouse, "company") not in (None, company):
		frappe.throw(_("That store belongs to another business."), frappe.PermissionError)


# --- the list ---------------------------------------------------------------------------------------


@frappe.whitelist()
def list_products(
	search=None, category=None, status=None, page=1, page_size=20, company=None, warehouse=None
):
	"""One page of the Products list, its exact count and its summary (see the contract for the answer)."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	ensure_record_access("Warehouse", warehouse)
	try:
		status = rules.normalize_status(status)
	except rules.ListError:
		frappe.throw(_("Choose a product view: active, all, low, out or disabled."), frappe.ValidationError)
	company = resolve_company(company)
	_check_store(warehouse, company)
	show_cost = role_tiers.can("cost_of_goods")

	try:
		industry, has_industry = _industry()
		parts = rules.build_query(
			company=company,
			warehouse=warehouse or None,
			category=(category or "").strip() or None,
			status=status,
			search=search,
			industry=industry,
			has_industry_column=has_industry,
		)
		# The count and the rows are made from the same parts, so they always agree
		total = cint(frappe.db.sql(rules.count_sql(parts), parts["params"])[0][0])
		numbers = rules.page_numbers(page, page_size, total)
		raw_rows = frappe.db.sql(
			rules.rows_sql(parts),
			{**parts["params"], "limit": numbers["page_size"], "offset": numbers["offset"]},
			as_dict=True,
		)
		codes = [row["item_code"] for row in raw_rows]
		price_list = _selling_price_list()
		prices, barcodes = _prices(codes, price_list), _barcodes(codes)
		costs = _costs(codes, company, warehouse or _default_warehouse(company)) if show_cost else {}
		rows = [
			rules.shape_row(
				row,
				prices=prices.get(row["item_code"]),
				barcodes=barcodes.get(row["item_code"]),
				cost=costs.get(row["item_code"]),
				show_cost=show_cost,
			)
			for row in raw_rows
		]
		summary = _summary(parts)
	except Exception:
		frappe.log_error("Products list error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the products. Please try again.")}

	return {
		"success": True,
		"data": {
			"rows": rows,
			"total": total,
			"page": numbers["page"],
			"page_size": numbers["page_size"],
			"pages": numbers["pages"],
			"summary": summary,
			"status": status,
			"stock_scope": {"kind": "store", "warehouse": warehouse}
			if warehouse
			else {"kind": "all_stores", "warehouse": None},
			"price_list": price_list,
			"can_see_cost": bool(show_cost),
		},
	}


def _summary(parts):
	"""The numbers above the list. If they fail the list still draws: the summary is null and the error is logged."""
	try:
		row = frappe.db.sql(rules.summary_sql(parts), parts["params"], as_dict=True)
		return rules.shape_summary(row[0] if row else None)
	except Exception:
		frappe.log_error("Products summary error", frappe.get_traceback())
		return None


@frappe.whitelist()
def list_categories(company=None):
	"""The categories of the shop with how many products each holds (see the contract for the answer)."""
	if role_tiers.get_tier() is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	company = resolve_company(company)
	try:
		industry, has_industry = _industry()
		parts = rules.build_query(company=company, industry=industry, has_industry_column=has_industry)
		groups = frappe.get_all(
			"Item Group", fields=["name", "parent_item_group", "is_group"], order_by="name"
		)
		counts = {
			row["category"]: cint(row["n"])
			for row in frappe.db.sql(rules.category_counts_sql(parts), parts["params"], as_dict=True)
		}
		categories = rules.shape_categories(groups, counts)
	except Exception:
		frappe.log_error("Product categories error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the categories. Please try again.")}
	return {
		"success": True,
		"data": {
			"categories": categories,
			"total": len(categories),
			"product_total": sum(counts.values()),
		},
	}


# --- the New product form ---------------------------------------------------------------------------


def _field_messages():
	"""The words for each reason a form field is refused, in the caller's language."""
	return {
		"name_required": _("Enter a name for the product."),
		"name_too_long": _("The product name is too long. Use 140 letters or fewer."),
		"price_required": _("Enter a price."),
		"price_invalid": _("The price must be a number, 0 or more."),
		"cost_invalid": _("The cost price must be a number, 0 or more."),
		"stock_invalid": _("Opening stock must be a number, 0 or more."),
		"low_invalid": _("The low-stock warning must be a number, 0 or more."),
		"too_big": _("That number is too big. Check it and try again."),
		"code_invalid": _(
			"The product code can have letters, numbers, spaces, dashes, dots and underscores only."
		),
		"code_too_long": _("The product code is too long."),
		"code_taken": _("A product with this code already exists."),
		"barcode_invalid": _("The barcode cannot have spaces or hidden characters."),
		"barcode_taken": _("This barcode is already used by another product."),
		"category_missing": _("That category does not exist. Choose one from the list."),
		"unit_missing": _("That unit does not exist. Choose one from the list."),
		"no_store": _("Add a store before you enter opening stock or a low-stock warning."),
		"save_failed": _("The product could not be saved. Please check the details and try again."),
	}


def _refusal(problems):
	"""The answer for a form that cannot be saved: the first message, and every field with its own message."""
	words = _field_messages()
	errors = [{"field": p["field"], "message": words.get(p["code"], words["save_failed"])} for p in problems]
	return {"success": False, "message": errors[0]["message"], "errors": errors}


def _store_for(company, warehouse):
	"""The store opening stock and the low-stock warning go to: the one named, else the business's own."""
	if warehouse:
		return warehouse
	names = frappe.get_all(
		"Warehouse", filters={"company": company, "is_group": 0}, pluck="name", order_by="creation asc"
	)
	return rules.pick_warehouse(_default_warehouse(company), names)


def _step(notes, label, work):
	"""Run one extra step after the product exists. If it fails the product stays and `label` is noted."""
	try:
		return True, work()
	except Exception:
		frappe.log_error("New product extra step", f"{label}\n{frappe.get_traceback()}")
		notes.append(label)
		return False, None


def _note_words(step):
	"""What to do again when an extra step failed, in plain words."""
	words = {
		"low_stock_warning": _(
			"The product was saved, but the low-stock warning was not. Set it again later."
		),
		"cost_price": _("The product was saved, but the cost price was not. Add it again later."),
		"opening_stock": _(
			"The product was saved, but the opening stock was not added. Use Stock in to add it."
		),
	}
	return words.get(step, _("The product was saved, but one extra step failed."))


def _add_warning(code, store, level):
	"""The low-stock warning, as a row of the product's Reorder table (what the alerts and the Home card read)."""
	doc = frappe.get_doc("Item", code)
	doc.append("reorder_levels", rules.reorder_row(store, level))
	_relax_etims(doc)
	doc.save(ignore_permissions=True)


def _add_cost(code, store, company, cost):
	"""The cost price: on the product, and kept for the store the way the old product screen keeps it."""
	doc = frappe.get_doc("Item", code)
	doc.valuation_rate = rules.money(cost)
	_relax_etims(doc)
	doc.save(ignore_permissions=True)
	if store:
		_save_buying_price(code, store, company, rules.money(cost))


def _add_stock(code, store, company, qty, cost):
	"""The opening stock, as a Material Receipt through the existing stock entry. Raises if it is refused."""
	line = rules.opening_stock_item(code, qty, store, cost)
	reply = _stock_entry(
		stock_entry_type="Material Receipt", items=[line], company=company, to_warehouse=store
	)
	# The existing call answers in words instead of raising when it refuses
	if isinstance(reply, dict) and reply.get("success") is False:
		raise frappe.ValidationError(str(reply.get("message")))
	return reply


@frappe.whitelist(methods=["POST"])
def create_product_simple(
	item_name=None,
	price=None,
	item_code=None,
	category=None,
	unit=None,
	barcode=None,
	cost_price=None,
	opening_stock=None,
	low_stock_warning=None,
	warehouse=None,
	company=None,
):
	"""
	Save a new product from the one-screen form. Only the name and the price are needed.

	The product itself is made by the existing `create_product`. Then, when given: the low-stock warning is saved on
	the product, the cost price is kept on the product and for the store (as the old screen does), and the opening
	stock goes in as a Material Receipt through the existing stock entry the Stock in screen uses. If one of those
	extra steps fails the product is still saved and the answer says which step to do again.
	"""
	# Prices and the product catalogue: only roles the frontend gives the price screens
	require_catalogue_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	_check_store(warehouse, company)

	clean, problems = rules.check_new_product(
		{
			"item_name": item_name,
			"price": price,
			"item_code": item_code,
			"category": category,
			"unit": unit,
			"barcode": barcode,
			"cost_price": cost_price,
			"opening_stock": opening_stock,
			"low_stock_warning": low_stock_warning,
		}
	)
	if clean["cost_price"] is not None:
		role_tiers.require_access("cost_of_goods")

	# Things only the database can tell
	if not problems:
		if clean["item_code"] and frappe.db.exists("Item", clean["item_code"]):
			problems.append({"field": "item_code", "code": "code_taken"})
		if clean["barcode"] and frappe.db.exists("Item Barcode", {"barcode": clean["barcode"]}):
			problems.append({"field": "barcode", "code": "barcode_taken"})
		if clean["category"] and not frappe.db.exists("Item Group", clean["category"]):
			problems.append({"field": "category", "code": "category_missing"})
		if clean["unit"] and not frappe.db.exists("UOM", clean["unit"]):
			problems.append({"field": "unit", "code": "unit_missing"})
	wants_store = bool(clean["opening_stock"] or clean["low_stock_warning"])
	store = _store_for(company, warehouse) if not problems and (wants_store or clean["cost_price"]) else None
	if not problems and wants_store and not store:
		problems.append(
			{"field": "opening_stock" if clean["opening_stock"] else "low_stock_warning", "code": "no_store"}
		)
	if problems:
		return _refusal(problems)

	code = clean["item_code"] or _next_code()
	try:
		_create_product(
			item_code=code,
			item_name=clean["item_name"],
			item_group=clean["category"] or rules.ROOT_CATEGORY,
			stock_uom=clean["unit"] or rules.DEFAULT_UNIT,
			standard_rate=clean["price"],
			barcode=clean["barcode"] or None,
			company=company,
		)
	except frappe.ValidationError as error:
		# The existing call refuses in words; show them beside the field they are about when that can be told
		message = str(error)
		field = rules.classify_save_error(message)
		frappe.clear_messages()
		return {
			"success": False,
			"message": message,
			"errors": [{"field": field, "message": message}] if field else [],
		}

	notes: list = []
	cost = clean["cost_price"]
	warning = None
	if clean["low_stock_warning"]:
		saved, _result = _step(
			notes, "low_stock_warning", lambda: _add_warning(code, store, clean["low_stock_warning"])
		)
		warning = {"saved": saved, "level": rules.quantity(clean["low_stock_warning"])}
	if cost:
		_step(notes, "cost_price", lambda: _add_cost(code, store, company, cost))
	opening = None
	if clean["opening_stock"]:
		saved, reply = _step(
			notes, "opening_stock", lambda: _add_stock(code, store, company, clean["opening_stock"], cost)
		)
		opening = {"saved": saved, "qty": rules.quantity(clean["opening_stock"]), "store": store}
		if saved and isinstance(reply, dict):
			opening["stock_entry"] = (reply.get("data") or {}).get("name")

	frappe.db.commit()
	frappe.local.response["http_status_code"] = 200
	return {
		"success": True,
		"message": _("Product saved."),
		"data": {
			"product": {
				"item_code": code,
				"item_name": clean["item_name"],
				"category": clean["category"] or rules.ROOT_CATEGORY,
				"unit": clean["unit"] or rules.DEFAULT_UNIT,
				"price": rules.money(clean["price"]),
				"barcode": clean["barcode"],
			},
			"code_made_automatically": not clean["item_code"],
			"low_stock_warning": warning,
			"opening_stock": opening,
			"store": store,
			"notes": [{"step": step, "message": _note_words(step)} for step in notes],
		},
	}
