"""
The rules behind the Stock levels page (`stock_levels_api`), with no database and no Frappe.

Every function here takes plain rows (dicts, numbers, text) and returns plain results, so each rule can be tested
with a few lines of data. The database reads, the role checks, the cache and the writes are in stock_levels_api.py.

The rules in plain words
- One row per product. The quantity is the stock in every store of the business (or in the store chosen).
- A product is "out" when the quantity is 0 or less, "low" when some store holds stock at or below that store's
  warning level (the Item Reorder warehouse_reorder_level, the same rule as the Home "Low on stock" tile and the
  low-stock email alert), and "ok" otherwise.
- A product is listed when it holds stock, or when it sold in the last 30 days (the same list Home uses to say a
  product is out of stock). A product with no stock that never sold recently is not listed, so the Out count here
  is the Out count on Home.
- The tiles above the list are worked out from the very rows the list shows (before the level filter), so they can
  never disagree with the list.
- Stock value is quantity times valuation rate (the stock value kept by ERPNext in each store). It is cost
  information, so it is removed for a cashier.
- A write-off needs a reason from a fixed list; "Other" also needs a note.

Conventions: quantities are rounded to 3 decimals and money to 2; dates are `datetime.date` inside and
`YYYY-MM-DD` text in the answer.
"""

from __future__ import annotations

import html
import json
import math
import re
from datetime import date, datetime, time, timedelta

from murzak_pos.api import overview_calc
from murzak_pos.api.expiry_rules import CRITICAL, EXPIRED, SOON

TABS = ("overview", "low", "movements", "expiry")
LEVELS = ("ok", "low", "out")
MOVEMENT_STATUSES = ("draft", "submitted", "cancelled")
MOVEMENT_KINDS = ("ledger", "entry", "receipt", "issue", "transfer")
EXPIRY_STATES = ("expired", "critical", "soon")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_MOVEMENT_DAYS = 30
MAX_MOVEMENT_DAYS = 366
MAX_COUNT_ROWS = 1000
MAX_MOVE_ROWS = 200
MAX_STORE_NAME = 60
MAX_NOTE = 140
MAX_QUANTITY = 1_000_000
LOW_TAB_LEVELS = ("out", "low")  # the Low stock tab lists what to reorder: out first, then low

# Why stock leaves the shop other than by a sale. The key is what the page sends; the label is what is written on
# the stock entry, so the owner reads it in plain words on the old screens too.
WRITE_OFF_REASONS = {
	"damaged": "Damaged",
	"expired": "Expired",
	"lost": "Lost",
	"used_in_shop": "Used in the shop",
	"other": "Other",
}
REASON_NEEDS_NOTE = ("other",)

# Warehouses a new ERPNext business comes with that are not places to sell from (the same list the New look till
# uses: src/newlook/till/sellingLocations.js)
NOT_SELLING = (
	re.compile(r"^work in progress\b", re.I),
	re.compile(r"^finished goods\b", re.I),
	re.compile(r"^goods in transit\b", re.I),
	re.compile(r"^all warehouses\b", re.I),
)

# Stock Entry purposes, grouped for the plain word shown in the Movements list
IN_PURPOSES = ("Material Receipt",)
OUT_PURPOSES = ("Material Issue",)
TRANSFER_PURPOSES = ("Material Transfer", "Send to Warehouse", "Receive at Warehouse")
MAKE_PURPOSES = (
	"Manufacture",
	"Repack",
	"Material Consumption for Manufacture",
	"Material Transfer for Manufacture",
)
SALE_VOUCHERS = ("Sales Invoice", "POS Invoice", "Delivery Note")
PURCHASE_VOUCHERS = ("Purchase Receipt", "Purchase Invoice")


