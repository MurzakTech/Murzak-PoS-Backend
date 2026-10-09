# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Tests for closing and cancelling a POS shift that need no database: who may do it, and
that the step run as Administrator always hands the signed-in user's session back intact.
"""

import types
import unittest
from unittest.mock import patch

import frappe

from techsavanna_pos.api import sales_api


def _shift(user="cashier@shop-a.test", company="Shop A"):
	return frappe._dict(name="POS-OPE-1", user=user, company=company)


class TestEnsureCanManageShift(unittest.TestCase):
	def check(self, session_user, roles=(), companies=("Shop A",), shift=None):
		with (
			patch.object(frappe, "session", frappe._dict(user=session_user)),
			patch.object(frappe, "get_roles", return_value=list(roles)),
			patch("techsavanna_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)),
		):
			sales_api._ensure_can_manage_shift(shift or _shift())

	def test_cashier_may_close_own_shift(self):
		self.check("cashier@shop-a.test")

	def test_manager_may_close_a_colleagues_shift(self):
		for role in ("Sales Manager", "Accounts Manager", "System Manager"):
			self.check("boss@shop-a.test", roles=[role, "Sales User"])

	def test_another_cashier_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.check("other@shop-a.test", roles=["Sales User", "Accounts User"])

	def test_other_business_is_refused_even_for_a_manager(self):
		with self.assertRaises(frappe.PermissionError):
			self.check("boss@shop-b.test", roles=["System Manager"], companies=("Shop B",))

	def test_guest_is_refused(self):
		with self.assertRaises(frappe.AuthenticationError):
			self.check("Guest")

	def test_administrator_is_allowed(self):
		self.check("Administrator", companies=())


class TestAsAdministrator(unittest.TestCase):
	def setUp(self):
		self.session = frappe._dict(user="cashier@shop-a.test", sid="real-sid", data=frappe._dict(csrf_token="t"))
		self.cache = {"kept": 1}
		self.local = types.SimpleNamespace(
			session=self.session,
			cache=self.cache,
			form_dict=frappe._dict(pos_opening_entry="POS-OPE-1"),
			jenv=None,
			role_permissions={},
			new_doc_templates={},
			user_perms=None,
		)

		def set_user(username):
			# What frappe.set_user does: edit the current session in place and reset caches
			self.local.session.user = username
			self.local.session.sid = username
			self.local.session.data = frappe._dict()
			self.local.cache = {}
			self.local.form_dict = frappe._dict()

		self.patches = [patch.object(frappe, "local", self.local, create=True), patch.object(frappe, "set_user", set_user)]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in reversed(self.patches):
			p.stop()

	def assert_restored(self):
		self.assertIs(self.local.session, self.session)
		self.assertEqual(self.session, {"user": "cashier@shop-a.test", "sid": "real-sid", "data": {"csrf_token": "t"}})
		self.assertIs(self.local.cache, self.cache)
		self.assertEqual(self.local.form_dict, {"pos_opening_entry": "POS-OPE-1"})

	def test_runs_as_administrator_then_restores_the_session(self):
		with sales_api._as_administrator():
			self.assertEqual(self.local.session.user, "Administrator")
			self.assertEqual(self.session.user, "cashier@shop-a.test")  # the real session is never edited
		self.assert_restored()

	def test_restores_the_session_when_the_step_fails(self):
		with self.assertRaises(RuntimeError), sales_api._as_administrator():
			raise RuntimeError("merge failed")
		self.assert_restored()


if __name__ == "__main__":
	unittest.main()
