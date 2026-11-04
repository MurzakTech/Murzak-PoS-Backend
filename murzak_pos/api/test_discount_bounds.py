import unittest

from murzak_pos.api.discount_bounds import problem


class TestDiscountBounds(unittest.TestCase):
	def test_within_bounds(self):
		for percent in (0, 10, 100, None, ""):
			self.assertIsNone(problem(percent, 0, 500))
		self.assertIsNone(problem(0, 500, 500))

	def test_percent_over_100_or_below_0(self):
		for percent in (101, 150, -5):
			self.assertEqual(problem(percent, 0, 500), "percent_range")

	def test_amount_negative_or_above_the_sale(self):
		self.assertEqual(problem(0, -1, 500), "amount_negative")
		self.assertEqual(problem(0, 501, 500), "amount_too_big")
