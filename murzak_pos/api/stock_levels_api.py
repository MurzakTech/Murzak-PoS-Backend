"""
The Stock levels page: one call to read it and short calls to change stock (tickets P4-3 and P4-4).

`get_stock_levels` gives the New look Stock levels page its four tabs (overview, low stock, movements, expiry), the
four tiles above the overview list and the two facts the page needs to decide what to show (does the shop have a
second location, does it track expiry dates). The contract is docs/redesign/P4-3-contract.md. It is new; no existing
call, report or record is changed.

`stock_in`, `stock_out`, `get_count_sheet` and `submit_count` are the short forms (Stock in, Stock out or write-off,
Count stock). Each one wraps the existing helper in inventory_api.py that already creates the stock entry or the
stock reconciliation, so the same records, accounts and checks are used as on the current screens; those helpers
are not edited.

How it is built
- The rules are in stock_levels_calc.py (plain functions, no database). This file reads the database in small
  functions, one job each, and joins the two.
- The low-stock and out-of-stock rules are Home's (see stock_levels_calc.py), so a number on Home and the same
  number on this page cannot disagree.
- The tiles are worked out from the rows of the list, not from a second query.
- Stock value is cost information: it is left out of the answer, not set to null, for anyone who may not see cost
  (role_tiers capability `cost_of_goods`).
- The stock lists and the expiry list are kept for 30 seconds per business, store and tab. The cache holds the whole
  list with costs; the cost figures are removed after it is read, so a cashier is never served an owner's answer.
  Every write on this page clears the business's cache, so a person sees their own change at once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import frappe
from frappe import _

from murzak_pos.api import overview_calc, role_tiers
from murzak_pos.api import stock_levels_calc as calc
from murzak_pos.api.access_control import STOCK_ROLES, ensure_record_access, require_stock_role
from murzak_pos.api.payment_gateway_common import resolve_company

CACHE_SECONDS = 30
CACHE_PREFIX = "murzak_pos:stock_levels"


# --- the clock and the cache (small on purpose, so tests can replace them) ---------------------------


def _today():
	return overview_calc.nairobi_today(datetime.now(timezone.utc))


def _cache_get(key: str):
	return frappe.cache().get_value(key)


def _cache_set(key: str, value) -> None:
	frappe.cache().set_value(key, value, expires_in_sec=CACHE_SECONDS)


def _cache_clear(company: str) -> None:
	"""Forget every kept answer of the business, so the next read shows a change just made."""
	try:
		frappe.cache().delete_keys(f"{CACHE_PREFIX}:{company}:")
	except Exception:
		pass  # a kept answer lives 30 seconds at most, so a failure here is not worth failing the change for


def cache_key(company, store, tab, extra=None) -> str:
	"""One key per business, store and tab (movements and expiry add the filters that change their answer)."""
	parts = [company, store, tab] + ([extra] if extra else [])
	return CACHE_PREFIX + ":" + ":".join(str(p or "-") for p in parts)


# --- refusing a request in plain words ---------------------------------------------------------------


def _refuse(code: str):
	"""Turn a calc.StockError code into a sentence the person can act on."""
	if code == "bad_tab":
		frappe.throw(_("Choose a tab: overview, low, movements or expiry."), frappe.ValidationError)
	if code == "bad_status":
		frappe.throw(_("That status is not one of the choices for this list."), frappe.ValidationError)
	if code == "bad_kind":
		frappe.throw(_("Choose a kind of movement: ledger, entry, receipt or issue."), frappe.ValidationError)
	if code == "bad_date":
		frappe.throw(_("Dates must look like 2026-10-13."), frappe.ValidationError)
	if code == "date_order":
		frappe.throw(_("The start date must not be after the end date."), frappe.ValidationError)
	if code == "too_long":
		frappe.throw(_("Please choose a period of one year or less."), frappe.ValidationError)
	if code == "bad_qty":
		frappe.throw(_("Enter a quantity above zero."), frappe.ValidationError)
	if code == "need_item":
		frappe.throw(_("Choose a product."), frappe.ValidationError)
	if code == "need_reason":
		frappe.throw(
			_("Please say why the stock is leaving: damaged, expired, lost, used in the shop or other."),
			frappe.ValidationError,
		)
	if code == "bad_reason":
		frappe.throw(
			_("That reason is not one of the choices: damaged, expired, lost, used in the shop or other."),
			frappe.ValidationError,
		)
	if code == "need_note":
		frappe.throw(_("Please add a short note when the reason is Other."), frappe.ValidationError)
	if code == "need_store":
		frappe.throw(_("Please choose which store this is for."), frappe.ValidationError)
	if code == "no_store":
		frappe.throw(_("This business has no store to put stock in yet."), frappe.ValidationError)
	if code == "empty_count":
		frappe.throw(_("Please count at least one product."), frappe.ValidationError)
	if code == "bad_count":
		frappe.throw(_("Each counted quantity must be a number, zero or more."), frappe.ValidationError)
	if code == "too_many":
		frappe.throw(_("Please count no more than 1000 products at a time."), frappe.ValidationError)
	if code == "duplicate_item":
		frappe.throw(_("A product appears twice in the count."), frappe.ValidationError)
	if code == "bad_scope":
		frappe.throw(_("Choose to count the whole store or one category."), frappe.ValidationError)
	if code == "need_category":
		frappe.throw(_("Please choose a category to count."), frappe.ValidationError)
	frappe.throw(_("That request could not be understood."), frappe.ValidationError)


def _signed_in() -> str:
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return tier


def _can_change_stock() -> bool:
	"""Whether the signed-in person holds a role the current stock screens already require for changing stock."""
	user = frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & STOCK_ROLES)


# --- reading the page -----------------------------------------------------------------------------------


@frappe.whitelist()
def get_stock_levels(
	tab="overview",
	search=None,
	category=None,
	warehouse=None,
	status=None,
	from_date=None,
	to_date=None,
	page=1,
	page_size=20,
	company=None,
	kind=None,
	days=30,
):
	"""One page of the Stock levels page for a tab (see the module notes and the contract for the answer)."""
	tier = _signed_in()
	ensure_record_access("Warehouse", warehouse)
	try:
		tab = calc.clean_tab(tab)
		status = calc.clean_status(tab, status)
		kind = calc.clean_kind(kind) if tab == "movements" else None
		page, size = calc.clean_paging(page, page_size)
		window = calc.movement_window(from_date, to_date, _today()) if tab == "movements" else None
	except calc.StockError as error:
		_refuse(error.code)
	company = resolve_company(company)
	store = warehouse or None
	show_value = role_tiers.can("cost_of_goods")
	search = calc.clean_text(search, 80) or None
	category = calc.clean_text(category, 140) or None

	if tab == "movements":
		start, end = window
		extra = "|".join(str(p or "-") for p in (start, end, search, store, kind, status, page, size))
		answer, cached = _kept(
			company,
			store,
			tab,
			extra,
			lambda: _movements(company, store, start, end, search, kind, status, page, size),
		)
		data = {"tab": tab, "rows": answer["rows"], **calc.page_info(answer["total"], page, size)}
		data["from_date"], data["to_date"] = str(start), str(end)
	elif tab == "expiry":
		from murzak_pos.api.expiry_rules import clamp_window

		window_days = clamp_window(days)
		answer, cached = _kept(
			company, store, tab, str(window_days), lambda: {"rows": _expiry(company, store, window_days)}
		)
		rows = calc.filter_expiry_rows(answer["rows"], search, status)
		data = {"tab": tab, "rows": calc.page_of(rows, page, size), **calc.page_info(len(rows), page, size)}
		data["days"] = window_days
		if not show_value:
			data["rows"] = calc.strip_costs(data["rows"])
	else:
		answer, cached = _kept(company, store, tab, None, lambda: {"rows": _stock_rows(company, store)})
		scoped = calc.filter_stock_rows(answer["rows"], search, category)
		listed = calc.low_stock_rows(scoped) if tab == "low" else scoped
		listed = calc.filter_stock_rows(listed, status=status)
		data = {
			"tab": tab,
			"rows": calc.page_of(listed, page, size),
			**calc.page_info(len(listed), page, size),
		}
		data["categories"] = calc.categories(answer["rows"])
		if tab == "overview":
			data["tiles"] = calc.tiles(scoped, show_value)
		if not show_value:
			data["rows"] = calc.strip_costs(data["rows"])

	data["features"] = _features(company)
	data["can"] = {"change_stock": _can_change_stock()}
	data["role"] = tier
	data["cached"] = cached
	return {"success": True, "data": data}


def _kept(company, store, tab, extra, compute):
	"""(the answer, whether it came from the cache). A failed read is not kept."""
	key = cache_key(company, store, tab, extra)
	kept = _cache_get(key)
	if kept:
		return kept, True
	answer = compute()
	_cache_set(key, answer)
	return answer, False


def _movements(company, store, start, end, search, kind, status, page, size) -> dict:
	count_sql, page_sql, values = calc.movements_query(
		company, start, end, warehouse=store, search=search, kind=kind, status=status
	)
	counted = frappe.db.sql(count_sql, values, as_dict=True)
	total = int((counted[0]["n"] if counted else 0) or 0)
	rows = frappe.db.sql(page_sql, {**values, "limit": size, "offset": (page - 1) * size}, as_dict=True)
	return {"rows": [calc.shape_movement(r) for r in rows], "total": total}


def _expiry(company, store, window_days) -> list[dict]:
	from murzak_pos.api.expiry_api import get_expiry_alerts

	answer = get_expiry_alerts(company=company, days=window_days, warehouse=store, include_expired=1)
	return [calc.shape_expiry(r, show_value=True) for r in (answer.get("data") or [])]


def _stock_rows(company, store) -> list[dict]:
	"""Every product of the stock list, with its level and value (the cache keeps these and trims them per person)."""
	bins = _bin_rows(company, store)
	sold = _sold_items(company, store)
	held = {row["item_code"] for row in bins}
	missing = [row["item_code"] for row in sold if row["item_code"] not in held]
	return calc.build_stock_rows(bins, _low_rows(company, store), sold, _item_info(missing))


def _bin_rows(company, store) -> list[dict]:
	"""Stock in each store for the business's enabled stock products."""
	conditions = ["w.company = %(company)s", "i.is_stock_item = 1", "i.disabled = 0"]
	values = {"company": company}
	if store:
		conditions.append("b.warehouse = %(store)s")
		values["store"] = store
	return frappe.db.sql(
		"SELECT b.item_code, i.item_name, i.item_group, i.stock_uom, b.warehouse, b.actual_qty, b.stock_value "
		"FROM `tabBin` b JOIN `tabItem` i ON i.name = b.item_code JOIN `tabWarehouse` w ON w.name = b.warehouse "
		f"WHERE {' AND '.join(conditions)}",
		values,
		as_dict=True,
	)


