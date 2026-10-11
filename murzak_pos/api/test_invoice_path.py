# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the choice of sale path and for shift close, which together must never count a sale twice.

This app posts a POS Invoice's stock and accounts at the moment of sale. Merging the same invoice into a
Sales Invoice at shift close posts it again. A business on eTIMS therefore sells through Sales Invoice,
and shift close skips POS Invoices that are already posted. No database and no ERPNext install are needed.
"""

import importlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from murzak_pos.api import sales_api


class InvoiceTypeCase(unittest.TestCase):
	def resolve(self, override=None, uses_etims=False, global_type="POS Invoice", company="Acme"):
		with (
			patch.object(sales_api.frappe, "db", MagicMock()) as db,
			patch.object(sales_api, "_company_uses_etims", return_value=uses_etims),
			patch.object(sales_api, "_get_pos_settings_invoice_type", return_value=global_type),
		):
			db.get_default.return_value = override
			return sales_api._get_invoice_type(company)

	def test_business_not_on_etims_uses_pos_invoice(self):
		self.assertEqual(self.resolve(), "POS Invoice")

	def test_business_on_etims_uses_sales_invoice(self):
		self.assertEqual(self.resolve(uses_etims=True), "Sales Invoice")

	def test_an_explicit_choice_for_the_business_wins(self):
		self.assertEqual(self.resolve(override="POS Invoice", uses_etims=True), "POS Invoice")

	def test_no_business_given_falls_back_to_the_global_setting(self):
		self.assertEqual(self.resolve(company=None, global_type="Sales Invoice"), "Sales Invoice")

	def test_etims_check_is_false_when_the_kenya_app_is_missing(self):
		with patch.object(sales_api.frappe, "db", MagicMock()) as db:
			db.exists.return_value = False
			self.assertFalse(sales_api._company_uses_etims("Acme"))

	def test_etims_check_needs_an_active_record(self):
		with patch.object(sales_api.frappe, "db", MagicMock()) as db:
			db.exists.side_effect = [True, "settings-1"]
			self.assertTrue(sales_api._company_uses_etims("Acme"))
			filters = db.exists.call_args_list[1].args[1]
			self.assertEqual(filters, {"company": "Acme", "is_active": 1})


def load_closing_entry_override():
	"""Import the override with ERPNext stand-ins, leaving no trace in sys.modules afterwards."""
	closing = ModuleType("erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry")
	closing.POSClosingEntry = type("POSClosingEntry", (), {})
	merge = ModuleType("erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log")
	merge.consolidate_pos_invoices = MagicMock(name="consolidate_pos_invoices")
	merge.create_merge_logs = MagicMock(name="create_merge_logs")
	names = (
		"erpnext",
		"erpnext.accounts",
		"erpnext.accounts.doctype",
		"erpnext.accounts.doctype.pos_closing_entry",
		"erpnext.accounts.doctype.pos_invoice_merge_log",
	)
	stubs = {name: ModuleType(name) for name in names}
	stubs["erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry"] = closing
	stubs["erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log"] = merge
	with patch.dict(sys.modules, stubs):
		sys.modules.pop("murzak_pos.overrides.pos_closing_entry_class", None)
		module = importlib.import_module("murzak_pos.overrides.pos_closing_entry_class")
	sys.modules.pop("murzak_pos.overrides.pos_closing_entry_class", None)
	return module, merge


def rows(*names):
	return [SimpleNamespace(pos_invoice=name) for name in names]


class ShiftCloseCase(unittest.TestCase):
	def setUp(self):
		self.module, self.merge = load_closing_entry_override()

	def closing_entry(self, *names):
		entry = self.module.POSClosingEntry()
		entry.pos_transactions = rows(*names)
		entry.pos_opening_entry = "POS-OPE-1"
		entry.get = lambda key, default=None: getattr(entry, key, default)
		return entry

	def test_split_keeps_posted_invoices_out_of_the_merge(self):
		to_merge, posted = self.module.split_already_posted(rows("A", "B", "C"), {"B"})
		self.assertEqual([r.pos_invoice for r in to_merge], ["A", "C"])
		self.assertEqual([r.pos_invoice for r in posted], ["B"])

	def test_nothing_posted_means_everything_is_merged(self):
		to_merge, posted = self.module.split_already_posted(rows("A", "B"), set())
		self.assertEqual(len(to_merge), 2)
		self.assertEqual(posted, [])

	def test_a_shift_of_posted_invoices_closes_without_a_merge(self):
		entry = self.closing_entry("A", "B")
		with (
			patch.object(self.module, "posted_pos_invoices", return_value={"A", "B"}),
			patch.object(self.module.frappe, "publish_realtime"),
		):
			entry.on_submit()
		self.merge.consolidate_pos_invoices.assert_not_called()
		self.merge.create_merge_logs.assert_called_once_with({}, entry)

	def test_only_unposted_invoices_are_sent_to_the_merge(self):
		entry = self.closing_entry("A", "B")
		with (
			patch.object(self.module, "posted_pos_invoices", return_value={"A"}),
			patch.object(self.module.frappe, "publish_realtime"),
		):
			entry.on_submit()
		kwargs = self.merge.consolidate_pos_invoices.call_args.kwargs
		self.assertEqual([r.pos_invoice for r in kwargs["pos_invoices"]], ["B"])
		self.assertIs(kwargs["closing_entry"], entry)
		self.merge.create_merge_logs.assert_not_called()

	def test_retry_uses_the_same_rule(self):
		entry = self.closing_entry("A")
		with patch.object(self.module, "posted_pos_invoices", return_value={"A"}):
			entry.retry()
		self.merge.consolidate_pos_invoices.assert_not_called()
		self.merge.create_merge_logs.assert_called_once_with({}, entry)

	def test_no_lookup_for_a_shift_with_no_invoices(self):
		self.assertEqual(self.module.posted_pos_invoices([]), set())


if __name__ == "__main__":
	unittest.main()
