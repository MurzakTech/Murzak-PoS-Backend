# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Cancelling a finished sale is for a manager. Needs no database."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_api


class TestCancellingASale(unittest.TestCase):
	def given(self, roles):
		for p in (
			patch.object(frappe, "session", frappe._dict(user="someone@shop.test"), create=True),
			patch.object(frappe, "get_roles", return_value=list(roles), create=True),
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(sales_api, "ensure_record_access"),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_a_cashier_is_refused_before_the_sale_is_read(self):
		self.given(["Sales User"])
		with patch.object(frappe, "get_doc", create=True) as get_doc:
			for call in (sales_api.cancel_sales_invoice, sales_api.cancel_pos_invoice):
				with self.subTest(call=call.__name__), self.assertRaises(frappe.PermissionError):
					call(name="SALE-1", reason="customer left")
		get_doc.assert_not_called()

	def test_a_manager_gets_past_the_role_check(self):
		self.given(["Sales Manager"])
		frappe.db.exists.return_value = False
		for call in (sales_api.cancel_sales_invoice, sales_api.cancel_pos_invoice):
			with self.subTest(call=call.__name__):
				self.assertFalse(call(name="SALE-1")["success"])  # refused only because there is no such sale


if __name__ == "__main__":
	unittest.main()