def _low_rows(company, store) -> list[dict]:
	"""Stores at or below their warning level: the very rows Home's Low on stock tile starts from."""
	from murzak_pos.api.alerts import low_stock_rows

	rows = low_stock_rows(company)
	return [r for r in rows if r.get("warehouse") == store] if store else rows


def _sold_items(company, store) -> list[dict]:
	"""The stock products that sold in the last 30 days: the very list Home uses to say a product is out of stock."""
	from murzak_pos.api.dashboard_overview import _sold_recently

	return _sold_recently(SimpleNamespace(company=company, store=store, today=_today()))


def _item_info(codes) -> dict:
	if not codes:
		return {}
	rows = frappe.get_all(
		"Item",
		filters={"name": ["in", list(codes)]},
		fields=["name", "item_name", "item_group", "stock_uom"],
	)
	return {r["name"]: r for r in rows}


def _warehouses(company) -> list[dict]:
	return frappe.get_all(
		"Warehouse",
		filters={"company": company},
		fields=["name", "warehouse_name", "disabled", "is_group"],
	)


def _locations(company) -> list[str]:
	return calc.selling_locations(_warehouses(company))


def _features(company) -> dict:
	"""What the shop has: how many real selling locations, and whether it tracks expiry dates. Never fails the page."""
	key = cache_key(company, None, "features")
	kept = _cache_get(key)
	if kept:
		return kept
	try:
		features = {"expiry": _tracks_expiry(company), "locations": len(_locations(company))}
	except Exception:
		frappe.log_error("Stock levels features error", frappe.get_traceback())
		return {"expiry": False, "locations": 0}
	_cache_set(key, features)
	return features


