"""
The rules behind the product import (ticket P7-1), with no database and no Frappe.

`product_import_api` reads what the shop already has (categories, brands, units, stores, products) into one plain
`facts` dict, hands it to `plan_import` with the rows of the sheet, and gets back a plan: for every row what was
understood and what will happen. The preview shows the plan; the import carries it out. Both come from the same
function, so what the preview says is what the import does.

Decisions
- A product code that already exists is UPDATED, never created twice and never skipped silently. Only the cells
  that are filled in change anything: a blank cell keeps what the product has. A second import of the same sheet
  therefore finds every row already up to date ("No changes") and does nothing.
- A product's unit is never changed after it exists (the books may already use it); the preview says so.
- Opening stock goes in only for a product that has never had any stock (it is new, or has no stock history), so
  importing the same sheet twice cannot add the stock twice. It needs a cost price, because the books value
  stock at cost; without one the product is saved and the stock is left out, and the preview says so.
- A picture is added only to a product that has none. A picture that cannot be used never stops the product.
- Names of categories, brands, units and stores are matched ignoring capital letters and spaces, so "Drinks" and
  " drinks" are one category. The spelling already in the shop wins; for a new one the first spelling in the sheet.
- Row problems stop only that row. File-wide limits are checked first (`MAX_ROWS`).
"""

from __future__ import annotations

import re

from murzak_pos.api import product_import_images as pictures
from murzak_pos.api.products_list_calc import (
	DEFAULT_UNIT,
	MAX_AMOUNT,
	MAX_BARCODE_LENGTH,
	ROOT_CATEGORY,
	AmountError,
	money,
	parse_amount,
	quantity,
)

MAX_ROWS = 2000
MAX_TEXT = 140  # product code, name, category, brand
MAX_DESCRIPTION = 2000
NEW_PRODUCT_CATEGORY = ROOT_CATEGORY

VAT_CLASSES = ("standard", "zero_rated", "exempt")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_YES = ("1", "true", "yes", "y")
_NO = ("0", "false", "no", "n")
_VAT_WORDS = {
	"standard": "standard",
	"vat": "standard",
	"taxed": "standard",
	"16": "standard",
	"16%": "standard",
	"zerorated": "zero_rated",
	"zero": "zero_rated",
	"0": "zero_rated",
	"0%": "zero_rated",
	"exempt": "exempt",
	"exempted": "exempt",
}


def key_of(text) -> str:
	"""A name for comparing: capitals and spaces ignored, so 'Soft Drinks' and 'softdrinks ' are the same."""
	return re.sub(r"\s+", "", str(text if text is not None else "")).casefold()


def tidy(text) -> str:
	"""A name for keeping: outer spaces removed and runs of spaces made one."""
	return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


def _flag(value):
	"""True or False for a yes/no cell, None for blank, 'bad' for anything else."""
	if isinstance(value, bool):
		return value
	text = str(value if value is not None else "").strip().lower()
	if text == "":
		return None
	if text in _YES:
		return True
	if text in _NO:
		return False
	return "bad"


# --- one row on its own -----------------------------------------------------------------------------


