import unittest

from murzak_pos.api.return_payments import refund_amount, refund_mode


class TestReturnPayments(unittest.TestCase):
	def test_the_mode_asked_for_wins(self):
		self.assertEqual(refund_mode("M-Pesa", [{"mode_of_payment": "Cash", "amount": 100}]), "M-Pesa")

	def test_otherwise_the_way_the_sale_was_mostly_paid(self):
		sale = [{"mode_of_payment": "Cash", "amount": 30}, {"mode_of_payment": "M-Pesa", "amount": 70}]
		self.assertEqual(refund_mode(None, sale), "M-Pesa")
		self.assertEqual(refund_mode("  ", sale), "M-Pesa")

	def test_otherwise_cash(self):
		self.assertEqual(refund_mode(None, []), "Cash")
		self.assertEqual(refund_mode(None, [{"mode_of_payment": "Cash", "amount": 0}]), "Cash")

	def test_the_amount_is_negative_whatever_the_sign_of_the_total(self):
		self.assertEqual(refund_amount(-190), -190)
		self.assertEqual(refund_amount(190), -190)
		self.assertEqual(refund_amount(None), 0)
