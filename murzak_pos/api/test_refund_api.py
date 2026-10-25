# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for paying a credit note back (refund_api.py) that need no database: what is refused, that cash needs an open
shift and comes out of it as a cash movement, that other ways of paying do not touch the drawer, and that a refused
cash movement undoes the payment. Every Frappe call is replaced.
"""

import unittest
from unittest.mock import MagicMock, patch

from murzak_pos.api import refund_api


def credit_note(outstanding=-500.0, is_return=1, docstatus=1):
	doc = MagicMock()
	doc.name = "ACC-SINV-1"
	doc.company = "Shop A"
	doc.is_return = is_return
	doc.docstatus = docstatus
	doc.outstanding_amount = outstanding
	return doc


class PayOutCase(unittest.TestCase):
	def run_pay(self, doc=None, exists=True, mode_type="Cash", mode_exists=True, shift="POS-OPE-1", **kwargs):
		doc = doc or credit_note()
		db = MagicMock()
		db.exists.side_effect = lambda doctype, name=None: (
			exists if doctype == "Sales Invoice" else mode_exists
		)
		db.get_value.return_value = mode_type
		entry = MagicMock()
		entry.name = "ACC-PAY-1"
		movement = MagicMock(return_value={"success": True, "data": {"movement": {"id": "M1"}}})
		with (
			patch.object(refund_api.frappe, "db", db, create=True),
			patch.object(refund_api.frappe, "get_doc", return_value=doc),
			patch.object(refund_api, "_open_shift", return_value=shift),
			patch.object(refund_api, "_payment_entry", return_value=entry) as pay_entry,
			patch.object(refund_api.shift_api, "record_movement", movement),
		):
			result = refund_api.pay_out("ACC-SINV-1", **kwargs)
		return result, db, pay_entry, movement

	def test_a_credit_note_that_does_not_exist_is_said_so(self):
		result, _db, pay_entry, _mv = self.run_pay(exists=False)
		self.assertEqual(result["error_type"], "not_found")
		pay_entry.assert_not_called()

	def test_a_sale_is_not_a_return(self):
		result, _db, pay_entry, _mv = self.run_pay(doc=credit_note(is_return=0))
		self.assertEqual(result["error_type"], "not_a_return")
		pay_entry.assert_not_called()

	def test_a_draft_return_waiting_for_approval_cannot_be_paid(self):
		result, _db, pay_entry, _mv = self.run_pay(doc=credit_note(docstatus=0))
		self.assertEqual(result["error_type"], "not_a_return")
		pay_entry.assert_not_called()

	def test_a_return_already_paid_is_refused(self):
		result, _db, pay_entry, _mv = self.run_pay(doc=credit_note(outstanding=0))
		self.assertEqual(result["error_type"], "nothing_to_refund")
		pay_entry.assert_not_called()

	def test_more_than_is_owed_is_refused(self):
		result, _db, pay_entry, _mv = self.run_pay(amount=900)
		self.assertEqual(result["error_type"], "more_than_owed")
		pay_entry.assert_not_called()

	def test_a_way_of_paying_the_business_does_not_have_is_refused(self):
		result, _db, pay_entry, _mv = self.run_pay(mode_exists=False, mode_of_payment="Gold coins")
		self.assertEqual(result["error_type"], "bad_mode")
		pay_entry.assert_not_called()

	def test_cash_needs_an_open_shift(self):
		result, _db, pay_entry, movement = self.run_pay(shift=None)
		self.assertEqual(result["error_type"], "open_shift_required")
		pay_entry.assert_not_called()
		movement.assert_not_called()

	def test_cash_comes_out_of_the_shift_as_a_movement_named_for_the_credit_note(self):
		result, db, pay_entry, movement = self.run_pay()
		self.assertTrue(result["success"], result)
		pay_entry.assert_called_once()
		self.assertEqual(pay_entry.call_args.args[1:], (500.0, "Cash"))
		args = movement.call_args.args
		self.assertEqual(args[:5], ("POS-OPE-1", "Paid Out", 500.0, "Customer refund", "ACC-SINV-1"))
		self.assertEqual(args[5], "refund-ACC-SINV-1-ACC-PAY-1")
		self.assertTrue(movement.call_args.kwargs["system"])
		self.assertEqual(result["data"]["cash_movement"], {"id": "M1"})
		self.assertEqual(result["data"]["shift"], "POS-OPE-1")
		db.commit.assert_called()

	def test_a_part_payment_pays_the_part(self):
		result, _db, pay_entry, movement = self.run_pay(amount="200")
		self.assertTrue(result["success"], result)
		self.assertEqual(pay_entry.call_args.args[1], 200.0)
		self.assertEqual(movement.call_args.args[2], 200.0)

	def test_a_drawer_too_short_undoes_the_payment(self):
		doc = credit_note()
		db = MagicMock()
		db.exists.return_value = True
		db.get_value.return_value = "Cash"
		refused = {
			"success": False,
			"error_type": "more_than_in_till",
			"message": "That is more than the till holds.",
		}
		with (
			patch.object(refund_api.frappe, "db", db, create=True),
			patch.object(refund_api.frappe, "get_doc", return_value=doc),
			patch.object(refund_api, "_open_shift", return_value="POS-OPE-1"),
			patch.object(refund_api, "_payment_entry", return_value=MagicMock(name="PE")),
			patch.object(refund_api.shift_api, "record_movement", return_value=refused),
		):
			result = refund_api.pay_out("ACC-SINV-1")
		self.assertEqual(result["error_type"], "more_than_in_till")
		db.rollback.assert_called_once()
		db.commit.assert_not_called()

	def test_another_way_of_paying_does_not_touch_the_drawer(self):
		result, _db, pay_entry, movement = self.run_pay(mode_type="Phone", mode_of_payment="M-Pesa")
		self.assertTrue(result["success"], result)
		pay_entry.assert_called_once()
		self.assertEqual(pay_entry.call_args.args[2], "M-Pesa")
		movement.assert_not_called()
		self.assertIsNone(result["data"]["shift"])

	def test_a_method_with_no_account_is_said_so_and_undone(self):
		db = MagicMock()
		db.exists.return_value = True
		db.get_value.return_value = "Phone"
		from murzak_pos.api import refund_rules

		with (
			patch.object(refund_api.frappe, "db", db, create=True),
			patch.object(refund_api.frappe, "get_doc", return_value=credit_note()),
			patch.object(
				refund_api, "_payment_entry", side_effect=refund_rules.RefundError("no_account", "M-Pesa")
			),
		):
			result = refund_api.pay_out("ACC-SINV-1", mode_of_payment="M-Pesa")
		self.assertEqual(result["error_type"], "no_account")
		db.rollback.assert_called_once()
