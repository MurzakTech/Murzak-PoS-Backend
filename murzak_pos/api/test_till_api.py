# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Till open panel and the shift-end count (till_api.py) that need no database: who may close which
session, that a preview changes nothing, that a confirmed close goes through the existing closing path exactly once,
and what is refused. Every Frappe call is replaced.
"""

import contextlib
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import till_api

SESSION = {
	"name": "POS-OPE-0001",
	"user": "amina@shop-a.test",
	"company": "Shop A",
	"pos_profile": "Main till",
	"period_start_date": "2026-10-13 08:15:00",
	"posting_date": "2026-10-13",
	"status": "Open",
	"docstatus": 1,
	"pos_closing_entry": None,
}


def expected_lines():
	return [
		{
			"mode": "Cash",
			"key": "cash",
			"label": "Cash",
			"opening": 5000.0,
			"sales": 2350.0,
			"paid_in": 0.0,
			"refunds": 0.0,
			"paid_out": 0.0,
			"expected": 7350.0,
			"counts_cash": True,
		},
		{
			"mode": "M-Pesa",
			"key": "mpesa",
			"label": "M-Pesa",
			"opening": 0.0,
			"sales": 800.0,
			"paid_in": 0.0,
			"refunds": 0.0,
			"paid_out": 0.0,
			"expected": 800.0,
			"counts_cash": True,
		},
	]


class Row:
	def __init__(self, mode, opening, expected):
		self.mode_of_payment, self.opening_amount, self.expected_amount = mode, opening, expected
		self.closing_amount = 0
		self.difference = 0


class ClosingDoc:
	"""A draft POS Closing Entry as ERPNext makes it: counts empty."""

	def __init__(self):
		self.name = "POS-CLO-0001"
		self.status = "Submitted"
		self.flags = frappe._dict()
		self.payment_reconciliation = [Row("Cash", 5000, 7500), Row("M-Pesa", 0, 800)]
		self.saved = 0
		self.submitted = 0
		self.events = []

	def get(self, name):
		return getattr(self, name, None)

	def append(self, table, values):
		self.events.append(("append", values))
		getattr(self, table).append(type("R", (), values)())

	def save(self):
		self.saved += 1
		self.events.append("save")

	def submit(self):
		self.submitted += 1
		self.events.append("submit")

	def reload(self):
		pass


class TillCase(unittest.TestCase):
	def setUp(self):
		self.user = "amina@shop-a.test"
		self.tier = "cashier"
		self.entry = dict(SESSION)
		self.db = MagicMock()
		self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False: (
			self.entry if doctype == "POS Opening Entry" else "Amina Wanjiru"
		)
		self.closing = ClosingDoc()
		self.close_existing = MagicMock(
			return_value={
				"success": True,
				"data": {
					"outcome": "closed",
					"closing_entry": {"name": "POS-CLO-0001", "docstatus": 0},
					"opening_entry": {"name": "POS-OPE-0001", "status": "Open"},
				},
			}
		)
		self.get_doc = MagicMock(return_value=self.closing)
		patches = [
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "session", frappe._dict(user=self.user), create=True),
			patch.object(frappe, "get_doc", self.get_doc),
			patch.object(till_api.role_tiers, "get_tier", lambda *a: self.tier),
			patch.object(till_api, "ensure_record_access"),
			patch.object(till_api, "get_user_companies", lambda *a: {"Shop A"}),
			patch.object(till_api, "_expected_lines", lambda entry: expected_lines()),
			patch.object(till_api.sales_api, "close_pos_opening_entry", self.close_existing),
			patch.object(till_api.sales_api, "_as_administrator", contextlib.nullcontext),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def as_user(self, user, tier):
		frappe.session.user = user
		self.tier = tier

	def cash(self, amount):
		return [{"mode": "Cash", "amount": amount}]


class TestPreview(TillCase):
	def test_the_default_is_a_preview_that_changes_nothing(self):
		result = till_api.close_till("POS-OPE-0001", self.cash(7250))
		self.assertTrue(result["success"])
		self.assertFalse(result["data"]["closed"])
		self.close_existing.assert_not_called()
		self.get_doc.assert_not_called()
		self.db.commit.assert_not_called()
		self.assertEqual(self.closing.saved + self.closing.submitted, 0)

	def test_a_preview_with_confirm_false_text_is_still_a_preview(self):
		for off in ("false", "0", "", None, False):
			till_api.close_till("POS-OPE-0001", self.cash(7350), off)
		self.close_existing.assert_not_called()

	def test_the_preview_shows_short_by(self):
		data = till_api.close_till("POS-OPE-0001", self.cash(7250))["data"]
		self.assertEqual(data["word"], "short")
		self.assertEqual(data["by"], 100.0)
		self.assertEqual(data["difference"], -100.0)
		self.assertEqual(data["total_expected"], 8150.0)
		self.assertEqual(data["total_counted"], 8050.0)
		self.assertEqual([x["mode"] for x in data["lines"]], ["Cash", "M-Pesa"])
		self.assertEqual(data["lines"][1]["counted"], 800.0)

	def test_a_preview_without_a_count_shows_the_expected_and_asks_for_the_count(self):
		data = till_api.close_till("POS-OPE-0001")["data"]
		self.assertTrue(data["needs_cash_count"])
		self.assertIsNone(data["word"])
		self.assertEqual(data["lines"][0]["expected"], 7350.0)

	def test_counted_may_arrive_as_json_text(self):
		data = till_api.close_till("POS-OPE-0001", '[{"mode": "Cash", "amount": 7350}]')["data"]
		self.assertEqual(data["word"], "matches")

	def test_a_bad_count_is_refused_in_words(self):
		result = till_api.close_till("POS-OPE-0001", [{"mode": "Cash", "amount": -5}])
		self.assertFalse(result["success"])
		self.assertEqual(result["error_type"], "negative_count")

	def test_an_unknown_method_is_refused(self):
		result = till_api.close_till("POS-OPE-0001", [{"mode": "Gold", "amount": 5}])
		self.assertEqual(result["error_type"], "unknown_mode")
		self.assertIn("Gold", result["message"])


class TestConfirm(TillCase):
	def test_confirm_closes_through_the_existing_path_exactly_once(self):
		result = till_api.close_till("POS-OPE-0001", self.cash(7250), True)
		self.assertTrue(result["success"])
		self.assertTrue(result["data"]["closed"])
		self.assertEqual(result["data"]["closing_entry"], "POS-CLO-0001")
		self.close_existing.assert_called_once_with("POS-OPE-0001", do_not_submit=True)
		self.assertEqual(self.closing.submitted, 1)
		self.assertEqual(self.closing.saved, 1)

	def test_the_counts_are_saved_before_the_entry_is_submitted(self):
		till_api.close_till("POS-OPE-0001", self.cash(7250), True)
		self.assertEqual(self.closing.events, ["save", "submit"])
		cash, mpesa = self.closing.payment_reconciliation
		self.assertEqual(
			(cash.closing_amount, cash.difference, cash.expected_amount), (7250.0, -100.0, 7350.0)
		)
		self.assertEqual((mpesa.closing_amount, mpesa.difference), (800.0, 0.0))

	def test_confirm_as_text_true_closes(self):
		till_api.close_till("POS-OPE-0001", self.cash(7350), "true")
		self.close_existing.assert_called_once()

	def test_the_answer_has_the_same_lines_as_the_preview(self):
		preview = till_api.close_till("POS-OPE-0001", self.cash(7250))["data"]
		done = till_api.close_till("POS-OPE-0001", self.cash(7250), True)["data"]
		for key in ("lines", "total_expected", "total_counted", "difference", "word"):
			self.assertEqual(preview[key], done[key])

	def test_refused_without_a_cash_count(self):
		for missing in (None, [], [{"mode": "M-Pesa", "amount": 800}]):
			result = till_api.close_till("POS-OPE-0001", missing, True)
			self.assertFalse(result["success"])
			self.assertEqual(result["error_type"], "cash_count_required")
		self.close_existing.assert_not_called()

	def test_a_cash_count_of_zero_is_accepted(self):
		self.assertTrue(till_api.close_till("POS-OPE-0001", self.cash(0), True)["success"])

	def test_a_mode_missing_from_the_closing_entry_is_added(self):
		self.closing.payment_reconciliation = [Row("Cash", 5000, 7500)]
		till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertEqual(self.closing.events[0][0], "append")
		self.assertEqual(self.closing.events[0][1]["mode_of_payment"], "M-Pesa")

	def test_a_queued_close_is_reported_as_closing(self):
		self.closing.status = "Queued"
		result = till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertEqual(result["data"]["outcome"], "closing")
		self.assertTrue(result["data"]["closed"])

	def test_when_the_existing_path_says_it_is_already_done_nothing_more_is_made(self):
		self.close_existing.return_value = {
			"success": True,
			"message": "Till closed. The shift has ended.",
			"data": {"outcome": "closed", "closing_entry": {"name": "POS-CLO-0009", "docstatus": 1}},
		}
		result = till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertTrue(result["data"]["closed"])
		self.assertEqual(result["data"]["closing_entry"], "POS-CLO-0009")
		self.get_doc.assert_not_called()

	def test_a_failed_background_close_is_not_reported_as_closed(self):
		self.close_existing.return_value = {
			"success": True,
			"message": "Closing this shift failed",
			"data": {"outcome": "failed", "closing_entry": {"name": "POS-CLO-0009", "docstatus": 1}},
		}
		result = till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertFalse(result["data"]["closed"])

	def test_an_error_from_the_existing_path_is_passed_on(self):
		self.close_existing.return_value = {
			"success": False,
			"message": "No.",
			"error_type": "validation_error",
		}
		result = till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "No.")

	def test_a_failure_while_submitting_is_reported_not_raised(self):
		self.closing.submit = MagicMock(side_effect=RuntimeError("boom"))
		with patch.object(
			till_api.sales_api, "_shift_error", return_value={"success": False, "message": "boom"}
		) as err:
			result = till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertFalse(result["success"])
		err.assert_called_once()


class TestWhoMayClose(TillCase):
	def test_a_cashier_cannot_close_another_cashiers_session(self):
		self.as_user("juma@shop-a.test", "cashier")
		with self.assertRaises(frappe.PermissionError):
			till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.close_existing.assert_not_called()

	def test_nor_preview_it(self):
		self.as_user("juma@shop-a.test", "cashier")
		with self.assertRaises(frappe.PermissionError):
			till_api.close_till("POS-OPE-0001", self.cash(7350))

	def test_a_manager_may(self):
		self.as_user("boss@shop-a.test", "manager")
		self.assertTrue(till_api.close_till("POS-OPE-0001", self.cash(7350), True)["success"])

	def test_an_owner_may(self):
		self.as_user("owner@shop-a.test", "owner")
		self.assertTrue(till_api.close_till("POS-OPE-0001", self.cash(7350), True)["success"])

	def test_the_cashier_who_opened_it_may(self):
		self.assertTrue(till_api.close_till("POS-OPE-0001", self.cash(7350), True)["success"])

	def test_a_manager_of_another_business_is_refused(self):
		self.as_user("boss@shop-b.test", "owner")
		with (
			patch.object(till_api, "get_user_companies", lambda *a: {"Shop B"}),
			self.assertRaises(frappe.PermissionError),
		):
			till_api.close_till("POS-OPE-0001", self.cash(7350), True)

	def test_nobody_signed_in_is_asked_to_sign_in(self):
		self.tier = None
		with (
			patch.object(till_api.role_tiers, "get_tier", lambda *a: None),
			self.assertRaises(frappe.AuthenticationError),
		):
			till_api.close_till("POS-OPE-0001")

	def test_the_record_check_comes_first(self):
		with patch.object(till_api, "ensure_record_access", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.close_existing.assert_not_called()


class TestStates(TillCase):
	def test_a_closed_session_is_a_message_and_is_not_closed_twice(self):
		self.entry["status"] = "Closed"
		self.entry["pos_closing_entry"] = "POS-CLO-0001"
		for confirm in (False, True):
			result = till_api.close_till("POS-OPE-0001", self.cash(7350), confirm)
			self.assertFalse(result["success"])
			self.assertEqual(result["error_type"], "already_closed")
			self.assertIn("already closed", result["message"])
			self.assertEqual(result["data"]["closing_entry"], "POS-CLO-0001")
		self.close_existing.assert_not_called()

	def test_a_session_that_is_not_open_is_refused(self):
		self.entry["status"] = "Cancelled"
		result = till_api.close_till("POS-OPE-0001", self.cash(7350), True)
		self.assertEqual(result["error_type"], "not_open")
		self.assertIn("Cancelled", result["message"])
		self.close_existing.assert_not_called()

	def test_a_draft_session_is_refused(self):
		self.entry["docstatus"] = 0
		self.assertEqual(till_api.close_till("POS-OPE-0001", self.cash(7350), True)["error_type"], "not_open")

	def test_a_missing_session_is_a_message(self):
		self.db.get_value.side_effect = lambda *a, **k: None
		result = till_api.close_till("POS-OPE-9999")
		self.assertEqual(result["error_type"], "not_found")
		self.assertIn("POS-OPE-9999", result["message"])


class TestGetTillState(TillCase):
	def setUp(self):
		super().setUp()
		extra = [
			patch.object(till_api, "_open_sessions", MagicMock(return_value=[dict(SESSION)])),
			patch.object(
				till_api, "_opening_rows", lambda name: [{"mode_of_payment": "Cash", "opening_amount": 5000}]
			),
			patch.object(till_api, "_held_count", MagicMock(return_value=2)),
			patch.object(
				till_api, "_recent_sales", MagicMock(return_value=[{"id": "A", "receipt": "#0001"}])
			),
			patch.object(till_api, "resolve_company", lambda *a: "Shop A"),
		]
		for p in extra:
			p.start()
			self.addCleanup(p.stop)

	def test_an_open_session(self):
		data = till_api.get_till_state()["data"]
		self.assertEqual(data["state"], "open")
		self.assertEqual(
			data["session"],
			{
				"name": "POS-OPE-0001",
				"opened_on": "2026-10-13",
				"opened_at": "08:15",
				"cashier": "amina@shop-a.test",
				"cashier_name": "Amina Wanjiru",
				"pos_profile": "Main till",
				"opening": [{"mode": "Cash", "amount": 5000.0}],
			},
		)
		self.assertEqual(data["held_count"], 2)
		self.assertEqual(data["cash_in_till"], 7350.0)
		self.assertEqual(data["recent_sales"][0]["receipt"], "#0001")
		self.assertEqual(data["errors"], [])

	def test_no_open_session_is_closed_not_an_error(self):
		till_api._open_sessions.return_value = []
		result = till_api.get_till_state()
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["state"], "closed")
		self.assertIsNone(result["data"]["session"])
		self.assertEqual(result["data"]["expected"], [])
		self.assertEqual(result["data"]["recent_sales"], [])

	def test_a_cashier_gets_only_their_own_session(self):
		till_api._open_sessions.return_value = [
			dict(SESSION, name="OTHERS", user="juma@shop-a.test"),
			dict(SESSION),
		]
		self.assertEqual(till_api.get_till_state()["data"]["session"]["name"], "POS-OPE-0001")
		till_api._open_sessions.return_value = [dict(SESSION, name="OTHERS", user="juma@shop-a.test")]
		self.assertEqual(till_api.get_till_state()["data"]["state"], "closed")

	def test_a_manager_without_their_own_session_gets_the_newest(self):
		self.as_user("boss@shop-a.test", "manager")
		self.assertEqual(till_api.get_till_state()["data"]["session"]["name"], "POS-OPE-0001")

	def test_a_cashier_is_asked_only_for_their_own(self):
		till_api.get_till_state(pos_profile="Main till", warehouse="Stores - SA")
		args = till_api._open_sessions.call_args.args
		self.assertEqual(args[1:], ("amina@shop-a.test", "cashier", "Main till", "Stores - SA"))

	def test_a_failing_held_count_leaves_the_rest(self):
		till_api._held_count.side_effect = RuntimeError("down")
		with patch.object(frappe, "log_error"), patch.object(frappe, "get_traceback", return_value=""):
			data = till_api.get_till_state()["data"]
		self.assertIsNone(data["held_count"])
		self.assertEqual(data["errors"], ["held_count"])
		self.assertEqual(data["state"], "open")

	def test_a_failing_recent_sales_leaves_the_rest(self):
		till_api._recent_sales.side_effect = RuntimeError("down")
		with patch.object(frappe, "log_error"), patch.object(frappe, "get_traceback", return_value=""):
			data = till_api.get_till_state()["data"]
		self.assertEqual(data["recent_sales"], [])
		self.assertEqual(data["errors"], ["recent_sales"])

	def test_the_record_checks_are_made_for_the_profile_and_the_store(self):
		till_api.get_till_state(pos_profile="Main till", warehouse="Stores - SA")
		kinds = [c.args[0] for c in till_api.ensure_record_access.call_args_list]
		self.assertEqual(kinds, ["POS Profile", "Warehouse"])

	def test_signed_out_is_asked_to_sign_in(self):
		with (
			patch.object(till_api.role_tiers, "get_tier", lambda *a: None),
			self.assertRaises(frappe.AuthenticationError),
		):
			till_api.get_till_state()

	def test_an_unexpected_failure_is_a_friendly_message(self):
		till_api._open_sessions.side_effect = RuntimeError("db gone")
		with patch.object(frappe, "log_error"), patch.object(frappe, "get_traceback", return_value=""):
			with self.assertRaises(frappe.ValidationError):
				till_api.get_till_state()


class TestLabels(unittest.TestCase):
	def test_standard_and_other_labels(self):
		self.assertEqual(till_api._label({"key": "cash", "mode": "Cash"}), "Cash")
		self.assertEqual(till_api._label({"key": "mpesa", "mode": "MPESA"}), "M-Pesa")
		self.assertEqual(till_api._label({"key": "card", "mode": "Credit Card"}), "Card")
		self.assertEqual(till_api._label({"key": "other", "mode": "Cheque"}), "Other")
		self.assertEqual(till_api._label({"key": "other", "mode": "Airtel Money"}), "Airtel Money")
		self.assertEqual(till_api._label({"key": "credit", "mode": "On credit"}), "On credit")


if __name__ == "__main__":
	unittest.main()
