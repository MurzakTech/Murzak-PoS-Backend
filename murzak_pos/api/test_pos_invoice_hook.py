# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the POS Invoice submit hook on ERPNext releases with and without an `update_stock` field.

The live server runs ERPNext 15.94.1, where POS Invoice has the field. ERPNext 15.122 has no such
field, and the hook used to read and write it blindly, so every POS sale failed there. Both cases must
work. No database is needed.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from murzak_pos.overrides import pos_invoice


class FakeDoc(SimpleNamespace):
	"""A document with plain attributes and Frappe's get(), unlike a dict, which has its own .items."""

	def get(self, key, default=None):
		return getattr(self, key, default)


def pos_invoice_doc(has_update_stock_field, update_stock=None, doctype="POS Invoice"):
	doc = FakeDoc(
		doctype=doctype,
		name="POS-INV-0001",
		docstatus=0,
		items=[SimpleNamespace(item_code="PEN", warehouse="Stores - A")],
		meta=SimpleNamespace(
			has_field=lambda fieldname: has_update_stock_field and fieldname == "update_stock"
		),
	)
	if update_stock is not None:
		doc.update_stock = update_stock
	return doc


class BeforeSubmitCase(unittest.TestCase):
	def run_hook(self, doc):
		db = MagicMock()
		with (
			patch.object(pos_invoice.frappe, "db", db),
			patch.object(pos_invoice.frappe, "logger", MagicMock()),
		):
			pos_invoice.before_submit(doc)
		return db

	def test_where_the_field_exists_it_is_set_and_saved(self):
		doc = pos_invoice_doc(has_update_stock_field=True, update_stock=0)
		db = self.run_hook(doc)
		self.assertEqual(doc.update_stock, 1)
		db.set_value.assert_called_once_with(
			"POS Invoice", "POS-INV-0001", "update_stock", 1, update_modified=False
		)

	def test_where_there_is_no_field_it_is_set_in_memory_and_nothing_is_written(self):
		doc = pos_invoice_doc(has_update_stock_field=False)
		db = self.run_hook(doc)
		self.assertEqual(doc.update_stock, 1)
		db.set_value.assert_not_called()

	def test_no_error_when_the_attribute_was_never_set(self):
		# This is what used to raise: 'POSInvoice' object has no attribute 'update_stock'
		doc = pos_invoice_doc(has_update_stock_field=False)
		self.run_hook(doc)

	def test_other_documents_are_left_alone(self):
		doc = pos_invoice_doc(has_update_stock_field=True, update_stock=0, doctype="Sales Invoice")
		db = self.run_hook(doc)
		self.assertEqual(doc.update_stock, 0)
		db.set_value.assert_not_called()

	def test_an_unsaved_invoice_is_not_written_to_the_database(self):
		doc = pos_invoice_doc(has_update_stock_field=True)
		doc.name = None
		db = self.run_hook(doc)
		self.assertEqual(doc.update_stock, 1)
		db.set_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()
