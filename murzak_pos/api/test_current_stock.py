# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Unit tests for clean_code_list in stock_levels_calc: the list of products the Bulk stock update screen asks about.
"""

from __future__ import annotations

import unittest

from murzak_pos.api import stock_levels_calc as calc


class TestCleanCodeList(unittest.TestCase):
	def test_a_list_is_trimmed_and_without_repeats(self):
		self.assertEqual(calc.clean_code_list([" A ", "B", "A", "", None]), ["A", "B"])

	def test_the_json_text_of_a_list_is_read(self):
		self.assertEqual(calc.clean_code_list('["A", "B"]'), ["A", "B"])

	def test_something_that_is_not_a_list_is_refused(self):
		for bad in ("not json", '{"a": 1}', 5, None):
			with self.assertRaises(calc.StockError) as caught:
				calc.clean_code_list(bad)
			self.assertEqual(caught.exception.code, "bad_codes")

	def test_too_many_products_are_refused(self):
		with self.assertRaises(calc.StockError) as caught:
			calc.clean_code_list([str(n) for n in range(1001)])
		self.assertEqual(caught.exception.code, "too_many")

	def test_an_empty_list_is_fine(self):
		self.assertEqual(calc.clean_code_list([]), [])


if __name__ == "__main__":
	unittest.main()
