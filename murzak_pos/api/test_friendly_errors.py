import unittest

from murzak_pos.api.friendly_errors import MISSING_DETAILS, MISSING_TAX_DETAILS, clean, friendly


class TestFriendlyErrors(unittest.TestCase):
	def test_missing_etims_fields(self):
		raw = (
			"<br>Error: Value missing for <strong>[Purchase Invoice, ACC-PINV-2026-00001]: custom_purchase_type, "
			"custom_receipt_type, custom_payment_type</strong>"
		)
		self.assertEqual(friendly(raw), MISSING_TAX_DETAILS)

	def test_other_missing_values(self):
		self.assertEqual(friendly("Value missing for Supplier"), MISSING_DETAILS)

	def test_no_stock_uses_the_product_name(self):
		raw = "Row #1: Item ITEM084 has no stock in warehouse Linda mwananchi - LI."
		self.assertEqual(
			friendly(raw, {"ITEM084": "Beef Samosa (Pair)"}.get),
			"Beef Samosa (Pair) is out of stock. Remove it from the sale, or add stock first.",
		)

	def test_no_stock_falls_back_to_the_code(self):
		self.assertIn("ITEM084 is out of stock", friendly("Item ITEM084 has no stock in warehouse W - LI."))

	def test_unknown_message_is_kept_whole(self):
		long = "Something odd happened. " * 40
		self.assertEqual(friendly(long), clean(long))
		self.assertGreater(len(friendly(long)), 240)

	def test_empty(self):
		self.assertEqual(friendly(None), "")


if __name__ == "__main__":
	unittest.main()