class StockError(ValueError):
	"""Something asked for cannot be used. `code` says why; stock_levels_api turns it into a plain sentence."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


# --- numbers and text ------------------------------------------------------------------------------


def num(value) -> float:
	try:
		number = float(value or 0)
	except (TypeError, ValueError):
		return 0.0
	return number if math.isfinite(number) else 0.0


def qty(value) -> float:
	"""A quantity with at most 3 decimals (and never -0.0)."""
	return round(num(value), 3) + 0.0


def money(value) -> float:
	"""A money amount with at most 2 decimals (and never -0.0)."""
	return round(num(value), 2) + 0.0


def clean_text(value, limit: int = MAX_NOTE) -> str:
	"""Tidy text typed by a person: one line, single spaces, at most `limit` characters."""
	return " ".join(str(value or "").split())[:limit]


def plain_message(text, limit: int = 300) -> str:
	"""A message from another part of the app as plain words: no tags, no "Validation error:" lead-in."""
	text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
	text = re.sub(r"^(validation error|error creating [a-z ]+):\s*", "", text.strip(), flags=re.I)
	return clean_text(text, limit)


def like_pattern(text) -> str:
	"""A SQL LIKE pattern that finds `text` anywhere, with the characters LIKE treats as special made plain."""
	plain = clean_text(text, 80).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f"%{plain}%"


def format_time(value) -> str:
	"""'14:05' for a time the database gave as text, a time or a duration; '' when there is none."""
	if value is None or value == "":
		return ""
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds()) % 86400
		return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
	if isinstance(value, time | datetime):
		return f"{value.hour:02d}:{value.minute:02d}"
	match = re.match(r"^(\d{1,2}):(\d{2})", str(value))
	return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""


def format_day(value) -> str:
	if isinstance(value, datetime):
		return value.date().isoformat()
	if isinstance(value, date):
		return value.isoformat()
	return str(value or "")[:10]


# --- the request ---------------------------------------------------------------------------------


def clean_tab(tab) -> str:
	tab = str(tab or "overview").strip().lower()
	if tab not in TABS:
		raise StockError("bad_tab")
	return tab


def clean_paging(page, page_size) -> tuple[int, int]:
	"""(page, rows per page): page 1 and 20 rows when missing or not a number, never more than 100 rows."""
	try:
		page = int(page)
	except (TypeError, ValueError):
		page = 1
	try:
		size = int(page_size)
	except (TypeError, ValueError):
		size = DEFAULT_PAGE_SIZE
	if size < 1:
		size = DEFAULT_PAGE_SIZE
	return max(1, page), min(size, MAX_PAGE_SIZE)


def clean_status(tab: str, status) -> str | None:
	"""The status filter for a tab: a level for the stock lists, a life-cycle state for movements, an urgency for expiry."""
	status = str(status or "").strip().lower()
	if not status:
		return None
	allowed = {
		"overview": LEVELS,
		"low": LEVELS,
		"movements": MOVEMENT_STATUSES,
		"expiry": EXPIRY_STATES,
	}[tab]
	if status not in allowed:
		raise StockError("bad_status")
	return status


def clean_kind(kind) -> str | None:
	kind = str(kind or "").strip().lower()
	if not kind or kind == "all":
		return None
	if kind not in MOVEMENT_KINDS:
		raise StockError("bad_kind")
	return kind


def parse_day(value) -> date:
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = str(value or "").strip()
	if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
		raise StockError("bad_date")
	try:
		return date.fromisoformat(text)
	except ValueError:
		raise StockError("bad_date") from None


def movement_window(from_date, to_date, today: date) -> tuple[date, date]:
	"""
	The dates the Movements list covers. With none given it is the last 30 days; with only one end given the other is
	30 days away from it. The start may not be after the end and the span is one year at most.
	"""
	start = parse_day(from_date) if from_date else None
	end = parse_day(to_date) if to_date else None
	if start is None and end is None:
		end = today
	if end is None:
		end = min(today, start + timedelta(days=DEFAULT_MOVEMENT_DAYS - 1)) if start <= today else start
	if start is None:
		start = end - timedelta(days=DEFAULT_MOVEMENT_DAYS - 1)
	if start > end:
		raise StockError("date_order")
	if (end - start).days + 1 > MAX_MOVEMENT_DAYS:
		raise StockError("too_long")
	return start, end


def page_info(total: int, page: int, size: int) -> dict:
	return {"total": total, "page": page, "page_size": size, "pages": max(1, math.ceil(total / size))}


def page_of(rows: list, page: int, size: int) -> list:
	start = (page - 1) * size
	return rows[start : start + size]


# --- places and features ----------------------------------------------------------------------------


def is_selling_location(warehouse: dict) -> bool:
	"""A real place to sell from: not a group, not switched off, not one of the warehouses every new business gets."""
	if not warehouse:
		return False
	if warehouse.get("disabled") in (1, True, "1") or warehouse.get("is_group") in (1, True, "1"):
		return False
	name = str(warehouse.get("warehouse_name") or warehouse.get("name") or "")
	return not any(pattern.match(name) for pattern in NOT_SELLING)


def selling_locations(warehouses) -> list[str]:
	"""The names of the real selling locations."""
	return [w["name"] for w in warehouses or [] if is_selling_location(w)]


def pick_warehouse(requested, locations: list[str]) -> str:
	"""
	The store a stock change or a count is for: the one asked for, or the only one the shop has. With two or more
	and none chosen, the person must choose; with none at all there is nowhere to put stock.
	"""
	if requested:
		return requested
	if not locations:
		raise StockError("no_store")
	if len(locations) == 1:
		return locations[0]
	raise StockError("need_store")


def tracks_expiry(batches_with_expiry, details_with_expiry) -> bool:
	"""The shop tracks expiry dates when any batch or any stock-loading record carries one."""
	return int(num(batches_with_expiry)) > 0 or int(num(details_with_expiry)) > 0


# --- the stock list --------------------------------------------------------------------------------------


def build_stock_rows(bins, low_rows, sold_items, item_info=None) -> list[dict]:
	"""
	One row per product from the store-level stock rows.

	bins        item_code, item_name, item_group, stock_uom, warehouse, actual_qty, stock_value (one per store)
	low_rows    item_code, item_name, warehouse, actual_qty, reorder_level: stores at or below their warning level
	sold_items  item_code, item_name: stock products that sold in the last 30 days
	item_info   item_code -> item_name, item_group, stock_uom, for sold products that have no stock record yet
	"""
	item_info = item_info or {}
	low = {row["item_code"]: row for row in overview_calc.running_low_items(low_rows)}
	sold = {row.get("item_code"): row for row in sold_items or []}
	products: dict[str, dict] = {}
	for row in bins or []:
		code = row.get("item_code")
		product = products.setdefault(
			code,
			{
				"item_code": code,
				"item_name": row.get("item_name") or code,
				"category": row.get("item_group") or "",
				"unit": row.get("stock_uom") or "",
				"qty": 0.0,
				"value": 0.0,
				"stores": 0,
			},
		)
		stored = num(row.get("actual_qty"))
		product["qty"] += stored
		product["value"] += num(row.get("stock_value"))
		if stored > 0:
			product["stores"] += 1
	for code, row in sold.items():
		if code in products:
			continue
		info = item_info.get(code) or {}
		products[code] = {
			"item_code": code,
			"item_name": info.get("item_name") or row.get("item_name") or code,
			"category": info.get("item_group") or "",
			"unit": info.get("stock_uom") or "",
			"qty": 0.0,
			"value": 0.0,
			"stores": 0,
		}
	rows = []
	for code, product in products.items():
		total = qty(product["qty"])
		if total == 0 and code not in sold:
			continue  # no stock and not selling: not part of the stock list
		level = "out" if total <= 0 else "low" if code in low else "ok"
		rows.append(
			{
				**product,
				"qty": total,
				"value": money(product["value"]),
				"level": level,
				"warning_level": qty(low[code]["warning_level"]) if level == "low" else None,
			}
		)
	return sorted(rows, key=lambda r: (str(r["item_name"]).lower(), str(r["item_code"])))


def filter_stock_rows(rows, search=None, category=None, status=None) -> list[dict]:
	"""Rows whose name or code contains `search`, in `category`, at the `status` level."""
	needle = clean_text(search, 80).lower()
	result = []
	for row in rows or []:
		if needle and needle not in f"{row['item_name']} {row['item_code']}".lower():
			continue
		if category and row.get("category") != category:
			continue
		if status and row.get("level") != status:
			continue
		result.append(row)
	return result


def low_stock_rows(rows) -> list[dict]:
	"""What needs reordering: out of stock first, then low, the least stock first within each."""
	wanted = [r for r in rows or [] if r["level"] in LOW_TAB_LEVELS]
	return sorted(
		wanted,
		key=lambda r: (
			LOW_TAB_LEVELS.index(r["level"]),
			r["qty"],
			str(r["item_name"]).lower(),
			str(r["item_code"]),
		),
	)


def tiles(rows, show_value: bool) -> dict:
	"""
	The four tiles above the list, counted from the same rows as the list: items, low, out of stock and the stock
	value (left out when the person may not see cost). `items` counts every row, so with no level chosen it is the
	list's total.
	"""
	rows = rows or []
	result = {
		"items": len(rows),
		"low": sum(1 for r in rows if r["level"] == "low"),
		"out_of_stock": sum(1 for r in rows if r["level"] == "out"),
	}
	if show_value:
		result["stock_value"] = money(sum(num(r.get("value")) for r in rows))
	return result


def strip_costs(rows) -> list[dict]:
	"""The rows without any cost figure (for a cashier)."""
	return [{k: v for k, v in row.items() if k not in ("value", "valuation_rate")} for row in rows or []]


def categories(rows) -> list[str]:
	return sorted({r["category"] for r in rows or [] if r.get("category")}, key=str.lower)


# --- movements ----------------------------------------------------------------------------------------


def movement_kind(voucher_type, purpose, qty_change) -> str:
	"""The plain-word kind of a movement, as a key the page turns into words."""
	voucher_type, purpose = voucher_type or "", purpose or ""
	change = num(qty_change)
	if voucher_type == "Stock Entry":
		if purpose in IN_PURPOSES:
			return "stock_in"
		if purpose in OUT_PURPOSES:
			return "stock_out"
		if purpose in TRANSFER_PURPOSES:
			return "transfer"
		if purpose in MAKE_PURPOSES:
			return "made"
		return "entry"
	if voucher_type in SALE_VOUCHERS:
		return "sale_return" if change > 0 else "sale"
	if voucher_type in PURCHASE_VOUCHERS:
		return "purchase_return" if change < 0 else "purchase"
	if voucher_type == "Stock Reconciliation":
		return "count"
	return "other"


def movement_status(docstatus, is_cancelled) -> str:
	if int(num(docstatus)) == 0:
		return "draft"
	if int(num(is_cancelled)) == 1 or int(num(docstatus)) == 2:
		return "cancelled"
	return "submitted"


def reason_remark(reason: str, note: str = "") -> str:
	"""The line written on a write-off stock entry: 'Write-off (Damaged): two crates fell'."""
	label = WRITE_OFF_REASONS[reason]
	return f"Write-off ({label}): {note}" if note else f"Write-off ({label})"


def reason_of(remarks) -> str | None:
	"""The reason key found in a stock entry's remarks, or None when it was not a write-off made here."""
	match = re.match(r"^Write-off \((.+?)\)", str(remarks or ""))
	if not match:
		return None
	for key, label in WRITE_OFF_REASONS.items():
		if label == match.group(1):
			return key
	return None