def clean_row(raw: dict, row_number: int):
	"""
	Read one row of the sheet on its own (nothing about the shop yet).
	Returns (clean values, problems); a problem is {"field", "code", "value"?}.
	"""
	problems: list = []
	clean: dict = {"row": row_number}

	def fail(field, code, value=None):
		problem = {"field": field, "code": code}
		if value is not None:
			problem["value"] = value
		problems.append(problem)

	def text(name):
		return str(raw.get(name) if raw.get(name) is not None else "")

	code = text("item_code").strip()
	clean["item_code"] = code
	if not code:
		fail("item_code", "code_required")
	elif len(code) > MAX_TEXT:
		fail("item_code", "code_long")
	elif _CONTROL.search(code):
		fail("item_code", "code_bad")

	name = tidy(text("item_name"))
	clean["item_name"] = name
	if not name:
		fail("item_name", "name_required")
	elif len(name) > MAX_TEXT:
		fail("item_name", "name_long")

	for field in ("category", "brand", "unit", "store"):
		value = tidy(text(field))
		clean[field] = value
		if len(value) > MAX_TEXT:
			fail(field, "text_long", value[:30])

	description = text("description").strip()
	clean["description"] = description
	if len(description) > MAX_DESCRIPTION:
		fail("description", "text_long", description[:30])

	def amount(field, code_bad):
		try:
			value = parse_amount(raw.get(field))
		except AmountError:
			fail(field, code_bad, text(field).strip()[:30])
			return None
		if value is not None and value > MAX_AMOUNT:
			fail(field, "too_big", text(field).strip()[:30])
			return None
		return value

	clean["price"] = amount("price", "price_bad")
	clean["cost_price"] = amount("cost_price", "cost_bad")
	clean["opening_stock"] = amount("opening_stock", "stock_bad")
	clean["low_stock_level"] = amount("low_stock_level", "low_bad")

	barcode = text("barcode").strip()
	clean["barcode"] = barcode
	if barcode and (len(barcode) > MAX_BARCODE_LENGTH or _CONTROL.search(barcode) or " " in barcode):
		fail("barcode", "barcode_bad")

	service = _flag(raw.get("is_service"))
	clean["is_service"] = service is True
	clean["is_service_given"] = service is not None and service != "bad"
	if service == "bad":
		fail("is_service", "flag_bad", text("is_service").strip()[:30])

	vat = text("vat_class").strip()
	clean["vat_class"] = ""
	if vat:
		found = _VAT_WORDS.get(key_of(vat).replace("-", "").replace("_", ""))
		if found:
			clean["vat_class"] = found
		else:
			fail("vat_class", "vat_bad", vat[:30])

	image = text("image").strip()
	clean["image"] = {"kind": "", "value": ""}
	if image:
		if re.match(r"^https?://", image, re.I):
			clean["image"] = {"kind": "link", "value": image}
		elif image.lower().startswith("/files/") and ".." not in image:
			clean["image"] = {"kind": "path", "value": image}
		else:
			file_name = image.replace("\\", "/").rsplit("/", 1)[-1].strip()
			if ".." in image.replace("\\", "/").split("/") or pictures.extension_of(file_name) not in (
				pictures.ALLOWED_EXTENSIONS
			):
				fail("image", "image_name_bad", image[:40])
			else:
				clean["image"] = {"kind": "file", "value": file_name}
	return clean, problems


# --- the plan ---------------------------------------------------------------------------------------


def _same_number(a, b) -> bool:
	return abs(money(a) - money(b)) < 0.005


