# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests that the Frappe desk dashboards only show a person their own business. No database is needed.

For a manager bound to business A, every card, chart and report is run with the data layer replaced by a
recorder. Whatever the data layer is asked for must name business A and nothing else, and a request for
business B must be refused. (The same checks were run against real data for two businesses on staging.)
"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import desk_cards, desk_reports, desk_scope, sales_figures, standard_reports

FIGURE_FUNCTIONS = (
	"sales_total",
	"sales_by_day",
	"sales_by_month",
	"purchases_by_month",
	"sales_by_payment_method",
	"top_items",
	"stock_value_by_warehouse",
	"open_shift_count",
	"unpaid_purchase_invoices",
	"sales_by_item_group",
	"stock_by_item",
	"unsent_etims_rows",
)
REPORTS = (
	"sales_by_day",
	"sales_by_payment_method",
	"top_items",
	"sales_vs_purchases",
	"stock_value_by_warehouse",
	"sales_analytics",
	"inventory_valuation",
	"unsent_etims",
)
CARDS = ("sales_today", "sales_this_month", "open_shifts", "unpaid_purchase_invoices")


class Recorder:
	"""Stands in for sales_figures: remembers which businesses each call asked for."""

	def __init__(self):
		self.asked = []
		self.patches = []

	def __enter__(self):
		for name in FIGURE_FUNCTIONS:
			real = getattr(sales_figures, name)
			recorder = self

			def fake(companies, *args, _name=name, _real=real, **kwargs):
				recorder.asked.append((_name, list(companies)))
				return (
					0
					if _name in ("sales_total", "open_shift_count")
					else ((0, 0.0) if _name == "unpaid_purchase_invoices" else [])
				)

			patcher = patch.object(sales_figures, name, side_effect=fake)
			patcher.start()
			self.patches.append(patcher)
		return self

	def __exit__(self, *exc):
		for patcher in self.patches:
			patcher.stop()


class _Combined:
	"""Several patches entered together."""

	def __init__(self, *patches):
		self.patches = patches

	def __enter__(self):
		for patcher in self.patches:
			patcher.start()
		return self

	def __exit__(self, *exc):
		for patcher in reversed(self.patches):
			patcher.stop()


def as_manager_of(*companies):
	"""Sign in as a manager of these businesses, on a fixed day (the clock needs the database otherwise)."""
	return _Combined(
		patch.object(desk_scope, "visible_companies", return_value=sorted(companies)),
		patch.object(desk_cards, "visible_companies", return_value=sorted(companies)),
		patch.object(desk_reports, "nowdate", return_value="2026-10-11"),
		patch.object(desk_reports, "now_datetime", return_value=datetime(2026, 10, 11, 12, 0)),
		patch.object(desk_cards, "nowdate", return_value="2026-10-11"),
	)


class ScopeCase(unittest.TestCase):
	def test_every_card_asks_only_for_the_managers_business(self):
		with (
			as_manager_of("A"),
			patch.object(desk_cards, "visible_companies", return_value=["A"]),
			Recorder() as recorder,
		):
			for name in CARDS:
				getattr(desk_cards, name)()
		self.assertTrue(recorder.asked)
		for _name, companies in recorder.asked:
			self.assertEqual(companies, ["A"])

	def test_every_report_asks_only_for_the_managers_business(self):
		with as_manager_of("A"), Recorder() as recorder:
			for name in REPORTS:
				getattr(desk_reports, name)({})
		self.assertTrue(recorder.asked)
		for _name, companies in recorder.asked:
			self.assertEqual(companies, ["A"])

	def test_asking_for_another_business_is_refused_in_every_report(self):
		with as_manager_of("A"), Recorder() as recorder:
			for name in REPORTS:
				with self.assertRaises(frappe.PermissionError):
					getattr(desk_reports, name)({"company": "B"})
		self.assertEqual(recorder.asked, [])

	def test_a_manager_of_two_businesses_may_pick_either_or_see_both(self):
		with as_manager_of("A", "B"), Recorder() as recorder:
			desk_reports.sales_by_day({})
			desk_reports.sales_by_day({"company": "B"})
		self.assertEqual([c for _n, c in recorder.asked], [["A", "B"], ["B"]])

	def test_someone_bound_to_nothing_sees_nothing(self):
		with as_manager_of(), Recorder() as recorder:
			desk_reports.sales_by_day({})
		self.assertEqual(recorder.asked, [("sales_by_day", [])])


class VisibleCompaniesCase(unittest.TestCase):
	def run_for(self, user, bound, roles, all_companies=("A", "B", "C")):
		with (
			patch.object(desk_scope.frappe, "get_all", return_value=list(all_companies)),
			patch.object(desk_scope.frappe, "get_roles", return_value=roles),
			patch.object(desk_scope.frappe, "db", MagicMock()) as db,
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(bound)),
		):
			db.exists.return_value = True
			return desk_scope.visible_companies(user)

	def test_administrator_sees_every_business(self):
		self.assertEqual(self.run_for("Administrator", [], []), ["A", "B", "C"])

	def test_a_bound_user_sees_only_their_businesses(self):
		self.assertEqual(self.run_for("mgr@a.test", ["A"], ["System Manager"]), ["A"])

	def test_an_unbound_system_manager_is_the_platform_operator(self):
		self.assertEqual(self.run_for("ops@murzak.test", [], ["System Manager"]), ["A", "B", "C"])

	def test_an_unbound_ordinary_user_sees_nothing(self):
		self.assertEqual(self.run_for("x@a.test", [], ["Sales User"]), [])


class StandardReportCase(unittest.TestCase):
	def test_only_listed_reports_run(self):
		with self.assertRaises(frappe.PermissionError):
			standard_reports.run_standard_report("Customer Ledger Summary", {})

	def test_the_company_filter_is_forced_to_the_callers_only_business(self):
		with as_manager_of("A"):
			self.assertEqual(standard_reports.forced_filters({"from_date": "2026-01-01"})["company"], "A")

	def test_another_business_is_refused(self):
		with as_manager_of("A"), self.assertRaises(frappe.PermissionError):
			standard_reports.forced_filters({"company": "B"})

	def test_several_businesses_must_choose(self):
		with as_manager_of("A", "B"), self.assertRaises(frappe.ValidationError):
			standard_reports.forced_filters({})

	def test_filters_arrive_as_json_text_too(self):
		with as_manager_of("A"):
			self.assertEqual(standard_reports.forced_filters('{"company": "A"}')["company"], "A")

	def test_listed_reports_run_with_the_forced_filter(self):
		fake_run = MagicMock(return_value={"result": []})
		with (
			as_manager_of("A"),
			patch.dict("sys.modules", {"frappe.desk.query_report": SimpleNamespace(run=fake_run)}),
		):
			standard_reports.run_standard_report("Trial Balance", {})
		self.assertEqual(fake_run.call_args.kwargs["filters"]["company"], "A")


if __name__ == "__main__":
	unittest.main()
