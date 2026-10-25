# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for recording cash in a shift (shift_api.py) that need no database: who may record, what is refused, that a
resent movement is recorded once, and that money leaving the till cannot be more than it holds. Every Frappe call is
replaced.
"""

import contextlib
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import shift_api

SESSION = {
	"name": "POS-OPE-0001",
	"user": "amina@shop-a.test",
	"company": "Shop A",
	"pos_profile": "Main till",
	"period_start_date": "2026-10-13 08:15:00",
	"posting_date": "2026-10-13",
	"status": "Open",
	"docstatus": 1,
}


def cash_lines(expected=7350.0):
	return [
		{"mode": "Cash", "key": "cash", "label": "Cash", "expected": expected, "counts_cash": True},
		{"mode": "M-Pesa", "key": "mpesa", "label": "M-Pesa", "expected": 800.0, "counts_cash": True},
	]


@contextlib.contextmanager
def world(
	caller="amina@shop-a.test",
	tier="cashier",
	entry=SESSION,
	lines=None,
	existing=None,
	companies=("Shop A",),
):
	db = MagicMock()
	db.get_value.side_effect = lambda doctype, *args, **kwargs: (
		entry if doctype == "POS Opening Entry" else existing
	)
	saved = MagicMock()
	doc = MagicMock()
	doc.get.side_effect = lambda field: {
		"name": "TCM-1",
		"kind": "Paid Out",
		"amount": 200.0,
		"mode_of_payment": "Cash",
		"reason": "Petty cash",
		"note": "",
		"recorded_by": caller,
		"recorded_at": "2026-10-13 10:00:00",
		"client_id": None,
		"pos_opening_entry": SESSION["name"],
	}.get(field)
	saved.get_doc.return_value = doc
	with (
		patch.object(shift_api.frappe, "db", db, create=True),
		patch.object(shift_api.frappe, "session", MagicMock(user=caller)),
		patch.object(shift_api.frappe, "get_doc", saved.get_doc),
		patch.object(shift_api, "ensure_record_access"),
		patch.object(shift_api, "get_user_companies", return_value=set(companies)),
		patch.object(shift_api.till_api, "_tier", return_value=tier),
		patch.object(shift_api.till_api, "_person", side_effect=lambda user: user),
		patch.object(
			shift_api.till_api, "_expected_lines", return_value=lines if lines is not None else cash_lines()
		),
		patch.object(shift_api, "now_datetime", return_value="2026-10-13 10:00:00"),
	):
		yield db, saved, doc


class RecordCase(unittest.TestCase):
	def test_the_cashier_of_the_shift_records_a_paid_out(self):
		with world() as (db, saved, doc):
			result = shift_api.record_cash_movement("POS-OPE-0001", "Paid Out", 200, "Petty cash", "tea")
		self.assertTrue(result["success"], result)
		self.assertFalse(result["data"]["duplicate"])
		made = saved.get_doc.call_args.args[0]
		self.assertEqual(
			(made["kind"], made["amount"], made["mode_of_payment"], made["company"], made["recorded_by"]),
			("Paid Out", 200.0, "Cash", "Shop A", "amina@shop-a.test"),
		)
		doc.insert.assert_called_once_with()
		db.commit.assert_called()

	def test_a_manager_may_record_in_another_cashiers_shift(self):
		with world(caller="boss@shop-a.test", tier="manager") as (_db, saved, _doc):
			result = shift_api.record_cash_movement("POS-OPE-0001", "Paid In", 500, "Float top-up")
		self.assertTrue(result["success"], result)
		saved.get_doc.assert_called_once()

	def test_another_cashier_may_not(self):
		with world(caller="joe@shop-a.test", tier="cashier") as (_db, saved, _doc):
			with self.assertRaises(frappe.PermissionError):
				shift_api.record_cash_movement("POS-OPE-0001", "Paid Out", 10, "Petty cash")
		saved.get_doc.assert_not_called()

	def test_a_shift_of_another_business_is_refused(self):
		with world(companies=("Shop B",)) as (_db, saved, _doc):
			with self.assertRaises(frappe.PermissionError):
				shift_api.record_cash_movement("POS-OPE-0001", "Paid Out", 10, "Petty cash")
		saved.get_doc.assert_not_called()

	def test_a_shift_that_does_not_exist_is_said_so(self):
		with world(entry=None) as (_db, saved, _doc):
			result = shift_api.record_cash_movement("POS-OPE-9999", "Paid In", 10)
		self.assertEqual(result["error_type"], "not_found")
		saved.get_doc.assert_not_called()

	def test_a_closed_shift_takes_no_cash(self):
		with world(entry={**SESSION, "status": "Closed"}) as (_db, saved, _doc):
			result = shift_api.record_cash_movement("POS-OPE-0001", "Paid In", 10)
		self.assertEqual(result["error_type"], "not_open")
		saved.get_doc.assert_not_called()

	def test_bad_input_is_refused_in_words(self):
		cases = [
			(("Paid Out", 0, None, None), "bad_amount"),
			(("Refund", 10, None, None), "bad_kind"),
			(("Paid Out", 10, "Other", None), "note_required"),
			(("Safe Drop", 10, "Petty cash", None), "bad_reason"),
		]
		for args, code in cases:
			with self.subTest(code=code), world() as (_db, saved, _doc):
				result = shift_api.record_cash_movement("POS-OPE-0001", *args)
				self.assertEqual(result["error_type"], code)
				self.assertFalse(result["success"])
				saved.get_doc.assert_not_called()

	def test_money_out_cannot_be_more_than_the_till_holds(self):
		with world(lines=cash_lines(expected=100.0)) as (_db, saved, _doc):
			result = shift_api.record_cash_movement("POS-OPE-0001", "Safe Drop", 150, "Bank deposit")
		self.assertEqual(result["error_type"], "more_than_in_till")
		self.assertIn("100.00", result["message"])
		saved.get_doc.assert_not_called()

	def test_money_in_is_never_blocked_by_the_till_total(self):
		with world(lines=cash_lines(expected=0.0)) as (_db, _saved, _doc):
			result = shift_api.record_cash_movement("POS-OPE-0001", "Paid In", 150, "Float top-up")
		self.assertTrue(result["success"], result)

	def test_a_till_with_no_cash_line_is_refused(self):
		with world(lines=[cash_lines()[1]]) as (_db, saved, _doc):
			result = shift_api.record_cash_movement("POS-OPE-0001", "Paid In", 10)
		self.assertEqual(result["error_type"], "no_cash_line")
		saved.get_doc.assert_not_called()

	def test_a_resent_movement_is_recorded_once(self):
		again = {
			"name": "TCM-1",
			"kind": "Paid Out",
			"amount": 200.0,
			"mode_of_payment": "Cash",
			"reason": "Petty cash",
			"note": "",
			"recorded_by": "amina@shop-a.test",
			"recorded_at": "2026-10-13 10:00:00",
			"client_id": "t-1",
			"pos_opening_entry": "POS-OPE-0001",
		}
		with world(existing=again) as (_db, saved, _doc):
			result = shift_api.record_cash_movement(
				"POS-OPE-0001", "Paid Out", 200, "Petty cash", None, "t-1"
			)
		self.assertTrue(result["success"], result)
		self.assertTrue(result["data"]["duplicate"])
		saved.get_doc.assert_not_called()

	def test_the_shift_is_locked_while_the_till_total_is_checked(self):
		with world() as (db, _saved, _doc):
			shift_api.record_cash_movement("POS-OPE-0001", "Paid In", 10)
		sql_calls = [call.args[0] for call in db.sql.call_args_list]
		self.assertTrue(any("FOR UPDATE" in sql for sql in sql_calls))


class ListCase(unittest.TestCase):
	def test_the_movements_come_with_their_totals(self):
		rows = [
			{
				"name": "A",
				"kind": "Paid In",
				"amount": 1000.0,
				"mode_of_payment": "Cash",
				"reason": "Float top-up",
				"note": "",
				"recorded_by": "amina@shop-a.test",
				"recorded_at": "2026-10-13 09:00:00",
				"client_id": None,
			},
			{
				"name": "B",
				"kind": "Paid Out",
				"amount": 250.0,
				"mode_of_payment": "Cash",
				"reason": "Petty cash",
				"note": "tea",
				"recorded_by": "amina@shop-a.test",
				"recorded_at": "2026-10-13 11:00:00",
				"client_id": None,
			},
		]
		with world() as (_db, _saved, _doc), patch.object(shift_api.frappe, "get_all", return_value=rows):
			result = shift_api.list_cash_movements("POS-OPE-0001")
		self.assertTrue(result["success"])
		self.assertEqual([m["id"] for m in result["data"]["movements"]], ["A", "B"])
		self.assertEqual(result["data"]["totals"], {"Paid In": 1000.0, "Paid Out": 250.0, "Safe Drop": 0.0})
		self.assertIn("Petty cash", result["data"]["reasons"]["Paid Out"])

	def test_another_business_cannot_list(self):
		with world(companies=("Shop B",)) as (_db, _saved, _doc):
			with self.assertRaises(frappe.PermissionError):
				shift_api.list_cash_movements("POS-OPE-0001")
