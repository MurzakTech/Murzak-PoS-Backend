# Plain unit tests for offline sale handling. They need no Frappe site:
#   python -m unittest techsavanna_pos.api.test_offline_sales
import unittest

from techsavanna_pos.api.offline_sales import offline_remark, parse_sold_at


class TestParseSoldAt(unittest.TestCase):
	def test_accepted_formats(self):
		self.assertEqual(parse_sold_at("2026-10-10 14:05:09"), "2026-10-10 14:05:09")
		self.assertEqual(parse_sold_at("2026-10-10T14:05:09.123Z"), "2026-10-10 14:05:09")
		self.assertEqual(parse_sold_at("2026-10-10T14:05:09+03:00"), "2026-10-10 14:05:09")
		self.assertEqual(parse_sold_at("2026-10-10 14:05"), "2026-10-10 14:05:00")

	def test_missing_or_unreadable(self):
		self.assertIsNone(parse_sold_at(None))
		self.assertIsNone(parse_sold_at(""))
		self.assertIsNone(parse_sold_at("yesterday"))


class TestOfflineRemark(unittest.TestCase):
	def test_adds_once(self):
		first = offline_remark("2026-10-10 14:05:09")
		self.assertIn("Sold offline at 2026-10-10 14:05:09", first)
		self.assertEqual(offline_remark("2026-10-10 14:05:09", first), first)
		self.assertTrue(offline_remark("2026-10-10 14:05:09", "Paid in coins").startswith("Paid in coins\n"))


if __name__ == "__main__":
	unittest.main()
