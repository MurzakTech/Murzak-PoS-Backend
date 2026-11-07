import json
import unittest

from murzak_pos.api.settings_export_calc import VERSION, build_export

FACTS = {
	"till": {
		"update_stock": True,
		"allow_discount_change": False,
		"allow_rate_change": False,
		"allow_partial_payment": True,
	},
	"payment_methods": ["M-Pesa", "Cash"],
	"cashier_discount_limit": 10,
	"customer_defaults": {"group": "Individual", "territory": "Kenya"},
	"shop_type": "Retail",
	"currency": "KES",
	"time_zone": "Africa/Nairobi",
}


class TestSettingsExport(unittest.TestCase):
	def test_holds_the_choices_in_a_stable_order(self):
		doc = build_export("Linda mwananchi", FACTS, "2026-11-07")
		self.assertEqual(doc["version"], VERSION)
		self.assertEqual(doc["payment_methods"], ["Cash", "M-Pesa"])
		self.assertEqual(doc["till"]["update_stock"], True)
		self.assertEqual(doc["new_customer_defaults"], {"group": "Individual", "territory": "Kenya"})
		json.dumps(doc)  # it can be written out as it is

	def test_never_carries_a_secret_or_a_personal_detail(self):
		dirty = {
			**FACTS,
			"till": {**FACTS["till"], "api_secret": "x"},
			"customer_defaults": {"group": "A", "owner_phone": "0712", "contact_email": "a@b.c"},
		}
		doc = build_export("Shop", dirty, "2026-11-07")
		text = json.dumps(doc).lower()
		for word in ("secret", "0712", "a@b.c", "phone", "email"):
			self.assertNotIn(word, text)

	def test_missing_facts_are_empty_not_errors(self):
		doc = build_export("Shop", {}, "2026-11-07")
		self.assertEqual(doc["till"], {})
		self.assertEqual(doc["payment_methods"], [])
