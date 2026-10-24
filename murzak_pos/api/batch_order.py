"""
Which batch a sale takes from when the cashier does not choose one.

ERPNext's default is the batch received first. For medicine and food that leaves the batch about to expire on the
shelf while a later one is sold. Setting it to "Expiry" takes the batch that expires first; items with no expiry date
keep their receiving order. A shop that deliberately chose another order (LIFO) is left alone.
"""

from __future__ import annotations

import frappe

SETTING = "pick_serial_and_batch_based_on"
ERPNEXT_DEFAULT = "FIFO"
EARLIEST_EXPIRY = "Expiry"


def pick_earliest_expiry_first() -> None:
	"""Make a sale without a chosen batch take the earliest expiry first. Safe to run again."""
	if frappe.db.get_single_value("Stock Settings", SETTING) != ERPNEXT_DEFAULT:
		return
	frappe.db.set_single_value("Stock Settings", SETTING, EARLIEST_EXPIRY)
