# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the New look switch. They need no database: every Frappe call is mocked, so they run under
`bench run-tests` and in plain Python alike.

What they pin down: a business is OFF until an owner turns it on; only an owner may change it; nobody
can change another business's switch; the switch of one business never leaks to another; and the one
call that changes anything checks for an owner before it touches the database.
"""

import ast
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import new_look_api

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager")
CASHIER_ROLES = ("Sales User",)
MANAGER_ROLES = ("Sales Manager", "Sales User")


class NewLookCase(unittest.TestCase):
	def as_user(self, user="owner@shop-a.test", roles=OWNER_ROLES, companies=("Shop A",), stored=None):
		"""Run as `user` holding `roles`, who belongs to `companies`. `stored` maps a default key to its value."""
		self.stored = dict(stored or {})
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
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestReading(NewLookCase):
	def test_a_business_that_never_chose_is_off(self):
		self.as_user()
		result = new_look_api.get_new_look()
		self.assertEqual(result["data"], {"company": "Shop A", "enabled": False})

	def test_on_and_off_are_read_back(self):
		self.as_user(stored={new_look_api._key("Shop A"): "1"})
		self.assertTrue(new_look_api.get_new_look()["data"]["enabled"])
		self.as_user(stored={new_look_api._key("Shop A"): "0"})
		self.assertFalse(new_look_api.get_new_look()["data"]["enabled"])

	def test_a_cashier_may_ask_whether_their_own_business_has_it_on(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES, stored={new_look_api._key("Shop A"): "1"})
		self.assertTrue(new_look_api.get_new_look()["data"]["enabled"])

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			new_look_api.get_new_look()

	def test_another_businesss_switch_cannot_be_read(self):
		self.as_user(companies=("Shop A",), stored={new_look_api._key("Shop B"): "1"})
		with self.assertRaises(frappe.PermissionError):
			new_look_api.get_new_look(company="Shop B")


class TestChanging(NewLookCase):
	def test_an_owner_turns_it_on_and_off(self):
		self.as_user()
		on = new_look_api.set_new_look(True)
		self.assertEqual(on["data"], {"company": "Shop A", "enabled": True})
		self.assertTrue(new_look_api.get_new_look()["data"]["enabled"])
		off = new_look_api.set_new_look(False)
		self.assertFalse(off["data"]["enabled"])
		self.assertFalse(new_look_api.get_new_look()["data"]["enabled"])

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
				self.assertEqual(new_look_api.set_new_look(sent)["data"]["enabled"], expected)

	def test_nonsense_is_refused_and_nothing_is_written(self):
		self.as_user()
		for sent in ("maybe", None, "2"):
			with self.subTest(sent=sent), self.assertRaises(frappe.ValidationError):
				new_look_api.set_new_look(sent)
		self.db.set_default.assert_not_called()

	def test_a_cashier_cannot_change_it_and_nothing_is_written(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			new_look_api.set_new_look(True)
		self.db.set_default.assert_not_called()

	def test_a_manager_who_is_not_the_owner_cannot_change_it(self):
		self.as_user("sales@shop-a.test", roles=MANAGER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			new_look_api.set_new_look(True)
		self.db.set_default.assert_not_called()

	def test_a_guest_cannot_change_it(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			new_look_api.set_new_look(True)
		self.db.set_default.assert_not_called()

	def test_an_owner_cannot_change_another_businesss_switch(self):
		self.as_user(companies=("Shop A",))
		with self.assertRaises(frappe.PermissionError):
			new_look_api.set_new_look(True, company="Shop B")
		self.db.set_default.assert_not_called()

	def test_the_administrator_may_change_any_business(self):
		self.as_user("Administrator", roles=(), companies=())
		self.assertTrue(new_look_api.set_new_look(True, company="Shop B")["data"]["enabled"])

	def test_one_business_never_turns_on_another(self):
		self.as_user(companies=("Shop A", "Shop B"))
		new_look_api.set_new_look(True, company="Shop A")
		self.assertTrue(new_look_api.get_new_look(company="Shop A")["data"]["enabled"])
		self.assertFalse(new_look_api.get_new_look(company="Shop B")["data"]["enabled"])


class TestKey(unittest.TestCase):
	def test_each_business_has_its_own_key(self):
		self.assertNotEqual(new_look_api._key("Shop A"), new_look_api._key("Shop B"))

	def test_a_very_long_company_name_still_fits_and_stays_unique(self):
		a = new_look_api._key("A" * 200)
		b = new_look_api._key("A" * 199 + "B")
		self.assertLessEqual(len(a), new_look_api.MAX_KEY_LENGTH)
		self.assertNotEqual(a, b)
		self.assertEqual(a, new_look_api._key("A" * 200))


class TestGuardIsNotForgotten(unittest.TestCase):
	"""Reads the source, so a later edit that moves the owner check after a database call fails here."""

	def test_the_call_that_changes_the_switch_checks_for_an_owner_first(self):
		path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "new_look_api.py")
		with open(path) as handle:
			source = handle.read()
		node = next(
			n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "set_new_look"
		)
		body = ast.get_source_segment(source, node)
		call = body.find("require_manager()")
		touching = [body.find(word) for word in ("frappe.db", "resolve_company(")]
		self.assertNotEqual(call, -1, "set_new_look must call require_manager()")
		self.assertTrue(all(call < position for position in touching if position != -1))
		self.assertIn('methods=["POST"]', ast.unparse(node.decorator_list[0]).replace("'", '"'))
