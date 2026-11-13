import typing
import unittest
from datetime import date
from unittest.mock import patch

import frappe

from murzak_pos.api import levy_api
from murzak_pos.api import levy_rules as rules

TODAY = date(2026, 11, 13)


class TestMonthRange(unittest.TestCase):
	def test_a_month_runs_from_its_first_to_its_last_day(self):
		self.assertEqual(
			rules.month_range("2026-02", TODAY), (date(2026, 2, 1), date(2026, 2, 28), "2026-02")
		)
		self.assertEqual(rules.month_range("2024-02", TODAY)[1], date(2024, 2, 29))

	def test_no_month_is_this_month_so_far(self):
		self.assertEqual(rules.month_range(None, TODAY)[2], "2026-11")

	def test_a_bad_or_future_month_is_refused(self):
		for bad in ("2026-13", "26-10", "October", "2026-12", "1999-01"):
			with self.assertRaises(ValueError, msg=bad):
				rules.month_range(bad, TODAY)


class TestLevyReport(unittest.TestCase):
	ROWS: typing.ClassVar[list] = [
		{"warehouse": "REST", "net": 100000, "gross": 116000},
		{"warehouse": "REST", "net": -5000, "gross": -5800},  # a return
		{"warehouse": "CHEM", "net": 40000, "gross": 40000},
	]
	NAMES: typing.ClassVar[dict] = {"REST": "Restaurant", "CHEM": "Chemist"}

	def test_the_levy_is_two_percent_on_both_bases_for_levied_stores_only(self):
		report = rules.levy_report(self.ROWS, self.NAMES, {"REST"})
		self.assertEqual(report["rate_percent"], 2.0)
		self.assertEqual(len(report["stores"]), 1)
		rest = report["stores"][0]
		self.assertEqual((rest["sales_before_vat"], rest["sales_with_vat"]), (95000.0, 110200.0))
		self.assertEqual((rest["levy_before_vat"], rest["levy_with_vat"]), (1900.0, 2204.0))
		self.assertEqual(report["not_levied"][0]["name"], "Chemist")
		self.assertNotIn("levy_before_vat", report["not_levied"][0])
		self.assertEqual(report["totals"]["levy_with_vat"], 2204.0)

	def test_with_no_levied_list_every_store_counts(self):
		self.assertEqual(len(rules.levy_report(self.ROWS, self.NAMES, None)["stores"]), 2)

	def test_a_quiet_month_is_zero_not_an_error(self):
		self.assertEqual(rules.levy_report([], {}, set())["totals"]["levy_before_vat"], 0)


class TestGetTourismLevy(unittest.TestCase):
	def test_owners_and_managers_only_and_a_future_month_is_refused(self):
		with (
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(levy_api.role_tiers, "require_access") as require_access,
			patch.object(levy_api, "resolve_company", return_value="Bar Ltd"),
			patch.object(levy_api, "nairobi_today", return_value=TODAY),
		):
			with self.assertRaises(frappe.ValidationError):
				levy_api.get_tourism_levy(month="2027-01")
		require_access.assert_called_once_with("profit")

	def test_the_report_is_built_from_the_months_sales(self):
		with (
			patch.object(levy_api.role_tiers, "require_access"),
			patch.object(levy_api, "resolve_company", return_value="Bar Ltd"),
			patch.object(levy_api, "nairobi_today", return_value=TODAY),
			patch.object(levy_api, "_levied_stores", return_value=({"REST": "Restaurant"}, {"REST"})),
			patch.object(
				levy_api, "_sales", return_value=[{"warehouse": "REST", "net": 1000, "gross": 1160}]
			) as sales,
		):
			data = levy_api.get_tourism_levy(month="2026-10")["data"]
		self.assertEqual(sales.call_args.args, ("Bar Ltd", date(2026, 10, 1), date(2026, 10, 31)))
		self.assertEqual((data["month"], data["totals"]["levy_before_vat"]), ("2026-10", 20.0))


if __name__ == "__main__":
	unittest.main()
