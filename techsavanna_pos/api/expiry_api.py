"""
Expiry alerts: stock that has expired or will expire soon, per business.

Two places hold expiry dates:
- ERPNext Batch records, for batch-tracked items (medicines, food lots). The
  quantity left in each warehouse comes from ERPNext's own batch ledger.
- Inventory Item Details, where the POS stores the expiry date entered when
  stock is loaded for items that are not batch-tracked. Their quantity is the
  item's stock (Bin) in that warehouse.

Wording and urgency rules live in expiry_rules.py.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from techsavanna_pos.api.expiry_rules import clamp_window, classify, sort_alerts, summarize
from techsavanna_pos.api.payment_gateway_common import resolve_company


def _company_warehouses(company: str, warehouse: str | None) -> list[str]:
	warehouses = frappe.get_all("Warehouse", filters={"company": company, "is_group": 0}, pluck="name")
	if warehouse:
		if warehouse not in warehouses:
			frappe.throw(
				_("Warehouse {0} does not belong to {1}.").format(warehouse, company), frappe.PermissionError
			)
		return [warehouse]
	return warehouses


def _batch_lines(warehouses: list[str], cutoff, include_expired: bool, today) -> list[dict]:
	"""One line per batch per warehouse that still holds stock of it."""
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	filters = [["expiry_date", "<=", cutoff], ["disabled", "=", 0], ["batch_qty", ">", 0]]
	if not include_expired:
		filters.append(["expiry_date", ">=", today])
	batches = frappe.get_all("Batch", filters=filters, fields=["name", "item", "expiry_date"])

	allowed = set(warehouses)
	lines = []
	for batch in batches:
		for entry in get_batch_qty(batch_no=batch.name) or []:
			qty = flt(entry.get("qty"))
			if qty > 0 and entry.get("warehouse") in allowed:
				lines.append(
					{
						"item_code": batch.item,
						"warehouse": entry.get("warehouse"),
						"batch_no": batch.name,
						"qty": qty,
						"expiry_date": getdate(batch.expiry_date),
						"source": "Batch",
					}
				)
	return lines


def _detail_lines(company, warehouses, cutoff, include_expired, today) -> list[dict]:
	"""Expiry dates recorded at stock loading, for items without batch tracking."""
	if not warehouses or not frappe.db.exists("DocType", "Inventory Item Details"):
		return []
	filters = [
		["company", "=", company],
		["warehouse", "in", warehouses],
		["expiry_date", "is", "set"],
		["expiry_date", "<=", cutoff],
	]
	if not include_expired:
		filters.append(["expiry_date", ">=", today])
	details = frappe.get_all(
		"Inventory Item Details",
		filters=filters,
		fields=["item_code", "warehouse", "expiry_date", "batch_no"],
	)
	batch_items = set(
		frappe.get_all(
			"Item",
			filters={"has_batch_no": 1, "name": ["in", list({d.item_code for d in details}) or [""]]},
			pluck="name",
		)
	)
	lines = []
	for d in details:
		# Batch-tracked items are counted from their batches, so skip them here.
		if d.item_code in batch_items:
			continue
		qty = flt(
			frappe.db.get_value("Bin", {"item_code": d.item_code, "warehouse": d.warehouse}, "actual_qty")
		)
		if qty <= 0:
			continue
		lines.append(
			{
				"item_code": d.item_code,
				"warehouse": d.warehouse,
				"batch_no": d.batch_no or "",
				"qty": qty,
				"expiry_date": getdate(d.expiry_date),
				"source": "Stock entry",
			}
		)
	return lines


@frappe.whitelist()
def get_expiry_alerts(
	company: str | None = None,
	days: int = 30,
	warehouse: str | None = None,
	include_expired: int = 1,
) -> dict:
	"""
	Stock that has expired or expires within `days` (1 to 365, default 30),
	soonest first, with the stock value at risk.
	"""
	company = resolve_company(company)
	window = clamp_window(days)
	include_expired = bool(cint(include_expired))
	today = getdate(nowdate())
	cutoff = add_days(today, window)

	warehouses = _company_warehouses(company, warehouse)
	if not warehouses:
		return {"success": True, "data": [], "summary": summarize([]), "days": window}

	lines = _batch_lines(warehouses, cutoff, include_expired, today)
	lines += _detail_lines(company, warehouses, cutoff, include_expired, today)

	rows = []
	item_codes = list({ln["item_code"] for ln in lines})
	items = {
		i.name: i
		for i in frappe.get_all(
			"Item", filters={"name": ["in", item_codes or [""]]}, fields=["name", "item_name", "stock_uom"]
		)
	}
	for line in lines:
		verdict = classify(line["expiry_date"], today, window)
		if not verdict:
			continue
		rate = flt(
			frappe.db.get_value(
				"Bin", {"item_code": line["item_code"], "warehouse": line["warehouse"]}, "valuation_rate"
			)
		)
		item = items.get(line["item_code"]) or {}
		rows.append(
			{
				**line,
				**verdict,
				"expiry_date": str(line["expiry_date"]),
				"item_name": item.get("item_name") or line["item_code"],
				"stock_uom": item.get("stock_uom") or "",
				"valuation_rate": rate,
				"stock_value": round(rate * line["qty"], 2),
			}
		)

	rows = sort_alerts(rows)
	return {"success": True, "data": rows, "summary": summarize(rows), "days": window}
