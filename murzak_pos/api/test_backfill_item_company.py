# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the patch that gives untagged products an owning business, so products holding stock show at the till. No
database is needed: every Frappe call is mocked.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.patches import backfill_item_company as backfill


class TestDecidingTheBusiness(unittest.TestCase):
	def test_stock_in_the_stores_of_one_business_settles_it(self):
		self.assertEqual(backfill.decide_company({"Shop A"}, None, None), "Shop A")
		# the evidence beats a guess about who made it
		self.assertEqual(backfill.decide_company({"Shop A"}, "Shop B", "Shop C"), "Shop A")

	def test_stock_in_several_businesses_is_left_for_a_person_to_settle(self):
		self.assertIsNone(backfill.decide_company({"Shop A", "Shop B"}, "Shop A", "Shop A"))

	def test_without_stock_the_business_of_the_maker_decides(self):
		self.assertEqual(backfill.decide_company(set(), "Shop B", None), "Shop B")

	def test_without_stock_or_a_maker_the_only_business_of_the_site_decides(self):
		self.assertEqual(backfill.decide_company(set(), None, "Shop C"), "Shop C")
		self.assertIsNone(backfill.decide_company(set(), None, None))


class TestTheMaker(unittest.TestCase):
	def test_the_administrator_and_nobody_have_no_business(self):
		self.assertIsNone(backfill.creator_company("Administrator"))
		self.assertIsNone(backfill.creator_company(None))

	def test_a_person_has_the_business_they_are_bound_to(self):
		with patch.object(frappe, "db", MagicMock(), create=True) as db:
			db.get_value.return_value = "Shop A"
			self.assertEqual(backfill.creator_company("amina@shop-a.test"), "Shop A")
			db.get_value.assert_called_once_with("User", "amina@shop-a.test", "custom_company")
			db.get_value.return_value = ""
			self.assertIsNone(backfill.creator_company("nobody@x.test"))


class TestRunningThePatch(unittest.TestCase):
	def run_patch(self, untagged, bins, companies, creators=None):
		creators = creators or {}
		with (
			patch.object(frappe, "db", MagicMock(), create=True) as db,
			patch.object(frappe, "get_all", create=True) as get_all,
			patch.object(backfill, "creator_company", side_effect=lambda owner: creators.get(owner)),
			patch("builtins.print"),
		):
			db.has_column.return_value = True
			db.sql.return_value = [frappe._dict(item_code=i, company=c) for i, c in bins]
			get_all.side_effect = lambda doctype, **kw: (
				[frappe._dict(name=n, owner=o) for n, o in untagged] if doctype == "Item" else list(companies)
			)
			backfill.execute()
			return {call.args[1]: call.args[3] for call in db.set_value.call_args_list}, db

	def test_each_product_is_tagged_from_its_own_evidence(self):
		tagged, db = self.run_patch(
			untagged=[("MLK", "x@a"), ("UNG", "x@a"), ("SUK", "adm"), ("SBN", "m@b")],
			bins=[("MLK", "Shop A"), ("UNG", "Shop A"), ("UNG", "Shop B")],
			companies=["Shop A", "Shop B"],
			creators={"m@b": "Shop B"},
		)
		# MLK: stock in Shop A. UNG: stock in two businesses, left alone. SUK: no stock, no maker, two businesses: left alone. SBN: its maker's business
		self.assertEqual(tagged, {"MLK": "Shop A", "SBN": "Shop B"})
		for call in db.set_value.call_args_list:
			self.assertEqual(call.args[0], "Item")
			self.assertEqual(call.args[2], "custom_company")
			self.assertEqual(call.kwargs, {"update_modified": False})

	def test_on_a_one_business_site_everything_left_belongs_to_it(self):
		tagged, _ = self.run_patch(untagged=[("MLK", "adm")], bins=[], companies=["Shop A"])
		self.assertEqual(tagged, {"MLK": "Shop A"})

	def test_nothing_untagged_means_nothing_is_read_or_written(self):
		tagged, db = self.run_patch(untagged=[], bins=[], companies=["Shop A"])
		self.assertEqual(tagged, {})
		db.sql.assert_not_called()

	def test_a_site_without_the_field_is_left_alone(self):
		with (
			patch.object(frappe, "db", MagicMock(), create=True) as db,
			patch.object(frappe, "get_all", create=True) as get_all,
		):
			db.has_column.return_value = False
			backfill.execute()
			get_all.assert_not_called()


class TestTheDeploymentList(unittest.TestCase):
	def test_the_patch_is_listed_to_run_after_the_models_are_synced(self):
		path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "patches.txt")
		with open(path) as handle:
			lines = [line.strip() for line in handle if line.strip()]
		name = "murzak_pos.patches.backfill_item_company"
		self.assertIn(name, lines)
		self.assertGreater(lines.index(name), lines.index("[post_model_sync]"))


if __name__ == "__main__":
	unittest.main()
