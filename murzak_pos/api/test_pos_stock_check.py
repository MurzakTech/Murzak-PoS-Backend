import types
import unittest

from murzak_pos.overrides import pos_stock_check as check


class TestPosStockCheck(unittest.TestCase):
	def test_posted_sales_are_taken_out_of_the_reserved_quantity(self):
		self.assertEqual(check.unreserved(27, 27), 0)
		self.assertEqual(check.unreserved(30, 27), 3)

	def test_never_negative(self):
		self.assertEqual(check.unreserved(5, 9), 0)
		self.assertEqual(check.unreserved(None, None), 0)

	def test_reported_case_is_sellable(self):
		on_hand = 23
		reserved = check.wrap(lambda item, wh: 27, posted=lambda item, wh: 27)("ITEM084", "W - LI")
		self.assertEqual(on_hand - reserved, 23)

	def test_unposted_sales_still_reserve_stock(self):
		reserved = check.wrap(lambda item, wh: 4, posted=lambda item, wh: 0)("ITEM084", "W - LI")
		self.assertEqual(reserved, 4)

	def test_install_wraps_once(self):
		module = types.SimpleNamespace(get_pos_reserved_qty=lambda item, wh: 5)
		self.assertTrue(check.install(module))
		first = module.get_pos_reserved_qty
		self.assertFalse(check.install(module))
		self.assertIs(module.get_pos_reserved_qty, first)

	def test_install_without_the_function_does_nothing(self):
		self.assertFalse(check.install(types.SimpleNamespace()))


if __name__ == "__main__":
	unittest.main()