def plan_import(raw_rows, facts: dict, default_store=None, photos=None, link_check=None) -> dict:
	"""
	Work out what the import will do with every row.

	`facts` holds what the shop already has:
	  groups, brands, uoms, stores   key_of(name) -> name as stored
	  items                          key_of(code) -> {name, custom_company?, item_name, item_group, description, brand,
	                                 image, is_stock_item, price, valuation_rate, has_stock, disabled, stock_uom,
	                                 barcodes[], low_levels{store: level}}
	  barcode_owner                  barcode -> item code that already uses it
	  company                        the caller's business
	`photos` is the ZIP's picture index (key_of(file name) -> None when usable, else an error code) or None when no
	ZIP was sent. `link_check(url)` raises ImageProblem for a link the server must not fetch, or returns.
	"""
	rows = list(raw_rows or [])
	plan_rows: list = []
	seen_codes: dict = {}
	seen_barcodes: dict = {}
	new_categories: dict = {}
	new_brands: dict = {}
	spellings: dict = {}  # key_of(name) -> the first spelling met, for a category or brand the shop does not have yet
	stock_stores: set = set()

	for index, raw in enumerate(rows):
		raw = raw if isinstance(raw, dict) else {}
		number = raw.get("row_number") or index + 2
		clean, problems = clean_row(raw, number)
		notes: list = []
		changes: list = []

		def fail(field, code, value=None, _problems=problems):
			problem = {"field": field, "code": code}
			if value is not None:
				problem["value"] = value
			_problems.append(problem)

		def note(code, value=None, _notes=notes):
			entry = {"code": code}
			if value is not None:
				entry["value"] = value
			_notes.append(entry)

		code = clean["item_code"]
		existing = None
		if code:
			code_key = key_of(code)
			if code_key in seen_codes:
				fail("item_code", "code_twice", seen_codes[code_key])
			else:
				seen_codes[code_key] = number
			existing = facts["items"].get(code_key)
			if existing and existing.get("custom_company") not in (None, "", facts.get("company")):
				fail("item_code", "code_other_business", code[:40])
				existing = None

		# Category and brand: matched ignoring capitals and spaces; unknown ones will be created
		category = clean["category"]
		if category:
			found = facts["groups"].get(key_of(category))
			if found:
				category = found
			else:
				category = spellings.setdefault(("category", key_of(category)), category)
				clean["new_category"] = True
		clean["category"] = category
		brand = clean["brand"]
		if brand:
			found = facts["brands"].get(key_of(brand))
			if found:
				brand = found
			else:
				brand = spellings.setdefault(("brand", key_of(brand)), brand)
				clean["new_brand"] = True
		clean["brand"] = brand

		# Unit: must be a unit the shop has (a different spelling is fine); never silently dropped
		unit = clean["unit"]
		if unit:
			found = facts["uoms"].get(key_of(unit))
			if found:
				clean["unit"] = found
			else:
				fail("unit", "unit_unknown", unit[:30])

		# Store for the stock and the low-stock level
		store = clean["store"]
		if store:
			found = facts["stores"].get(key_of(store))
			if found:
				clean["store"] = found
			else:
				fail("store", "store_unknown", store[:30])
		else:
			clean["store"] = default_store or ""

		# Barcode: not twice in the sheet, not on another product
		barcode = clean["barcode"]
		if barcode:
			owner = facts["barcode_owner"].get(barcode)
			if barcode in seen_barcodes and key_of(seen_barcodes[barcode][0]) != key_of(code):
				fail("barcode", "barcode_twice", seen_barcodes[barcode][1])
			else:
				seen_barcodes.setdefault(barcode, (code, number))
			if owner and key_of(owner) != key_of(code):
				fail("barcode", "barcode_taken", owner[:40])

		service = clean["is_service"]
		if existing is not None:
			kept = not existing.get("is_stock_item", 1)
			if clean["is_service_given"] and service != kept:
				note("type_kept")
			service = kept
		elif clean["price"] is None and not problems:
			note("no_price")
		if service and (clean["opening_stock"] or clean["low_stock_level"]):
			note("service_no_stock")

		image = clean["image"]
		image_state = None
		if image["kind"] == "file":
			if photos is None:
				image_state = "no_zip"
				note("image_no_zip", image["value"])
			elif key_of(image["value"]) not in photos:
				image_state = "missing"
				note("image_missing", image["value"])
			elif photos[key_of(image["value"])]:
				image_state = "bad"
				note("image_file_bad", {"name": image["value"], "error": photos[key_of(image["value"])]})
			else:
				image_state = "ready"
		elif image["kind"] == "link":
			try:
				if link_check:
					link_check(image["value"])
				image_state = "ready"
			except pictures.ImageProblem as problem:
				image_state = "bad"
				note("image_link_bad", {"name": image["value"][:60], "error": problem.code})
		elif image["kind"] == "path":
			image_state = "ready"

		if problems:
			plan_rows.append(
				{
					"row": number,
					"item_code": code,
					"item_name": clean["item_name"],
					"action": "invalid",
					"problems": problems,
					"notes": [],
					"changes": [],
					"understood": _understood(clean, service, existing),
				}
			)
			continue

		if clean.get("new_category"):
			new_categories.setdefault(key_of(category), category)
		if clean.get("new_brand"):
			new_brands.setdefault(key_of(brand), brand)

		# Stock and cost
		stock = None
		cost = clean["cost_price"]
		wants_stock = bool(clean["opening_stock"]) and not service
		if existing is not None and cost is None and existing.get("valuation_rate"):
			stock_cost = existing.get("valuation_rate")
		else:
			stock_cost = cost
		if wants_stock:
			if not clean["store"]:
				note("stock_no_store")
			elif existing is not None and existing.get("has_stock"):
				note("stock_has_history")
			elif not stock_cost:
				note("stock_needs_cost")
			else:
				stock = {
					"qty": quantity(clean["opening_stock"]),
					"store": clean["store"],
					"cost": money(stock_cost),
				}
				stock_stores.add(clean["store"])
		low = None if service else clean["low_stock_level"]
		if low and not clean["store"]:
			note("low_no_store")
			low = None

		if existing is None:
			action = "create"
			if clean["vat_class"] == "":
				clean["vat_class"] = "standard"
		else:
			if existing.get("disabled"):
				note("disabled_item")
			if clean["unit"] and clean["unit"] != existing.get("stock_uom"):
				note("unit_kept", existing.get("stock_uom"))
			if clean["vat_class"]:
				note("vat_kept")
			if clean["item_name"] and clean["item_name"] != existing.get("item_name"):
				changes.append(
					{"field": "item_name", "from": existing.get("item_name"), "to": clean["item_name"]}
				)
			if clean["category"] and clean["category"] != existing.get("item_group"):
				changes.append(
					{"field": "category", "from": existing.get("item_group"), "to": clean["category"]}
				)
			if clean["description"] and clean["description"] != (existing.get("description") or ""):
				changes.append(
					{
						"field": "description",
						"from": existing.get("description") or "",
						"to": clean["description"],
					}
				)
			if clean["brand"] and clean["brand"] != (existing.get("brand") or ""):
				changes.append({"field": "brand", "from": existing.get("brand") or "", "to": clean["brand"]})
			if clean["price"] is not None and not _same_number(clean["price"], existing.get("price") or 0):
				changes.append(
					{"field": "price", "from": money(existing.get("price") or 0), "to": money(clean["price"])}
				)
			if cost is not None and cost > 0 and not _same_number(cost, existing.get("valuation_rate") or 0):
				if existing.get("has_stock"):
					note("cost_kept")
				else:
					changes.append(
						{
							"field": "cost_price",
							"from": money(existing.get("valuation_rate") or 0),
							"to": money(cost),
						}
					)
			if barcode and barcode not in (existing.get("barcodes") or []):
				changes.append({"field": "barcode", "from": "", "to": barcode})
			if low is not None and not _same_number(
				low, (existing.get("low_levels") or {}).get(clean["store"], 0)
			):
				changes.append(
					{
						"field": "low_stock_level",
						"from": (existing.get("low_levels") or {}).get(clean["store"], 0),
						"to": quantity(low),
					}
				)
			if image_state == "ready":
				if existing.get("image"):
					note("image_kept")
				else:
					changes.append({"field": "image", "from": "", "to": image["value"]})
			if stock:
				changes.append({"field": "opening_stock", "from": "", "to": stock["qty"]})
			action = "update" if changes else "unchanged"

		clean["is_service"] = service
		plan_rows.append(
			{
				"row": number,
				"item_code": existing["name"] if existing else code,
				"item_name": clean["item_name"],
				"action": action,
				"exists": existing is not None,
				"problems": [],
				"notes": notes,
				"changes": changes,
				"understood": _understood(clean, service, existing),
				"stock": stock,
				"low_stock_level": quantity(low) if low else None,
				"picture": image
				if image_state == "ready" and (existing is None or not existing.get("image"))
				else None,
			}
		)

	return {
		"rows": plan_rows,
		"new_categories": list(new_categories.values()),
		"new_brands": list(new_brands.values()),
		"counts": _counts(plan_rows),
		"stock": _stock_summary(plan_rows, stock_stores),
	}


