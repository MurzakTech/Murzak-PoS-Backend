# Plain unit tests for the expiry alert rules. They need no Frappe site:
#   python -m unittest techsavanna_pos.api.test_expiry_rules
import unittest
from datetime import date

from techsavanna_pos.api.expiry_rules import (
	CRITICAL,
	EXPIRED,
	SOON,
	clamp_window,
	classify,
	sort_alerts,
	summarize,
)

TODAY = date(2026, 10, 10)


class TestClassify(unittest.TestCase):
	def test_buckets(self):
		self.assertEqual(classify(date(2026, 10, 9), TODAY, 30), {"status": EXPIRED, "days_left": -1})
		self.assertEqual(classify(TODAY, TODAY, 30), {"status": CRITICAL, "days_left": 0})
		self.assertEqual(classify(date(2026, 10, 17), TODAY, 30)["status"], CRITICAL)
		self.assertEqual(classify(date(2026, 10, 18), TODAY, 30)["status"], SOON)
		self.assertEqual(classify(date(2026, 11, 9), TODAY, 30), {"status": SOON, "days_left": 30})

	def test_outside_window_or_no_date(self):
		self.assertIsNone(classify(date(2026, 11, 10), TODAY, 30))
		self.assertIsNone(classify(None, TODAY, 30))


class TestClampWindow(unittest.TestCase):
	def test_clamp(self):
		self.assertEqual(clamp_window("14"), 14)
		self.assertEqual(clamp_window(0), 30)
		self.assertEqual(clamp_window("abc"), 30)
		self.assertEqual(clamp_window(1000), 365)


class TestSummaryAndOrder(unittest.TestCase):
	def test_summary_and_order(self):
		rows = [
			{
				"item_code": "B",
				"warehouse": "W",
				"expiry_date": "2026-10-20",
				"status": SOON,
				"stock_value": 100,
			},
			{
				"item_code": "A",
				"warehouse": "W",
				"expiry_date": "2026-10-01",
				"status": EXPIRED,
				"stock_value": "50.5",
			},
			{
				"item_code": "C",
				"warehouse": "W",
				"expiry_date": "2026-10-12",
				"status": CRITICAL,
				"stock_value": None,
			},
		]
		self.assertEqual([r["item_code"] for r in sort_alerts(rows)], ["A", "C", "B"])
		s = summarize(rows)
		self.assertEqual(s["total_lines"], 3)
		self.assertEqual(s["value_at_risk"], 150.5)
		self.assertEqual(s["by_status"][0], {"status": EXPIRED, "lines": 1, "value": 50.5})


if __name__ == "__main__":
	unittest.main()
