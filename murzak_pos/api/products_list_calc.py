"""
The rules and the arithmetic behind the Products page (`products_list_api`), with no database and no Frappe.

Every function here takes plain values (numbers, text, dicts) and returns plain results, so each rule can be tested
with a few lines of data. The database work, the role checks and the messages are in products_list_api.py.

The one idea that matters most: **the count and the rows come from the same query parts.** `build_query` makes the
FROM and WHERE text once; `rows_sql`, `count_sql` and `summary_sql` only put a different SELECT in front of it. A
filter can therefore never change the rows without changing the count, which is what was wrong with the old list
(it counted without the search text, and in some shops without the category either).

Conventions
- Stock is the sum over the business's stores (or one store when the caller names it). A product that is not kept
  in stock (a service) has no stock figure and is never "low" or "out".
- Low on stock means some is left (more than 0) but no more than the product's low-stock warning. Out of stock means
  none left. The two never overlap, so Low + Out + OK always adds up to the products kept in stock.
- A low-stock warning of 0 (or none) means "no warning set": the product can be out of stock but never low.
- Money is rounded to 2 decimals. Profit per item is the price minus the cost price, and is None when the cost is
  not known (never shown as if the cost were 0).
"""

from __future__ import annotations

import math
import re

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_SEARCH_LENGTH = 100
MAX_NAME_LENGTH = 140
MAX_CODE_LENGTH = 140
MAX_BARCODE_LENGTH = 100
MAX_AMOUNT = 1_000_000_000  # a price, cost or quantity above this is a typing mistake

# What the Products page can show. "active" (the default) is every product in use; "all" adds the disabled ones.
STATUSES = ("active", "all", "low", "out", "disabled")

# The category every product falls into when none is chosen (ERPNext's top group)
ROOT_CATEGORY = "All Item Groups"
DEFAULT_UNIT = "Nos"
# The numbering of automatic product codes: PRD-00001, PRD-00002 ...
CODE_SERIES = "PRD-.#####"

# The character that protects % and _ in a search (a pipe is the same in every SQL dialect, unlike a backslash)
LIKE_ESCAPE = "|"

# The stock rules written as SQL, once. `s` is the stock subquery and `r` the low-stock warning subquery that
# `build_query` joins. `stock_status` below says the same in Python; the tests check the two agree.
STOCK_SQL = "IFNULL(s.qty, 0)"
LEVEL_SQL = "IFNULL(r.level, 0)"
OUT_SQL = f"(i.is_stock_item = 1 AND {STOCK_SQL} <= 0)"
LOW_SQL = f"(i.is_stock_item = 1 AND {STOCK_SQL} > 0 AND {LEVEL_SQL} > 0 AND {STOCK_SQL} <= {LEVEL_SQL})"


