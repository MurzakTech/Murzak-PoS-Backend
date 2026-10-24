# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""An M-Pesa code a cashier types must look real and be used once. Needs no database."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import payment_gateway_api as api


class ManualCodeCase(unittest.TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.db.sql.return_value = []
		self.db.has_column.return_value = True
		self.db.get_value.return_value = None  # no M-Pesa record for the code
		for p in (
			patch.object(frappe, "db", self.db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(
				api, "_gateway_modes", return_value={"M-Pesa": {"gateway": "mpesa", "allow_manual_code": 1}}
			),
		):
			p.start()
			self.addCleanup(p.stop)

	def sale(self, *codes):
		rows = [{"mode_of_payment": "M-Pesa", "amount": 100, "reference_no": code} for code in codes]
		return api.validate_gateway_payments(rows, "Shop A"), rows


class TestTypedCodes(ManualCodeCase):
	def test_a_real_looking_unused_code_is_accepted(self):
		claims, rows = self.sale("qk12ab34cd")
		self.assertEqual((claims, rows[0]["reference_no"]), ([], "QK12AB34CD"))
		self.assertEqual(self.db.sql.call_args.args[1], {"code": "QK12AB34CD", "company": "Shop A"})

	def test_a_code_that_is_not_shaped_like_one_is_refused(self):
		for code in ("12345", "QK12AB34CD9", "QK12-B34CD", "hello world"):
			with self.subTest(code=code), self.assertRaises(frappe.ValidationError):
				self.sale(code)

	def test_a_code_already_on_another_sale_is_refused(self):
		self.db.sql.return_value = [("ACC-SINV-2026-00042",)]
		with self.assertRaises(frappe.ValidationError) as refused:
			self.sale("QK12AB34CD")
		self.assertIn("ACC-SINV-2026-00042", str(refused.exception))

	def test_the_same_code_twice_on_one_sale_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.sale("QK12AB34CD", "QK12AB34CD")


if __name__ == "__main__":
	unittest.main()
