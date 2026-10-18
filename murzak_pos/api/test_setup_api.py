# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the first-time set-up calls (`setup_api`). They need no database: every Frappe call is mocked.

What they pin down: who may read and who may write; that nothing is written for a refused person or a bad answer;
that a business keeps its own answers; that the wizard is offered to a new store and never to a shop with data;
and that the calls that write check for an owner before they touch the database.
"""

import ast
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import setup_api

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager")
CASHIER_ROLES = ("Sales User",)
MANAGER_ROLES = ("Sales Manager", "Sales User")


class SetupCase(unittest.TestCase):
	def as_user(
		self,
		user="owner@shop-a.test",
		roles=OWNER_ROLES,
		companies=("Shop A",),
		stored=None,
		sales=(),
		products=0,
		signup_industry=None,
	):
		"""
		Run as `user` holding `roles`, who belongs to `companies`. `stored` maps a default key to its value;
		`sales` lists the document types that have a submitted sale; `products` is how many products exist.
		"""
		self.stored = dict(stored or {})
		db = MagicMock()
		db.exists.side_effect = lambda doctype, filters=None, *a, **k: (
			True if doctype in ("Company",) else doctype in sales
		)
		db.get_default.side_effect = lambda key, *a, **k: self.stored.get(key)
		db.set_default.side_effect = lambda key, value, *a, **k: self.stored.__setitem__(key, value)

		def get_value(doctype, name, field=None, *a, **k):
			if doctype == "User":
				return signup_industry
			if doctype == "POS Industry":
				return name
			return None

		db.get_value.side_effect = get_value
		self.db = db
		self.get_all = MagicMock(
			side_effect=lambda doctype, **kw: ["ITEM"] * min(products, kw.get("limit", 99))
		)
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_all", self.get_all, create=True),
			patch.object(frappe, "log_error", MagicMock(), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)

	def kind_key(self, company="Shop A"):
		return setup_api._key(setup_api.KIND_PREFIX, company)

	def progress_key(self, company="Shop A"):
		return setup_api._key(setup_api.PROGRESS_PREFIX, company)

	def cash_key(self, company="Shop A"):
		return setup_api._key(setup_api.CASH_PREFIX, company)


class TestFeatures(SetupCase):
	def test_a_business_that_never_answered_has_no_kind_and_nothing_turned_on(self):
		self.as_user()
		data = setup_api.get_business_features()["data"]
		self.assertIsNone(data["kind"])
		self.assertFalse(any(data["features"].values()))
		self.assertEqual(data["opening_cash"], 0.0)

	def test_a_pharmacy_has_expiry_on(self):
		self.as_user(stored={self.kind_key(): "pharmacy"})
		data = setup_api.get_business_features()["data"]
		self.assertEqual(data["kind"], "pharmacy")
		self.assertTrue(data["features"]["expiry"])
		self.assertTrue(data["features"]["prescription_only"])
		self.assertFalse(data["features"]["barcodes_first"])

	def test_a_cashier_may_read_their_own_businesss_features_and_usual_opening_cash(self):
		self.as_user(
			"till@shop-a.test",
			roles=CASHIER_ROLES,
			stored={self.kind_key(): "retail", self.cash_key(): "5000.0"},
		)
		data = setup_api.get_business_features()["data"]
		self.assertTrue(data["features"]["barcodes_first"])
		self.assertEqual(data["opening_cash"], 5000.0)

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			setup_api.get_business_features()

	def test_another_businesss_answers_cannot_be_read(self):
		self.as_user(companies=("Shop A",), stored={self.kind_key("Shop B"): "pharmacy"})
		with self.assertRaises(frappe.PermissionError):
			setup_api.get_business_features(company="Shop B")

	def test_a_stored_value_that_is_not_a_kind_is_read_as_no_kind(self):
		self.as_user(stored={self.kind_key(): "bakery"})
		self.assertIsNone(setup_api.get_business_features()["data"]["kind"])


class TestState(SetupCase):
	def test_a_new_store_is_offered_the_wizard(self):
		self.as_user()
		data = setup_api.get_setup_state()["data"]
		self.assertTrue(data["needs_setup"])
		self.assertTrue(data["offer"])
		self.assertTrue(data["can_edit"])
		self.assertIsNone(data["industry_code"])
		self.assertEqual(data["next_step"], "business")
		self.assertEqual(data["done_steps"], [])
		self.assertFalse(data["finished"])

	def test_a_shop_with_a_submitted_sale_is_never_offered_it(self):
		for doctype in ("Sales Invoice", "POS Invoice"):
			with self.subTest(doctype=doctype):
				self.as_user(sales=(doctype,))
				data = setup_api.get_setup_state()["data"]
				self.assertFalse(data["needs_setup"])
				self.assertFalse(data["offer"])

	def test_a_shop_with_five_products_is_never_offered_it(self):
		self.as_user(products=5)
		self.assertFalse(setup_api.get_setup_state()["data"]["offer"])

	def test_a_shop_with_four_products_and_no_sales_is_still_new(self):
		self.as_user(products=4)
		self.assertTrue(setup_api.get_setup_state()["data"]["needs_setup"])

	def test_products_are_counted_for_this_business_only_and_never_past_the_limit(self):
		self.as_user(products=40)
		setup_api.get_setup_state()
		args = self.get_all.call_args
		self.assertEqual(args.args[0], "Item")
		self.assertEqual(args.kwargs["filters"], {"custom_company": "Shop A", "disabled": 0})
		self.assertEqual(args.kwargs["limit"], 5)

	def test_a_recorded_kind_means_it_is_no_longer_new_but_a_started_wizard_can_be_resumed(self):
		self.as_user(
			stored={
				self.kind_key(): "retail",
				self.progress_key(): '{"done":["business"],"skipped":[],"finished":false}',
			}
		)
		data = setup_api.get_setup_state()["data"]
		self.assertFalse(data["needs_setup"])
		self.assertTrue(data["in_progress"])
		self.assertTrue(data["offer"])
		self.assertEqual(data["next_step"], "shop")
		self.assertEqual(data["kind"], "retail")

	def test_a_finished_wizard_is_never_offered_and_costs_no_counting(self):
		self.as_user(stored={self.progress_key(): '{"done":[],"skipped":[],"finished":true}'})
		data = setup_api.get_setup_state()["data"]
		self.assertFalse(data["offer"])
		self.assertTrue(data["finished"])
		self.get_all.assert_not_called()

	def test_a_kind_chosen_at_sign_up_counts_as_a_recorded_kind(self):
		self.as_user(signup_industry="PHARMACY")
		data = setup_api.get_setup_state()["data"]
		self.assertFalse(data["needs_setup"])
		self.assertEqual(data["suggested_kind"], "pharmacy")
		self.assertEqual(data["industry_code"], "PHARMACY")
		# it is only a suggestion: no feature is turned on by it
		self.assertFalse(any(data["features"].values()))

	def test_a_count_that_fails_never_offers_the_wizard_to_a_real_shop(self):
		self.as_user()
		self.get_all.side_effect = RuntimeError("database down")
		data = setup_api.get_setup_state()["data"]
		self.assertFalse(data["offer"])

	def test_a_cashier_and_a_manager_can_read_it_but_cannot_edit(self):
		for roles in (CASHIER_ROLES, MANAGER_ROLES):
			with self.subTest(roles=roles):
				self.as_user("someone@shop-a.test", roles=roles)
				self.assertFalse(setup_api.get_setup_state()["data"]["can_edit"])

	def test_one_business_never_sees_anothers_progress(self):
		self.as_user(
			companies=("Shop A", "Shop B"),
			stored={
				self.kind_key("Shop B"): "pharmacy",
				self.progress_key("Shop B"): '{"done":["business"],"skipped":[],"finished":true}',
			},
		)
		data = setup_api.get_setup_state(company="Shop A")["data"]
		self.assertIsNone(data["kind"])
		self.assertTrue(data["needs_setup"])


class TestWriting(SetupCase):
	def test_an_owner_saves_what_they_sell_and_the_matching_features_turn_on(self):
		self.as_user()
		data = setup_api.save_business_kind("Pharmacy")["data"]
		self.assertEqual(data["kind"], "pharmacy")
		self.assertTrue(data["features"]["expiry"])
		self.assertEqual(data["done_steps"], ["business"])
		self.assertEqual(data["next_step"], "shop")
		self.assertEqual(setup_api.get_business_features()["data"]["kind"], "pharmacy")

	def test_a_bad_answer_is_refused_and_nothing_is_written(self):
		self.as_user()
		for sent in ("bakery", "", None):
			with self.subTest(sent=sent), self.assertRaises(frappe.ValidationError):
				setup_api.save_business_kind(sent)
		self.db.set_default.assert_not_called()

	def test_progress_is_saved_step_by_step_and_skips_are_kept_apart(self):
		self.as_user()
		setup_api.save_setup_progress("shop")
		data = setup_api.save_setup_progress("cash", skipped=1)["data"]
		self.assertEqual(data["done_steps"], ["shop"])
		self.assertEqual(data["skipped_steps"], ["cash"])
		self.assertEqual(data["next_step"], "business")
		self.assertFalse(data["finished"])

	def test_finish_and_skip_for_now_both_mark_it_done(self):
		self.as_user()
		data = setup_api.save_setup_progress("finish")["data"]
		self.assertTrue(data["finished"])
		self.assertFalse(setup_api.get_setup_state()["data"]["offer"])

	def test_an_unknown_step_is_refused_and_nothing_is_written(self):
		self.as_user()
		for sent in ("tax", "", None):
			with self.subTest(sent=sent), self.assertRaises(frappe.ValidationError):
				setup_api.save_setup_progress(sent)
		self.db.set_default.assert_not_called()

	def test_the_usual_opening_cash_is_kept_as_a_suggestion_and_marks_the_step(self):
		self.as_user()
		data = setup_api.save_opening_cash("5,000")["data"]
		self.assertEqual(data["opening_cash"], 5000.0)
		self.assertEqual(data["done_steps"], ["cash"])
		self.assertEqual(setup_api.get_business_features()["data"]["opening_cash"], 5000.0)

	def test_an_empty_opening_cash_clears_the_suggestion(self):
		self.as_user(stored={self.cash_key(): "5000.0"})
		self.assertEqual(setup_api.save_opening_cash("")["data"]["opening_cash"], 0.0)
		self.assertEqual(setup_api.get_business_features()["data"]["opening_cash"], 0.0)

	def test_a_bad_opening_cash_is_refused_and_nothing_is_written(self):
		self.as_user()
		for sent in ("abc", -5, "nan"):
			with self.subTest(sent=sent), self.assertRaises(frappe.ValidationError):
				setup_api.save_opening_cash(sent)
		self.db.set_default.assert_not_called()

	def test_a_cashier_cannot_write_anything(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		for call in (
			lambda: setup_api.save_business_kind("retail"),
			lambda: setup_api.save_setup_progress("shop"),
			lambda: setup_api.save_opening_cash(100),
		):
			with self.assertRaises(frappe.PermissionError):
				call()
		self.db.set_default.assert_not_called()

	def test_a_manager_who_is_not_the_owner_cannot_write(self):
		self.as_user("sales@shop-a.test", roles=MANAGER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			setup_api.save_business_kind("retail")
		self.db.set_default.assert_not_called()

	def test_a_guest_cannot_write(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			setup_api.save_business_kind("retail")
		self.db.set_default.assert_not_called()

	def test_an_owner_cannot_write_for_another_business(self):
		self.as_user(companies=("Shop A",))
		with self.assertRaises(frappe.PermissionError):
			setup_api.save_business_kind("retail", company="Shop B")
		self.db.set_default.assert_not_called()

	def test_one_business_never_changes_another(self):
		self.as_user(companies=("Shop A", "Shop B"))
		setup_api.save_business_kind("pharmacy", company="Shop A")
		self.assertEqual(setup_api.get_business_features(company="Shop A")["data"]["kind"], "pharmacy")
		self.assertIsNone(setup_api.get_business_features(company="Shop B")["data"]["kind"])

	def test_the_administrator_may_write_for_any_business(self):
		self.as_user("Administrator", roles=(), companies=())
		self.assertEqual(setup_api.save_business_kind("other", company="Shop B")["data"]["kind"], "other")

	def test_saving_touches_only_this_wizards_own_keys(self):
		self.as_user()
		setup_api.save_business_kind("retail")
		setup_api.save_opening_cash(100)
		setup_api.save_setup_progress("finish")
		for call in self.db.set_default.call_args_list:
			self.assertTrue(call.args[0].startswith("murzak_setup_"), call.args[0])


class TestKey(unittest.TestCase):
	def test_each_business_and_each_answer_has_its_own_key(self):
		keys = {
			setup_api._key(prefix, company)
			for prefix in (setup_api.KIND_PREFIX, setup_api.PROGRESS_PREFIX, setup_api.CASH_PREFIX)
			for company in ("Shop A", "Shop B")
		}
		self.assertEqual(len(keys), 6)

	def test_a_very_long_company_name_still_fits_and_stays_unique(self):
		a = setup_api._key(setup_api.PROGRESS_PREFIX, "A" * 200)
		b = setup_api._key(setup_api.PROGRESS_PREFIX, "A" * 199 + "B")
		self.assertLessEqual(len(a), setup_api.MAX_KEY_LENGTH)
		self.assertNotEqual(a, b)
		self.assertEqual(a, setup_api._key(setup_api.PROGRESS_PREFIX, "A" * 200))


class TestGuardIsNotForgotten(unittest.TestCase):
	"""Reads the source, so a later edit that moves the owner check after a database call fails here."""

	def function(self, name):
		path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup_api.py")
		with open(path) as handle:
			source = handle.read()
		node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
		return node, ast.get_source_segment(source, node)

	def test_every_call_that_writes_checks_for_an_owner_first_and_is_a_post(self):
		for name in ("save_business_kind", "save_setup_progress", "save_opening_cash"):
			with self.subTest(name=name):
				node, body = self.function(name)
				call = body.find("require_manager()")
				touching = [
					body.find(word) for word in ("frappe.db", "resolve_company(", "_read_", "_write_")
				]
				self.assertNotEqual(call, -1, f"{name} must call require_manager()")
				self.assertTrue(all(call < position for position in touching if position != -1))
				self.assertIn('methods=["POST"]', ast.unparse(node.decorator_list[0]).replace("'", '"'))

	def test_the_reading_calls_never_write(self):
		for name in ("get_business_features", "get_setup_state"):
			with self.subTest(name=name):
				_node, body = self.function(name)
				self.assertNotIn("set_default", body)
				self.assertNotIn("_write_", body)