class ListError(Exception):
	"""A request that cannot be used. `code` says why, so the caller can answer in words."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- numbers ----------------------------------------------------------------------------------------


def num(value) -> float:
	"""A number from a database value; anything that is not a number is 0."""
	try:
		result = float(value or 0)
	except (TypeError, ValueError):
		return 0.0
	return result if math.isfinite(result) else 0.0


def money(value) -> float:
	"""A money amount with at most 2 decimals (and never -0.0)."""
	return round(num(value), 2) + 0.0


def quantity(value) -> float:
	"""A stock quantity with at most 3 decimals, as a whole number when it is one (never -0.0)."""
	result = round(num(value), 3) + 0.0
	return int(result) if result == int(result) else result


def _whole(value, fallback: int) -> int:
	try:
		if isinstance(value, bool):
			return fallback
		return int(float(value))
	except (TypeError, ValueError, OverflowError):
		return fallback


def page_numbers(page, page_size, total) -> dict:
	"""
	The page to read: `page` (from 1), `page_size`, `pages` and `offset`. A size that is missing or wrong is 20, one
	above 100 is 100, one below 1 is 20. A page past the last one is the last page, so rows are never empty while
	the list has products. `pages` is never below 1.
	"""
	size = _whole(page_size, DEFAULT_PAGE_SIZE)
	if size < 1:
		size = DEFAULT_PAGE_SIZE
	size = min(size, MAX_PAGE_SIZE)
	count = max(_whole(total, 0), 0)
	pages = max(math.ceil(count / size), 1)
	number = min(max(_whole(page, 1), 1), pages)
	return {"page": number, "page_size": size, "pages": pages, "offset": (number - 1) * size}


# --- the request ------------------------------------------------------------------------------------


def normalize_status(value) -> str:
	"""One of STATUSES. Nothing (or an empty text) is "active"; anything else is refused."""
	text = str(value or "").strip().lower()
	if not text:
		return "active"
	if text not in STATUSES:
		raise ListError("bad_status")
	return text


def clean_search(value) -> str:
	"""The search text with spaces tidied and a sensible length."""
	if value is None:
		return ""
	return re.sub(r"\s+", " ", str(value)).strip()[:MAX_SEARCH_LENGTH]


def like_pattern(text: str) -> str:
	"""`%text%` for a LIKE, with % and _ (and the escape character) in the text taken literally."""
	escaped = (
		text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
		.replace("%", LIKE_ESCAPE + "%")
		.replace("_", LIKE_ESCAPE + "_")
	)
	return f"%{escaped}%"


def stock_status(disabled, is_stock_item, stock, level) -> str:
	"""
	One word for a product: disabled, not_tracked (a service), out, low or ok. The same rule as OUT_SQL and LOW_SQL.
	"""
	if num(disabled):
		return "disabled"
	if not num(is_stock_item):
		return "not_tracked"
	qty, warn = num(stock), num(level)
	if qty <= 0:
		return "out"
	if warn > 0 and qty <= warn:
		return "low"
	return "ok"


# --- the query, built once --------------------------------------------------------------------------


def build_query(
	*,
	company,
	warehouse=None,
	category=None,
	status="active",
	search="",
	industry=None,
	has_industry_column=False,
) -> dict:
	"""
	The FROM and WHERE of the Products list as text with `%(name)s` placeholders, and their values.

	Returns { "from": ..., "where": ..., "base_where": ..., "params": {...} }:
	- `from` joins the product to its stock and its low-stock warning (one row each, so the product is never repeated)
	- `where` is every filter of the request, and is what the rows and the count both use
	- `base_where` is the catalogue without the request's filters (the business's sellable, enabled products), which
	  the summary and the category counts use
	"""
	params: dict = {"company": company}
	store_stock = store_reorder = ""
	if warehouse:
		params["warehouse"] = warehouse
		store_stock = " AND b.warehouse = %(warehouse)s"
		store_reorder = " AND ir.warehouse = %(warehouse)s"

	source = (
		"FROM `tabItem` i "
		"LEFT JOIN (SELECT b.item_code AS item_code, SUM(b.actual_qty) AS qty FROM `tabBin` b "
		"JOIN `tabWarehouse` w ON w.name = b.warehouse "
		f"WHERE w.company = %(company)s AND w.is_group = 0{store_stock} GROUP BY b.item_code) s "
		"ON s.item_code = i.name "
		"LEFT JOIN (SELECT ir.parent AS item_code, SUM(ir.warehouse_reorder_level) AS level "
		"FROM `tabItem Reorder` ir JOIN `tabWarehouse` w2 ON w2.name = ir.warehouse "
		f"WHERE ir.parenttype = 'Item' AND w2.company = %(company)s AND w2.is_group = 0{store_reorder} "
		"GROUP BY ir.parent) r ON r.item_code = i.name"
	)

	base = ["i.custom_company = %(company)s", "i.is_sales_item = 1"]
	# The same rule the old list applied: a person on an industry sees the shared products and their industry's
	if industry and has_industry_column:
		base.append(
			"(i.custom_pos_industry IS NULL OR i.custom_pos_industry = '' OR i.custom_pos_industry = %(industry)s)"
		)
		params["industry"] = industry

	conditions = list(base)
	if status == "disabled":
		conditions.append("i.disabled = 1")
	elif status != "all":
		conditions.append("i.disabled = 0")
	if status == "low":
		conditions.append(LOW_SQL)
	elif status == "out":
		conditions.append(OUT_SQL)
	if category:
		conditions.append("i.item_group = %(category)s")
		params["category"] = category
	text = clean_search(search)
	if text:
		conditions.append(
			f"(i.item_name LIKE %(like)s ESCAPE '{LIKE_ESCAPE}' OR i.name LIKE %(like)s ESCAPE '{LIKE_ESCAPE}' "
			"OR EXISTS (SELECT 1 FROM `tabItem Barcode` bc WHERE bc.parent = i.name "
			f"AND bc.barcode LIKE %(like)s ESCAPE '{LIKE_ESCAPE}'))"
		)
		params["like"] = like_pattern(text)

	return {
		"from": source,
		"where": " AND ".join(conditions),
		"base_where": " AND ".join(base),
		"params": params,
	}


def rows_sql(parts: dict) -> str:
	"""The page of products (newest first). Needs `limit` and `offset` added to the values."""
	return (
		"SELECT i.name AS item_code, i.item_name AS item_name, i.item_group AS category, "
		"i.stock_uom AS unit, i.standard_rate AS standard_rate, i.disabled AS disabled, "
		f"i.is_stock_item AS is_stock_item, {STOCK_SQL} AS stock, {LEVEL_SQL} AS low_stock_level "
		f"{parts['from']} WHERE {parts['where']} "
		"ORDER BY i.creation DESC, i.name ASC LIMIT %(limit)s OFFSET %(offset)s"
	)


def count_sql(parts: dict) -> str:
	"""How many products the filters match: the same FROM and WHERE as the rows."""
	return f"SELECT COUNT(*) AS n {parts['from']} WHERE {parts['where']}"


def summary_sql(parts: dict) -> str:
	"""The numbers above the list: the enabled products of the business, how many are low and how many are out."""
	return (
		"SELECT COUNT(*) AS total, "
		f"SUM(CASE WHEN {LOW_SQL} THEN 1 ELSE 0 END) AS low, "
		f"SUM(CASE WHEN {OUT_SQL} THEN 1 ELSE 0 END) AS out_of_stock "
		f"{parts['from']} WHERE {parts['base_where']} AND i.disabled = 0"
	)


def category_counts_sql(parts: dict) -> str:
	"""How many enabled products each category holds (the same catalogue as the summary)."""
	return (
		"SELECT i.item_group AS category, COUNT(*) AS n FROM `tabItem` i "
		f"WHERE {parts['base_where']} AND i.disabled = 0 GROUP BY i.item_group"
	)


# --- shaping the answer -----------------------------------------------------------------------------


def shape_summary(raw) -> dict:
	"""The summary with whole numbers, whatever the database gave (a SUM over no rows is NULL)."""
	raw = raw or {}
	return {
		"total": max(_whole(raw.get("total"), 0), 0),
		"low_stock": max(_whole(raw.get("low"), 0), 0),
		"out_of_stock": max(_whole(raw.get("out_of_stock"), 0), 0),
	}


def pick_price(prices, unit, standard_rate) -> tuple[float, str]:
	"""
	The selling price: the price list's price in the product's own unit, else any price in the list, else the
	product's standard rate. Returns (price, "price_list" or "standard_rate").
	`prices` is a list of { "uom", "price_list_rate" }.
	"""
	prices = prices or []
	for entry in prices:
		if entry.get("uom") == unit and entry.get("price_list_rate") is not None:
			return money(entry["price_list_rate"]), "price_list"
	for entry in prices:
		if entry.get("price_list_rate") is not None:
			return money(entry["price_list_rate"]), "price_list"
	return money(standard_rate), "standard_rate"


def cost_price(buying_price=None, item_rate=None, bin_rates=None) -> float | None:
	"""
	The cost price of one item: the buying price the shop recorded for the store, else the product's own valuation
	rate, else the highest valuation rate in any store. None when none is known (a cost of 0 is "not known").
	"""
	for value in (buying_price, item_rate, max([num(r) for r in bin_rates or []] or [0])):
		if num(value) > 0:
			return money(value)
	return None


def choose_buying_price(rows, preferred_store=None):
	"""
	The buying price among a product's per-store records: the preferred store's if it has one, else the highest
	recorded. `rows` are { "warehouse", "buying_price" }. Returns None when none is above 0.
	"""
	rows = [r for r in rows or [] if num(r.get("buying_price")) > 0]
	for row in rows:
		if preferred_store and row.get("warehouse") == preferred_store:
			return money(row["buying_price"])
	return money(max(num(r["buying_price"]) for r in rows)) if rows else None


def profit_per_item(price, cost) -> float | None:
	"""Price minus cost, or None when the cost is not known."""
	if cost is None or num(cost) <= 0:
		return None
	return money(num(price) - num(cost))


def margin_percent(price, cost) -> float | None:
	"""Profit as a percentage of the price (one decimal), or None when it cannot be worked out."""
	profit = profit_per_item(price, cost)
	if profit is None or num(price) <= 0:
		return None
	return round(profit / num(price) * 100, 1) + 0.0


def shape_row(raw, *, prices=None, barcodes=None, cost=None, show_cost=False) -> dict:
	"""
	One product for the list. `cost` is the cost price (or None). A person who may not see cost (`show_cost`
	False) gets neither the cost price nor the profit: the keys are left out, not set to null.
	"""
	unit = raw.get("unit")
	price, source = pick_price(prices, unit, raw.get("standard_rate"))
	tracked = bool(num(raw.get("is_stock_item")))
	level = num(raw.get("low_stock_level"))
	codes = [str(b) for b in barcodes or [] if b]
	row = {
		"item_code": raw.get("item_code"),
		"item_name": raw.get("item_name"),
		"category": raw.get("category"),
		"price": price,
		"price_source": source,
		"stock": quantity(raw.get("stock")) if tracked else None,
		"unit": unit,
		"barcode": codes[0] if codes else "",
		"barcodes": codes,
		"disabled": 1 if num(raw.get("disabled")) else 0,
		"is_stock_item": 1 if tracked else 0,
		"low_stock_level": quantity(level) if tracked and level > 0 else None,
		"status": stock_status(raw.get("disabled"), raw.get("is_stock_item"), raw.get("stock"), level),
	}
	if show_cost:
		row["cost_price"] = cost
		row["profit_per_item"] = profit_per_item(price, cost)
		row["margin_percent"] = margin_percent(price, cost)
	return row


def shape_categories(groups, counts) -> list[dict]:
	"""
	The categories for the Categories tab and the chips: every Item Group with its product count, A to Z, the
	default top group last and only when products sit in it. `groups` are Item Group rows; `counts` is
	{ category: number } (enabled products of the business).
	"""
	result = []
	for group in groups or []:
		name = group.get("name")
		if not name:
			continue
		is_default = name == ROOT_CATEGORY
		count = max(_whole(counts.get(name), 0), 0)
		if is_default and count == 0:
			continue
		result.append(
			{
				"name": name,
				"parent": group.get("parent_item_group") or None,
				"is_group": 1 if num(group.get("is_group")) else 0,
				"is_default": is_default,
				"product_count": count,
			}
		)
	result.sort(key=lambda c: (c["is_default"], c["name"].lower()))
	return result


# --- making a product -------------------------------------------------------------------------------

_CODE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._\-]*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class AmountError(ValueError):
	"""A price, cost or quantity that is not a usable number."""


def parse_amount(value):
	"""
	A number from what the form sent: digits, a decimal point, spaces and thousands commas are fine.
	Returns None for nothing at all, and raises AmountError for anything else (a letter, a minus sign, infinity).
	"""
	if value is None:
		return None
	if isinstance(value, bool):
		raise AmountError("not a number")
	if isinstance(value, int | float):
		result = float(value)
	else:
		text = str(value).strip().replace(",", "").replace(" ", "")
		if text == "":
			return None
		if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", text):
			raise AmountError("not a number")
		result = float(text)
	if not math.isfinite(result) or result < 0:
		raise AmountError("not a number")
	return result


def normalize_code(value) -> str:
	"""A product code with its outer spaces removed ('' when none)."""
	return str(value).strip() if value is not None else ""


def check_new_product(raw: dict) -> tuple[dict, list[dict]]:
	"""
	Check what the New product form sends. Returns (clean values, errors); each error is { field, code } and the
	API turns the code into words. Only the name and the price are required.

	Clean values: item_name, price, item_code ('' when it is to be made automatically), category ('' for the default),
	unit ('' for the default), barcode, cost_price (None), opening_stock (None when 0 or blank), low_stock_warning
	(None when 0 or blank).
	"""
	errors: list[dict] = []
	clean: dict = {}

	def fail(field, code):
		errors.append({"field": field, "code": code})

	name = re.sub(r"\s+", " ", str(raw.get("item_name") or "")).strip()
	clean["item_name"] = name
	if not name:
		fail("item_name", "name_required")
	elif len(name) > MAX_NAME_LENGTH:
		fail("item_name", "name_too_long")

	def amount(key, field, missing_code, bad_code, required=False):
		try:
			value = parse_amount(raw.get(key))
		except AmountError:
			fail(field, bad_code)
			return None
		if value is None:
			if required:
				fail(field, missing_code)
			return None
		if value > MAX_AMOUNT:
			fail(field, "too_big")
			return None
		return value

	clean["price"] = amount("price", "price", "price_required", "price_invalid", required=True)
	cost = amount("cost_price", "cost_price", "", "cost_invalid")
	stock = amount("opening_stock", "opening_stock", "", "stock_invalid")
	low = amount("low_stock_warning", "low_stock_warning", "", "low_invalid")
	clean["cost_price"] = cost
	clean["opening_stock"] = stock if stock else None
	clean["low_stock_warning"] = low if low else None

	code = normalize_code(raw.get("item_code"))
	clean["item_code"] = code
	if code and len(code) > MAX_CODE_LENGTH:
		fail("item_code", "code_too_long")
	elif code and not _CODE_OK.match(code):
		fail("item_code", "code_invalid")

	barcode = normalize_code(raw.get("barcode"))
	clean["barcode"] = barcode
	if barcode and (len(barcode) > MAX_BARCODE_LENGTH or _CONTROL.search(barcode) or " " in barcode):
		fail("barcode", "barcode_invalid")

	clean["category"] = normalize_code(raw.get("category"))
	clean["unit"] = normalize_code(raw.get("unit"))
	return clean, errors


def classify_save_error(message) -> str | None:
	"""
	Which field a refusal from the existing create call is about, read from its words, or None when it cannot be
	told. Used so the form can show the message beside the right field.
	"""
	text = str(message or "").lower()
	if "barcode" in text:
		return "barcode"
	if (
		"product with the code" in text
		or "item code" in text
		or ("already exists" in text and "code" in text)
	):
		return "item_code"
	if "category" in text or "item group" in text:
		return "category"
	if "unit of measure" in text or ("unit" in text and "does not exist" in text):
		return "unit"
	if "name" in text and "required" in text:
		return "item_name"
	return None


def pick_warehouse(default, names) -> str | None:
	"""
	The store for opening stock and the low-stock warning when none is named: the business's default store, else
	one called Stores, else the oldest. `names` are the business's stores, oldest first.
	"""
	names = list(names or [])
	if default:
		return default
	for name in names:
		if str(name).lower().startswith("stores"):
			return name
	return names[0] if names else None


def reorder_row(warehouse, level) -> dict:
	"""The low-stock warning as a row of the product's Reorder table (the field ERPNext and the alerts read)."""
	return {
		"warehouse": warehouse,
		"warehouse_reorder_level": quantity(level),
		"warehouse_reorder_qty": 0,
		"material_request_type": "Purchase",
	}


def opening_stock_item(item_code, qty, warehouse, cost) -> dict:
	"""The one line of the opening stock entry. The cost goes in only when it is known."""
	line = {"item_code": item_code, "qty": quantity(qty), "t_warehouse": warehouse}
	if cost is not None and num(cost) > 0:
		line["basic_rate"] = money(cost)
	return line