def _tracks_expiry(company) -> bool:
	"""Any batch of the business's products with an expiry date, or any stock-loading record carrying one."""
	batches = frappe.db.sql(
		"SELECT COUNT(*) AS n FROM `tabBatch` b JOIN `tabItem` i ON i.name = b.item "
		"WHERE b.expiry_date IS NOT NULL AND b.disabled = 0 "
		"AND (i.custom_company = %(company)s OR IFNULL(i.custom_company, '') = '')",
		{"company": company},
		as_dict=True,
	)
	details = [{"n": 0}]
	if frappe.db.exists("DocType", "Inventory Item Details"):
		details = frappe.db.sql(
			"SELECT COUNT(*) AS n FROM `tabInventory Item Details` WHERE company = %(company)s AND expiry_date IS NOT NULL",
			{"company": company},
			as_dict=True,
		)
	return calc.tracks_expiry(batches[0]["n"] if batches else 0, details[0]["n"] if details else 0)


@frappe.whitelist()
def find_products(search=None, warehouse=None, company=None, limit=10):
	"""Stock products by name or code, with the quantity in stock, for the pickers in the short forms."""
	_signed_in()
	require_stock_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	try:
		size = max(1, min(int(limit), 20))
	except (TypeError, ValueError):
		size = 10
	conditions = [
		"i.disabled = 0",
		"i.is_stock_item = 1",
		"(i.custom_company = %(company)s OR IFNULL(i.custom_company, '') = '')",
	]
	values = {"company": company, "limit": size}
	join = "b.warehouse IN (SELECT name FROM `tabWarehouse` WHERE company = %(company)s)"
	if warehouse:
		join += " AND b.warehouse = %(warehouse)s"
		values["warehouse"] = warehouse
	if calc.clean_text(search, 80):
		values["like"] = calc.like_pattern(search)
		conditions.append("(i.name LIKE %(like)s OR i.item_name LIKE %(like)s)")
	rows = frappe.db.sql(
		"SELECT i.name AS item_code, i.item_name AS item_name, i.item_group AS category, i.stock_uom AS unit, "
		"IFNULL(SUM(b.actual_qty), 0) AS qty FROM `tabItem` i LEFT JOIN `tabBin` b ON b.item_code = i.name "
		f"AND {join} WHERE {' AND '.join(conditions)} GROUP BY i.name, i.item_name, i.item_group, i.stock_uom "
		"ORDER BY i.item_name LIMIT %(limit)s",
		values,
		as_dict=True,
	)
	return {
		"success": True,
		"data": {
			"rows": [
				{
					"item_code": r["item_code"],
					"item_name": r["item_name"] or r["item_code"],
					"category": r["category"] or "",
					"unit": r["unit"] or "",
					"qty": calc.qty(r["qty"]),
				}
				for r in rows
			]
		},
	}


