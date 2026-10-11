# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for sending goods on a Purchase Receipt (GRN) back to the supplier.

No database is needed. ERPNext's own "make a return" step is replaced by a stand-in, and each test says
what the receipt holds. What is checked here is this app's part: which lines go back, in what quantity,
and that bad requests are refused without anything being created.
"""

import importlib
import pkgutil
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import murzak_pos
from murzak_pos.api import purchase_receipt


def receipt_line(name, item_code, qty, returned_qty=0, warehouse="Stores - A"):
	return SimpleNamespace(
		name=name, item_code=item_code, qty=qty, returned_qty=returned_qty, warehouse=warehouse
	)


def return_line(source_line, item_code, remaining, warehouse="Stores - A"):
	"""
	A line as ERPNext builds it on a new return: linked to the receipt line, and holding what can still
	be returned as a negative number (0 once the line has been fully returned).
	"""
	return SimpleNamespace(
		purchase_receipt_item=source_line,
		item_code=item_code,
		qty=-remaining,
		received_qty=-remaining,
		rejected_qty=1,
		warehouse=warehouse,
	)


class FakeReturn:
	"""The return document ERPNext hands back, before it is saved."""

	name = "MAT-PRE-RET-0001"
	remarks = None

	def __init__(self, lines):
		self.items = lines
		self.inserted = False
		self.submitted = False

	def set(self, fieldname, value):
		setattr(self, fieldname, value)

	def insert(self, **kwargs):
		self.inserted = True

	def submit(self):
		self.submitted = True


class ReturnCase(unittest.TestCase):
	def send_back(self, lines, items, *, docstatus=1, status="To Bill", is_return=0, reason=None):
		"""Runs the call on a receipt holding `lines`. Returns (response, the return document made)."""
		receipt = SimpleNamespace(
			name="MAT-PRE-0001", docstatus=docstatus, status=status, is_return=is_return, items=lines
		)
		made = FakeReturn(
			[
				return_line(line.name, line.item_code, line.qty - line.returned_qty, line.warehouse)
				for line in lines
			]
		)

		db = MagicMock()
		db.exists.return_value = True
		with (
			patch.object(purchase_receipt, "ensure_record_access"),
			patch.object(purchase_receipt, "make_return_doc", return_value=made),
			patch.object(purchase_receipt.frappe, "get_doc", return_value=receipt),
			patch.object(purchase_receipt.frappe, "db", db),
			patch.object(purchase_receipt.frappe, "log_error"),
		):
			response = purchase_receipt.reject_return_items("MAT-PRE-0001", items, reason)
		return response, made, db

	def test_returns_only_the_lines_and_quantities_asked_for(self):
		lines = [receipt_line("r1", "PEN", 10), receipt_line("r2", "INK", 5)]
		response, made, _db = self.send_back(lines, [{"item_code": "PEN", "qty": 4}])

		self.assertTrue(response["success"], response)
		self.assertEqual([line.item_code for line in made.items], ["PEN"])
		# Negative, because stock leaves; and the "rejected" amount ERPNext pre-filled is cleared
		self.assertEqual(made.items[0].qty, -4)
		self.assertEqual(made.items[0].received_qty, -4)
		self.assertEqual(made.items[0].rejected_qty, 0)
		self.assertTrue(made.inserted and made.submitted)
		self.assertEqual(response["data"]["return_document"], "MAT-PRE-RET-0001")
		self.assertEqual(response["data"]["returned_items"][0]["qty"], 4)

	def test_the_reason_is_kept_on_the_return(self):
		_response, made, _db = self.send_back(
			[receipt_line("r1", "PEN", 10)], [{"item_code": "PEN", "qty": 1}], reason="Damaged in transit"
		)
		self.assertEqual(made.remarks, "Damaged in transit")

	def test_items_may_be_sent_as_json_text(self):
		response, made, _db = self.send_back(
			[receipt_line("r1", "PEN", 10)], '[{"item_code": "PEN", "qty": 2}]'
		)
		self.assertTrue(response["success"], response)
		self.assertEqual(made.items[0].qty, -2)

	def test_a_quantity_is_shared_across_lines_of_the_same_item(self):
		lines = [receipt_line("r1", "PEN", 3), receipt_line("r2", "PEN", 5)]
		response, made, _db = self.send_back(lines, [{"item_code": "PEN", "qty": 6}])

		self.assertTrue(response["success"], response)
		self.assertEqual([line.qty for line in made.items], [-3, -3])

	def test_earlier_returns_reduce_what_can_go_back(self):
		lines = [receipt_line("r1", "PEN", 10, returned_qty=8)]

		response, made, _db = self.send_back(lines, [{"item_code": "PEN", "qty": 3}])
		self.assertFalse(response["success"])
		self.assertFalse(made.inserted)

		response, _made, _db = self.send_back(lines, [{"item_code": "PEN", "qty": 2}])
		self.assertTrue(response["success"], response)

	def test_a_fully_returned_line_cannot_be_returned_again(self):
		lines = [receipt_line("r1", "PEN", 10, returned_qty=10)]
		response, made, _db = self.send_back(lines, [{"item_code": "PEN", "qty": 1}])
		self.assertFalse(response["success"])
		self.assertIn("Quantity left to return: 0", response["message"])
		self.assertFalse(made.inserted)

	def test_the_refusal_says_how_much_can_still_go_back(self):
		lines = [receipt_line("r1", "PEN", 10, returned_qty=8)]
		response, _made, _db = self.send_back(lines, [{"item_code": "PEN", "qty": 3}])
		self.assertIn("Quantity left to return: 2", response["message"])

	def test_the_whole_received_quantity_can_go_back(self):
		response, made, _db = self.send_back(
			[receipt_line("r1", "PEN", 10)], [{"item_code": "PEN", "qty": 10}]
		)
		self.assertTrue(response["success"], response)
		self.assertEqual(made.items[0].qty, -10)

	def test_more_than_was_received_is_refused(self):
		response, made, db = self.send_back(
			[receipt_line("r1", "PEN", 10)], [{"item_code": "PEN", "qty": 11}]
		)
		self.assertFalse(response["success"])
		self.assertIn("more than the received quantity", response["message"])
		self.assertFalse(made.inserted)
		db.rollback.assert_called()

	def test_an_item_not_on_the_receipt_is_refused(self):
		response, made, _db = self.send_back(
			[receipt_line("r1", "PEN", 10)], [{"item_code": "INK", "qty": 1}]
		)
		self.assertFalse(response["success"])
		self.assertIn("not found in Purchase Receipt", response["message"])
		self.assertFalse(made.inserted)

	def test_zero_negative_and_missing_quantities_are_refused(self):
		for qty in (0, -1, None):
			with self.subTest(qty=qty):
				response, made, _db = self.send_back(
					[receipt_line("r1", "PEN", 10)], [{"item_code": "PEN", "qty": qty}]
				)
				self.assertFalse(response["success"])
				self.assertFalse(made.inserted)

	def test_an_empty_request_is_refused(self):
		response, made, _db = self.send_back([receipt_line("r1", "PEN", 10)], [])
		self.assertFalse(response["success"])
		self.assertFalse(made.inserted)

	def test_receipts_that_cannot_take_a_return_are_refused(self):
		cases = {
			"draft": {"docstatus": 0},
			"cancelled": {"docstatus": 2},
			"closed": {"status": "Closed"},
			"itself a return": {"is_return": 1},
		}
		for label, state in cases.items():
			with self.subTest(label):
				response, made, _db = self.send_back(
					[receipt_line("r1", "PEN", 10)], [{"item_code": "PEN", "qty": 1}], **state
				)
				self.assertFalse(response["success"])
				self.assertFalse(made.inserted)

	def test_an_unknown_receipt_is_refused(self):
		db = MagicMock()
		db.exists.return_value = False
		with (
			patch.object(purchase_receipt, "ensure_record_access"),
			patch.object(purchase_receipt.frappe, "db", db),
		):
			response = purchase_receipt.reject_return_items("NOPE", [{"item_code": "PEN", "qty": 1}])
		self.assertFalse(response["success"])
		self.assertIn("not found", response["message"])

	def test_an_unexpected_failure_is_reported_and_rolled_back(self):
		receipt = SimpleNamespace(
			name="R", docstatus=1, status="To Bill", is_return=0, items=[receipt_line("r1", "PEN", 10)]
		)
		db = MagicMock()
		db.exists.return_value = True
		with (
			patch.object(purchase_receipt, "ensure_record_access"),
			patch.object(purchase_receipt, "make_return_doc", side_effect=RuntimeError("boom")),
			patch.object(purchase_receipt.frappe, "get_doc", return_value=receipt),
			patch.object(purchase_receipt.frappe, "db", db),
			patch.object(purchase_receipt.frappe, "log_error") as log_error,
		):
			response = purchase_receipt.reject_return_items("R", [{"item_code": "PEN", "qty": 1}])
		self.assertFalse(response["success"])
		self.assertEqual(response["errors"], ["boom"])
		db.rollback.assert_called()
		log_error.assert_called_once()


class EveryModuleLoadsCase(unittest.TestCase):
	def test_every_module_in_the_app_can_be_imported(self):
		"""
		A module that cannot be imported breaks every endpoint in it, and nothing else notices until
		someone calls it. (The goods-return module sat broken like this because no test loaded it.)

		Only a wrong or missing import fails this test. Other errors are left alone: they come from the
		bare test setup (no site folders, no database), not from the code.
		"""
		broken = []
		for module in pkgutil.walk_packages(murzak_pos.__path__, "murzak_pos."):
			try:
				importlib.import_module(module.name)
			except (ImportError, SyntaxError, NameError) as error:
				broken.append(f"{module.name}: {type(error).__name__}: {error}")
			except Exception:
				pass
		self.assertEqual(broken, [], "These modules cannot be imported:\n" + "\n".join(broken))


if __name__ == "__main__":
	unittest.main()
