import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import pharmacy
from murzak_pos.api.pharmacy_rules import (
	add_to_remarks,
	check,
	clean_dispensing,
	dispensing_fields,
	is_complete,
	looks_like_antibiotic,
	prescription_default,
	register_rows,
	register_summary,
	remark,
	rx_codes,
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

	def test_review_gives_the_note_and_the_fields_together(self):
		db = MagicMock()
		db.get_value.return_value = "Amina"
		meta = MagicMock()
		meta.has_field.return_value = True
		with (
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "session", frappe._dict(user="amina@shop-a.test")),
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "get_all", return_value=["AMOX"]),
		):
			review = pharmacy.review_sale([{"item_code": "AMOX"}], FULL, False)
		self.assertIn("patient Jane Wanjiru", review["note"])
		self.assertEqual(review["fields"]["custom_rx_status"], "Complete")
		self.assertEqual(review["fields"]["custom_rx_dispensed_by"], "Amina")

	def test_the_fields_are_written_only_where_the_invoice_has_them(self):
		doc = MagicMock()
		doc.meta.has_field.side_effect = lambda name: name != "custom_rx_reference"
		pharmacy.write_dispensing_fields(doc, {"custom_rx_status": "Complete", "custom_rx_reference": "RX-1"})
		doc.set.assert_called_once_with("custom_rx_status", "Complete")
		pharmacy.write_dispensing_fields(doc, {})
		self.assertEqual(doc.set.call_count, 1)


class TestDispensingFields(unittest.TestCase):
	def test_a_complete_sale_keeps_every_part_of_the_record(self):
		fields = dispensing_fields(["AMOX", "DIAZ"], FULL, "Amina", "ok")
		self.assertEqual(fields["custom_rx_status"], "Complete")
		self.assertEqual(fields["custom_rx_items"], "AMOX, DIAZ")
		self.assertEqual(fields["custom_rx_patient"], "Jane Wanjiru")
		self.assertEqual(fields["custom_rx_prescriber"], "Dr Otieno")
		self.assertEqual(fields["custom_rx_reference"], "RX-1042")
		self.assertEqual(fields["custom_rx_dispensed_by"], "Amina")

	def test_an_offline_sale_without_details_is_marked_missing(self):
		fields = dispensing_fields(["AMOX"], None, "Amina", "flag")
		self.assertEqual(fields["custom_rx_status"], "Missing")
		self.assertEqual(fields["custom_rx_patient"], "")

	def test_a_sale_with_no_prescription_items_gets_no_fields(self):
		self.assertEqual(dispensing_fields([], FULL, "Amina", "ok"), {})


class TestAntibiotics(unittest.TestCase):
	def test_common_antibiotic_names_are_recognised_however_written(self):
		for name in (
			"Amoxicillin 500mg Capsules",
			"AMOXYCILLIN SYRUP",
			"Co-Trimoxazole 480mg",
			"Augmentin 625",
			"Cipro-floxacin",
			"Metronidazole (Flagyl) 400mg",
			"Antibiotic Ointment",
		):
			self.assertTrue(looks_like_antibiotic(name), name)

	def test_everyday_products_are_not_flagged(self):
		for name in (
			"Paracetamol Tablet",
			"Vitamin C Tablets",
			"Cough Syrup",
			"ORS Sachet",
			"Hand Sanitizer",
			"",
			None,
		):
			self.assertFalse(looks_like_antibiotic(name), name)

	def test_any_of_several_names_counts(self):
		self.assertTrue(looks_like_antibiotic("Product 12", "AMOXICILLIN-500"))
		self.assertFalse(looks_like_antibiotic(None, ""))

	def test_what_the_person_chose_always_wins(self):
		self.assertFalse(prescription_default(False, True, "Amoxicillin"))
		self.assertTrue(prescription_default(True, False, "Paracetamol"))

	def test_with_no_choice_only_a_pharmacys_antibiotic_starts_prescription_only(self):
		self.assertTrue(prescription_default(None, True, "Amoxicillin"))
		self.assertFalse(prescription_default(None, False, "Amoxicillin"))
		self.assertFalse(prescription_default(None, True, "Paracetamol"))


