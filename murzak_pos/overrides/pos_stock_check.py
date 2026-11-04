"""
Stop ERPNext counting a sale's stock twice when it checks that a POS item is in stock.

ERPNext's POS Invoice checks "stock on hand minus stock reserved by POS Invoices that are submitted but not yet merged
into a Sales Invoice". That is right where stock only moves when the shift is closed. This app moves the stock (and the
accounts) at the moment of sale, and its shift close leaves those sales unmerged on purpose so nothing is posted twice
(overrides/pos_closing_entry_class.py). So every sale stayed "reserved" for ever, on top of already being taken off the
shelf: 23 on hand and 27 sold gave -4 available, and a sale of an item that was really in stock was refused with
"Item has no stock in warehouse".

The reserved quantity is therefore reduced by the quantity of sales whose stock has already been posted. A sale whose
stock has not been posted still counts as reserved, as before.

install() is called when the POS Invoice class is loaded (overrides/pos_invoice_class.py).
"""

from __future__ import annotations

from collections.abc import Callable

MARK = "_murzak_posted_stock"


def unreserved(reserved, already_posted) -> float:
	"""The reserved quantity once sales that have already left stock are taken out. Never below zero."""
	return max(0.0, float(reserved or 0) - float(already_posted or 0))


def posted_unmerged_qty(item_code, warehouse) -> float:
	"""Quantity of this item, in this warehouse, on submitted POS Invoices not yet merged whose stock is already posted."""
	import frappe

	rows = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(i.stock_qty), 0)
		FROM `tabPOS Invoice Item` i
		JOIN `tabPOS Invoice` p ON p.name = i.parent
		WHERE p.docstatus = 1
			AND p.status != 'Consolidated'
			AND i.item_code = %(item_code)s
			AND i.warehouse = %(warehouse)s
			AND EXISTS (
				SELECT 1 FROM `tabStock Ledger Entry` s
				WHERE s.voucher_type = 'POS Invoice' AND s.voucher_no = p.name AND s.is_cancelled = 0
			)
		""",
		{"item_code": item_code, "warehouse": warehouse},
	)
	return float(rows[0][0]) if rows else 0.0


def wrap(reserved_qty: Callable, posted: Callable = posted_unmerged_qty) -> Callable:
	"""`get_pos_reserved_qty` with the already-posted sales taken out. A wrapped function is not wrapped twice."""
	if getattr(reserved_qty, MARK, False):
		return reserved_qty

	def get_pos_reserved_qty(item_code, warehouse):
		return unreserved(reserved_qty(item_code, warehouse), posted(item_code, warehouse))

	get_pos_reserved_qty.__name__ = getattr(reserved_qty, "__name__", "get_pos_reserved_qty")
	setattr(get_pos_reserved_qty, MARK, True)
	return get_pos_reserved_qty


def install(module=None) -> bool:
	"""Wrap ERPNext's get_pos_reserved_qty. Returns True when it was wrapped now."""
	if module is None:
		from erpnext.accounts.doctype.pos_invoice import pos_invoice as module
	original = getattr(module, "get_pos_reserved_qty", None)
	if not callable(original) or getattr(original, MARK, False):
		return False
	module.get_pos_reserved_qty = wrap(original)
	return True
