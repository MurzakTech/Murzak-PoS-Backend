# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the closing-entry summary the till receives when a shift is closed.

Closing a shift used to end in an error AFTER the shift had closed, because the code read a field
("total_taxes_and_charges") that ERPNext's POS Closing Entry does not have. ERPNext keeps tax per
account in the `taxes` table. No database is needed.
"""

import unittest

import frappe

from murzak_pos.api.sales_api import _closing_entry_summary


def closing_entry(taxes=None, **overrides):
	"""A closing entry shaped like ERPNext 15's: tax rows, and NO total_taxes_and_charges field."""
	values = {
		"name": "POS-CLO-0001",
		"pos_opening_entry": "POS-OPE-0001",
		"pos_profile": "Shop POS",
		"company": "Shop A",
		"user": "cashier@example.com",
		"status": "Submitted",
		"posting_date": "2026-10-11",
		"period_start_date": "2026-10-11 08:00:00",
		"period_end_date": "2026-10-11 18:00:00",
		"grand_total": 1160,
		"net_total": 1000,
		"total_quantity": 7,
		"docstatus": 1,
		"taxes": taxes or [],
	}
	values.update(overrides)
	return frappe._dict(values)


class ClosingEntrySummaryCase(unittest.TestCase):
	def test_tax_rows_are_added_up(self):
		entry = closing_entry(taxes=[frappe._dict(amount=100.5), frappe._dict(amount=59.5)])
		self.assertEqual(_closing_entry_summary(entry)["total_taxes_and_charges"], 160)

	def test_a_shift_with_no_tax_rows_has_zero_tax(self):
		self.assertEqual(_closing_entry_summary(closing_entry())["total_taxes_and_charges"], 0)

	def test_it_does_not_need_the_field_erpnext_lacks(self):
		entry = closing_entry()
		self.assertNotIn("total_taxes_and_charges", entry)
		self.assertEqual(_closing_entry_summary(entry)["name"], "POS-CLO-0001")  # no AttributeError

	def test_the_rest_of_the_summary_is_unchanged(self):
		summary = _closing_entry_summary(closing_entry())
		self.assertEqual(summary["grand_total"], 1160)
		self.assertEqual(summary["net_total"], 1000)
		self.assertEqual(summary["total_quantity"], 7)
		self.assertEqual(summary["posting_date"], "2026-10-11")
		self.assertEqual(summary["docstatus"], 1)
		self.assertEqual(summary["status"], "Submitted")


if __name__ == "__main__":
	unittest.main()