REGISTER_INVOICES = [
	{
		"name": "POS-1",
		"posting_date": "2026-11-12",
		"posting_time": "09:15:00.123",
		"is_return": 0,
		"custom_rx_status": "Complete",
		"custom_rx_items": "AMOX, DIAZ",
		"custom_rx_patient": "Jane",
		"custom_rx_prescriber": "Dr O",
		"custom_rx_reference": "RX-1",
		"custom_rx_dispensed_by": "Amina",
	},
	{
		"name": "POS-2",
		"posting_date": "2026-11-13",
		"posting_time": "08:00:00",
		"is_return": 0,
		"custom_rx_status": "Missing",
		"custom_rx_items": "AMOX",
	},
]
REGISTER_LINES = [
	{
		"parent": "POS-1",
		"item_code": "AMOX",
		"item_name": "Amoxicillin",
		"qty": 21,
		"uom": "Capsule",
		"warehouse": "Main",
	},
	{"parent": "POS-1", "item_code": "DIAZ", "item_name": "Diazepam", "qty": 10, "warehouse": "Branch"},
	{"parent": "POS-1", "item_code": "TEA", "item_name": "Tea", "qty": 1, "warehouse": "Main"},
	{
		"parent": "POS-2",
		"item_code": "AMOX",
		"item_name": "Amoxicillin",
		"qty": 14,
		"batch_no": "B7",
		"warehouse": "Main",
	},
	{"parent": "POS-9", "item_code": "AMOX", "qty": 1},
]


class TestDispensingRegister(unittest.TestCase):
	def test_only_prescription_items_of_listed_sales_are_shown_newest_first(self):
		rows = register_rows(REGISTER_INVOICES, REGISTER_LINES)
		self.assertEqual(
			[(r["invoice"], r["item_code"]) for r in rows],
			[("POS-2", "AMOX"), ("POS-1", "AMOX"), ("POS-1", "DIAZ")],
		)
		self.assertEqual(rows[0]["batch_no"], "B7")
		self.assertEqual(rows[0]["status"], "Missing")
		self.assertEqual(rows[1]["patient"], "Jane")
		self.assertEqual(rows[1]["time"], "09:15:00")

	def test_a_store_shows_only_what_it_dispensed(self):
		rows = register_rows(REGISTER_INVOICES, REGISTER_LINES, "Branch")
		self.assertEqual([r["item_code"] for r in rows], ["DIAZ"])

	def test_the_summary_counts_sales_lines_and_missing_records(self):
		self.assertEqual(
			register_summary(register_rows(REGISTER_INVOICES, REGISTER_LINES)),
			{"sales": 2, "lines": 3, "missing": 1},
		)

	def test_item_codes_are_read_back_once_each(self):
		self.assertEqual(rx_codes("AMOX, DIAZ, AMOX, "), ["AMOX", "DIAZ"])
		self.assertEqual(rx_codes(None), [])


class TestGetDispensingRegister(unittest.TestCase):
	def run_register(self, **kwargs):
		meta = MagicMock()
		meta.has_field.return_value = True
		calls = []

		def get_all(doctype, **kw):
			calls.append((doctype, kw))
			if doctype == "POS Invoice":
				return [frappe._dict(REGISTER_INVOICES[0])]
			if doctype == "POS Invoice Item":
				return [frappe._dict(line) for line in REGISTER_LINES if line["parent"] == "POS-1"]
			return []

		with (
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(pharmacy.role_tiers, "require_access") as require_access,
			patch.object(pharmacy, "resolve_company", return_value="Duka Ltd"),
			patch.object(pharmacy, "ensure_record_access"),
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "get_all", side_effect=get_all),
		):
			result = pharmacy.get_dispensing_register(**kwargs)
		return result, calls, require_access

	def test_only_the_owner_and_managers_may_read_it_because_it_names_patients(self):
		_result, _calls, require_access = self.run_register(from_date="2026-11-01", to_date="2026-11-13")
		require_access.assert_called_once_with("all_sales")

	def test_merged_shift_invoices_are_left_out_so_nothing_is_counted_twice(self):
		result, calls, _ = self.run_register(from_date="2026-11-01", to_date="2026-11-13")
		filters = {
			doctype: kw["filters"] for doctype, kw in calls if doctype in ("POS Invoice", "Sales Invoice")
		}
		self.assertEqual(filters["Sales Invoice"]["is_consolidated"], 0)
		self.assertNotIn("is_consolidated", filters["POS Invoice"])
		self.assertEqual(result["data"]["summary"], {"sales": 1, "lines": 2, "missing": 0})

	def test_a_backwards_or_overlong_period_is_refused(self):
		for dates in (("2026-11-13", "2026-11-01"), ("2025-01-01", "2026-11-13")):
			with self.assertRaises(frappe.ValidationError, msg=dates):
				self.run_register(from_date=dates[0], to_date=dates[1])


if __name__ == "__main__":
	unittest.main()
