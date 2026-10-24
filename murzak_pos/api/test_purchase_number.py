# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""A short purchase number finds only that document, and only in the caller's business. Needs no database."""

import re
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import purchases_list_api as api
from murzak_pos.api import purchases_list_calc as rules


class NumberCase(unittest.TestCase):
	def given(self, companies=("Shop A",), named=None, found=None):
		db = MagicMock()
		db.exists.return_value = False  # not a full document name
		db.sql.return_value = [{"name": found}] if found else []
		for p in (
			patch.object(frappe, "db", db, create=True),
			patch.object(api, "get_user_companies", return_value=set(companies)),
			patch.object(
				api, "resolve_company", side_effect=lambda c=None: named or next(iter(companies), None)
			),
		):
			p.start()
			self.addCleanup(p.stop)
		self.db = db


class TestShortNumbers(NumberCase):
	def test_only_the_callers_business_is_searched_with_the_number_at_the_end(self):
		self.given(found="PUR-ORD-2026-00012")
		self.assertEqual(api._resolve_purchase_id("O12"), ("Purchase Order", "PUR-ORD-2026-00012"))
		for call in self.db.sql.call_args_list:
			query, values = call.args[:2]
			self.assertIn("company IN %(companies)s", query)
			self.assertNotIn("LIKE", query)
			self.assertEqual(values["companies"], ("Shop A",))

	def test_nothing_is_searched_for_someone_who_belongs_to_no_business(self):
		self.given(companies=())
		self.assertEqual(api._resolve_purchase_id("12"), (None, "12"))
		self.db.sql.assert_not_called()

	def test_a_named_company_narrows_the_search_to_it(self):
		self.given(companies=("Shop A", "Shop B"), named="Shop B")
		api._resolve_purchase_id("B7", company="Shop B")
		self.assertEqual(self.db.sql.call_args.args[1]["companies"], ("Shop B",))

	def test_twelve_is_not_one_twenty_or_one_two_three_four(self):
		pattern = re.compile(rules.number_regex(12))
		self.assertTrue(pattern.search("PUR-ORD-2026-00012"))
		self.assertTrue(pattern.search("PUR-ORD-2026-00012-1"))
		for other in ("PUR-ORD-2026-00120", "PUR-ORD-2026-01234", "PUR-ORD-2026-00112"):
			self.assertFalse(pattern.search(other), other)


if __name__ == "__main__":
	unittest.main()
