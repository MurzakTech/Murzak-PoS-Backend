import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import batch_rules as rules
from murzak_pos.api import batches

TODAY = date(2026, 11, 13)
set_batch_tracking = getattr(batches.set_batch_tracking, "__wrapped__", batches.set_batch_tracking)


class TestBatchRules(unittest.TestCase):
	def test_a_batch_number_is_tidied_as_printed(self):
		self.assertEqual(rules.clean_batch_no("  bx-2291 / a "), "BX-2291 / A")
		self.assertEqual(rules.clean_batch_no("AB<script>12"), "ABSCRIPT12")
		self.assertEqual(len(rules.clean_batch_no("x" * 100)), rules.MAX_BATCH)
		self.assertEqual(rules.clean_batch_no(None), "")

	def test_expiry_dates_are_read_in_both_ways_people_write_them(self):
		self.assertEqual(rules.parse_expiry("2027-03-31"), date(2027, 3, 31))
		self.assertEqual(rules.parse_expiry("31/03/2027"), date(2027, 3, 31))
		self.assertIsNone(rules.parse_expiry("31/02/2027"))
		self.assertIsNone(rules.parse_expiry("soon"))

	def test_missing_impossible_and_past_expiry_dates_are_refused(self):
		self.assertEqual(rules.check_expiry("", TODAY), (None, rules.MISSING))
		self.assertEqual(rules.check_expiry("2027-13-01", TODAY), (None, rules.BAD))
		self.assertEqual(rules.check_expiry("2026-11-12", TODAY), (None, rules.PAST))
		# Stock expiring today may still be received (it is sold or written off today)
		self.assertEqual(rules.check_expiry("2026-11-13", TODAY), (TODAY, None))

	def test_a_batch_left_blank_is_named_from_the_product_and_expiry(self):
		self.assertEqual(rules.auto_batch_no("amox-500", date(2027, 3, 31)), "AMOX-500-EXP-2027-03-31")
		self.assertLessEqual(len(rules.auto_batch_no("X" * 200, date(2027, 3, 31))), rules.MAX_BATCH)

	def test_the_first_line_naming_a_batch_is_used_for_each_product(self):
		found = rules.batch_lines(
			[
				{"item_code": "AMOX", "qty": 5, "batch_no": "b1", "expiry_date": "2027-03-31"},
				{"item_code": "AMOX", "batch_no": "b2"},
				{"item_code": "TEA", "qty": 1},
				"junk",
				{"item_code": ""},
			]
		)
		self.assertEqual(found, {"AMOX": {"batch_no": "B1", "expiry_date": "2027-03-31"}})