def shape_movement(row: dict, can_open_entries: bool = True) -> dict:
	"""One movement as the page draws it, from a row of the joined query."""
	voucher_type = row.get("voucher_type") or ""
	reference = row.get("voucher_no") or ""
	source = row.get("source") or "ledger"
	after = row.get("qty_after")
	return {
		"id": f"{source}:{row.get('id')}",
		"date": format_day(row.get("posting_date")),
		"time": format_time(row.get("posting_time")),
		"item_code": row.get("item_code") or "",
		"item_name": row.get("item_name") or row.get("item_code") or "",
		"warehouse": row.get("warehouse") or "",
		"kind": movement_kind(voucher_type, row.get("purpose"), row.get("qty_change")),
		"source": source,
		"qty_change": qty(row.get("qty_change")),
		"qty_after": None if after is None else qty(after),
		"status": movement_status(row.get("docstatus") if source == "entry" else 1, row.get("is_cancelled")),
		"reference": reference,
		"voucher_type": voucher_type,
		"entry": reference if voucher_type == "Stock Entry" and can_open_entries else None,
		"reason": reason_of(row.get("remarks")),
	}


def movements_query(company, start: date, end: date, *, warehouse=None, search=None, kind=None, status=None):
	"""
	The SQL for one page of movements and for their count, with the values to fill in. Two kinds of row are joined:
	stock ledger lines (everything that moved stock, submitted or cancelled) and draft stock entries, which have not
	moved stock yet and so are not in the ledger. Returns (count_sql, page_sql, values).
	"""
	values = {"company": company, "start": start, "end": end}
	ledger = ["sle.company = %(company)s", "sle.posting_date BETWEEN %(start)s AND %(end)s"]
	drafts = [
		"se.company = %(company)s",
		"se.docstatus = 0",
		"se.posting_date BETWEEN %(start)s AND %(end)s",
	]
	if warehouse:
		values["warehouse"] = warehouse
		ledger.append("sle.warehouse = %(warehouse)s")
		drafts.append("(d.s_warehouse = %(warehouse)s OR d.t_warehouse = %(warehouse)s)")
	if search and clean_text(search, 80):
		values["like"] = like_pattern(search)
		ledger.append(
			"(sle.item_code LIKE %(like)s OR i.item_name LIKE %(like)s OR sle.voucher_no LIKE %(like)s)"
		)
		drafts.append("(d.item_code LIKE %(like)s OR d.item_name LIKE %(like)s OR se.name LIKE %(like)s)")
	use_ledger, use_drafts = True, True
	if kind == "ledger":
		use_drafts = False
	elif kind == "entry":
		ledger.append("sle.voucher_type = 'Stock Entry'")
	elif kind == "transfer":
		values["transfer_purposes"] = TRANSFER_PURPOSES
		ledger.append("sle.voucher_type = 'Stock Entry' AND se.purpose IN %(transfer_purposes)s")
		drafts.append("se.purpose IN %(transfer_purposes)s")
	elif kind in ("receipt", "issue"):
		values["purpose"] = "Material Receipt" if kind == "receipt" else "Material Issue"
		ledger.append("sle.voucher_type = 'Stock Entry' AND se.purpose = %(purpose)s")
		drafts.append("se.purpose = %(purpose)s")
	if status == "draft":
		use_ledger = False
	elif status == "cancelled":
		use_drafts = False
		ledger.append("sle.is_cancelled = 1")
	elif status == "submitted":
		use_drafts = False
		ledger.append("sle.is_cancelled = 0")
	branches = []
	if use_ledger:
		branches.append(
			"SELECT 'ledger' AS source, sle.name AS id, sle.posting_date AS posting_date, sle.posting_time AS posting_time, "
			"sle.item_code AS item_code, i.item_name AS item_name, sle.warehouse AS warehouse, "
			"sle.voucher_type AS voucher_type, sle.voucher_no AS voucher_no, sle.actual_qty AS qty_change, "
			"sle.qty_after_transaction AS qty_after, sle.is_cancelled AS is_cancelled, 1 AS docstatus, "
			"IFNULL(se.purpose, '') AS purpose, IFNULL(se.remarks, '') AS remarks "
			"FROM `tabStock Ledger Entry` sle LEFT JOIN `tabItem` i ON i.name = sle.item_code "
			"LEFT JOIN `tabStock Entry` se ON se.name = sle.voucher_no AND sle.voucher_type = 'Stock Entry' "
			f"WHERE {' AND '.join(ledger)}"
		)
	if use_drafts:
		branches.append(
			"SELECT 'entry' AS source, d.name AS id, se.posting_date AS posting_date, se.posting_time AS posting_time, "
			"d.item_code AS item_code, d.item_name AS item_name, IFNULL(d.t_warehouse, d.s_warehouse) AS warehouse, "
			"'Stock Entry' AS voucher_type, se.name AS voucher_no, "
			"CASE WHEN se.purpose = 'Material Issue' THEN -d.qty ELSE d.qty END AS qty_change, "
			"NULL AS qty_after, 0 AS is_cancelled, se.docstatus AS docstatus, "
			"IFNULL(se.purpose, '') AS purpose, IFNULL(se.remarks, '') AS remarks "
			"FROM `tabStock Entry Detail` d JOIN `tabStock Entry` se ON se.name = d.parent "
			f"WHERE {' AND '.join(drafts)}"
		)
	if not branches:
		branches.append(
			"SELECT 'ledger' AS source, NULL AS id, NULL AS posting_date, NULL AS posting_time, NULL AS item_code, "
			"NULL AS item_name, NULL AS warehouse, NULL AS voucher_type, NULL AS voucher_no, NULL AS qty_change, "
			"NULL AS qty_after, 0 AS is_cancelled, 1 AS docstatus, '' AS purpose, '' AS remarks FROM DUAL WHERE 1 = 0"
		)
	joined = " UNION ALL ".join(f"({b})" for b in branches)
	count_sql = f"SELECT COUNT(*) AS n FROM ({joined}) m"
	page_sql = (
		f"SELECT m.* FROM ({joined}) m ORDER BY m.posting_date DESC, m.posting_time DESC, m.id DESC "
		"LIMIT %(limit)s OFFSET %(offset)s"
	)
	return count_sql, page_sql, values


