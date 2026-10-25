# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for opening a till on a brand-new shop, which has no POS Profile yet, that need no database: the profile
that exists is used as sent, a missing one is created for the company, and the open-entry lookup runs on the
profile that was resolved. Every Frappe call is replaced.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_api


class TestProfileToOpenOn(unittest.TestCase):
	def resolve(self, sent, exists):
		db = MagicMock()
		db.exists.return_value = exists
		with (
			patch.object(frappe, "db", db),
			patch.object(sales_api, "_get_or_create_pos_profile", return_value="Shop A POS Profile") as make,
		):
			return sales_api._profile_to_open_on(sent, "Shop A"), make

	def test_a_profile_that_exists_is_used_as_sent(self):
		name, make = self.resolve("Main till", True)
		self.assertEqual(name, "Main till")
		make.assert_not_called()

	def test_a_missing_profile_is_created_for_the_company(self):
		name, make = self.resolve("Shop A POS Profile", False)
		self.assertEqual(name, "Shop A POS Profile")
		make.assert_called_once_with("Shop A")

	def test_no_profile_sent_is_created_for_the_company(self):
		name, make = self.resolve(None, False)
		self.assertEqual(name, "Shop A POS Profile")
		make.assert_called_once_with("Shop A")


class TestOpeningEntryOnANewShop(unittest.TestCase):
	def test_the_open_entry_lookup_uses_the_resolved_profile(self):
		# Today's entry is already open for the resolved profile, so nothing else is needed. The date helpers
		# are replaced: the real ones read the site's time zone, and this test has no site.
		entry = frappe._dict(name="POS-OPE-0001", period_start_date="2026-10-25", user="owner@shop-a.test")
		get_all = MagicMock(return_value=[entry])
		with (
			patch.object(frappe, "get_all", get_all),
			patch.object(frappe, "session", frappe._dict(user="owner@shop-a.test")),
			patch("frappe.utils.today", return_value="2026-10-25"),
			patch("frappe.utils.get_date_str", side_effect=str),
			patch.object(sales_api, "_profile_to_open_on", return_value="Shop A POS Profile") as resolve,
		):
			name = sales_api._get_or_create_pos_opening_entry("Missing profile", "Shop A")
		resolve.assert_called_once_with("Missing profile", "Shop A")
		self.assertEqual(name, "POS-OPE-0001")
		self.assertEqual(get_all.call_args.kwargs["filters"]["pos_profile"], "Shop A POS Profile")


if __name__ == "__main__":
	unittest.main()