class Base(unittest.TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.items = {
			"AMOX": {"has_batch_no": 1, "has_expiry_date": 1},
			"TEA": {"has_batch_no": 0, "has_expiry_date": 0},
		}
		self.batch_rows = {}

		def get_value(doctype, name, fields=None, *args, **kwargs):
			if doctype == "Item" and fields == "item_name":
				return {"AMOX": "Amoxicillin 500mg"}.get(name, name)
			if doctype == "Item":
				return frappe._dict(self.items.get(name, {}))
			if doctype == "Batch":
				row = self.batch_rows.get(name)
				return frappe._dict(row) if row else None
			return None

		self.db.get_value.side_effect = get_value
		self.get_doc = MagicMock()
		self.get_doc.return_value.name = "NEW-BATCH"
		for p in (
			patch.object(frappe, "db", self.db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(batches, "nairobi_today", return_value=TODAY),
			patch.object(batches.frappe, "get_doc", self.get_doc),
		):
			p.start()
		self.addCleanup(patch.stopall)


class TestApplyToRow(Base):
	def test_a_product_that_does_not_track_batches_is_left_alone(self):
		row = {"item_code": "TEA"}
		batches.apply_to_row(row, "TEA", None)
		self.assertEqual(row, {"item_code": "TEA"})

	def test_a_tracked_medicine_gets_a_new_batch_with_its_expiry(self):
		row = {"item_code": "AMOX"}
		batches.apply_to_row(row, "AMOX", {"batch_no": "bx-1", "expiry_date": "2027-03-31"})
		self.assertEqual(row["batch_no"], "NEW-BATCH")
		self.assertEqual(row["use_serial_batch_fields"], 1)
		made = self.get_doc.call_args.args[0]
		self.assertEqual(
			(made["batch_id"], made["item"], made["expiry_date"]), ("BX-1", "AMOX", date(2027, 3, 31))
		)

	def test_a_batch_already_received_is_used_again(self):
		self.batch_rows["BX-1"] = {"item": "AMOX", "expiry_date": date(2027, 3, 31)}
		row = {}
		batches.apply_to_row(row, "AMOX", {"batch_no": "BX-1", "expiry_date": "2027-03-31"})
		self.assertEqual(row["batch_no"], "BX-1")
		self.get_doc.assert_not_called()

	def test_a_batch_number_of_another_product_is_refused(self):
		self.batch_rows["BX-1"] = {"item": "PARA", "expiry_date": None}
		with self.assertRaises(frappe.ValidationError):
			batches.apply_to_row({}, "AMOX", {"batch_no": "BX-1", "expiry_date": "2027-03-31"})

	def test_no_expiry_or_an_expired_one_is_refused_in_plain_words(self):
		for typed, words in (
			({}, "Enter the expiry date"),
			({"expiry_date": "2026-01-01"}, "already expired"),
		):
			with self.assertRaises(frappe.ValidationError) as caught:
				batches.apply_to_row({}, "AMOX", typed)
			self.assertIn(words, str(caught.exception))
			self.assertIn("Amoxicillin 500mg", str(caught.exception))
		self.get_doc.assert_not_called()

	def test_check_lines_refuses_before_anything_is_made(self):
		with self.assertRaises(frappe.ValidationError):
			batches.check_lines(["TEA", "AMOX"], {"TEA": {}})
		batches.check_lines(["TEA", "AMOX"], {"AMOX": {"expiry_date": "31/03/2027"}})
		self.get_doc.assert_not_called()


class TestSetBatchTracking(Base):
	def item(self, **values):
		doc = MagicMock()
		doc.configure_mock(
			**{
				"has_batch_no": 0,
				"has_expiry_date": 0,
				"is_stock_item": 1,
				"item_name": "Amoxicillin",
				**values,
			}
		)
		return doc

	def switch(self, doc, on=True, history=False):
		self.db.exists.return_value = history
		with (
			patch.object(batches, "ensure_record_access"),
			patch("murzak_pos.api.access_control.require_catalogue_role"),
			patch("murzak_pos.api.payment_gateway_common.resolve_company", return_value="Duka"),
			patch.object(batches.frappe, "get_doc", return_value=doc),
			patch.object(batches, "relax_etims_mandatory"),
		):
			return set_batch_tracking("AMOX", on)

	def test_a_new_product_is_switched_on_without_automatic_batch_numbers(self):
		doc = self.item()
		self.switch(doc, "1")
		self.assertEqual((doc.has_batch_no, doc.has_expiry_date, doc.create_new_batch), (1, 1, 0))
		doc.save.assert_called_once()

	def test_a_product_with_history_is_refused_in_plain_words(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.switch(self.item(), "true", history=True)
		self.assertIn("already been sold or received", str(caught.exception))

	def test_a_service_has_no_batches(self):
		with self.assertRaises(frappe.ValidationError):
			self.switch(self.item(is_stock_item=0), True)

	def test_nothing_to_change_saves_nothing(self):
		doc = self.item(has_batch_no=1, has_expiry_date=1)
		self.switch(doc, "on", history=True)
		doc.save.assert_not_called()


if __name__ == "__main__":
	unittest.main()
