import unittest
from unittest.mock import patch

from murzak_pos.api import industry_profile_api, product_api


class TestStartsPrescriptionOnly(unittest.TestCase):
	def starts(self, chosen, *names, pharmacy=True):
		with patch.object(industry_profile_api, "business_has", return_value=pharmacy) as business_has:
			result = product_api._starts_prescription_only(chosen, "Duka Ltd", *names)
		return result, business_has

	def test_what_the_person_chose_wins_however_it_arrives(self):
		for chosen in (True, 1, "1", "true", "Yes"):
			self.assertTrue(self.starts(chosen, "Paracetamol")[0], chosen)
		for chosen in (False, 0, "0", "false", "no"):
			self.assertFalse(self.starts(chosen, "Amoxicillin")[0], chosen)

	def test_a_pharmacys_antibiotic_starts_prescription_only_when_nothing_was_chosen(self):
		self.assertTrue(self.starts(None, "AMX-500", "Amoxicillin 500mg")[0])
		self.assertTrue(self.starts("", "Ciprofloxacin")[0])

	def test_another_kind_of_shop_is_left_alone(self):
		self.assertFalse(self.starts(None, "Amoxicillin", pharmacy=False)[0])

	def test_the_business_is_only_asked_about_when_the_name_looks_like_an_antibiotic(self):
		result, business_has = self.starts(None, "Paracetamol Tablet")
		self.assertFalse(result)
		business_has.assert_not_called()


if __name__ == "__main__":
	unittest.main()