# --- expiry ---------------------------------------------------------------------------------------------

_EXPIRY_STATE = {EXPIRED: "expired", CRITICAL: "critical", SOON: "soon"}


def shape_expiry(row: dict, show_value: bool) -> dict:
	"""One expiring or expired line (a row of expiry_api.get_expiry_alerts) as the page draws it."""
	shaped = {
		"item_code": row.get("item_code") or "",
		"item_name": row.get("item_name") or row.get("item_code") or "",
		"warehouse": row.get("warehouse") or "",
		"batch_no": row.get("batch_no") or "",
		"qty": qty(row.get("qty")),
		"unit": row.get("stock_uom") or "",
		"expiry_date": format_day(row.get("expiry_date")),
		"days_left": int(num(row.get("days_left"))),
		"state": _EXPIRY_STATE.get(row.get("status"), "soon"),
	}
	if show_value:
		shaped["value"] = money(row.get("stock_value"))
	return shaped


def filter_expiry_rows(rows, search=None, status=None) -> list[dict]:
	needle = clean_text(search, 80).lower()
	return [
		r
		for r in rows or []
		if (not needle or needle in f"{r['item_name']} {r['item_code']} {r['batch_no']}".lower())
		and (not status or r["state"] == status)
	]


# --- writes ----------------------------------------------------------------------------------------------


