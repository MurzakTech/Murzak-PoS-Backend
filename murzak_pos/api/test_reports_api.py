# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Reports (`reports_api.run_report`, `reports_api.list_reports`) and the arithmetic behind them
(`reports_calc`). They need no database: every Frappe call is mocked.

The first half tests the plain functions with plain rows: the Build Pack's profit example, the age bands of what
customers owe, running costs by category, and the stock lists. The second half tests the calls: who is let in (a
cashier gets only the sales report, and only their own sales), that a report agrees with the Home overview for the
same period, that one failing part does not take the rest with it, and the 60-second cache.
"""

import json
import unittest
from datetime import date
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import dashboard_overview as overview
from murzak_pos.api import overview_calc as oc
from murzak_pos.api import reports_api as api
from murzak_pos.api import reports_calc as calc
from murzak_pos.api import role_tiers
from murzak_pos.api.test_dashboard_overview import LINES, NOW, SALES, SOLD, TODAY, OverviewCase

D = date.fromisoformat
PERIOD_30 = oc.resolve_period(
	"30d", None, None, TODAY
)  # 14 Sep to 13 Oct; the 30 days before it end on 13 Sep


def sale(day, net, n=1, is_return=0):
	return {"posting_date": D(day), "is_return": is_return, "net": net, "gross": net, "n": n}


def profit_line(day, code, net, qty, recorded=0.0, uncosted=0.0, fallback=0.0, stock=1):
	return {
		"posting_date": D(day),
		"item_code": code,
		"item_name": code,
		"warehouse": "W",
		"is_stock_item": stock,
		"net_amount": net,
		"qty": qty,
		"recorded_cost": recorded,
		"uncosted_qty": uncosted,
		"fallback_rate": fallback,
	}


def expense(day, account, amount, name=None, n=1):
	return {"posting_date": D(day), "account": account, "account_name": name, "amount": amount, "n": n}


# --- who may open what ----------------------------------------------------------------------------------


class TestTheReportsAndWhoMayOpenThem(unittest.TestCase):
	def test_a_cashier_may_open_only_the_sales_report(self):
		self.assertEqual(calc.reports_for("cashier"), ["sales"])

	def test_owners_and_managers_may_open_every_report(self):
		for tier in ("owner", "manager"):
			self.assertEqual(calc.reports_for(tier), list(calc.REPORTS))

	def test_the_table_here_says_what_role_tiers_says(self):
		for name, report in calc.REPORTS.items():
			for tier in ("owner", "manager", "cashier"):
				capability = report["capability"]
				expected = True if capability is None else tier in role_tiers.CAPABILITIES[capability]
				with self.subTest(report=name, tier=tier):
					self.assertEqual(calc.allowed(name, tier), expected)

	def test_an_unknown_report_or_person_is_not_allowed(self):
		self.assertFalse(calc.allowed("balance-sheet", "owner"))
		self.assertFalse(calc.allowed("sales", None))

	def test_there_are_six_questions_and_every_report_is_under_one(self):
		self.assertEqual(len(calc.QUESTIONS), 6)
		under = [name for q in calc.QUESTIONS for name in q["reports"]]
		self.assertCountEqual(under, list(calc.REPORTS))
		for question in calc.QUESTIONS:
			self.assertIn(question["opens"], question["reports"])

	def test_the_stock_question_has_two_views(self):
		stock = next(q for q in calc.QUESTIONS if q["key"] == "stock")
		self.assertEqual(stock["reports"], ("stock-summary", "stock-value"))

	def test_a_cashiers_landing_page_has_one_question(self):
		questions = calc.questions_for("cashier")
		self.assertEqual([q["key"] for q in questions], ["sold"])
		self.assertEqual(questions[0]["reports"], ["sales"])

	def test_an_owners_landing_page_has_all_six_in_order(self):
		self.assertEqual(
			[q["key"] for q in calc.questions_for("owner")],
			["sold", "profit", "owed", "costs", "stock", "not_selling"],
		)

	def test_the_reports_about_now_say_so(self):
		as_of = {q["key"]: q["as_of"] for q in calc.questions_for("owner")}
		self.assertEqual(
			as_of,
			{
				"sold": False,
				"profit": False,
				"owed": True,
				"costs": False,
				"stock": True,
				"not_selling": True,
			},
		)


# --- sales ----------------------------------------------------------------------------------------------


class TestSalesReport(unittest.TestCase):
	def setUp(self):
		raw = [
			sale("2026-10-13", 12450, 9),
			sale("2026-10-12", 8000, 6),
			sale("2026-10-12", -500, 1, is_return=1),
			sale("2026-10-06", 11000, 8),
			sale("2026-09-20", 4000, 3),
			sale("2026-09-13", 7000, 5),
		]
		self.totals = oc.daily_totals(raw)
		self.rows = calc.sales_rows(self.totals, PERIOD_30, TODAY)

	def test_one_row_for_every_day_of_the_period_oldest_first(self):
		self.assertEqual(len(self.rows), 30)
		self.assertEqual(self.rows[0]["date"], "2026-09-14")
		self.assertEqual(self.rows[-1]["date"], "2026-10-13")

	def test_a_day_with_no_sales_is_a_row_of_zeros(self):
		quiet = next(r for r in self.rows if r["date"] == "2026-10-01")
		self.assertEqual((quiet["revenue"], quiet["count"], quiet["average_basket"]), (0.0, 0, 0.0))

	def test_returns_take_value_off_and_are_not_sales(self):
		day = next(r for r in self.rows if r["date"] == "2026-10-12")
		self.assertEqual((day["revenue"], day["count"]), (7500.0, 6))

	def test_today_is_marked_so_far_and_no_other_day_is(self):
		self.assertEqual([r["date"] for r in self.rows if r["so_far"]], ["2026-10-13"])

	def test_each_day_carries_the_same_position_of_the_previous_period(self):
		# 14 Sep is day 1; day 1 of the previous period is 15 Aug. 13 Sep is the last day of the previous period.
		self.assertEqual(self.rows[0]["previous_revenue"], 0.0)
		self.assertEqual(self.rows[-1]["previous_revenue"], 7000.0)
		self.assertEqual(self.rows[-1]["previous_count"], 5)

	def test_the_chart_points_nest_the_previous_period_as_the_home_chart_does(self):
		points = calc.sales_points(self.rows)
		self.assertEqual(
			points[-1],
			{
				"date": "2026-10-13",
				"revenue": 12450.0,
				"count": 9,
				"previous": {"revenue": 7000.0, "count": 5},
				"so_far": True,
			},
		)
		self.assertNotIn("so_far", points[0])

	def test_the_summary_is_the_home_revenue_tile(self):
		summary = calc.sales_summary(self.totals, self.rows, PERIOD_30)
		home = oc.sum_totals(self.totals, PERIOD_30["from"], PERIOD_30["to"])
		self.assertEqual(summary["revenue"], home["revenue"])
		self.assertEqual(summary["revenue"], 34950.0)
		self.assertEqual(summary["count"], 26)
		self.assertEqual(summary["previous_revenue"], 7000.0)
		self.assertEqual(summary["change_pct"], oc.pct_change(34950, 7000))
		self.assertEqual(summary["average_basket"], oc.average_basket(34950, 26))
		self.assertEqual(summary["best_day"], {"date": "2026-10-13", "revenue": 12450.0})
		self.assertEqual(summary["days_with_sales"], 4)

	def test_the_days_add_up_to_the_summary(self):
		summary = calc.sales_summary(self.totals, self.rows, PERIOD_30)
		self.assertEqual(round(sum(r["revenue"] for r in self.rows), 2), summary["revenue"])
		self.assertEqual(sum(r["count"] for r in self.rows), summary["count"])

	def test_no_sales_at_all(self):
		rows = calc.sales_rows({}, PERIOD_30, TODAY)
		summary = calc.sales_summary({}, rows, PERIOD_30)
		self.assertEqual((summary["revenue"], summary["count"], summary["best_day"]), (0.0, 0, None))
		self.assertIsNone(summary["change_pct"])


# --- profit ---------------------------------------------------------------------------------------------


class TestProfitReport(unittest.TestCase):
	def report(
		self, rows, expenses, revenue_days, uncosted=0, period=PERIOD_30, group_by=None, before_rows=()
	):
		return calc.profit_report(
			rows,
			list(before_rows),
			expenses,
			oc.daily_totals(revenue_days),
			uncosted,
			period,
			TODAY,
			group_by,
		)

	def test_the_build_pack_example_gives_23000_and_23_percent(self):
		# Revenue KES 100,000, cost of goods KES 62,000, running costs KES 15,000
		rows = [profit_line("2026-10-05", "A", 100000, 100, recorded=62000)]
		report = self.report(
			rows, [expense("2026-10-06", "Rent - DS", 15000)], [sale("2026-10-05", 100000, 40)]
		)
		summary = report["summary"]
		self.assertEqual(summary["revenue"], 100000.0)
		self.assertEqual(summary["cost_of_goods"], 62000.0)
		self.assertEqual(summary["running_costs"], 15000.0)
		self.assertEqual(summary["net_profit"], 23000.0)
		self.assertEqual(summary["margin_pct"], 23.0)

	def test_revenue_minus_cost_minus_running_costs_is_net_profit(self):
		rows = [
			profit_line("2026-10-05", "A", 5000, 10, recorded=3000),
			profit_line("2026-10-07", "B", 800, 2, recorded=500),
		]
		summary = self.report(
			rows, [expense("2026-10-06", "Rent", 700)], [sale("2026-10-05", 5000), sale("2026-10-07", 800)]
		)["summary"]
		self.assertEqual(
			summary["sales_counted"] - summary["cost_of_goods"] - summary["running_costs"],
			summary["net_profit"],
		)
		self.assertEqual(summary["sales_not_counted"], 0.0)

	def test_it_is_the_same_number_as_the_home_net_profit_tile(self):
		rows = [
			profit_line("2026-10-05", "A", 5000, 10, recorded=3000),
			profit_line("2026-09-20", "A", 2000, 4, recorded=1200),
		]
		before = [profit_line("2026-09-10", "A", 1000, 2, recorded=600)]
		expenses = [expense("2026-10-06", "Rent", 700), expense("2026-09-01", "Rent", 300)]
		report = self.report(
			rows, expenses, [sale("2026-10-05", 5000), sale("2026-09-20", 2000)], before_rows=before
		)
		from murzak_pos.api.profit_calc import summarize_profit

		tile = oc.net_profit_tile(
			summarize_profit(oc.for_profit(rows), 700), summarize_profit(oc.for_profit(before), 300), [], 0
		)
		self.assertEqual(report["summary"]["net_profit"], tile["value"])
		self.assertEqual(report["summary"]["margin_pct"], tile["margin_pct"])
		self.assertEqual(report["summary"]["previous_net_profit"], tile["previous"])
		self.assertEqual(report["summary"]["change_pct"], tile["change_pct"])

	def test_a_product_with_no_cost_is_left_out_and_the_gap_is_shown(self):
		rows = [
			profit_line("2026-10-05", "A", 1000, 10, recorded=600),
			profit_line("2026-10-05", "Z", 400, 4, uncosted=4),
		]
		summary = self.report(rows, [], [sale("2026-10-05", 1400)], uncosted=1)["summary"]
		self.assertEqual(summary["revenue"], 1400.0)
		self.assertEqual(summary["sales_counted"], 1000.0)
		self.assertEqual(summary["sales_not_counted"], 400.0)
		self.assertEqual(summary["uncosted_products"], 1)
		self.assertEqual(summary["net_profit"], 400.0)

	def test_a_product_with_an_estimated_cost_marks_the_figure_as_estimated(self):
		rows = [profit_line("2026-10-05", "A", 1000, 10, uncosted=10, fallback=60)]
		summary = self.report(rows, [], [sale("2026-10-05", 1000)])["summary"]
		self.assertTrue(summary["estimated"])
		self.assertEqual(summary["cost_of_goods"], 600.0)

	def test_no_sales_and_no_costs_is_zero_not_a_failure(self):
		report = self.report([], [], [])
		self.assertEqual(report["summary"]["net_profit"], 0.0)
		self.assertEqual(report["summary"]["margin_pct"], 0.0)
		self.assertIsNone(report["summary"]["change_pct"])

	def test_costs_alone_make_a_loss(self):
		summary = self.report([], [expense("2026-10-06", "Rent", 700)], [])["summary"]
		self.assertEqual(summary["net_profit"], -700.0)

	def test_thirty_days_are_listed_by_day_and_a_year_by_month(self):
		self.assertEqual(self.report([], [], [])["group_by"], "day")
		year = oc.resolve_period("custom", "2026-01-01", "2026-10-13", TODAY)
		report = self.report([], [], [], period=year)
		self.assertEqual(report["group_by"], "month")
		self.assertEqual(len(report["rows"]), 10)
		self.assertEqual(report["rows"][0]["period"], "2026-01")

	def test_the_choice_can_be_forced_and_nonsense_is_ignored(self):
		self.assertEqual(self.report([], [], [], group_by="month")["group_by"], "month")
		self.assertEqual(self.report([], [], [], group_by="week")["group_by"], "day")

	def test_the_rows_add_up_to_the_summary(self):
		rows = [
			profit_line("2026-10-05", "A", 5000, 10, recorded=3000),
			profit_line("2026-10-07", "B", 800, 2, recorded=500),
		]
		report = self.report(
			rows, [expense("2026-10-06", "Rent", 700)], [sale("2026-10-05", 5000), sale("2026-10-07", 800)]
		)
		by_day = report["rows"]
		self.assertEqual(len(by_day), 30)
		self.assertEqual(round(sum(r["net_profit"] for r in by_day), 2), report["summary"]["net_profit"])
		self.assertEqual(round(sum(r["sales"] for r in by_day), 2), report["summary"]["sales_counted"])
		self.assertEqual(
			round(sum(r["running_costs"] for r in by_day), 2), report["summary"]["running_costs"]
		)
		rent_day = next(r for r in by_day if r["period"] == "2026-10-06")
		self.assertEqual((rent_day["sales"], rent_day["net_profit"]), (0.0, -700.0))

	def test_by_month_the_rows_add_up_too(self):
		rows = [
			profit_line("2026-10-05", "A", 5000, 10, recorded=3000),
			profit_line("2026-09-20", "A", 2000, 4, recorded=1200),
		]
		report = self.report(rows, [expense("2026-10-06", "Rent", 700)], [], group_by="month")
		self.assertEqual([r["period"] for r in report["rows"]], ["2026-09", "2026-10"])
		self.assertEqual(report["rows"][0]["net_profit"], 800.0)
		self.assertEqual(report["rows"][1]["net_profit"], 1300.0)
		self.assertEqual(report["rows"][1]["margin_pct"], 26.0)

	def test_this_day_is_marked_so_far(self):
		report = self.report([], [], [])
		self.assertEqual([r["period"] for r in report["rows"] if r["so_far"]], ["2026-10-13"])

	def test_the_waterfall_balances(self):
		rows = [profit_line("2026-10-05", "A", 100000, 100, recorded=62000)]
		steps = self.report(rows, [expense("2026-10-06", "Rent", 15000)], [sale("2026-10-05", 100000)])[
			"series"
		]["waterfall"]
		self.assertEqual([s["key"] for s in steps], ["sales", "cost_of_goods", "running_costs", "net_profit"])
		sales, cost, running, net = (s["amount"] for s in steps)
		self.assertEqual(sales - cost - running, net)

	def test_expenses_outside_both_periods_are_ignored(self):
		summary = self.report([], [expense("2026-01-01", "Rent", 999)], [])["summary"]
		self.assertEqual((summary["running_costs"], summary["previous_running_costs"]), (0.0, 0.0))

	def test_running_costs_of_the_previous_period_are_kept_for_comparison(self):
		summary = self.report([], [expense("2026-09-01", "Rent", 300)], [])["summary"]
		self.assertEqual(summary["previous_running_costs"], 300.0)


# --- money owed -----------------------------------------------------------------------------------------


def owed(customer, day, amount, name=None, mobile=None):
	return {
		"customer": customer,
		"customer_name": name or customer,
		"mobile_no": mobile,
		"posting_date": D(day),
		"outstanding_amount": amount,
	}


class TestMoneyOwedReport(unittest.TestCase):
	def test_the_age_bands_follow_the_spec(self):
		self.assertEqual(
			[calc.age_band(d) for d in (0, 7, 8, 14, 15, 30, 31, 400)],
			["0_7", "0_7", "8_14", "8_14", "15_30", "15_30", "over_30", "over_30"],
		)

	def test_age_is_days_since_the_sale_and_never_negative(self):
		self.assertEqual(calc.age_in_days("2026-10-06", TODAY), 7)
		self.assertEqual(calc.age_in_days(D("2026-10-13"), TODAY), 0)
		self.assertEqual(calc.age_in_days("2026-10-20", TODAY), 0)

	def test_a_customer_is_placed_in_a_band_for_each_unpaid_sale(self):
		rows = [
			owed("C1", "2026-10-12", 500, "Wanjiru"),
			owed("C1", "2026-09-01", 1500, "Wanjiru"),
			owed("C2", "2026-10-01", 2000, "Brian"),
		]
		report = calc.owed_report(rows, TODAY)
		first = report["rows"][0]
		self.assertEqual(first["name"], "Brian")  # 2,000 against Wanjiru's 2,000: the tie goes to the name
		wanjiru = next(r for r in report["rows"] if r["customer"] == "C1")
		self.assertEqual(wanjiru["amount"], 2000.0)
		self.assertEqual(wanjiru["bands"], {"0_7": 500.0, "8_14": 0.0, "15_30": 0.0, "over_30": 1500.0})
		self.assertEqual(wanjiru["oldest_days"], 42)
		self.assertEqual(wanjiru["oldest_sale"], "2026-09-01")

	def test_the_biggest_debt_comes_first(self):
		report = calc.owed_report(
			[owed("A", "2026-10-10", 100), owed("B", "2026-10-10", 900), owed("C", "2026-10-10", 400)], TODAY
		)
		self.assertEqual([r["customer"] for r in report["rows"]], ["B", "C", "A"])

	def test_the_total_is_the_home_money_owed_tile(self):
		rows = [owed("C1", "2026-10-12", 500), owed("C1", "2026-09-01", 1500), owed("C2", "2026-10-01", 2000)]
		summary = calc.owed_report(rows, TODAY)["summary"]
		home = oc.money_owed_summary(rows)
		self.assertEqual((summary["amount"], summary["customers"]), (home["amount"], home["customers"]))
		self.assertEqual((summary["amount"], summary["customers"]), (4000.0, 2))

	def test_the_bands_add_up_to_the_total(self):
		rows = [
			owed("C1", "2026-10-12", 500),
			owed("C1", "2026-09-01", 1500),
			owed("C2", "2026-10-01", 2000),
			owed("C3", "2026-09-25", 250.5),
		]
		report = calc.owed_report(rows, TODAY)
		self.assertEqual(
			round(sum(b["amount"] for b in report["series"]["age_bands"]), 2), report["summary"]["amount"]
		)
		self.assertEqual(
			round(sum(sum(r["bands"].values()) for r in report["rows"]), 2), report["summary"]["amount"]
		)
		self.assertEqual(
			[b["key"] for b in report["series"]["age_bands"]], ["0_7", "8_14", "15_30", "over_30"]
		)
		self.assertEqual([b["customers"] for b in report["series"]["age_bands"]], [1, 1, 1, 1])

	def test_the_part_over_30_days(self):
		summary = calc.owed_report([owed("C1", "2026-09-01", 300), owed("C2", "2026-10-12", 100)], TODAY)[
			"summary"
		]
		self.assertEqual(
			(summary["over_30_amount"], summary["over_30_share_pct"], summary["oldest_days"]),
			(300.0, 75.0, 42),
		)

	def test_nothing_owed(self):
		report = calc.owed_report([], TODAY)
		self.assertEqual(
			(report["summary"]["amount"], report["summary"]["customers"], report["rows"]), (0.0, 0, [])
		)
		self.assertEqual(report["summary"]["over_30_share_pct"], 0.0)

	def test_a_paid_or_overpaid_sale_is_not_counted(self):
		report = calc.owed_report(
			[owed("C1", "2026-10-10", 0), owed("C2", "2026-10-10", -50), owed("C3", "2026-10-10", 80)], TODAY
		)
		self.assertEqual([r["customer"] for r in report["rows"]], ["C3"])

	def test_the_list_is_cut_but_the_count_says_how_many(self):
		rows = [owed(f"C{i}", "2026-10-10", 10 + i) for i in range(7)]
		report = calc.owed_report(rows, TODAY, limit=3)
		self.assertEqual((len(report["rows"]), report["rows_total"]), (3, 7))

	def test_the_phone_number_is_kept_and_a_missing_name_falls_back_to_the_customer(self):
		rows = [
			{
				"customer": "C9",
				"customer_name": None,
				"mobile_no": "0712345678",
				"posting_date": D("2026-10-10"),
				"outstanding_amount": 50,
			}
		]
		row = calc.owed_report(rows, TODAY)["rows"][0]
		self.assertEqual((row["name"], row["mobile"]), ("C9", "0712345678"))


# --- running costs --------------------------------------------------------------------------------------


class TestRunningCostsReport(unittest.TestCase):
	ROWS: ClassVar[list] = [
		expense("2026-10-02", "Rent - DS", 12000, "Rent", 1),
		expense("2026-10-05", "Electricity - DS", 2500, "Electricity", 2),
		expense("2026-10-09", "Electricity - DS", 1500, "Electricity", 1),
		expense("2026-10-13", "Transport - DS", 400, None, 1),
		expense("2026-09-05", "Rent - DS", 12000, "Rent", 1),
		expense("2026-01-05", "Rent - DS", 99999, "Rent", 1),
	]

	def setUp(self):
		self.report = calc.running_costs_report(self.ROWS, PERIOD_30, TODAY)

	def test_each_category_adds_its_days_and_the_biggest_comes_first(self):
		self.assertEqual(
			[(c["category"], c["amount"]) for c in self.report["rows"]],
			[("Rent", 12000.0), ("Electricity", 4000.0), ("Transport", 400.0)],
		)

	def test_the_total_is_the_sum_of_the_categories(self):
		summary = self.report["summary"]
		self.assertEqual(summary["total"], 16400.0)
		self.assertEqual(round(sum(c["amount"] for c in self.report["rows"]), 2), summary["total"])

	def test_the_period_before_is_kept_for_comparison_and_older_entries_are_ignored(self):
		summary = self.report["summary"]
		self.assertEqual(summary["previous_total"], 12000.0)
		self.assertEqual(summary["change_pct"], oc.pct_change(16400, 12000))

	def test_shares_and_the_biggest(self):
		self.assertEqual(self.report["rows"][0]["share_pct"], 73.2)
		self.assertEqual(
			self.report["summary"]["biggest"], {"category": "Rent", "amount": 12000.0, "share_pct": 73.2}
		)
		self.assertEqual(self.report["summary"]["categories"], 3)

	def test_a_category_without_a_name_loses_the_business_code(self):
		self.assertEqual(calc.category_name({"account": "Transport - DS", "account_name": None}), "Transport")
		self.assertEqual(calc.display_name("Stores - Main Branch"), "Stores - Main Branch")
		self.assertEqual(calc.display_name("Rent"), "Rent")

	def test_the_days_are_every_day_of_the_period_with_zeros_and_today_so_far(self):
		days = self.report["series"]["by_day"]
		self.assertEqual(len(days), 30)
		self.assertEqual(next(d for d in days if d["date"] == "2026-10-02")["amount"], 12000.0)
		self.assertEqual(next(d for d in days if d["date"] == "2026-10-03")["amount"], 0.0)
		self.assertEqual([d["date"] for d in days if d["so_far"]], ["2026-10-13"])
		self.assertEqual(round(sum(d["amount"] for d in days), 2), self.report["summary"]["total"])

	def test_the_chart_shows_six_categories_and_folds_the_rest_into_other(self):
		rows = [expense("2026-10-02", f"Cost {i} - DS", 1000 - i * 10, f"Cost {i}") for i in range(9)]
		report = calc.running_costs_report(rows, PERIOD_30, TODAY)
		chart = report["series"]["categories"]
		self.assertEqual(len(chart), 7)
		self.assertTrue(chart[-1]["other"])
		self.assertEqual(chart[-1]["amount"], 940 + 930 + 920)
		self.assertEqual(round(sum(c["amount"] for c in chart), 2), report["summary"]["total"])
		self.assertEqual(len(report["rows"]), 9)

	def test_a_category_that_nets_to_nothing_is_left_out(self):
		rows = [
			expense("2026-10-02", "Refunded - DS", 500, "Refunded"),
			expense("2026-10-03", "Refunded - DS", -500, "Refunded"),
		]
		self.assertEqual(calc.running_costs_report(rows, PERIOD_30, TODAY)["rows"], [])

	def test_no_expenses(self):
		report = calc.running_costs_report([], PERIOD_30, TODAY)
		self.assertEqual(
			(report["summary"]["total"], report["summary"]["biggest"], report["rows"]), (0.0, None, [])
		)
		self.assertIsNone(report["summary"]["change_pct"])


# --- stock ----------------------------------------------------------------------------------------------


class TestStockReports(unittest.TestCase):
	def test_what_is_in_stock_by_store(self):
		rows = [
			{"warehouse": "Stores - DS", "total_qty": 120, "total_value": 30000},
			{"warehouse": "Shop floor - DS", "total_qty": 40, "total_value": 10000},
			{"warehouse": "Empty - DS", "total_qty": 0, "total_value": 0},
		]
		report = calc.stock_summary_report(rows)
		self.assertEqual([s["store"] for s in report["rows"]], ["Stores", "Shop floor"])
		self.assertEqual(report["summary"], {"stores": 2, "quantity": 160.0, "value": 40000.0})
		self.assertEqual([s["share_pct"] for s in report["rows"]], [75.0, 25.0])

	def test_what_it_is_worth_by_category(self):
		rows = [
			{"item_group": "Drinks", "total_value": 6000, "total_qty": 100, "item_count": 4},
			{"item_group": "Snacks", "total_value": 14000, "total_qty": 300, "item_count": 9},
		]
		report = calc.stock_value_report(rows)
		self.assertEqual([c["category"] for c in report["rows"]], ["Snacks", "Drinks"])
		self.assertEqual(report["summary"]["value"], 20000.0)
		self.assertEqual(report["summary"]["items"], 13)
		self.assertEqual(
			report["summary"]["biggest"], {"category": "Snacks", "value": 14000.0, "share_pct": 70.0}
		)
		self.assertEqual(round(sum(c["amount"] for c in report["series"]["by_category"]), 2), 20000.0)

	def test_no_stock(self):
		self.assertEqual(calc.stock_value_report([])["summary"]["value"], 0.0)
		self.assertIsNone(calc.stock_value_report([])["summary"]["biggest"])
		self.assertEqual(calc.stock_summary_report([])["rows"], [])


def entry(code, warehouse, qty, age, sold=0.0):
	return {
		"item_code": code,
		"warehouse": warehouse,
		"actual_qty": qty,
		"age_days": age,
		"movement_rate": sold,
		"is_slow_moving": sold < 1,
	}


class TestAgingStockReport(unittest.TestCase):
	BUCKETS: ClassVar[dict] = {
		"0-30": [entry("A", "W1", 10, 12, sold=5.0), entry("B", "W1", 4, 20)],
		"31-60": [entry("C", "W1", 6, 45)],
		"61-90": [],
		"90+": [entry("D", "W1", 3, 200), entry("D", "W2", 2, 150), entry("E", "W1", 0, 300)],
	}
	NAMES: ClassVar[dict] = {"A": "Soap", "B": "Milk", "C": "Sugar", "D": "Rice", "E": "Gone"}
	RATES: ClassVar[dict] = {
		("A", "W1"): 10.0,
		("B", "W1"): 50.0,
		("C", "W1"): 100.0,
		("D", "W1"): 20.0,
		("D", "W2"): 30.0,
	}

	def report(self, sold_recently=()):
		return calc.aging_stock_report(self.BUCKETS, self.NAMES, self.RATES, sold_recently)

	def test_a_product_that_sold_is_not_listed_and_nor_is_one_with_no_stock(self):
		codes = [r["item_code"] for r in self.report()["rows"]]
		self.assertEqual(codes, ["C", "B", "D"])  # the biggest value first
		self.assertNotIn("A", codes)
		self.assertNotIn("E", codes)

	def test_a_product_across_two_stores_is_one_row(self):
		rice = next(r for r in self.report()["rows"] if r["item_code"] == "D")
		self.assertEqual(
			(rice["quantity"], rice["value"], rice["age_days"], rice["stores"]), (5.0, 120.0, 200, 2)
		)
		self.assertEqual(rice["name"], "Rice")

	def test_a_till_sale_with_no_stock_movement_yet_still_counts_as_selling(self):
		codes = [r["item_code"] for r in self.report(sold_recently=["C"])["rows"]]
		self.assertEqual(codes, ["B", "D"])

	def test_the_summary_and_the_age_bands_add_up(self):
		report = self.report()
		self.assertEqual(report["summary"]["products"], 3)
		self.assertEqual(report["summary"]["products_in_stock"], 4)
		self.assertEqual(report["summary"]["value"], 920.0)
		self.assertEqual(report["summary"]["share_of_products_pct"], 75.0)
		self.assertEqual(report["summary"]["oldest_days"], 200)
		bands = {b["key"]: (b["value"], b["products"]) for b in report["series"]["age_bands"]}
		self.assertEqual(
			bands, {"0_30": (200.0, 1), "31_60": (600.0, 1), "61_90": (0.0, 0), "over_90": (120.0, 1)}
		)
		self.assertEqual(
			round(sum(b["value"] for b in report["series"]["age_bands"]), 2), report["summary"]["value"]
		)

	def test_the_age_bands_of_stock(self):
		self.assertEqual(
			[calc.stock_age_band(d) for d in (0, 30, 31, 60, 61, 90, 91)],
			["0_30", "0_30", "31_60", "31_60", "61_90", "61_90", "over_90"],
		)

	def test_everything_selling_gives_an_empty_list(self):
		report = self.report(sold_recently=["B", "C", "D"])
		self.assertEqual(
			(report["rows"], report["summary"]["products"], report["summary"]["value"]), ([], 0, 0.0)
		)

	def test_nothing_in_stock(self):
		report = calc.aging_stock_report({"0-30": []}, {}, {})
		self.assertEqual((report["rows"], report["summary"]["share_of_products_pct"]), ([], 0.0))


class TestHelpers(unittest.TestCase):
	def test_share(self):
		self.assertEqual(calc.share(1, 3), 33.3)
		self.assertEqual(calc.share(5, 0), 0.0)

	def test_the_choice_of_day_or_month(self):
		self.assertEqual(calc.choose_group(30), "day")
		self.assertEqual(calc.choose_group(62), "day")
		self.assertEqual(calc.choose_group(63), "month")
		self.assertEqual(calc.choose_group(10, "month"), "month")
		self.assertEqual(calc.choose_group(100, "day"), "day")
		self.assertEqual(calc.choose_group(100, "year"), "month")

	def test_previews(self):
		self.assertEqual(
			calc.preview_of("owed", {"amount": 5.0, "customers": 2}), {"amount": 5.0, "customers": 2}
		)
		self.assertIsNone(calc.preview_of("aging-stock", {"products": 1}))
		self.assertIsNone(calc.preview_of("sales", {}))

	def test_the_pure_module_needs_no_database(self):
		import inspect

		self.assertNotIn("import frappe", inspect.getsource(calc))


# --- the calls ------------------------------------------------------------------------------------------

EXPENSES = [
	expense("2026-10-06", "Rent - SA", 500, "Rent"),
	expense("2026-10-07", "Power - SA", 100, "Power"),
	expense("2026-09-01", "Rent - SA", 200, "Rent"),
]
OWED_ROWS = [
	owed("C1", "2026-09-23", 3500, "Wanjiru Kamau", "0712345678"),
	owed("C2", "2026-10-10", 21000, "Brian Otieno"),
]
STOCK_BY_STORE = [{"warehouse": "Main - SA", "total_qty": 50, "total_value": 5000}]
STOCK_BY_CATEGORY = [{"item_group": "Drinks", "total_value": 5000, "total_qty": 50, "item_count": 4}]
AGING = {"0-30": [entry("X", "Main - SA", 5, 20)], "31-60": [], "61-90": [], "90+": []}


class ReportCase(OverviewCase):
	"""A signed-in user, Home's reads and this file's reads replaced by fixed data, and a dictionary as the cache."""

	def setUp(self):
		super().setUp()
		self.legacy = MagicMock()
		self.legacy.inventory_summary_report.return_value = {"success": True, "data": STOCK_BY_STORE}
		self.legacy.inventory_value_by_category_report.return_value = {
			"success": True,
			"data": STOCK_BY_CATEGORY,
		}
		self.legacy.stock_aging_report.return_value = {"success": True, "data": AGING}

	def as_user(self, *args, **kwargs):
		super().as_user(*args, **kwargs)
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "_now_utc"), return_value=NOW)
		self.start((api, "_cache_get"), side_effect=lambda key: self.cache.get(key))
		self.start((api, "_cache_set"), side_effect=self.cache.__setitem__)
		self.mine(
			"_expense_rows",
			lambda scope, start, end: [dict(r) for r in EXPENSES if start <= r["posting_date"] <= end],
		)
		self.mine("_owed_rows", lambda scope: [dict(r) for r in OWED_ROWS])
		self.mine("_item_names", lambda codes: {"X": "Cola"})
		self.mine("_valuation_rates", lambda codes: {("X", "Main - SA"): 40.0})
		self.start((api, "_legacy"), return_value=self.legacy)
		# Home's own money-owed and cost reads agree with this file's, as they do on a real shop
		self.reads["_money_owed_rows"].side_effect = lambda scope: [
			{"customer": "C1", "outstanding_amount": 3500},
			{"customer": "C2", "outstanding_amount": 21000},
		]

	def mine(self, name, function):
		mock = self.start((api, name), side_effect=function)
		self.reads[f"mine.{name}"] = mock
		return mock

	def run_report(self, name, **kwargs):
		return api.run_report(name, **kwargs)

	def data(self, name="sales", **kwargs):
		result = self.run_report(name, **kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]

	def home(self, **kwargs):
		result = overview.get_overview(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]


class TestReportsAgreeWithHome(ReportCase):
	"""For the same period a report and the Home overview give the same figures (the ticket's rule)."""

	def setUp(self):
		super().setUp()
		self.as_user()

	def test_revenue_and_the_number_of_sales(self):
		report, home = self.data("sales")["summary"], self.home()["tiles"]["revenue"]
		self.assertEqual(report["revenue"], home["value"])
		self.assertEqual(report["previous_revenue"], home["previous"])
		self.assertEqual(report["change_pct"], home["change_pct"])
		self.assertEqual(report["revenue"], 34950.0)

	def test_how_customers_paid_and_the_top_products(self):
		series, charts = self.data("sales")["series"], self.home()["charts"]
		self.assertEqual(series["payments"], charts["payments"])
		self.assertEqual(series["top_products"], charts["top_products"])

	def test_the_days_of_the_sales_chart(self):
		report, home = self.data("sales"), self.home()["charts"]["sales"]
		by_date = {p["date"]: p for p in report["series"]["sales"]}
		for point in home:  # Home leaves today out of the line; every other day matches
			self.assertEqual(by_date[point["date"]]["revenue"], point["revenue"])
			self.assertEqual(by_date[point["date"]]["count"], point["count"])
			self.assertEqual(by_date[point["date"]]["previous"]["revenue"], point["previous"]["revenue"])

	def test_net_profit_and_its_margin(self):
		report, home = self.data("profit")["summary"], self.home()["tiles"]["net_profit"]
		self.assertEqual(report["net_profit"], home["value"])
		self.assertEqual(report["margin_pct"], home["margin_pct"])
		self.assertEqual(report["previous_net_profit"], home["previous"])
		self.assertEqual(report["change_pct"], home["change_pct"])
		self.assertEqual(report["uncosted_products"], home["uncosted_products"])
		self.assertEqual(report["estimated"], home["estimated"])
		self.assertEqual(report["running_costs"], 600.0)

	def test_the_profit_per_day_matches_the_home_chart(self):
		report, home = self.data("profit"), self.home()["charts"]["sales"]
		# Home's chart shows profit before running costs; the report's rows take the running costs off on the day they fell
		running = {r["period"]: r["running_costs"] for r in report["rows"]}
		by_date = {r["period"]: r for r in report["rows"]}
		for point in home:
			self.assertEqual(by_date[point["date"]]["net_profit"] + running[point["date"]], point["profit"])

	def test_money_owed(self):
		report, home = self.data("owed")["summary"], self.home()["tiles"]["money_owed"]
		self.assertEqual((report["amount"], report["customers"]), (home["amount"], home["customers"]))

	def test_the_choice_of_period_changes_both_the_same_way(self):
		report = self.data("sales", period="7d")["summary"]
		home = self.home(period="7d")["tiles"]["revenue"]
		self.assertEqual((report["revenue"], report["previous_revenue"]), (home["value"], home["previous"]))
		report = self.data("profit", period="custom", from_date="2026-10-01", to_date="2026-10-10")["summary"]
		home = self.home(period="custom", from_date="2026-10-01", to_date="2026-10-10")["tiles"]["net_profit"]
		self.assertEqual(report["net_profit"], home["value"])


class TestTheAnswerForAnOwner(ReportCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_the_shape_of_every_report(self):
		for name in calc.REPORTS:
			with self.subTest(report=name):
				data = self.data(name)
				for key in (
					"name",
					"role",
					"period",
					"as_of",
					"updated_at",
					"cached",
					"summary",
					"series",
					"rows",
					"rows_total",
					"errors",
				):
					self.assertIn(key, data)
				self.assertEqual(
					(data["name"], data["role"], data["cached"], data["errors"]), (name, "owner", False, {})
				)
				self.assertEqual(data["as_of"], "2026-10-13")
				self.assertEqual(data["rows_total"], len(data["rows"]))

	def test_the_default_period_is_the_last_30_days_with_the_one_before_it(self):
		period = self.data("sales")["period"]
		self.assertEqual(
			(period["key"], period["from"], period["to"], period["days"]),
			("30d", "2026-09-14", "2026-10-13", 30),
		)
		self.assertEqual((period["previous_from"], period["previous_to"]), ("2026-08-15", "2026-09-13"))
		self.assertEqual(period["days_of_data"], 30)

	def test_the_reports_about_now_have_no_period(self):
		for name in ("owed", "stock-summary", "stock-value", "aging-stock"):
			self.assertIsNone(self.data(name)["period"])

	def test_a_manager_gets_the_same_as_an_owner(self):
		owner = self.data("profit")["summary"]
		self.cache.clear()
		self.as_user("manager@shop-a.test", roles=("Stock Manager",))
		data = self.data("profit")
		self.assertEqual(data["role"], "manager")
		self.assertEqual(data["summary"], owner)

	def test_the_name_may_be_typed_in_capitals(self):
		self.assertEqual(self.data(" Sales ")["name"], "sales")

	def test_the_profit_choice_of_day_or_month_is_passed_on(self):
		self.assertEqual(self.data("profit")["group_by"], "day")
		self.assertEqual(self.data("profit", group_by="month")["group_by"], "month")
		self.assertNotIn("group_by", self.data("sales"))

	def test_the_owed_report_lists_each_customer_with_their_phone(self):
		rows = self.data("owed")["rows"]
		self.assertEqual([r["name"] for r in rows], ["Brian Otieno", "Wanjiru Kamau"])
		self.assertEqual(rows[1]["mobile"], "0712345678")

	def test_the_stock_reports_use_the_current_reports_with_the_company_and_store(self):
		self.data("stock-summary", store="Main - SA")
		self.legacy.inventory_summary_report.assert_called_once_with(company="Shop A", warehouse="Main - SA")
		self.data("stock-value")
		self.legacy.inventory_value_by_category_report.assert_called_once_with(
			company="Shop A", warehouse=None
		)

	def test_what_is_not_selling_adds_names_and_values(self):
		data = self.data("aging-stock")
		self.assertEqual(data["rows"][0]["name"], "Cola")
		self.assertEqual(data["rows"][0]["value"], 200.0)
		self.legacy.stock_aging_report.assert_called_once_with(company="Shop A", warehouse=None)

	def test_a_current_report_that_fails_is_a_plain_failure_without_its_detail(self):
		self.legacy.inventory_summary_report.return_value = {"success": False, "message": "secret db detail"}
		result = self.run_report("stock-summary")
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "Could not load this report. Please try again.")
		self.assertNotIn("secret", json.dumps(result))
		frappe.log_error.assert_called()

	def test_a_bad_period_is_refused_before_anything_is_read(self):
		with self.assertRaises(frappe.ValidationError):
			self.run_report("sales", period="decade")
		with self.assertRaises(frappe.ValidationError):
			self.run_report("sales", period="custom")
		self.reads["_sale_day_rows"].assert_not_called()

	def test_an_unknown_report_is_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.run_report("balance-sheet")
		with self.assertRaises(frappe.DoesNotExistError):
			self.run_report(None)


class TestTheAnswerForACashier(ReportCase):
	USER = "till@shop-a.test"

	def setUp(self):
		super().setUp()
		self.as_user(self.USER, roles=("Sales User",))

	def test_the_sales_report_is_limited_to_their_own_sales_whoever_they_ask_for(self):
		self.data("sales", store=None)
		for name in ("_sale_day_rows", "_line_rows", "_payment_rows"):
			scope = self.reads[name].call_args_list[0].args[0]
			with self.subTest(read=name):
				self.assertEqual(scope.owner, self.USER)

	def test_the_sales_report_has_no_profit_cost_or_owed_field_anywhere(self):
		data = self.data("sales")
		self.assertEqual(data["role"], "cashier")
		text = json.dumps(data, default=str)
		for word in ("profit", "cost", "owed", "uncosted"):
			self.assertNotIn(word, text)

	def test_every_other_report_is_refused_before_anything_is_read(self):
		for name in calc.REPORTS:
			if name == "sales":
				continue
			with self.subTest(report=name), self.assertRaises(frappe.PermissionError):
				self.run_report(name)
		for key, mock in self.reads.items():
			with self.subTest(read=key):
				mock.assert_not_called()
		self.assertEqual(self.legacy.method_calls, [])
		self.assertEqual(self.cache, {})

	def test_the_cost_and_owed_reads_are_never_made_for_the_sales_report(self):
		self.data("sales")
		for name in ("_money_owed_rows", "_expenses", "mine._expense_rows", "mine._owed_rows", "_cost_rates"):
			self.reads[name].assert_not_called()

	def test_the_landing_page_has_the_one_question(self):
		data = api.list_reports()["data"]
		self.assertEqual(data["role"], "cashier")
		self.assertEqual([q["key"] for q in data["questions"]], ["sold"])

	def test_a_cashiers_answer_is_kept_per_cashier(self):
		self.data("sales")
		self.as_user("other@shop-a.test", roles=("Sales User",))
		self.assertIs(self.data("sales")["cached"], False)


class TestWhoIsLetIn(ReportCase):
	def test_a_guest_is_refused_and_nothing_is_read(self):
		self.as_user("Guest", roles=("Guest",))
		with self.assertRaises(frappe.AuthenticationError):
			self.run_report("sales")
		with self.assertRaises(frappe.AuthenticationError):
			api.list_reports()
		self.reads["_sale_day_rows"].assert_not_called()
		self.assertEqual(self.cache, {})

	def test_a_store_of_another_business_is_refused_before_anything_else(self):
		self.as_user(warehouse_owner="Shop B")
		with self.assertRaises(frappe.PermissionError):
			self.run_report("sales", store="Other Store - SB")
		with self.assertRaises(frappe.PermissionError):
			self.run_report("nonsense", store="Other Store - SB")
		self.reads["_sale_day_rows"].assert_not_called()

	def test_a_store_of_your_own_business_narrows_the_reads(self):
		self.as_user(warehouse_owner="Shop A")
		self.data("sales", store="Main Store - SA")
		self.assertEqual(self.reads["_sale_day_rows"].call_args_list[0].args[0].store, "Main Store - SA")
		self.data("profit", store="Main Store - SA")
		self.assertEqual(self.reads["_line_rows"].call_args_list[0].args[0].store, "Main Store - SA")


class TestAPartThatFails(ReportCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_one_failing_part_of_the_sales_report_is_null_and_named_while_the_rest_returns(self):
		self.fail("_payment_rows")
		result = self.run_report("sales")
		self.assertTrue(result["success"])
		data = result["data"]
		self.assertIsNone(data["series"]["payments"])
		self.assertEqual(data["errors"], {"payments": "Could not load this."})
		self.assertEqual(data["summary"]["revenue"], 34950.0)
		self.assertTrue(data["series"]["top_products"])

	def test_the_real_error_is_logged_and_never_sent_out(self):
		self.fail("_line_rows")
		result = self.run_report("sales")
		self.assertNotIn("secret", json.dumps(result, default=str))
		frappe.log_error.assert_called()
		self.assertIsNone(result["data"]["series"]["top_products"])

	def test_a_partial_answer_is_not_cached(self):
		self.fail("_payment_rows")
		self.run_report("sales")
		self.assertEqual(self.cache, {})

	def test_the_sales_themselves_failing_is_a_failure(self):
		self.fail("_sale_day_rows")
		result = self.run_report("sales")
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "Could not load this report. Please try again.")
		self.assertNotIn("secret", json.dumps(result))

	def test_the_profit_report_fails_whole_if_the_lines_cannot_be_read(self):
		self.fail("_line_rows")
		self.assertFalse(self.run_report("profit")["success"])

	def test_the_landing_page_still_answers_when_one_preview_fails(self):
		self.fail("_line_rows")
		questions = {q["key"]: q for q in api.list_reports(preview=1)["data"]["questions"]}
		self.assertIsNone(questions["profit"]["preview"])
		self.assertEqual(questions["sold"]["preview"]["revenue"], 34950.0)
		self.assertEqual(questions["owed"]["preview"], {"amount": 24500.0, "customers": 2})


class TestTheCache(ReportCase):
	def test_the_answer_is_kept_and_served_again_without_recomputing(self):
		self.as_user()
		first = self.data("sales")
		reads = self.reads["_sale_day_rows"].call_count
		again = self.data("sales")
		self.assertIs(first["cached"], False)
		self.assertIs(again["cached"], True)
		self.assertEqual(again["summary"], first["summary"])
		self.assertEqual(self.reads["_sale_day_rows"].call_count, reads)
		self.assertEqual(len(self.cache), 1)

	def test_the_key_differs_by_report_role_company_store_person_period_dates_and_choice(self):
		def key(**changes):
			args = dict(
				name="profit",
				tier="owner",
				company="Shop A",
				store=None,
				owner=None,
				period=PERIOD_30,
				group_by=None,
			)
			args.update(changes)
			return api.cache_key(**args)

		base = key()
		variants = [
			key(name="sales"),
			key(tier="manager"),
			key(tier="cashier", owner="a@b.test"),
			key(company="Shop B"),
			key(store="Main"),
			key(period=oc.resolve_period("7d", None, None, TODAY)),
			key(period=oc.resolve_period("custom", "2026-10-01", "2026-10-05", TODAY)),
			key(period=oc.resolve_period("custom", "2026-10-02", "2026-10-05", TODAY)),
			key(group_by="month"),
		]
		self.assertEqual(len({base, *variants}), len(variants) + 1)

	def test_a_report_about_now_does_not_depend_on_the_period(self):
		def key(period):
			return api.cache_key(
				"owed", "owner", "Shop A", None, None, oc.resolve_period(period, None, None, TODAY)
			)

		self.assertEqual(key("7d"), key("30d"))

	def test_an_owners_answer_is_never_served_to_a_cashier(self):
		self.as_user()
		self.data("sales")
		self.as_user("till@shop-a.test", roles=("Sales User",))
		data = self.data("sales")
		self.assertIs(data["cached"], False)
		self.assertEqual(len(self.cache), 2)

	def test_a_cashier_cannot_reach_an_owners_cached_profit_report(self):
		self.as_user()
		self.data("profit")
		self.as_user("till@shop-a.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			self.run_report("profit")

	def test_a_manager_and_an_owner_do_not_share_an_answer(self):
		self.as_user()
		self.data("profit")
		self.as_user("manager@shop-a.test", roles=("Stock Manager",))
		data = self.data("profit")
		self.assertIs(data["cached"], False)
		self.assertEqual(data["role"], "manager")

	def test_the_cache_is_kept_for_sixty_seconds(self):
		self.assertEqual(api.CACHE_SECONDS, 60)
		cache = MagicMock()
		with patch.object(frappe, "cache", create=True, return_value=cache):
			api._cache_set("k", {"a": 1})
			api._cache_get("k")
		cache.set_value.assert_called_once_with("k", {"a": 1}, expires_in_sec=60)
		cache.get_value.assert_called_once_with("k")

	def test_a_guest_is_refused_before_the_cache_is_looked_at(self):
		self.as_user("Guest", roles=("Guest",))
		self.cache["anything"] = {"summary": {}}
		with self.assertRaises(frappe.AuthenticationError):
			self.run_report("sales")


class TestTheLandingPage(ReportCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_an_owner_gets_six_questions_and_no_numbers_unless_asked(self):
		data = api.list_reports()["data"]
		self.assertEqual(
			[q["key"] for q in data["questions"]], ["sold", "profit", "owed", "costs", "stock", "not_selling"]
		)
		self.assertTrue(all("preview" not in q for q in data["questions"]))
		self.reads["_sale_day_rows"].assert_not_called()

	def test_the_previews_are_the_one_line_answers(self):
		questions = {q["key"]: q["preview"] for q in api.list_reports(preview=1)["data"]["questions"]}
		self.assertEqual(questions["sold"]["revenue"], 34950.0)
		self.assertEqual(questions["profit"]["net_profit"], self.home()["tiles"]["net_profit"]["value"])
		self.assertEqual(questions["owed"], {"amount": 24500.0, "customers": 2})
		self.assertEqual(questions["costs"]["total"], 600.0)
		self.assertEqual(questions["stock"], {"value": 5000.0, "quantity": 50.0, "stores": 1})
		self.assertIsNone(questions["not_selling"])  # that card shows the question only
		self.legacy.stock_aging_report.assert_not_called()

	def test_the_previews_share_the_cache_with_the_reports(self):
		api.list_reports(preview=1)
		self.assertTrue(self.data("profit")["cached"])
		self.assertTrue(self.data("sales")["cached"])

	def test_the_preview_may_be_asked_for_in_the_words_a_browser_sends(self):
		for asked in ("1", "true", 1, True):
			questions = api.list_reports(preview=asked)["data"]["questions"]
			self.assertIn("preview", questions[0])
		self.assertNotIn("preview", api.list_reports(preview="0")["data"]["questions"][0])


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	def test_both_calls_are_whitelisted(self):
		for function in (api.run_report, api.list_reports):
			self.assertIn(function, frappe.whitelisted)

	def test_the_role_and_store_checks_come_before_any_read(self):
		import ast
		import inspect

		tree = ast.parse(inspect.getsource(api))
		function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_report")
		order = [
			ast.unparse(s).split("\n")[0]
			for s in function.body
			if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		]
		first_check = next(i for i, text in enumerate(order) if "ensure_record_access" in text)
		role_check = next(i for i, text in enumerate(order) if "_require(" in text)
		read = next(i for i, text in enumerate(order) if "_answer(" in text or "try:" in text)
		self.assertLess(first_check, role_check)
		self.assertLess(role_check, read)

	def test_the_stock_module_is_loaded_only_when_a_stock_report_runs(self):
		import inspect

		source = inspect.getsource(api)
		self.assertNotIn("\nfrom murzak_pos.api import reports\n", source)
		self.assertNotIn("\nimport erpnext", source)


if __name__ == "__main__":
	unittest.main()
