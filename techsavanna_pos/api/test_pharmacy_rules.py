import unittest
from unittest.mock import MagicMock, patch

import frappe

from techsavanna_pos.api import pharmacy
from techsavanna_pos.api.pharmacy_rules import (
	add_to_remarks,
	check,
	clean_dispensing,
	is_complete,
	remark,
)

FULL = {"patient": "Jane Wanjiru", "prescriber": "Dr Otieno", "reference": "RX-1042"}


class TestCleanDispensing(unittest.TestCase):
	def test_a_record_is_tidied_and_cut_to_length(self):
		record = clean_dispensing(
			{"patient": "  Jane   Wanjiru ", "prescriber": "Dr  Otieno", "reference": "x" * 100}
		)
		self.assertEqual(record["patient"], "Jane Wanjiru")
		self.assertEqual(record["prescriber"], "Dr Otieno")
		self.assertEqual(len(record["reference"]), 60)

	def test_it_may_arrive_as_json_text(self):
		self.assertEqual(clean_dispensing('{"patient": "Jane", "prescriber": "Dr O"}')["prescriber"], "Dr O")

	def test_nothing_usable_is_none(self):
		for raw in (None, "", "{not json", "[1]", 5, {}, {"patient": "  ", "prescriber": None}):
			self.assertIsNone(clean_dispensing(raw), raw)

	def test_unknown_fields_are_dropped(self):
		self.assertEqual(
			set(clean_dispensing({"patient": "J", "diagnosis": "secret"})),
			{"patient", "prescriber", "reference"},
		)


class TestIsComplete(unittest.TestCase):
	def test_a_patient_and_a_prescriber_are_both_needed_but_the_reference_is_optional(self):
		self.assertTrue(is_complete({"patient": "J", "prescriber": "Dr O", "reference": ""}))
		self.assertFalse(is_complete({"patient": "J", "prescriber": "", "reference": "1"}))
		self.assertFalse(is_complete({"patient": "", "prescriber": "Dr O"}))
		self.assertFalse(is_complete(None))


class TestCheck(unittest.TestCase):
	def test_a_sale_with_no_prescription_items_never_needs_a_record(self):
		self.assertEqual(check([], None, False), "ok")
		self.assertEqual(check([], None, True), "ok")

	def test_a_sale_with_a_complete_record_is_fine(self):
		self.assertEqual(check(["AMOX"], FULL, False), "ok")

	def test_a_sale_without_one_is_refused(self):
		self.assertEqual(check(["AMOX"], None, False), "refuse")
		self.assertEqual(check(["AMOX"], {"patient": "J", "prescriber": ""}, False), "refuse")

	def test_a_sale_already_made_offline_is_flagged_not_refused_because_the_medicine_has_gone(self):
		self.assertEqual(check(["AMOX"], None, True), "flag")
		self.assertEqual(check(["AMOX"], FULL, True), "ok")


class TestRemark(unittest.TestCase):
	def test_no_prescription_items_writes_nothing(self):
		self.assertIsNone(remark([], FULL, "Amina", "ok"))

	def test_the_record_is_written_so_it_can_be_read_back(self):
		self.assertEqual(
			remark(["Amoxicillin", "Diazepam"], FULL, "Amina", "ok"),
			"Prescription-only medicines (Amoxicillin, Diazepam): patient Jane Wanjiru; prescriber Dr Otieno; "
			"prescription ref RX-1042; dispensed by Amina.",
		)

	def test_the_reference_and_the_dispenser_are_left_out_when_not_known(self):
		note = remark(["Amoxicillin"], {"patient": "Jane", "prescriber": "Dr O"}, "", "ok")
		self.assertEqual(note, "Prescription-only medicines (Amoxicillin): patient Jane; prescriber Dr O.")

	def test_a_sale_flagged_as_missing_says_so_plainly(self):
		note = remark(["Amoxicillin"], None, "Amina", "flag")
		self.assertIn("PRESCRIPTION DETAILS MISSING", note)
		self.assertIn("Amoxicillin", note)


class TestAddToRemarks(unittest.TestCase):
	def test_the_note_is_added_after_what_is_already_there_and_never_twice(self):
		self.assertEqual(add_to_remarks("Sold offline.", "Rx note."), "Sold offline.\nRx note.")
		self.assertEqual(add_to_remarks("Sold offline.\nRx note.", "Rx note."), "Sold offline.\nRx note.")
		self.assertEqual(add_to_remarks(None, "Rx note."), "Rx note.")
		self.assertEqual(add_to_remarks("Existing", None), "Existing")


class TestSaleRemark(unittest.TestCase):
	"""The part that asks the database which items are flagged."""

	def sale(self, items, dispensing=None, offline=False, flagged=("AMOX",), has_field=True):
		db = MagicMock()
		db.get_value.return_value = "Amina"
		meta = MagicMock()
		meta.has_field.return_value = has_field
		with (
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "session", frappe._dict(user="amina@shop-a.test")),
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "get_all", return_value=list(flagged)) as get_all,
		):
			self.get_all = get_all
			return pharmacy.sale_remark([{"item_code": c} for c in items], dispensing, offline)

	def test_a_sale_with_no_flagged_items_is_untouched(self):
		self.assertIsNone(self.sale(["TEA"], flagged=()))

	def test_a_sale_of_a_flagged_item_without_a_record_is_refused_with_a_plain_message(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.sale(["AMOX", "TEA"])
		self.assertIn("prescription-only medicines (AMOX)", str(caught.exception))
		self.assertIn("patient", str(caught.exception))

	def test_a_sale_with_a_record_writes_it_with_the_signed_in_users_name(self):
		note = self.sale(["AMOX"], dispensing=FULL)
		self.assertIn("patient Jane Wanjiru", note)
		self.assertIn("dispensed by Amina", note)

	def test_an_offline_sale_is_flagged_not_refused(self):
		self.assertIn("PRESCRIPTION DETAILS MISSING", self.sale(["AMOX"], offline=True))

	def test_only_the_items_on_the_sale_are_asked_about_once_each(self):
		self.sale(["AMOX", "AMOX", "TEA", None, ""], dispensing=FULL)
		self.assertEqual(self.get_all.call_args.kwargs["filters"]["item_code"], ["in", ["AMOX", "TEA"]])
		self.assertEqual(self.get_all.call_args.kwargs["filters"]["custom_prescription_only"], 1)

	def test_a_site_without_the_flag_yet_sells_as_before_and_asks_nothing(self):
		self.assertIsNone(self.sale(["AMOX"], has_field=False))
		self.get_all.assert_not_called()


if __name__ == "__main__":
	unittest.main()
