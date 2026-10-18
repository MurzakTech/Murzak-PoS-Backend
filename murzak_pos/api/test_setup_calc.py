# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the plain rules of the first-time set-up (`setup_calc`). They need no database."""

import unittest

from murzak_pos.api import setup_calc as calc


class TestKinds(unittest.TestCase):
	def test_the_four_answers_are_understood_however_they_are_typed(self):
		for sent, expected in (
			("pharmacy", "pharmacy"),
			(" Retail ", "retail"),
			("SERVICES", "services"),
			("other", "other"),
		):
			with self.subTest(sent=sent):
				self.assertEqual(calc.clean_kind(sent), expected)

	def test_anything_else_is_not_a_kind(self):
		for sent in ("", None, "shop", "restaurant", 3, ["retail"]):
			with self.subTest(sent=sent):
				self.assertIsNone(calc.clean_kind(sent))

	def test_a_pharmacy_gets_expiry_and_prescription_only_and_nothing_else(self):
		on = {k for k, v in calc.features_for("pharmacy").items() if v}
		self.assertEqual(on, {"expiry", "prescription_only"})

	def test_retail_puts_barcodes_and_units_first(self):
		on = {k for k, v in calc.features_for("retail").items() if v}
		self.assertEqual(on, {"barcodes_first", "units_first"})

	def test_services_are_listed_first(self):
		on = {k for k, v in calc.features_for("services").items() if v}
		self.assertEqual(on, {"services_first"})

	def test_other_and_no_answer_turn_nothing_on(self):
		for kind in ("other", None, "", "nonsense"):
			with self.subTest(kind=kind):
				features = calc.features_for(kind)
				self.assertEqual(set(features), set(calc.FEATURE_NAMES))
				self.assertFalse(any(features.values()))

	def test_a_sign_up_industry_suggests_a_kind_only_when_it_is_clear(self):
		self.assertEqual(calc.kind_from_industry("PHARMACY"), "pharmacy")
		self.assertEqual(calc.kind_from_industry("grocery"), "retail")
		self.assertEqual(calc.kind_from_industry("SALON"), "services")
		for code in ("OTHER", "REST", "FUEL", "", None, "NOPE"):
			with self.subTest(code=code):
				self.assertIsNone(calc.kind_from_industry(code))


class TestSteps(unittest.TestCase):
	def test_the_six_steps_in_order(self):
		self.assertEqual(calc.STEPS, ("business", "shop", "cash", "payments", "products", "staff"))

	def test_a_step_name_is_checked(self):
		self.assertEqual(calc.clean_step(" Cash "), "cash")
		for sent in ("", None, "finish", "tax", 7):
			with self.subTest(sent=sent):
				self.assertIsNone(calc.clean_step(sent))