def _understood(clean: dict, service, existing=None) -> dict:
	"""What was read from the row, in the shape the page shows (what a blank cell will become is filled in)."""
	image = clean["image"]
	if existing is not None:
		category = clean["category"] or existing.get("item_group") or ""
		unit = existing.get("stock_uom") or clean["unit"]
	else:
		category = clean["category"] or ROOT_CATEGORY
		unit = clean["unit"] or DEFAULT_UNIT
	return {
		"category": category,
		"unit": unit,
		"price": clean["price"],
		"cost_price": clean["cost_price"],
		"brand": clean["brand"],
		"barcode": clean["barcode"],
		"image": image,
		"low_stock_level": clean["low_stock_level"],
		"is_service": bool(service),
		"vat_class": clean["vat_class"],
		"opening_stock": clean["opening_stock"],
		"store": clean["store"],
		"description": clean["description"],
	}


def _counts(rows) -> dict:
	counts = {"total": len(rows), "create": 0, "update": 0, "unchanged": 0, "invalid": 0}
	for row in rows:
		counts[row["action"]] += 1
	return counts


def _stock_summary(rows, stores) -> dict:
	with_stock = [r for r in rows if r.get("stock")]
	return {
		"rows": len(with_stock),
		"units": quantity(sum(r["stock"]["qty"] for r in with_stock)),
		"stores": sorted(stores),
	}