# --- changing stock -----------------------------------------------------------------------------------------


def _check_store(company, store) -> None:
	"""A store named in a request must be one of this business's stores."""
	if frappe.db.get_value("Warehouse", store, "company") != company:
		frappe.throw(_("You do not have access to this store."), frappe.PermissionError)


def _stock_of(item_code, store) -> float:
	return calc.qty(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": store}, "actual_qty"))


def _remember(entry, remark) -> None:
	"""Write the note or reason on the stock entry that was just made (the helper has no place for it)."""
	if remark:
		frappe.db.set_value("Stock Entry", entry, "remarks", remark, update_modified=False)


def _plain(result) -> str:
	return calc.plain_message((result or {}).get("message")) or _(
		"The stock change was not saved. Please try again."
	)


@frappe.whitelist(methods=["POST"])
def stock_in(item_code, qty, warehouse=None, note=None, company=None, unit_cost=None):
	"""
	Put stock into a store without a supplier bill (a material receipt). `unit_cost` is optional and used only for a
	person who may see cost. A purchase from a supplier is a purchase, not this.
	"""
	require_stock_role()
	ensure_record_access("Item", item_code)
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	try:
		code = calc.clean_item(item_code)
		amount = calc.clean_quantity(qty)
		text = calc.clean_text(note)
		store = calc.pick_warehouse(warehouse, _locations(company))
	except calc.StockError as error:
		_refuse(error.code)
	_check_store(company, store)

	from murzak_pos.api.inventory_api import create_material_receipt

	line = {"item_code": code, "qty": amount}
	cost = calc.num(unit_cost)
	if cost > 0 and role_tiers.can("cost_of_goods"):
		line["basic_rate"] = cost
	result = create_material_receipt(items=[line], target_warehouse=store, company=company)
	if not result or not result.get("success"):
		frappe.db.rollback()  # the helper can leave a half-made draft behind when it fails
		return {"success": False, "message": _plain(result)}
	entry = result["data"]["name"]
	_remember(entry, calc.receipt_remark(text))
	_cache_clear(company)
	return {
		"success": True,
		"data": {
			"entry": entry,
			"item_code": code,
			"qty": amount,
			"warehouse": store,
			"in_stock": _stock_of(code, store),
		},
	}


@frappe.whitelist(methods=["POST"])
def stock_out(item_code, qty, reason, warehouse=None, note=None, company=None):
	"""
	Take stock out of a store (a material issue) for a reason: damaged, expired, lost, used in the shop or other
	(Other needs a note). The reason is checked first, before anything is read or written, and is written on the
	stock entry.
	"""
	require_stock_role()
	try:
		reason_key, text = calc.clean_reason(reason, note)
	except calc.StockError as error:
		_refuse(error.code)
	ensure_record_access("Item", item_code)
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	try:
		code = calc.clean_item(item_code)
		amount = calc.clean_quantity(qty)
		store = calc.pick_warehouse(warehouse, _locations(company))
	except calc.StockError as error:
		_refuse(error.code)
	_check_store(company, store)
	held = _stock_of(code, store)
	if amount > held:
		frappe.throw(
			_("There is only {0} in stock, so {1} cannot be taken out.").format(held, amount),
			frappe.ValidationError,
		)

	from murzak_pos.api.inventory_api import create_material_issue

	result = create_material_issue(
		items=[{"item_code": code, "qty": amount}], source_warehouse=store, company=company
	)
	if not result or not result.get("success"):
		frappe.db.rollback()
		return {"success": False, "message": _plain(result)}
	entry = result["data"]["name"]
	_remember(entry, calc.reason_remark(reason_key, text))
	_cache_clear(company)
	return {
		"success": True,
		"data": {
			"entry": entry,
			"item_code": code,
			"qty": amount,
			"reason": reason_key,
			"warehouse": store,
			"in_stock": _stock_of(code, store),
		},
	}


# --- counting stock -----------------------------------------------------------------------------------------------


def _count_bins(store) -> list[dict]:
	return frappe.db.sql(
		"SELECT b.item_code, i.item_name, i.item_group, i.stock_uom, b.actual_qty FROM `tabBin` b "
		"JOIN `tabItem` i ON i.name = b.item_code WHERE b.warehouse = %(store)s AND i.is_stock_item = 1 AND i.disabled = 0",
		{"store": store},
		as_dict=True,
	)


def _expected(store, codes) -> dict:
	rows = frappe.db.sql(
		"SELECT item_code, actual_qty FROM `tabBin` WHERE warehouse = %(store)s AND item_code IN %(codes)s",
		{"store": store, "codes": tuple(codes)},
		as_dict=True,
	)
	return {r["item_code"]: r["actual_qty"] for r in rows}


@frappe.whitelist()
def get_count_sheet(scope="all", category=None, warehouse=None, company=None):
	"""What the system says is in a store (the whole store, or one category), for the guided count."""
	require_stock_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	try:
		store = calc.pick_warehouse(warehouse, _locations(company))
	except calc.StockError as error:
		_refuse(error.code)
	_check_store(company, store)
	try:
		rows, cut = calc.sheet_rows(_count_bins(store), scope, calc.clean_text(category, 140) or None)
	except calc.StockError as error:
		_refuse(error.code)
	return {"success": True, "data": {"warehouse": store, "rows": rows, "total": len(rows), "truncated": cut}}


@frappe.whitelist(methods=["POST"])
def submit_count(counts, warehouse=None, company=None):
	"""
	Save a count: the quantities found on the shelves become the stock of the store (a stock reconciliation). Only
	the products whose count differs from the system are sent to the reconciliation; the answer lists every product
	counted with expected, counted and the difference.
	"""
	require_stock_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	try:
		cleaned = calc.clean_counts(counts)
		store = calc.pick_warehouse(warehouse, _locations(company))
	except calc.StockError as error:
		_refuse(error.code)
	_check_store(company, store)
	differences = calc.count_differences(cleaned, _expected(store, [c["item_code"] for c in cleaned]))
	totals = calc.count_totals(differences)
	changed = [d for d in differences if d["state"] != "matches"]
	answer = {"warehouse": store, "differences": differences, "totals": totals, "reconciliation": None}
	if not changed:
		return {"success": True, "data": answer}

	from murzak_pos.api.inventory_api import create_stock_reconciliation

	result = create_stock_reconciliation(
		items=[{"item_code": d["item_code"], "qty": d["counted"]} for d in changed],
		warehouse=store,
		company=company,
	)
	if not result or not result.get("success"):
		frappe.db.rollback()
		return {
			"success": False,
			"message": _("The count was not saved. Please try again, or ask your administrator."),
			"detail": calc.plain_message((result or {}).get("message")),
		}
	answer["reconciliation"] = result["data"]["name"]
	_cache_clear(company)
	return {"success": True, "data": answer}
