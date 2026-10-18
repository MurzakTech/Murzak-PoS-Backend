# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the payment method calls. They need no database: every Frappe call is mocked.

What they pin down: any signed-in user of a business may read the switches; only an owner may change one; a
cashier or a manager is refused before anything is read or written; the choices of one business never reach
another; and a change that is refused writes nothing.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import payment_methods_api as api
from murzak_pos.api import payment_methods_calc as calc

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User")
MANAGER_ROLES = ("Sales Manager", "Sales User")
CASHIER_ROLES = ("Sales User",)
SHOP_MODES = {"Shop A": ["Cash", "Mpesa", "Credit Card", "Credit", "Cheque"], "Shop B": ["Cash", "Mpesa"]}


class PaymentMethodsCase(unittest.TestCase):
	def as_user(
		self, user="owner@shop-a.test", roles=OWNER_ROLES, companies=("Shop A",), stored=None, modes=None
	):
		"""Run as `user` holding `roles`, who belongs to `companies`. `stored` maps a default key to its text."""
		self.stored = dict(stored or {})
		self.modes = dict(modes or SHOP_MODES)
		db = MagicMock()
		db.exists.return_value = True
		db.get_default.side_effect = lambda key, *a, **k: self.stored.get(key)
		db.set_default.side_effect = lambda key, value, *a, **k: self.stored.__setitem__(key, value)
		self.db = db
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)),
			patch.object(
				api, "modes_of_payment_for", side_effect=lambda company: self.modes.get(company, [])
			),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)

	def methods(self, reply):
		return {m["key"]: m for m in reply["data"]["methods"]}


