# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for closing and cancelling a POS shift that need no database: who may do it, and
that the step run as Administrator always hands the signed-in user's session back intact.
"""

import contextlib
import types
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_api


def _shift(user="cashier@shop-a.test", company="Shop A"):
	return frappe._dict(name="POS-OPE-1", user=user, company=company)


class TestEnsureCanManageShift(unittest.TestCase):
	def check(self, session_user, roles=(), companies=("Shop A",), shift=None):
		with (
			patch.object(frappe, "session", frappe._dict(user=session_user)),
			patch.object(frappe, "get_roles", return_value=list(roles)),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)),
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



class TestCloseOutcomes(unittest.TestCase):
	"""A close that needs no new closing entry: already closed, still closing, or failed before."""

	def close(self, opening_status="Open", closings=(), after_retry=None, reloaded_status=None):
		opening = MagicMock(docstatus=1, status=opening_status, company="Shop A", user="cashier@shop-a.test")
		opening.name = "POS-OPE-1"
		if reloaded_status:
			opening.reload.side_effect = lambda: setattr(opening, "status", reloaded_status)
		closing_doc = MagicMock()
		rows = [list(closings)] + ([list(after_retry)] if after_retry is not None else [])
		with (
			patch.object(frappe.db, "exists", return_value=True, create=True),
			patch.object(frappe.db, "commit", create=True),
			patch.object(frappe, "get_doc", side_effect=lambda doctype, name: opening if doctype == "POS Opening Entry" else closing_doc),
			patch.object(frappe, "get_all", side_effect=lambda *a, **k: rows[0] if len(rows) == 1 else rows.pop(0)),
			patch.object(sales_api, "_ensure_can_manage_shift"),
			patch.object(sales_api, "_as_administrator", return_value=contextlib.nullcontext()) as admin,
		):
			result = sales_api.close_pos_opening_entry("POS-OPE-1")
		return result, closing_doc, admin

	def test_already_closed_shift_is_reported_as_closed(self):
		result, closing_doc, _ = self.close(opening_status="Closed")
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["outcome"], "already_closed")
		closing_doc.submit.assert_not_called()

	def test_close_running_in_background_is_not_started_twice(self):
		queued = frappe._dict(name="POS-CLO-1", status="Queued", docstatus=1, error_message=None)
		result, closing_doc, _ = self.close(closings=[queued])
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["outcome"], "closing")
		closing_doc.retry.assert_not_called()
		closing_doc.submit.assert_not_called()

	def test_failed_close_is_retried_with_full_rights(self):
		failed = frappe._dict(name="POS-CLO-1", status="Failed", docstatus=1, error_message="x")
		done = frappe._dict(name="POS-CLO-1", status="Submitted", docstatus=1, error_message=None)
		result, closing_doc, admin = self.close(closings=[failed], after_retry=[done], reloaded_status="Closed")
		closing_doc.retry.assert_called_once()
		admin.assert_called_once()
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["outcome"], "closed")

	def test_failed_again_says_why(self):
		failed = frappe._dict(name="POS-CLO-1", status="Failed", docstatus=1, error_message="Stock is negative")
		result, _, _ = self.close(closings=[failed], after_retry=[failed])
		self.assertFalse(result["success"])
		self.assertEqual(result["data"]["outcome"], "failed")
		self.assertIn("Stock is negative", result["message"])

	def test_a_close_that_would_submit_without_a_cash_count_is_refused(self):
		result, closing_doc, _ = self.close()
		self.assertFalse(result["success"])
		self.assertEqual(result["error_type"], "cash_count_required")
		closing_doc.submit.assert_not_called()

if __name__ == "__main__":
	unittest.main()