def clean_quantity(value) -> float:
	"""A quantity to move: more than 0, at most 3 decimals, a sensible size. Refused with `bad_qty` otherwise."""
	if isinstance(value, bool) or value is None:
		raise StockError("bad_qty")
	try:
		number = float(str(value).strip().replace(",", ""))
	except ValueError:
		raise StockError("bad_qty") from None
	if not math.isfinite(number):
		raise StockError("bad_qty")
	number = round(number, 3)
	if number <= 0 or number > MAX_QUANTITY:
		raise StockError("bad_qty")
	return number


def clean_item(item_code) -> str:
	code = str(item_code or "").strip()
	if not code or len(code) > 140:
		raise StockError("need_item")
	return code


def clean_reason(reason, note) -> tuple[str, str]:
	"""
	(reason key, tidy note) for a write-off. A write-off cannot be saved without a reason (acceptance check AC-27):
	a blank reason is `need_reason`, a reason not on the list is `bad_reason`, and "Other" without a note is
	`need_note`.
	"""
	key = str(reason or "").strip().lower()
	if not key:
		raise StockError("need_reason")
	if key not in WRITE_OFF_REASONS:
		raise StockError("bad_reason")
	text = clean_text(note)
	if key in REASON_NEEDS_NOTE and not text:
		raise StockError("need_note")
	return key, text


