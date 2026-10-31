# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Unit tests for _sell_in_company_currency in sales_api: a till sale is in the shop's own currency.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_api


def _doc():
	doc = frappe._dict()
	doc.meta = MagicMock()
	doc.meta.has_field.return_value = True
	return doc


class TestSellInCompanyCurrency(unittest.TestCase):
	def _run(self, doc, currencies, profile_list=None, lists=()):
		def get_value(doctype, name, field=None, *args, **kwargs):
			if doctype == "Company":
				return "KES"
			if doctype == "POS Profile":
				return profile_list
			if doctype == "Price List":
				return currencies.get(name)

		with (
			patch.object(frappe.db, "get_value", side_effect=get_value),
			patch.object(frappe, "get_all", return_value=list(lists)),
		):
			sales_api._sell_in_company_currency(
				doc, "Test Co", "Profile" if profile_list is not None else None
			)

	def test_sale_is_pinned_to_company_currency_at_rate_one(self):
		doc = _doc()
		self._run(doc, {"Standard Selling": "INR"}, profile_list="Standard Selling")
		self.assertEqual(doc.currency, "KES")
		self.assertEqual(doc.conversion_rate, 1)
		self.assertEqual(doc.plc_conversion_rate, 1)

	def test_a_profile_list_in_another_currency_is_not_used(self):
		doc = _doc()
		self._run(
			doc,
			{"Standard Selling": "INR", "Selling KES": "KES"},
			profile_list="Standard Selling",
			lists=["Selling KES"],
		)
		self.assertEqual(doc.selling_price_list, "Selling KES")
		self.assertEqual(doc.price_list_currency, "KES")

	def test_a_profile_list_in_company_currency_is_kept(self):
		doc = _doc()
		self._run(doc, {"Mine": "KES", "Other": "KES"}, profile_list="Mine", lists=["Other"])
		self.assertEqual(doc.selling_price_list, "Mine")

	def test_no_list_in_company_currency_leaves_the_list_to_erpnext(self):
		doc = _doc()
		self._run(doc, {"Standard Selling": "INR"}, profile_list="Standard Selling")
		self.assertNotIn("selling_price_list", doc)
		self.assertEqual(doc.currency, "KES")


if __name__ == "__main__":
	unittest.main()