class TestReading(PaymentMethodsCase):
	def test_a_business_that_never_chose_has_every_available_method_on(self):
		self.as_user()
		reply = api.get_payment_methods()
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"]["company"], "Shop A")
		self.assertEqual([m["key"] for m in reply["data"]["methods"]], ["cash", "mpesa", "card", "credit"])
		self.assertTrue(all(m["enabled"] and m["available"] for m in reply["data"]["methods"]))
		self.assertEqual(
			reply["data"]["other"],
			[{"mode": "Cheque", "label": "Cheque", "enabled": True, "available": True}],
		)

	def test_a_cashier_may_read_the_switches_of_their_own_business(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		self.assertTrue(api.get_payment_methods()["success"])

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			api.get_payment_methods()
		with self.assertRaises(frappe.AuthenticationError):
			api.get_till_payment_modes()

	def test_another_businesss_switches_cannot_be_read(self):
		self.as_user(companies=("Shop A",))
		with self.assertRaises(frappe.PermissionError):
			api.get_payment_methods(company="Shop B")
		with self.assertRaises(frappe.PermissionError):
			api.get_till_payment_modes(company="Shop B")

	def test_reading_writes_nothing(self):
		self.as_user()
		api.get_payment_methods()
		api.get_till_payment_modes()
		self.db.set_default.assert_not_called()

	def test_a_method_the_shop_lacks_is_listed_but_unavailable(self):
		self.as_user(companies=("Shop B",))
		methods = self.methods(api.get_payment_methods())
		self.assertFalse(methods["card"]["available"])
		self.assertFalse(methods["card"]["enabled"])
		self.assertTrue(methods["mpesa"]["needs_code"])


class TestTillModes(PaymentMethodsCase):
	def test_default_list_is_ordered_with_cash_and_mpesa_first(self):
		self.as_user()
		reply = api.get_till_payment_modes()
		self.assertEqual(reply["data"]["modes"], ["Cash", "Mpesa", "Credit Card", "Credit", "Cheque"])
		self.assertEqual(reply["data"]["company"], "Shop A")

	def test_switching_mpesa_off_removes_it_after_a_reload(self):
		self.as_user()
		api.set_payment_method("mpesa", False)
		# A fresh call, as a reload of the till would make
		self.assertEqual(
			api.get_till_payment_modes()["data"]["modes"], ["Cash", "Credit Card", "Credit", "Cheque"]
		)
		api.set_payment_method("mpesa", True)
		self.assertIn("Mpesa", api.get_till_payment_modes()["data"]["modes"])

	def test_a_cashier_gets_the_same_list(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES, stored={api._key("Shop A"): '{"off":["card"]}'})
		self.assertEqual(api.get_till_payment_modes()["data"]["modes"], ["Cash", "Mpesa", "Credit", "Cheque"])


class TestChanging(PaymentMethodsCase):
	def test_an_owner_switches_a_method_off_and_the_answer_shows_it(self):
		self.as_user()
		reply = api.set_payment_method("mpesa", False)
		self.assertFalse(self.methods(reply)["mpesa"]["enabled"])
		self.assertTrue(self.methods(reply)["mpesa"]["available"])
		self.assertEqual(api.get_payment_methods(), reply)

	def test_the_same_shape_comes_back_from_get_and_set(self):
		self.as_user()
		self.assertEqual(
			set(api.set_payment_method("cash", True)["data"]), set(api.get_payment_methods()["data"])
		)

	def test_the_saved_text_lists_only_what_is_off(self):
		self.as_user()
		api.set_payment_method("mpesa", False)
		self.assertEqual(self.stored[api._key("Shop A")], '{"off":["mpesa"],"off_other":[]}')

	def test_switching_back_on_works(self):
		self.as_user()
		api.set_payment_method("card", False)
		reply = api.set_payment_method("card", True)
		self.assertTrue(self.methods(reply)["card"]["enabled"])

	def test_asking_twice_is_the_same_as_asking_once(self):
		self.as_user()
		first = api.set_payment_method("mpesa", False)
		writes = self.db.set_default.call_count
		second = api.set_payment_method("mpesa", False)
		self.assertEqual(first, second)
		self.assertEqual(self.db.set_default.call_count, writes)

	def test_the_browsers_spellings_of_on_and_off_are_understood(self):
		for sent, expected in (
			("true", True),
			("1", True),
			(1, True),
			("false", False),
			("0", False),
			(0, False),
		):
			with self.subTest(sent=sent):
				self.as_user()
				self.assertEqual(
					self.methods(api.set_payment_method("card", sent))["card"]["enabled"], expected
				)

	def test_nonsense_is_refused_and_nothing_is_written(self):
		self.as_user()
		for sent in ("maybe", None, "2"):
			with self.subTest(sent=sent), self.assertRaises(frappe.ValidationError):
				api.set_payment_method("card", sent)
		self.db.set_default.assert_not_called()

	def test_an_other_mode_is_switched_by_name(self):
		self.as_user()
		reply = api.set_payment_method("other", False, mode="Cheque")
		self.assertEqual(reply["data"]["other"][0]["enabled"], False)
		self.assertEqual(
			api.get_till_payment_modes()["data"]["modes"], ["Cash", "Mpesa", "Credit Card", "Credit"]
		)

	def test_other_without_a_mode_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("other", False)
		self.db.set_default.assert_not_called()

	def test_a_mode_with_its_own_switch_is_refused_under_other(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("other", False, mode="Cash")
		self.db.set_default.assert_not_called()

	def test_an_unknown_key_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("bitcoin", True)
		self.db.set_default.assert_not_called()

	def test_a_method_the_shop_lacks_cannot_be_switched_on(self):
		self.as_user(companies=("Shop B",))
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("card", True, company="Shop B")
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("other", True, mode="Gift Voucher", company="Shop B")
		self.db.set_default.assert_not_called()

	def test_the_last_method_cannot_be_switched_off_and_nothing_is_written(self):
		self.as_user(companies=("Shop B",))
		api.set_payment_method("mpesa", False, company="Shop B")
		writes = self.db.set_default.call_count
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("cash", False, company="Shop B")
		self.assertEqual(self.db.set_default.call_count, writes)
		self.assertEqual(api.get_till_payment_modes(company="Shop B")["data"]["modes"], ["Cash"])

	def test_the_last_method_rule_still_holds_when_the_last_one_is_an_other_mode(self):
		self.as_user(modes={"Shop A": ["Cash", "Cheque"]})
		api.set_payment_method("cash", False)
		with self.assertRaises(frappe.ValidationError):
			api.set_payment_method("other", False, mode="Cheque")

	def test_broken_saved_text_reads_as_everything_on(self):
		self.as_user(stored={api._key("Shop A"): "{oops"})
		self.assertEqual(len(api.get_till_payment_modes()["data"]["modes"]), 5)
		self.assertTrue(api.set_payment_method("mpesa", False)["success"])


class TestWhoMayChange(PaymentMethodsCase):
	def test_a_cashier_cannot_change_anything_and_nothing_is_read_or_written(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			api.set_payment_method("mpesa", False)
		self.db.set_default.assert_not_called()
		self.db.get_default.assert_not_called()

	def test_a_manager_who_is_not_the_owner_cannot_change_anything(self):
		self.as_user("sales@shop-a.test", roles=MANAGER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			api.set_payment_method("mpesa", False)
		self.db.set_default.assert_not_called()
		self.db.get_default.assert_not_called()

	def test_a_guest_cannot_change_anything(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			api.set_payment_method("mpesa", False)
		self.db.set_default.assert_not_called()

	def test_a_cashier_is_refused_even_for_nonsense_input(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			api.set_payment_method("bitcoin", "maybe")
		self.db.set_default.assert_not_called()

	def test_an_owner_cannot_change_another_businesss_switch(self):
		self.as_user(companies=("Shop A",))
		with self.assertRaises(frappe.PermissionError):
			api.set_payment_method("mpesa", False, company="Shop B")
		self.db.set_default.assert_not_called()

	def test_the_administrator_may_change_any_business(self):
		self.as_user("Administrator", roles=(), companies=())
		reply = api.set_payment_method("mpesa", False, company="Shop B")
		self.assertFalse(self.methods(reply)["mpesa"]["enabled"])

	def test_one_business_never_changes_another(self):
		self.as_user(companies=("Shop A", "Shop B"))
		api.set_payment_method("mpesa", False, company="Shop A")
		self.assertFalse(self.methods(api.get_payment_methods(company="Shop A"))["mpesa"]["enabled"])
		self.assertTrue(self.methods(api.get_payment_methods(company="Shop B"))["mpesa"]["enabled"])

	def test_the_capability_used_is_held_by_owners_alone(self):
		from murzak_pos.api import role_tiers

		self.assertEqual(role_tiers.CAPABILITIES[api.CAPABILITY], (role_tiers.OWNER,))


class TestKey(unittest.TestCase):
	def test_each_business_has_its_own_key(self):
		self.assertNotEqual(api._key("Shop A"), api._key("Shop B"))

	def test_it_never_clashes_with_the_new_look_key(self):
		from murzak_pos.api import new_look_api

		self.assertNotEqual(api._key("Shop A"), new_look_api._key("Shop A"))

	def test_a_very_long_name_still_fits(self):
		key = api._key("A" * 400)
		self.assertLessEqual(len(key), api.MAX_KEY_LENGTH)
		self.assertEqual(key, api._key("A" * 400))
		self.assertNotEqual(key, api._key("A" * 399 + "B"))

	def test_the_fixed_methods_match_the_rules(self):
		self.assertEqual(calc.FIXED_KEYS, ("cash", "mpesa", "card", "credit"))


if __name__ == "__main__":
	unittest.main()