def receipt_remark(note: str) -> str:
	return f"Stock in: {note}" if note else ""


def plain_qty(value) -> str:
	"""A quantity as people say it: 4 not 4.0, 2.5 as 2.5, 1000000 not 1e+06."""
	return f"{qty(value):.3f}".rstrip("0").rstrip(".")


def transfer_remark(note: str) -> str:
	return f"Stock moved: {note}" if note else "Stock moved between stores"


def clean_move(items) -> list[dict]:
	"""
	The products to move between stores, checked: a list (or JSON text) of { item_code, qty } with a quantity above
	zero, each product once. Refused with `empty_move`, `bad_move`, `too_many`, `bad_qty`, `need_item` or `duplicate_item`.
	"""
	if isinstance(items, str):
		try:
			items = json.loads(items or "[]")
		except ValueError:
			raise StockError("bad_move") from None
	if not isinstance(items, list) or not items:
		raise StockError("empty_move")
	if len(items) > MAX_MOVE_ROWS:
		raise StockError("too_many")
	cleaned, seen = [], set()
	for line in items:
		if not isinstance(line, dict):
			raise StockError("bad_move")
		code = clean_item(line.get("item_code"))
		if code in seen:
			raise StockError("duplicate_item")
		seen.add(code)
		cleaned.append({"item_code": code, "qty": clean_quantity(line.get("qty"))})
	return cleaned


def clean_move_stores(source, target) -> tuple[str, str]:
	"""(from store, to store) for a move: both named, and not the same store."""
	source, target = str(source or "").strip(), str(target or "").strip()
	if not source or not target:
		raise StockError("need_store")
	if source == target:
		raise StockError("same_store")
	return source, target


