# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Stock reports, stock counts and company details cover only the caller's business. Needs no database."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import onboarding_api, stock

STOCK_CALLS = {
	"inventory_summary_report": {},
	"inventory_movement_report": {"start_date": "2026-10-01", "end_date": "2026-10-24"},
	"inventory_value_report": {},
	"stock_aging_report": {},
	"list_stock_counts": {},
}


class ScopeCase(unittest.TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.db.sql.return_value = []
		for p in (
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "get_all", return_value=[], create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)


class TestStockReports(ScopeCase):
	def test_another_business_is_refused_before_anything_is_read(self):
		for name, args in STOCK_CALLS.items():
			with self.subTest(call=name):
				with (
					patch.object(stock, "resolve_company", side_effect=frappe.PermissionError),
					self.assertRaises(frappe.PermissionError),
				):
					getattr(stock, name)(company="Shop B", **args)
		self.db.sql.assert_not_called()
		frappe.get_all.assert_not_called()

	def test_each_report_reads_only_the_callers_business(self):
		for name, args in STOCK_CALLS.items():
			if name == "list_stock_counts":
				continue
			with self.subTest(call=name):
				self.db.sql.reset_mock()
				with patch.object(stock, "resolve_company", return_value="Shop A") as resolve:
					getattr(stock, name)(**args)
				resolve.assert_called_once_with(None)
				query, values = self.db.sql.call_args.args[:2]
				self.assertIn("company = %(company)s", query)
				self.assertEqual(values["company"], "Shop A")

	def test_stock_counts_list_only_the_callers_business(self):
		with patch.object(stock, "resolve_company", return_value="Shop A"):
			stock.list_stock_counts()
		self.assertEqual(frappe.get_all.call_args.kwargs["filters"], {"company": "Shop A"})


class TestCompanyDetails(ScopeCase):
	def test_another_business_is_refused(self):
		with (
			patch.object(onboarding_api, "resolve_company", side_effect=frappe.PermissionError),
			patch.object(frappe, "get_doc", create=True) as get_doc,
			self.assertRaises(frappe.PermissionError),
		):
			onboarding_api.get_company(company_name="Shop B")
		get_doc.assert_not_called()


if __name__ == "__main__":
	unittest.main()
