import unittest
from datetime import datetime, timedelta

from murzak_pos.api import shop_type_calc as calc

NOW = datetime(2026, 11, 7, 12, 0)


class TestChangeDecision(unittest.TestCase):
	def test_the_first_time_it_is_free(self):
		self.assertTrue(calc.change_decision(None, "retail", None, NOW)["allowed"])

	def test_nothing_chosen_is_refused(self):
		self.assertEqual(calc.change_decision(None, "", None, NOW)["reason"], "none")

	def test_the_same_type_is_refused(self):
		self.assertEqual(calc.change_decision("retail", "retail", None, NOW)["reason"], "same")

	def test_a_change_with_no_earlier_change_on_record_is_allowed(self):
		self.assertTrue(calc.change_decision("retail", "pharmacy", None, NOW)["allowed"])

	def test_a_change_soon_after_another_waits(self):
		last = NOW - timedelta(days=5)
		result = calc.change_decision("retail", "pharmacy", last, NOW)
		self.assertFalse(result["allowed"])
		self.assertEqual(result["reason"], "cooldown")
		self.assertEqual(result["next_change_on"], (last + timedelta(days=calc.COOLDOWN_DAYS)).date())

	def test_a_change_after_the_wait_is_allowed(self):
		last = NOW - timedelta(days=calc.COOLDOWN_DAYS + 1)
		self.assertTrue(calc.change_decision("retail", "pharmacy", last, NOW)["allowed"])

	def test_next_change_date(self):
		self.assertIsNone(calc.next_change_on(None, NOW))
		self.assertIsNone(calc.next_change_on(NOW - timedelta(days=40), NOW))
		self.assertIsNotNone(calc.next_change_on(NOW - timedelta(days=1), NOW))