def shortages(lines, held_by_item) -> list[dict]:
	"""The lines asking for more than the store holds: [{ item_code, wanted, held }]."""
	return [
		{
			"item_code": line["item_code"],
			"wanted": line["qty"],
			"held": qty(held_by_item.get(line["item_code"])),
		}
		for line in lines or []
		if line["qty"] > qty(held_by_item.get(line["item_code"]))
	]


def clean_store_name(value) -> str:
	"""The name of a new store: one tidy line of 2 to 60 characters. Refused with `need_store_name` or `bad_store_name`."""
	name = clean_text(value, MAX_STORE_NAME + 1)
	if not name:
		raise StockError("need_store_name")
	if len(name) < 2 or len(name) > MAX_STORE_NAME or re.search(r"[<>\\/]", name):
		raise StockError("bad_store_name")
	return name


# --- counting ---------------------------------------------------------------------------------------------


def sheet_rows(bins, scope: str, category=None) -> tuple[list[dict], bool]:
	"""
	The rows of a count sheet from the store's stock rows (item_code, item_name, item_group, stock_uom, actual_qty):
	the whole store or one category, in category then name order so the count follows the shelves. The second
	value is True when the sheet was cut at MAX_COUNT_ROWS.
	"""
	scope = str(scope or "all").strip().lower()
	if scope not in ("all", "category"):
		raise StockError("bad_scope")
	if scope == "category" and not category:
		raise StockError("need_category")
	rows = []
	for row in bins or []:
		if scope == "category" and row.get("item_group") != category:
			continue
		rows.append(
			{
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name") or row.get("item_code"),
				"category": row.get("item_group") or "",
				"unit": row.get("stock_uom") or "",
				"expected": qty(row.get("actual_qty")),
			}
		)
	rows.sort(key=lambda r: (str(r["category"]).lower(), str(r["item_name"]).lower(), str(r["item_code"])))
	return rows[:MAX_COUNT_ROWS], len(rows) > MAX_COUNT_ROWS


def clean_counts(counts) -> list[dict]:
	"""
	The counted quantities sent by the page, checked: a list (or JSON text) of { item_code, counted } with a counted
	quantity of 0 or more, each product once.
	"""
	if isinstance(counts, str):
		try:
			counts = json.loads(counts or "[]")
		except ValueError:
			raise StockError("bad_count") from None
	if not isinstance(counts, list) or not counts:
		raise StockError("empty_count")
	if len(counts) > MAX_COUNT_ROWS:
		raise StockError("too_many")
	cleaned, seen = [], set()
	for entry in counts:
		if not isinstance(entry, dict):
			raise StockError("bad_count")
		code = clean_item(entry.get("item_code"))
		if code in seen:
			raise StockError("duplicate_item")
		seen.add(code)
		value = entry.get("counted")
		if isinstance(value, bool) or value is None or str(value).strip() == "":
			raise StockError("bad_count")
		try:
			counted = round(float(str(value).strip().replace(",", "")), 3)
		except ValueError:
			raise StockError("bad_count") from None
		if not math.isfinite(counted) or counted < 0 or counted > MAX_QUANTITY:
			raise StockError("bad_count")
		cleaned.append({"item_code": code, "counted": counted})
	return cleaned


def difference_state(difference) -> str:
	"""matches, short (counted less than the system says) or over (counted more)."""
	value = qty(difference)
	return "matches" if value == 0 else "short" if value < 0 else "over"


def count_differences(counts, expected_by_item: dict) -> list[dict]:
	"""Expected against counted for each product counted, with the difference (counted minus expected)."""
	rows = []
	for entry in counts:
		expected = qty(expected_by_item.get(entry["item_code"]))
		difference = qty(entry["counted"] - expected)
		rows.append(
			{
				"item_code": entry["item_code"],
				"expected": expected,
				"counted": qty(entry["counted"]),
				"difference": difference,
				"state": difference_state(difference),
			}
		)
	return rows


def count_totals(rows) -> dict:
	"""Lines short, over and matching, and the net difference in units."""
	rows = rows or []
	return {
		"lines": len(rows),
		"short": sum(1 for r in rows if r["state"] == "short"),
		"over": sum(1 for r in rows if r["state"] == "over"),
		"matches": sum(1 for r in rows if r["state"] == "matches"),
		"net_difference": qty(sum(r["difference"] for r in rows)),
	}
