# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for handing a till over (handover_api.py) that need no database: the preview changes nothing, a confirmed
handover closes the old shift once and opens the next with the counted cash, and what is refused. Every Frappe call
is replaced.
"""

import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import handover_api, till_api

SESSION = {
	"name": "POS-OPE-0001",
	"user": "amina@shop-a.test",
	"company": "Shop A",
	"pos_profile": "Main till",
	"status": "Open",
	"docstatus": 1,
}


def lines():
	return [
		{"mode": "Cash", "key": "cash", "label": "Cash", "expected": 7350.0, "counts_cash": True},
		{"mode": "M-Pesa", "key": "mpesa", "label": "M-Pesa", "expected": 800.0, "counts_cash": True},
	]


@contextlib.contextmanager
def world(
	caller="amina@shop-a.test", tier="cashier", entry=SESSION, refusal=None, person_ok=True, close=None
):
	db = MagicMock()
	db.exists.return_value = person_ok
	db.get_value.return_value = 1
	close = close or {
		"success": True,
		"message": "closed",
		"data": {"closed": True, "outcome": "closed", "closing_entry": "CLO-1"},
	}
	with (
		patch.object(handover_api.frappe, "db", db, create=True),
		patch.object(handover_api.frappe, "session", MagicMock(user=caller)),
		patch.object(handover_api.frappe, "get_doc") as get_doc,
		patch.object(handover_api, "ensure_record_access") as guard,
		patch.object(handover_api, "get_user_companies", return_value={"Shop A"}),
		patch.object(handover_api.till_api, "_tier", return_value=tier),
		patch.object(handover_api.till_api, "_person", side_effect=lambda u: u.split("@")[0]),
		patch.object(
			handover_api.till_api, "closable_entry", return_value=(None if refusal else entry, refusal)
		),
		patch.object(handover_api.till_api, "_expected_lines", return_value=lines()),
		patch.object(handover_api.till_api, "_close_session", return_value=close) as closer,
		patch.object(handover_api.sales_api, "_as_administrator", contextlib.nullcontext),
		patch.object(
			handover_api.sales_api, "_get_or_create_pos_opening_entry", return_value="POS-OPE-0002"
		) as opener,
	):
		yield SimpleNamespace(db=db, get_doc=get_doc, guard=guard, closer=closer, opener=opener)


COUNT = '[{"mode": "Cash", "amount": 7290}]'


class PreviewCase(unittest.TestCase):
	def test_a_preview_changes_nothing(self):
		with world() as w:
			out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", COUNT)
		self.assertTrue(out["success"])
		self.assertFalse(out["data"]["closed"])
		self.assertEqual(out["data"]["handover_to"]["user"], "brian@shop-a.test")
		w.closer.assert_not_called()
		w.opener.assert_not_called()
		w.get_doc.assert_not_called()

	def test_the_record_check_comes_first(self):
		with world() as w:
			handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", COUNT)
		w.guard.assert_called_with("POS Opening Entry", SESSION["name"])


class ConfirmCase(unittest.TestCase):
	def test_closes_once_and_opens_the_next_shift_with_the_counted_cash(self):
		with world() as w:
			out = handover_api.hand_over_till(
				SESSION["name"], "brian@shop-a.test", COUNT, confirm="true", note="lunch"
			)
		w.closer.assert_called_once()
		w.opener.assert_called_once()
		kwargs = w.opener.call_args.kwargs
		self.assertEqual(kwargs["user"], "brian@shop-a.test")
		self.assertEqual(kwargs["pos_profile"], "Main till")
		self.assertEqual(kwargs["balance_details"], [{"mode_of_payment": "Cash", "opening_amount": 7290.0}])
		self.assertEqual(out["data"]["next_shift"]["name"], "POS-OPE-0002")
		self.assertEqual(out["data"]["next_shift"]["float"], 7290.0)
		# the note is kept on both shifts
		self.assertEqual(w.get_doc.call_count, 2)
		self.assertIn("lunch", w.get_doc.call_args.args[0]["content"])

	def test_confirm_needs_a_cash_count(self):
		with world() as w:
			out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", None, confirm=True)
		self.assertEqual(out["error_type"], "cash_count_required")
		w.closer.assert_not_called()

	def test_a_failed_close_opens_nothing(self):
		bad = {"success": False, "message": "no", "error_type": "close_failed"}
		with world(close=bad) as w:
			out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", COUNT, confirm=True)
		self.assertFalse(out["success"])
		w.opener.assert_not_called()

	def test_a_close_that_is_still_running_opens_nothing_yet(self):
		queued = {"success": True, "message": "q", "data": {"closed": True, "outcome": "closing"}}
		with world(close=queued) as w:
			out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", COUNT, confirm=True)
		w.opener.assert_not_called()
		self.assertIsNone(out["data"]["next_shift"])

	def test_if_the_next_shift_cannot_open_the_close_still_stands(self):
		with world() as w:
			w.opener.side_effect = RuntimeError("boom")
			with patch.object(handover_api.frappe, "log_error"):
				out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", COUNT, confirm=True)
		self.assertTrue(out["success"])
		self.assertIsNone(out["data"]["next_shift"])
		self.assertEqual(out["data"]["float"], 7290.0)


class RefusalCase(unittest.TestCase):
	def test_same_person(self):
		with world() as w:
			out = handover_api.hand_over_till(SESSION["name"], SESSION["user"], COUNT, confirm=True)
		self.assertEqual(out["error_type"], "same_person")
		w.closer.assert_not_called()

	def test_unknown_person(self):
		with world(person_ok=False) as w:
			out = handover_api.hand_over_till(SESSION["name"], "ghost@x.test", COUNT, confirm=True)
		self.assertEqual(out["error_type"], "no_such_person")
		w.closer.assert_not_called()

	def test_person_of_another_business(self):
		with world() as w:
			with patch.object(handover_api, "get_user_companies", return_value={"Shop B"}):
				out = handover_api.hand_over_till(SESSION["name"], "zed@shop-b.test", COUNT, confirm=True)
		self.assertEqual(out["error_type"], "other_business")
		w.closer.assert_not_called()

	def test_a_shift_that_cannot_be_closed_is_passed_on(self):
		refusal = till_api._refusal("already_closed")
		with world(refusal=refusal) as w:
			out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", COUNT, confirm=True)
		self.assertEqual(out["error_type"], "already_closed")
		w.closer.assert_not_called()

	def test_a_bad_count_is_refused_in_words(self):
		with world() as w:
			out = handover_api.hand_over_till(SESSION["name"], "brian@shop-a.test", "not json", confirm=True)
		self.assertFalse(out["success"])
		w.closer.assert_not_called()


class PeopleCase(unittest.TestCase):
	def test_lists_colleagues_without_the_leaving_cashier(self):
		with world() as w:
			with patch.object(handover_api.frappe, "get_all") as get_all:
				get_all.side_effect = [
					["amina@shop-a.test", "brian@shop-a.test"],
					[],
					[{"name": "brian@shop-a.test", "full_name": "Brian"}],
				]
				w.db.has_column.return_value = True
				out = handover_api.list_handover_people(SESSION["name"])
		self.assertEqual(out["data"]["people"], [{"user": "brian@shop-a.test", "name": "Brian"}])

	def test_a_refusal_is_passed_on(self):
		refusal = till_api._refusal("not_open", "Closed")
		with world(refusal=refusal):
			out = handover_api.list_handover_people(SESSION["name"])
		self.assertEqual(out["error_type"], "not_open")


if __name__ == "__main__":
	unittest.main()
