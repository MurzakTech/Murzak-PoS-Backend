# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for two purchasing calls that crashed every time they were used:

- cancel_purchase_order built its reply inside its own description text, so the reply never existed and every
  cancellation ended in an error (which also undid the cancellation);
- the stock receipt made from a goods received note checked its date with a name that two imports had made the
  wrong kind of thing, so it raised an error before making the stock entry.

No database is needed: every Frappe call is mocked.
"""

import datetime
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import purchase, stock


def _order(docstatus=1, status="Cancelled"):
	order = MagicMock()
	order.name = "PO-0001"
	order.docstatus = docstatus
	order.status = status
	return order


class TestCancellingAPurchaseOrder(unittest.TestCase):
	def cancel(self, order=None, exists=(True, None, None), **kwargs):
		"""Run the call with the order found; `exists` answers: the order, a receipt line, an invoice line."""
		answers = iter(exists)
		self.db = MagicMock()
		self.db.exists.side_effect = lambda *a, **k: next(answers)
		self.order = order or _order()
		with (
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "get_doc", create=True, return_value=self.order),
			patch.object(frappe, "log_error", create=True),
			patch.object(frappe, "get_traceback", create=True, return_value="trace"),
			patch.object(stock, "ensure_record_access"),
		):
			function = getattr(stock.cancel_purchase_order, "__wrapped__", stock.cancel_purchase_order)
			return function("PO-0001", **kwargs)

	def test_a_submitted_order_is_cancelled_and_the_reply_says_so(self):
		reply = self.cancel()
		self.order.cancel.assert_called_once_with()
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"], {"purchase_order": "PO-0001", "docstatus": 1, "status": "Cancelled"})
		self.db.rollback.assert_not_called()

	def test_the_reason_is_kept_as_a_comment(self):
		self.cancel(reason="supplier closed")
		self.order.add_comment.assert_called_once()
		self.assertIn("supplier closed", self.order.add_comment.call_args.args[1])

	def test_an_order_already_cancelled_is_a_success_and_is_left_alone(self):
		reply = self.cancel(order=_order(docstatus=2))
		self.assertTrue(reply["success"])
		self.order.cancel.assert_not_called()

	def test_a_draft_cannot_be_cancelled_and_is_explained(self):
		reply = self.cancel(order=_order(docstatus=0))
		self.assertFalse(reply["success"])
		self.assertIn("Only submitted Purchase Orders can be cancelled", reply["message"])
		self.order.cancel.assert_not_called()
		self.db.rollback.assert_called_once()

	def test_an_order_with_goods_received_or_a_bill_cannot_be_cancelled(self):
		for exists in ((True, "PR-1", None), (True, None, "PINV-1")):
			with self.subTest(exists=exists):
				reply = self.cancel(exists=exists)
				self.assertFalse(reply["success"])
				self.assertIn("has submitted receipts or invoices", reply["message"])
				self.order.cancel.assert_not_called()

	def test_an_order_that_does_not_exist_is_said_plainly(self):
		reply = self.cancel(exists=(None,))
		self.assertFalse(reply["success"])
		self.assertIn("Purchase Order not found", reply["message"])

	def test_an_unexpected_failure_comes_back_as_a_reply_not_a_crash(self):
		order = _order()
		order.cancel.side_effect = RuntimeError("database gone")
		reply = self.cancel(order=order)
		self.assertFalse(reply["success"])
		self.assertEqual(reply["errors"], ["database gone"])
		self.assertIn("Unexpected error", reply["message"])
		self.db.rollback.assert_called_once()


class TestThePostingDateOfAStockReceipt(unittest.TestCase):
	def test_a_date_a_datetime_and_text_all_become_a_plain_day(self):
		self.assertEqual(purchase._iso_day(datetime.date(2026, 10, 13)), "2026-10-13")
		self.assertEqual(purchase._iso_day(datetime.datetime(2026, 10, 13, 9, 30)), "2026-10-13")
		self.assertEqual(purchase._iso_day("2026-10-13"), "2026-10-13")

	def test_a_missing_date_stays_missing(self):
		self.assertIsNone(purchase._iso_day(None))
		self.assertEqual(purchase._iso_day(""), "")

	def test_the_call_no_longer_checks_the_date_with_the_name_that_broke_it(self):
		# `from datetime import datetime` hides the module, so `datetime.date` is a method and isinstance() refuses it
		with self.assertRaises(TypeError):
			isinstance(1, purchase.datetime.date)
		self.assertNotIn("isinstance(grn.posting_date, datetime.date)", open(purchase.__file__).read())


if __name__ == "__main__":
	unittest.main()
