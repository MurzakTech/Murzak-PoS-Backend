import unittest

from murzak_pos.api.currency_symbol import needs_change


class TestCurrencySymbol(unittest.TestCase):
	def test_shipped_symbols_are_changed(self):
		for symbol in ("Sh", "KSh", "", None, " Sh "):
			self.assertTrue(needs_change(symbol), symbol)

	def test_a_chosen_symbol_is_kept(self):
		for symbol in ("KES", "Ksh.", "/="):
			self.assertFalse(needs_change(symbol), symbol)
