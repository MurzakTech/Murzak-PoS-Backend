import unittest

from murzak_pos.api.setup_health_calc import build_report

HEALTHY = {
	"scheduler": "on",
	"stores": 2,
	"pos_profile": {"update_stock": True},
	"cash": True,
	"mpesa": {"prompt_ready": True},
	"etims": "active",
	"customer_defaults": True,
	"shop_type": True,
	"fiscal_year": True,
}


def status(report, check_id):
	return next(c["status"] for c in report["checks"] if c["id"] == check_id)


class TestSetupHealth(unittest.TestCase):
	def test_a_healthy_shop_is_all_green(self):
		report = build_report(HEALTHY)
		self.assertTrue(report["ok"])
		self.assertEqual(report["summary"], {"green": 9, "amber": 0, "red": 0})

	def test_the_scheduler_off_is_red_and_unknown_is_amber(self):
		self.assertEqual(status(build_report({**HEALTHY, "scheduler": "off"}), "scheduler"), "red")
		self.assertEqual(status(build_report({**HEALTHY, "scheduler": "unknown"}), "scheduler"), "amber")
		self.assertFalse(build_report({**HEALTHY, "scheduler": "off"})["ok"])

	def test_no_store_profile_cash_or_financial_year_are_red(self):
		for key, value, check in (
			("stores", 0, "stores"),
			("pos_profile", None, "till"),
			("cash", False, "cash"),
			("fiscal_year", False, "fiscal_year"),
		):
			self.assertEqual(status(build_report({**HEALTHY, key: value}), check), "red", key)

	def test_stock_not_taken_out_on_sale_is_amber(self):
		report = build_report({**HEALTHY, "pos_profile": {"update_stock": False}})
		self.assertEqual(status(report, "till"), "amber")
		self.assertTrue(report["ok"])

	def test_mpesa_states(self):
		self.assertEqual(status(build_report({**HEALTHY, "mpesa": None}), "mpesa"), "amber")
		half = build_report(
			{**HEALTHY, "mpesa": {"prompt_ready": False, "problem": "Add the passkey.", "code_entry": True}}
		)
		self.assertEqual(status(half, "mpesa"), "amber")
		detail = next(c["detail"] for c in half["checks"] if c["id"] == "mpesa")
		self.assertIn("Add the passkey.", detail)
		self.assertIn("M-Pesa code", detail)

	def test_etims_states(self):
		self.assertEqual(status(build_report({**HEALTHY, "etims": "absent"}), "etims"), "amber")
		self.assertEqual(status(build_report({**HEALTHY, "etims": "inactive"}), "etims"), "amber")

	def test_missing_facts_are_never_green(self):
		report = build_report({})
		self.assertEqual(status(report, "scheduler"), "amber")
		self.assertFalse(report["ok"])
