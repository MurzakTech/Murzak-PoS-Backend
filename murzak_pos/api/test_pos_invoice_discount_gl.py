# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the discount ledger rows of a POS Invoice that is submitted through Sales Invoice rules.

ERPNext 15.122 only handles "Purchase Invoice" and "Sales Invoice" when it builds discount ledger rows,
so a POS Invoice raised UnboundLocalError (enable_discount_accounting) on submit. The override runs the
Sales Invoice rules and then restores the POS Invoice name on the rows it added. ERPNext is replaced by
small stand-ins here, so no database and no ERPNext install are needed.
"""

import importlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


class FakeAccountsController:
	"""Behaves like ERPNext 15.122: only the two invoice kinds the real code knows are accepted."""

	def make_discount_gl_entries(self, gl_entries):
		if self.doctype not in ("Purchase Invoice", "Sales Invoice"):
			raise UnboundLocalError("enable_discount_accounting")
		gl_entries.append({"account": "Discount Allowed", "voucher_type": self.doctype})


def load_override():
	"""Import the override with ERPNext stand-ins, leaving no trace in sys.modules afterwards."""
	erpnext_pos = ModuleType("erpnext.accounts.doctype.pos_invoice.pos_invoice")
	erpnext_pos.POSInvoice = type("POSInvoice", (FakeAccountsController,), {})
	erpnext_si = ModuleType("erpnext.accounts.doctype.sales_invoice.sales_invoice")
	erpnext_si.SalesInvoice = type("SalesInvoice", (), {})
	stubs = {
		"erpnext": ModuleType("erpnext"),
		"erpnext.accounts": ModuleType("erpnext.accounts"),
		"erpnext.accounts.doctype": ModuleType("erpnext.accounts.doctype"),
		"erpnext.accounts.doctype.pos_invoice": ModuleType("erpnext.accounts.doctype.pos_invoice"),
		"erpnext.accounts.doctype.pos_invoice.pos_invoice": erpnext_pos,
		"erpnext.accounts.doctype.sales_invoice": ModuleType("erpnext.accounts.doctype.sales_invoice"),
		"erpnext.accounts.doctype.sales_invoice.sales_invoice": erpnext_si,
	}
	with patch.dict(sys.modules, stubs):
		sys.modules.pop("murzak_pos.overrides.pos_invoice_class", None)
		module = importlib.import_module("murzak_pos.overrides.pos_invoice_class")
	sys.modules.pop("murzak_pos.overrides.pos_invoice_class", None)
	return module


class DiscountLedgerCase(unittest.TestCase):
	def setUp(self):
		self.invoice = load_override().POSInvoice()
		self.invoice.doctype = "POS Invoice"

	def test_pos_invoice_no_longer_raises(self):
		rows = []
		self.invoice.make_discount_gl_entries(rows)
		self.assertEqual(len(rows), 1)

	def test_rows_added_here_carry_the_pos_invoice_name(self):
		rows = [{"account": "Debtors", "voucher_type": "POS Invoice"}]
		self.invoice.make_discount_gl_entries(rows)
		self.assertEqual([row["voucher_type"] for row in rows], ["POS Invoice", "POS Invoice"])

	def test_rows_from_earlier_steps_are_left_alone(self):
		rows = [{"account": "Sales", "voucher_type": "Sales Invoice"}]
		self.invoice.make_discount_gl_entries(rows)
		self.assertEqual(rows[0]["voucher_type"], "Sales Invoice")

	def test_document_type_is_restored_even_when_the_parent_fails(self):
		with patch.object(FakeAccountsController, "make_discount_gl_entries", side_effect=ValueError("boom")):
			with self.assertRaises(ValueError):
				self.invoice.make_discount_gl_entries([])
		self.assertEqual(self.invoice.doctype, "POS Invoice")


if __name__ == "__main__":
	unittest.main()
