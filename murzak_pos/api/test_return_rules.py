# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for how much of a sale can still be returned. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import return_rules, sales_api


class OveragesCase(unittest.TestCase):
	def test_returning_part_of_a_sale_is_fine(self):
		self.assertEqual(return_rules.overages({"A": 1}, {"A": 3}, {}), [])

	def test_returning_all_of_it_is_fine(self):
		self.assertEqual(return_rules.overages({"A": 3}, {"A": 3}, {}), [])

	def test_returning_more_than_was_sold_is_refused(self):
		found = return_rules.overages({"A": 5}, {"A": 3}, {})
		self.assertEqual([(o["item_code"], o["left"]) for o in found], [("A", 3)])

	def test_earlier_returns_use_up_what_can_come_back(self):
		self.assertEqual(return_rules.overages({"A": 2}, {"A": 3}, {"A": 2})[0]["left"], 1)

	def test_a_sale_already_fully_returned_has_nothing_left(self):
		self.assertEqual(return_rules.overages({"A": 1}, {"A": 3}, {"A": 3})[0]["left"], 0)

	def test_an_item_that_was_not_on_the_sale_is_refused(self):
		self.assertEqual(return_rules.overages({"B": 1}, {"A": 3}, {})[0]["left"], 0)

	def test_decimals_that_add_up_are_not_an_overage(self):
		self.assertEqual(return_rules.overages({"A": 0.3}, {"A": 0.1 + 0.2}, {}), [])

	def test_only_the_items_over_the_limit_are_listed(self):
		found = return_rules.overages({"A": 1, "B": 9}, {"A": 3, "B": 2}, {})
		self.assertEqual([o["item_code"] for o in found], ["B"])


class RefuseOverReturnCase(unittest.TestCase):
	def refuse(self, asked, sold, returned):
		with patch.object(sales_api, "_returnable_totals", return_value=(asked, sold, returned)):
			sales_api._refuse_over_return("Sales Invoice", MagicMock(), [])

	def test_a_return_within_the_limit_passes(self):
		self.refuse({"A": 1}, {"A": 3}, {"A": 1})

	def test_a_second_return_that_goes_past_the_sale_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.refuse({"A": 5}, {"A": 3}, {"A": 1})