class TestProgress(unittest.TestCase):
	def test_nothing_saved_is_a_fresh_start(self):
		for saved in (None, "", "not json", "[]", "7"):
			with self.subTest(saved=saved):
				progress = calc.parse_progress(saved)
				self.assertEqual(progress, {"done": [], "skipped": [], "finished": False})
				self.assertEqual(calc.next_step(progress), "business")

	def test_a_step_done_moves_the_wizard_on(self):
		progress = calc.record_step(calc.empty_progress(), "business")
		self.assertEqual(calc.next_step(progress), "shop")
		progress = calc.record_step(progress, "shop")
		self.assertEqual(progress["done"], ["business", "shop"])
		self.assertEqual(calc.next_step(progress), "cash")

	def test_a_skipped_step_also_moves_the_wizard_on_and_is_remembered_as_skipped(self):
		progress = calc.record_step(calc.empty_progress(), "business")
		progress = calc.record_step(progress, "shop", skipped=True)
		self.assertEqual(progress["skipped"], ["shop"])
		self.assertEqual(progress["done"], ["business"])
		self.assertEqual(calc.next_step(progress), "cash")
		self.assertEqual(calc.passed_steps(progress), ["business", "shop"])

	def test_doing_a_skipped_step_later_takes_it_off_the_skipped_list(self):
		progress = calc.record_step(calc.empty_progress(), "cash", skipped=True)
		progress = calc.record_step(progress, "cash")
		self.assertEqual(progress["skipped"], [])
		self.assertEqual(progress["done"], ["cash"])

	def test_skipping_a_step_already_done_does_not_undo_it(self):
		progress = calc.record_step(calc.empty_progress(), "cash")
		progress = calc.record_step(progress, "cash", skipped=True)
		self.assertEqual(progress["done"], ["cash"])
		self.assertEqual(progress["skipped"], [])

	def test_a_step_out_of_order_is_kept_and_the_first_gap_is_next(self):
		progress = calc.record_step(calc.empty_progress(), "payments")
		self.assertEqual(calc.next_step(progress), "business")

	def test_after_the_last_step_there_is_no_next_step(self):
		progress = calc.empty_progress()
		for step in calc.STEPS:
			progress = calc.record_step(progress, step)
		self.assertIsNone(calc.next_step(progress))

	def test_finishing_marks_it_done_whatever_was_passed(self):
		progress = calc.finish(calc.empty_progress())
		self.assertTrue(progress["finished"])

	def test_progress_survives_being_saved_and_read_back(self):
		progress = calc.finish(
			calc.record_step(calc.record_step(calc.empty_progress(), "shop"), "cash", True)
		)
		self.assertEqual(calc.parse_progress(calc.dump_progress(progress)), progress)

	def test_unknown_or_repeated_steps_in_saved_text_are_dropped(self):
		saved = '{"done":["cash","cash","tax","business"],"skipped":["business","staff"],"finished":"yes"}'
		progress = calc.parse_progress(saved)
		self.assertEqual(progress["done"], ["business", "cash"])
		self.assertEqual(progress["skipped"], ["staff"])
		self.assertFalse(progress["finished"])


class TestWhenTheWizardIsOffered(unittest.TestCase):
	def test_a_new_store_is_offered_it(self):
		self.assertTrue(calc.needs_setup(False, 0, 0, False))
		self.assertTrue(calc.needs_setup(False, 0, 4, False))

	def test_five_products_is_not_new(self):
		self.assertFalse(calc.needs_setup(False, 0, 5, False))
		self.assertFalse(calc.needs_setup(False, 0, 300, False))

	def test_one_sale_is_not_new(self):
		self.assertFalse(calc.needs_setup(False, 1, 0, False))

	def test_a_recorded_kind_is_not_new(self):
		self.assertFalse(calc.needs_setup(True, 0, 0, False))

	def test_a_finished_or_skipped_wizard_is_never_offered_again(self):
		self.assertFalse(calc.needs_setup(False, 0, 0, True))

	def test_an_existing_shop_with_data_never_sees_it(self):
		self.assertFalse(calc.needs_setup(False, 1200, 480, False))
		self.assertFalse(calc.needs_setup(True, 1200, 480, False))

	def test_bad_counts_are_read_as_zero_not_as_a_crash(self):
		self.assertTrue(calc.needs_setup(False, None, "", False))
		self.assertTrue(calc.needs_setup(False, "x", -3, False))

	def test_a_started_wizard_is_offered_again_only_while_the_shop_is_still_new(self):
		started = calc.record_step(calc.empty_progress(), "business")
		self.assertTrue(calc.should_resume(started, 0, 2))
		self.assertFalse(calc.should_resume(started, 1, 2))
		self.assertFalse(calc.should_resume(started, 0, 5))
		self.assertFalse(calc.should_resume(calc.finish(started), 0, 2))

	def test_an_untouched_wizard_is_not_a_resume(self):
		self.assertFalse(calc.should_resume(calc.empty_progress(), 0, 0))


class TestOpeningCash(unittest.TestCase):
	def test_amounts(self):
		for sent, expected in (
			(5000, 5000.0),
			("5,000", 5000.0),
			("1250.567", 1250.57),
			("0", 0.0),
			("", 0.0),
			("   ", 0.0),
			(None, 0.0),
		):
			with self.subTest(sent=sent):
				self.assertEqual(calc.clean_amount(sent), expected)

	def test_things_that_are_not_an_amount_are_refused(self):
		for sent in ("abc", -1, "-5", "nan", "inf", float("inf"), 10**12, True, [], {}):
			with self.subTest(sent=sent):
				self.assertIsNone(calc.clean_amount(sent))
