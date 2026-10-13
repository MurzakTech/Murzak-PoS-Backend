# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Home overview call (`dashboard_overview.get_overview`) and the arithmetic behind it
(`overview_calc`). They need no database: every Frappe call is mocked.

The first half tests the plain functions with plain rows. The second half tests the call itself: who is let in, what a
cashier does and does not receive, that one failing card does not take the rest with it, the 60-second cache, the
profit of sales still in an open till (ticket P1-9), the products without a cost price (ticket P2-7), and that the
call that lists them refuses a cashier before reading anything.
"""

import copy
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import dashboard_overview as overview
from murzak_pos.api import overview_calc as calc

TODAY = date(2026, 10, 13)  # a Tuesday
D = date.fromisoformat


class TestPercentChange(unittest.TestCase):
	def test_a_rise_and_a_fall(self):
		self.assertEqual(calc.pct_change(12450, 11000), 13.2)
		self.assertEqual(calc.pct_change(50, 100), -50.0)

	def test_no_previous_value_gives_none_never_a_division_by_zero(self):
		self.assertIsNone(calc.pct_change(500, 0))
		self.assertIsNone(calc.pct_change(500, None))
		self.assertIsNone(calc.pct_change(None, 100))
		self.assertIsNone(calc.pct_change(0, 0))

	def test_a_fall_from_a_negative_value_is_read_against_its_size(self):
		self.assertEqual(calc.pct_change(-50, -100), 50.0)

	def test_average_basket(self):
		self.assertEqual(calc.average_basket(12450, 9), 1383.33)
		self.assertEqual(calc.average_basket(0, 0), 0.0)
		self.assertEqual(calc.average_basket(500, 0), 0.0)


class TestPeriods(unittest.TestCase):
	def test_today(self):
		p = calc.resolve_period("today", None, None, TODAY)
		self.assertEqual((p["from"], p["to"], p["days"]), (TODAY, TODAY, 1))
		self.assertEqual((p["previous_from"], p["previous_to"]), (D("2026-10-12"), D("2026-10-12")))

	def test_seven_days_include_today(self):
		p = calc.resolve_period("7d", None, None, TODAY)
		self.assertEqual((p["from"], p["to"], p["days"]), (D("2026-10-07"), TODAY, 7))
		self.assertEqual((p["previous_from"], p["previous_to"]), (D("2026-09-30"), D("2026-10-06")))

	def test_thirty_days_match_the_contract_example(self):
		p = calc.resolve_period("30d", None, None, TODAY)
		self.assertEqual((str(p["from"]), str(p["to"])), ("2026-09-14", "2026-10-13"))
		self.assertEqual((str(p["previous_from"]), str(p["previous_to"])), ("2026-08-15", "2026-09-13"))
		self.assertEqual(p["days"], 30)

	def test_the_default_period_is_thirty_days(self):
		self.assertEqual(calc.resolve_period(None, None, None, TODAY)["key"], "30d")

	def test_month_so_far_and_the_same_number_of_days_before_the_first(self):
		p = calc.resolve_period("month", None, None, TODAY)
		self.assertEqual((p["from"], p["to"], p["days"]), (D("2026-10-01"), TODAY, 13))
		self.assertEqual((p["previous_from"], p["previous_to"]), (D("2026-09-18"), D("2026-09-30")))

	def test_weeks_start_on_monday(self):
		self.assertEqual(calc.week_start(D("2026-10-13")), D("2026-10-12"))  # Tuesday
		self.assertEqual(calc.week_start(D("2026-10-12")), D("2026-10-12"))  # Monday
		self.assertEqual(calc.week_start(D("2026-10-18")), D("2026-10-12"))  # Sunday
		p = calc.resolve_period("week", None, None, TODAY)
		self.assertEqual((p["from"], p["to"], p["days"]), (D("2026-10-12"), TODAY, 2))
		self.assertEqual((p["previous_from"], p["previous_to"]), (D("2026-10-10"), D("2026-10-11")))

	def test_custom_dates(self):
		p = calc.resolve_period("custom", "2026-10-01", "2026-10-10", TODAY)
		self.assertEqual(
			(p["days"], str(p["previous_from"]), str(p["previous_to"])), (10, "2026-09-21", "2026-09-30")
		)

	def test_a_custom_end_in_the_future_is_cut_back_to_today(self):
		self.assertEqual(calc.resolve_period("custom", "2026-10-10", "2026-12-31", TODAY)["to"], TODAY)

	def test_bad_periods_are_refused_with_a_reason(self):
		cases = [
			(("hourly", None, None), "bad_period"),
			(("custom", None, "2026-10-10"), "need_dates"),
			(("custom", "2026-10-10", None), "need_dates"),
			(("custom", "10/10/2026", "2026-10-12"), "bad_date"),
			(("custom", "2026-10-12", "2026-10-10"), "order"),
			(("custom", "2026-11-01", "2026-12-01"), "order"),
			(("custom", "2024-01-01", "2026-10-10"), "too_long"),
		]
		for args, code in cases:
			with self.subTest(args=args), self.assertRaises(calc.PeriodError) as caught:
				calc.resolve_period(*args, TODAY)
			self.assertEqual(caught.exception.code, code)

	def test_the_date_in_nairobi_not_in_utc(self):
		late = datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)  # 01:30 on the 13th in Nairobi
		self.assertEqual(calc.nairobi_today(late), D("2026-10-13"))
		self.assertEqual(calc.nairobi_today(datetime(2026, 10, 13, 20, 59)), D("2026-10-13"))
		self.assertEqual(calc.nairobi_today(datetime(2026, 10, 13, 21, 0)), D("2026-10-14"))

	def test_days_of_data(self):
		p = calc.resolve_period("30d", None, None, TODAY)
		self.assertEqual(calc.days_of_data(None, p), 0)
		self.assertEqual(calc.days_of_data(D("2026-08-20"), p), 30)  # sold before the period began
		self.assertEqual(calc.days_of_data(D("2026-10-09"), p), 5)  # a new business: 9th to 13th
		self.assertEqual(calc.days_of_data(D("2026-10-13"), p), 1)

	def test_period_payload_uses_text_dates(self):
		payload = calc.period_payload(calc.resolve_period("30d", None, None, TODAY), 30)
		self.assertEqual(payload["from"], "2026-09-14")
		self.assertEqual(payload["days_of_data"], 30)
		json.dumps(payload)


def sale(day, net, n=1, gross=None, is_return=0):
	return {"posting_date": D(day), "is_return": is_return, "net": net, "gross": gross or net, "n": n}


class TestSalesByDay(unittest.TestCase):
	def test_no_data_gives_nothing(self):
		self.assertEqual(calc.daily_totals([]), {})
		self.assertEqual(
			calc.sum_totals({}, D("2026-10-01"), TODAY), {"revenue": 0.0, "gross_total": 0.0, "count": 0}
		)

	def test_returns_take_value_off_and_are_not_counted_as_sales(self):
		totals = calc.daily_totals(
			[sale("2026-10-12", 8000, 6, 9280), sale("2026-10-12", -500, 1, -580, is_return=1)]
		)
		self.assertEqual(totals["2026-10-12"], {"revenue": 7500.0, "gross_total": 8700.0, "count": 6})

	def test_dates_as_text_work_too(self):
		totals = calc.daily_totals([{"date": "2026-10-12", "net": 10, "gross": 10, "n": 1, "is_return": 0}])
		self.assertEqual(totals["2026-10-12"]["revenue"], 10.0)

	def test_first_sale_date(self):
		self.assertIsNone(calc.first_sale_date([]))
		self.assertEqual(
			calc.first_sale_date([sale("2026-10-12", 1), sale("2026-10-02", 1)]), D("2026-10-02")
		)


class TestTheSalesSeries(unittest.TestCase):
	def test_one_day_of_data_is_zero_filled_and_leaves_today_out(self):
		period = calc.resolve_period("7d", None, None, TODAY)
		current = calc.daily_totals([sale("2026-10-12", 800, 4)])
		series = calc.build_sales_series(current, {}, period, TODAY)
		self.assertEqual([p["date"] for p in series], [f"2026-10-{d:02d}" for d in range(7, 13)])
		self.assertEqual([p["revenue"] for p in series], [0.0, 0.0, 0.0, 0.0, 0.0, 800.0])
		self.assertEqual(series[-1]["count"], 4)
		self.assertNotIn("profit", series[0])

	def test_today_is_left_out_even_when_it_has_sales(self):
		period = calc.resolve_period("7d", None, None, TODAY)
		series = calc.build_sales_series(calc.daily_totals([sale("2026-10-13", 999, 3)]), {}, period, TODAY)
		self.assertNotIn("2026-10-13", [p["date"] for p in series])

	def test_the_today_period_is_one_point_marked_so_far(self):
		period = calc.resolve_period("today", None, None, TODAY)
		series = calc.build_sales_series(calc.daily_totals([sale("2026-10-13", 999, 3)]), {}, period, TODAY)
		self.assertEqual(len(series), 1)
		self.assertEqual(
			(series[0]["date"], series[0]["revenue"], series[0]["so_far"]), ("2026-10-13", 999.0, True)
		)

	def test_thirty_days_have_twenty_nine_completed_points_and_each_has_its_previous_day(self):
		period = calc.resolve_period("30d", None, None, TODAY)
		current = calc.daily_totals([sale("2026-10-12", 800, 4)])
		before = calc.daily_totals(
			[sale("2026-09-12", 700, 5)]
		)  # 2026-10-12 is day 28 of 30; 09-12 is day 28 of its period
		series = calc.build_sales_series(current, before, period, TODAY)
		self.assertEqual(len(series), 29)
		last = series[-1]
		self.assertEqual(last["date"], "2026-10-12")
		self.assertEqual(last["previous"], {"revenue": 700.0, "count": 5})

	def test_profit_fields_appear_only_when_given(self):
		period = calc.resolve_period("7d", None, None, TODAY)
		series = calc.build_sales_series({}, {}, period, TODAY, {"2026-10-12": 210.5}, {"2026-10-05": 99.0})
		self.assertEqual(series[-1]["profit"], 210.5)
		self.assertEqual(series[-1]["previous"]["profit"], 99.0)  # 12 October lines up with 5 October
		self.assertEqual(series[0]["previous"]["profit"], 0.0)

	def test_the_first_of_the_month_has_no_completed_day(self):
		period = calc.resolve_period("month", None, None, D("2026-10-01"))
		self.assertEqual(calc.build_sales_series({}, {}, period, D("2026-10-01")), [])


class TestPaymentMethods(unittest.TestCase):
	def test_the_known_names(self):
		cases = {
			"M-Pesa": "M-Pesa",
			"MPESA": "M-Pesa",
			"Mpesa": "M-Pesa",
			"Lipa na M Pesa": "M-Pesa",
			"Cash": "Cash",
			"Credit Card": "Card",
			"Debit Card": "Card",
			"Card": "Card",
			"Credit": "On credit",
			"On credit": "On credit",
			"Bank Draft": "Other",
			"Cheque": "Other",
			"Wire Transfer": "Other",
			"Voucher": "Other",
			None: "Other",
		}
		for name, method in cases.items():
			with self.subTest(name=name):
				self.assertEqual(calc.classify_method(name), method)

	def test_a_card_is_never_on_credit(self):
		self.assertEqual(calc.classify_method("Credit Card"), "Card")

	def test_the_erpnext_type_places_a_mode_with_an_odd_name(self):
		self.assertEqual(calc.classify_method("Till drawer", "Cash"), "Cash")
		self.assertEqual(calc.classify_method("Till drawer", "Bank"), "Other")
		self.assertEqual(calc.classify_method("Wire Transfer", "Bank"), "Other")

	def test_grouping_adds_modes_together_and_orders_by_amount(self):
		rows = [
			{"mode_of_payment": "MPESA", "amount": 30000},
			{"mode_of_payment": "Mpesa", "amount": 11000},
			{"mode_of_payment": "Cash", "amount": 25500},
			{"mode_of_payment": "Credit Card", "amount": 2000},
			{"mode_of_payment": "Cheque", "amount": 300},
			{"mode_of_payment": "Bank Draft", "amount": 200},
		]
		got = calc.group_payments(rows, change_given=500, on_credit=5000)
		self.assertEqual(
			got,
			[
				{"method": "M-Pesa", "amount": 41000.0},
				{"method": "Cash", "amount": 25000.0},
				{"method": "On credit", "amount": 5000.0},
				{"method": "Card", "amount": 2000.0},
				{"method": "Other", "amount": 500.0},
			],
		)

	def test_nothing_gives_an_empty_list(self):
		self.assertEqual(calc.group_payments([]), [])

	def test_a_method_that_nets_to_nothing_is_left_out(self):
		rows = [{"mode_of_payment": "Cash", "amount": 100}, {"mode_of_payment": "Card", "amount": 0}]
		self.assertEqual(calc.group_payments(rows, change_given=100), [])

	def test_refunds_take_a_method_down(self):
		rows = [{"mode_of_payment": "Cash", "amount": 1000}, {"mode_of_payment": "Cash", "amount": -300}]
		self.assertEqual(calc.group_payments(rows), [{"method": "Cash", "amount": 700.0}])

	def test_payment_summary_counts_payments_of_one_method(self):
		rows = [
			{"mode_of_payment": "MPESA", "amount": 4000, "n": 20},
			{"mode_of_payment": "M-Pesa", "amount": 200, "n": 3},
			{"mode_of_payment": "Cash", "amount": 9800, "n": 5},
		]
		self.assertEqual(calc.payment_summary(rows, "M-Pesa"), {"amount": 4200.0, "payments": 23})
		self.assertEqual(calc.payment_summary([], "M-Pesa"), {"amount": 0.0, "payments": 0})


class TestCashInTill(unittest.TestCase):
	OPENING = (
		{"mode_of_payment": "Cash", "opening_amount": 2000},
		{"mode_of_payment": "MPESA", "opening_amount": 500},
	)

	def test_an_opened_till_with_no_sales_holds_the_float(self):
		self.assertEqual(
			calc.cash_in_till(self.OPENING, [], [], []), {"amount": 2000.0, "opening_float": 2000.0}
		)

	def test_cash_in_less_change_less_refunds_less_paid_out(self):
		payments = [
			{"is_return": 0, "mode_of_payment": "Cash", "amount": 9800},
			{"is_return": 0, "mode_of_payment": "MPESA", "amount": 4200},
			{"is_return": 1, "mode_of_payment": "Cash", "amount": -300},
		]
		change = [{"is_return": 0, "change": 200}]
		entries = [
			{"payment_type": "Pay", "mode_of_payment": "Cash", "amount": 500},
			{"payment_type": "Receive", "mode_of_payment": "Cash", "amount": 1000},
			{"payment_type": "Receive", "mode_of_payment": "MPESA", "amount": 777},
		]
		got = calc.cash_in_till(self.OPENING, payments, change, entries)
		self.assertEqual(
			got, {"amount": 11800.0, "opening_float": 2000.0}
		)  # 2000 + 9800 - 200 + 1000 - 300 - 500

	def test_no_opening_cash_row_means_a_float_of_zero(self):
		got = calc.cash_in_till([{"mode_of_payment": "MPESA", "opening_amount": 500}], [], [], [])
		self.assertEqual(got, {"amount": 0.0, "opening_float": 0.0})


class TestProductsAndHours(unittest.TestCase):
	def test_top_five_by_revenue(self):
		rows = [{"item_code": f"I{i}", "item_name": f"Item {i}", "net_amount": i * 100} for i in range(1, 9)]
		rows.append({"item_code": "I8", "item_name": "Item 8", "net_amount": 50})
		top = calc.top_products(rows)
		self.assertEqual([t["item_code"] for t in top], ["I8", "I7", "I6", "I5", "I4"])
		self.assertEqual(top[0], {"item_code": "I8", "name": "Item 8", "revenue": 850.0})

	def test_a_product_that_netted_to_nothing_is_left_out(self):
		rows = [
			{"item_code": "A", "item_name": "A", "net_amount": 100},
			{"item_code": "A", "net_amount": -100},
		]
		self.assertEqual(calc.top_products(rows), [])
		self.assertEqual(calc.top_products([]), [])

	def test_hours_run_from_8_to_19_and_are_averages(self):
		hours = calc.busiest_hours({9: 30, 14: 15, 7: 99, 22: 4}, 30)
		self.assertEqual([h["hour"] for h in hours], list(range(8, 20)))
		self.assertEqual(next(h for h in hours if h["hour"] == 9)["average_sales"], 1.0)
		self.assertEqual(next(h for h in hours if h["hour"] == 14)["average_sales"], 0.5)
		self.assertEqual(next(h for h in hours if h["hour"] == 8)["average_sales"], 0.0)

	def test_no_days_does_not_divide_by_zero(self):
		self.assertEqual(calc.busiest_hours({9: 3}, 0)[1]["average_sales"], 3.0)


class TestAttention(unittest.TestCase):
	def test_a_till_still_open_from_an_earlier_day(self):
		sessions = [
			{"name": "S-OLD", "period_start_date": datetime(2026, 10, 12, 8, 0)},
			{"name": "S-NEW", "period_start_date": datetime(2026, 10, 13, 8, 15)},
		]
		self.assertEqual(
			calc.open_session_items(sessions, TODAY),
			[{"type": "till_not_closed", "session": "S-OLD", "opened_on": "2026-10-12"}],
		)

	def test_out_of_stock_means_nothing_left_on_a_product_that_sold(self):
		sold = [{"item_code": "M", "item_name": "Milk"}, {"item_code": "S", "item_name": "Sugar"}]
		got = calc.out_of_stock_items(sold, {"M": 0, "S": 5})
		self.assertEqual([i["item_code"] for i in got], ["M"])
		self.assertEqual(calc.out_of_stock_items(sold, {"M": -2})[0]["item_code"], "M")  # negative stock
		self.assertEqual(len(calc.out_of_stock_items(sold, {})), 2)  # no stock record at all

	def test_running_low_needs_some_stock_at_or_below_the_warning(self):
		rows = [
			{"item_code": "A", "item_name": "A", "actual_qty": 4, "reorder_level": 10},
			{"item_code": "B", "item_name": "B", "actual_qty": 10, "reorder_level": 10},
			{"item_code": "C", "item_name": "C", "actual_qty": 11, "reorder_level": 10},
			{"item_code": "D", "item_name": "D", "actual_qty": 0, "reorder_level": 10},
			{"item_code": "E", "item_name": "E", "actual_qty": 1, "reorder_level": 0},
		]
		self.assertEqual({i["item_code"] for i in calc.running_low_items(rows)}, {"A", "B"})

	def test_running_low_shows_a_product_once_with_its_lowest_stock(self):
		rows = [
			{"item_code": "A", "item_name": "A", "warehouse": "W1", "actual_qty": 8, "reorder_level": 10},
			{"item_code": "A", "item_name": "A", "warehouse": "W2", "actual_qty": 3, "reorder_level": 10},
		]
		got = calc.running_low_items(rows)
		self.assertEqual(len(got), 1)
		self.assertEqual((got[0]["qty"], got[0]["warning_level"]), (3.0, 10.0))

	def overdue(self, posted, due=None, terms=False, owed=3500.0, customer="CUST-0004"):
		return {
			"customer": customer,
			"customer_name": "Wanjiru Kamau",
			"mobile_no": "0712345678",
			"posting_date": D(posted),
			"due_date": D(due) if due else None,
			"outstanding_amount": owed,
			"has_terms": terms,
		}

	def test_a_credit_sale_is_late_fourteen_days_after_the_sale_without_terms(self):
		got = calc.overdue_customer_items([self.overdue("2026-09-23")], TODAY)
		self.assertEqual(
			got,
			[
				{
					"type": "customer_overdue",
					"customer": "CUST-0004",
					"name": "Wanjiru Kamau",
					"mobile": "0712345678",
					"amount": 3500.0,
					"days_overdue": 6,
				}
			],
		)
		self.assertEqual(
			calc.overdue_customer_items([self.overdue("2026-09-29")], TODAY), []
		)  # due today: not late yet

	def test_the_invoice_due_date_is_used_only_when_the_customer_has_terms(self):
		with_terms = self.overdue("2026-09-01", due="2026-10-10", terms=True)
		self.assertEqual(calc.overdue_customer_items([with_terms], TODAY)[0]["days_overdue"], 3)
		not_yet = self.overdue("2026-09-01", due="2026-10-20", terms=True)
		self.assertEqual(calc.overdue_customer_items([not_yet], TODAY), [])
		# the due date ERPNext fills in on an invoice for a customer without terms is the day of the sale: ignored
		same_day = self.overdue("2026-10-05", due="2026-10-05", terms=False)
		self.assertEqual(calc.overdue_customer_items([same_day], TODAY), [])

	def test_one_item_per_customer_with_the_total_and_the_oldest_lateness(self):
		rows = [self.overdue("2026-09-01", owed=1000), self.overdue("2026-09-20", owed=2000)]
		got = calc.overdue_customer_items(rows, TODAY)
		self.assertEqual(len(got), 1)
		self.assertEqual((got[0]["amount"], got[0]["days_overdue"]), (3000.0, 28))

	def test_supplier_bills_due_within_three_days(self):
		rows = [
			{
				"name": "P1",
				"supplier": "S1",
				"supplier_name": "Brew Ltd",
				"outstanding_amount": 12000,
				"due_date": D("2026-10-15"),
			},
			{
				"name": "P2",
				"supplier": "S2",
				"supplier_name": "Far",
				"outstanding_amount": 100,
				"due_date": D("2026-10-17"),
			},
			{
				"name": "P3",
				"supplier": "S3",
				"supplier_name": "Late",
				"outstanding_amount": 50,
				"due_date": D("2026-10-01"),
			},
			{
				"name": "P4",
				"supplier": "S4",
				"supplier_name": "Paid",
				"outstanding_amount": 0,
				"due_date": D("2026-10-14"),
			},
		]
		got = calc.supplier_bill_items(rows, TODAY)
		self.assertEqual([i["invoice"] for i in got], ["P1", "P3"])
		self.assertEqual(
			got[0],
			{
				"type": "supplier_bill_due",
				"invoice": "P1",
				"supplier": "Brew Ltd",
				"amount": 12000.0,
				"due_on": "2026-10-15",
			},
		)

	def test_at_most_four_most_urgent_first(self):
		items = [
			{"type": "supplier_bill_due", "invoice": "P1", "due_on": "2026-10-15", "amount": 1},
			{"type": "customer_overdue", "name": "A", "amount": 5},
			{"type": "running_low", "name": "R", "qty": 4},
			{"type": "out_of_stock", "name": "O", "item_code": "O"},
			{"type": "till_not_closed", "session": "S", "opened_on": "2026-10-12"},
			{"type": "out_of_stock", "name": "Z", "item_code": "Z"},
		]
		got = calc.rank_attention(items, may_see_money=True)
		self.assertEqual(len(got), 4)
		self.assertEqual([i["urgency"] for i in got], [1, 2, 2, 3])
		self.assertEqual(
			[i["type"] for i in got], ["till_not_closed", "out_of_stock", "out_of_stock", "running_low"]
		)

	def test_within_a_kind_the_lowest_stock_and_the_biggest_debt_come_first(self):
		low = calc.rank_attention(
			[{"type": "running_low", "name": "B", "qty": 9}, {"type": "running_low", "name": "A", "qty": 2}],
			True,
		)
		self.assertEqual([i["qty"] for i in low], [2, 9])
		debts = calc.rank_attention(
			[
				{"type": "customer_overdue", "name": "Small", "amount": 10},
				{"type": "customer_overdue", "name": "Big", "amount": 900},
			],
			True,
		)
		self.assertEqual([i["name"] for i in debts], ["Big", "Small"])

	def test_without_the_right_to_see_money_the_money_items_are_dropped(self):
		items = [
			{"type": "customer_overdue", "name": "A", "amount": 5},
			{"type": "supplier_bill_due", "invoice": "P", "due_on": "2026-10-14", "amount": 5},
			{"type": "out_of_stock", "name": "O", "item_code": "O"},
		]
		self.assertEqual(
			[i["type"] for i in calc.rank_attention(items, may_see_money=False)], ["out_of_stock"]
		)

	def test_nothing_needs_attention(self):
		self.assertEqual(calc.rank_attention([], True), [])


class TestMoneyOwed(unittest.TestCase):
	def test_total_and_number_of_customers(self):
		rows = [
			{"customer": "A", "outstanding_amount": 3500},
			{"customer": "B", "outstanding_amount": 21000},
			{"customer": "A", "outstanding_amount": 0},
			{"customer": "C", "outstanding_amount": -50},
		]
		self.assertEqual(calc.money_owed_summary(rows), {"amount": 24500.0, "customers": 2})
		self.assertEqual(calc.money_owed_summary([]), {"amount": 0.0, "customers": 0})


def line(day, code, net, qty, recorded=0.0, uncosted=0.0, name=None, stock=1, warehouse="W"):
	return {
		"posting_date": D(day),
		"item_code": code,
		"item_name": name or code,
		"warehouse": warehouse,
		"is_stock_item": stock,
		"net_amount": net,
		"qty": qty,
		"recorded_cost": recorded,
		"uncosted_qty": uncosted,
	}


class TestProfitWorking(unittest.TestCase):
	def test_the_store_rate_comes_first_then_the_product_then_any_store(self):
		rows = [
			line("2026-10-13", "A", 100, 1, uncosted=1, warehouse="W1"),
			line("2026-10-13", "B", 100, 1, uncosted=1, warehouse="W1"),
			line("2026-10-13", "C", 100, 1, uncosted=1, warehouse="W1"),
			line("2026-10-13", "D", 100, 1, uncosted=1, warehouse="W1"),
		]
		item_rates = {"A": 5.0, "B": 7.0}
		bin_rates = {("A", "W1"): 6.0, ("C", "W9"): 9.0}
		calc.with_fallback_rates(rows, item_rates, bin_rates)
		self.assertEqual([r["fallback_rate"] for r in rows], [6.0, 7.0, 9.0, 0.0])

	def test_rows_already_costed_get_no_rate(self):
		rows = [line("2026-10-13", "A", 100, 1, recorded=60)]
		self.assertNotIn("fallback_rate", calc.with_fallback_rates(rows, {"A": 5.0}, {})[0])

	def test_unmerged_pos_lines_are_costed_from_the_valuation_and_estimated(self):
		rows = calc.with_fallback_rates([line("2026-10-13", "A", 3450, 5, uncosted=5)], {"A": 400.0}, {})
		summary = calc.summarize_profit(calc.for_profit(rows))
		self.assertEqual(summary["profitSales"], 3450.0)
		self.assertEqual(summary["costOfGoodsSold"], 2000.0)
		self.assertEqual(summary["grossProfit"], 1450.0)
		self.assertEqual(summary["costEstimatedItems"], 1)

	def test_a_product_with_no_cost_is_not_counted_instead_of_looking_like_pure_profit(self):
		rows = calc.with_fallback_rates(
			[line("2026-10-13", "A", 1000, 5, recorded=600), line("2026-10-13", "B", 500, 2, uncosted=2)],
			{},
			{},
		)
		summary = calc.summarize_profit(calc.for_profit(rows))
		self.assertEqual((summary["profitSales"], summary["grossProfit"]), (1000.0, 400.0))

	def test_only_the_uncosted_share_of_a_mixed_row_is_taken_out(self):
		rows = calc.with_fallback_rates([line("2026-10-13", "A", 1000, 10, recorded=300, uncosted=4)], {}, {})
		counted = calc.for_profit(rows)
		self.assertEqual(counted[0]["net_amount"], 600.0)
		self.assertEqual(rows[0]["net_amount"], 1000)  # the revenue rows are left as they were

	def test_profit_by_day(self):
		rows = [
			line("2026-10-12", "A", 500, 5, recorded=300),
			line("2026-10-12", "B", 100, 1, recorded=40),
			line("2026-10-11", "A", 200, 2, recorded=120),
		]
		self.assertEqual(calc.profit_by_day(rows), {"2026-10-12": 260.0, "2026-10-11": 80.0})

	def test_rows_between_dates(self):
		rows = [line("2026-10-12", "A", 1, 1), line("2026-09-01", "A", 1, 1)]
		self.assertEqual(len(calc.rows_between(rows, D("2026-10-01"), TODAY)), 1)

	def test_services_have_no_cost_and_are_not_uncosted_products(self):
		rows = [line("2026-10-13", "SERVICE", 500, 1, stock=0, uncosted=1)]
		self.assertEqual(calc.uncosted_products(rows, {}, {}), [])

	def test_a_product_with_a_rate_anywhere_has_a_cost(self):
		rows = [line("2026-10-13", c, 100, 2, uncosted=2) for c in ("ITEM", "BIN", "NONE")]
		got = calc.uncosted_products(rows, {"ITEM": 10.0}, {("BIN", "W9"): 4.0})
		self.assertEqual([p["item_code"] for p in got], ["NONE"])

	def test_a_product_with_a_recorded_cost_has_a_cost(self):
		rows = [line("2026-10-13", "A", 100, 2, recorded=50, uncosted=0)]
		self.assertEqual(calc.uncosted_products(rows, {}, {}), [])

	def test_uncosted_products_are_counted_once_each_with_units_sold_most_first(self):
		rows = [
			line("2026-10-12", "A", 100, 2, uncosted=2, name="Soap"),
			line("2026-10-13", "A", 100, 3, uncosted=3, name="Soap"),
			line("2026-10-13", "B", 100, 9, uncosted=9, name="Milk"),
		]
		self.assertEqual(
			calc.uncosted_products(rows, {}, {}),
			[
				{"item_code": "B", "item_name": "Milk", "units_sold": 9.0},
				{"item_code": "A", "item_name": "Soap", "units_sold": 5.0},
			],
		)

	def test_a_product_sold_then_returned_in_full_was_not_sold(self):
		rows = [line("2026-10-12", "A", 100, 2, uncosted=2), line("2026-10-13", "A", -100, -2, uncosted=-2)]
		self.assertEqual(calc.uncosted_products(rows, {}, {}), [])

	def test_the_net_profit_tile(self):
		now = {"netProfit": 3500.0, "profitMargin": 38.89, "costEstimatedItems": 1}
		before = {"netProfit": 600.0, "profitMargin": 30.0, "costEstimatedItems": 0}
		tile = calc.net_profit_tile(now, before, [1.0], 3)
		self.assertEqual(
			tile,
			{
				"value": 3500.0,
				"margin_pct": 38.9,
				"previous": 600.0,
				"change_pct": 483.3,
				"spark": [1.0],
				"estimated": True,
				"uncosted_products": 3,
			},
		)

	def test_the_net_profit_tile_with_a_previous_period_of_zero_has_no_percentage(self):
		now = {"netProfit": 100.0, "profitMargin": 10.0, "costEstimatedItems": 0}
		before = {"netProfit": 0.0, "profitMargin": 0.0, "costEstimatedItems": 0}
		tile = calc.net_profit_tile(now, before, [], 0)
		self.assertIsNone(tile["change_pct"])
		self.assertFalse(tile["estimated"])


# --- the call -------------------------------------------------------------------------------------------------

NOW = datetime(2026, 10, 13, 11, 52, tzinfo=timezone.utc)  # 14:52 in Nairobi

SALES = [
	sale("2026-10-13", 12450, 9, 14442),
	sale("2026-10-12", 8000, 6, 9280),
	sale("2026-10-12", -500, 1, -580, is_return=1),
	sale("2026-10-06", 11000, 8, 12760),
	sale("2026-09-20", 4000, 3, 4640),
	sale("2026-09-13", 7000, 5, 8120),
]
LINES = [
	line("2026-10-12", "ITM-A", 5000, 10, recorded=3000, name="Soap"),
	line("2026-10-13", "ITM-A", 3000, 5, uncosted=5, name="Soap"),
	line("2026-10-13", "ITM-B", 1000, 2, uncosted=2, name="Milk"),
	line("2026-09-13", "ITM-A", 2000, 4, recorded=1200, name="Soap"),
]
PAYMENTS = [
	{"mode_of_payment": "MPESA", "amount": 4200.0, "n": 23},
	{"mode_of_payment": "Cash", "amount": 9800.0, "n": 5},
]
SESSION = {
	"name": "POS-OPE-2026-00010",
	"user": "till@shop-a.test",
	"pos_profile": "Main",
	"period_start_date": datetime(2026, 10, 13, 8, 15),
	"posting_date": D("2026-10-13"),
}
SESSION_CASH = {
	"opening": [{"mode_of_payment": "Cash", "opening_amount": 2000}],
	"payments": [
		{"is_return": 0, "mode_of_payment": "Cash", "amount": 9800},
		{"is_return": 1, "mode_of_payment": "Cash", "amount": -300},
	],
	"change": [{"is_return": 0, "change": 200}],
	"entries": [{"payment_type": "Pay", "mode_of_payment": "Cash", "amount": 500}],
}
OVERDUE = [
	{
		"customer": "CUST-0004",
		"customer_name": "Wanjiru Kamau",
		"mobile_no": "0712345678",
		"posting_date": D("2026-09-23"),
		"due_date": None,
		"outstanding_amount": 3500.0,
		"has_terms": False,
	}
]
BILLS = [
	{
		"name": "PINV-0009",
		"supplier": "SUP-1",
		"supplier_name": "Brew Ltd",
		"outstanding_amount": 12000.0,
		"due_date": D("2026-10-15"),
	}
]
LOW = [
	{
		"item_code": "ITM-0003",
		"item_name": "Sugar 1kg",
		"warehouse": "W",
		"actual_qty": 4,
		"reorder_level": 10,
	}
]
SOLD = [{"item_code": "ITM-0007", "item_name": "Milk 500ml"}]


def in_range(rows, start, end):
	return [r for r in rows if start <= r["posting_date"] <= end]


class OverviewCase(unittest.TestCase):
	"""A signed-in user, every database read replaced by fixed data, and a dictionary standing in for the cache."""

	def setUp(self):
		self.cache = {}
		self.reads = {}

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, user="owner@shop-a.test", roles=("System Manager",), warehouse_owner=None):
		db = MagicMock()
		db.get_value.return_value = warehouse_owner
		db.has_column.return_value = True
		self.start((frappe, "session"), new=frappe._dict(user=user))
		self.start((frappe, "get_roles"), create=True, side_effect=lambda user=None: list(roles))
		self.start((frappe, "db"), new=db, create=True)
		self.start((frappe, "log_error"), create=True)
		self.start((frappe, "get_traceback"), create=True, return_value="traceback with secret db detail")
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start((overview, "resolve_company"), return_value="Shop A")
		self.start((overview, "_now_utc"), return_value=NOW)
		self.start((overview, "_cache_get"), side_effect=lambda key: self.cache.get(key))
		self.start((overview, "_cache_set"), side_effect=self.cache.__setitem__)
		self.stub("_sale_day_rows", lambda scope, start, end: in_range(SALES, start, end))
		self.stub("_payment_rows", lambda scope, start, end: copy.deepcopy(PAYMENTS))
		self.stub("_change_total", lambda scope, start, end: 200.0)
		self.stub("_credit_outstanding", lambda scope, start, end: 5000.0)
		self.stub("_mode_types", lambda scope: {})
		self.stub("_line_rows", lambda scope, start, end: copy.deepcopy(in_range(LINES, start, end)))
		self.stub("_cost_rates", lambda rows: ({"ITM-A": 400.0}, {}))
		self.stub("_expenses", lambda scope, start, end: 600.0 if start >= D("2026-09-14") else 200.0)
		self.stub("_hour_counts", lambda scope, start, end: {9: 30, 14: 15})
		self.stub(
			"_money_owed_rows",
			lambda scope: [
				{"customer": "C1", "outstanding_amount": 3500},
				{"customer": "C2", "outstanding_amount": 21000},
			],
		)
		self.stub("_overdue_rows", lambda scope: copy.deepcopy(OVERDUE))
		self.stub("_supplier_bill_rows", lambda scope: copy.deepcopy(BILLS))
		self.stub("_open_sessions", lambda scope: [dict(SESSION)])
		self.stub("_session_cash", lambda session: copy.deepcopy(SESSION_CASH))
		self.stub("_low_stock_rows", lambda scope: copy.deepcopy(LOW))
		self.stub("_sold_recently", lambda scope: copy.deepcopy(SOLD))
		self.stub("_stock_quantities", lambda scope, codes: {})

	def stub(self, name, function):
		mock = self.start((overview, name), side_effect=function)
		self.reads[name] = mock
		return mock

	def fail(self, name, text="secret db detail"):
		"""Make one database read fail."""
		mock = self.start((overview, name), side_effect=RuntimeError(text))
		self.reads[name] = mock
		return mock

	def get(self, **kwargs):
		return overview.get_overview(**kwargs)

	def data(self, **kwargs):
		result = self.get(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]


def keys_of(value, found=None):
	"""Every key anywhere in a nested answer."""
	found = set() if found is None else found
	if isinstance(value, dict):
		for key, item in value.items():
			found.add(key)
			keys_of(item, found)
	elif isinstance(value, list):
		for item in value:
			keys_of(item, found)
	return found


class TestTheAnswerForAnOwner(OverviewCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_the_shape_follows_the_contract(self):
		data = self.data()
		self.assertEqual(
			set(data),
			{"role", "period", "updated_at", "cached", "hero", "tiles", "charts", "attention", "errors"},
		)
		self.assertEqual(set(data["tiles"]), {"revenue", "net_profit", "money_owed", "low_stock"})
		self.assertEqual(set(data["charts"]), {"sales", "payments", "top_products", "busiest_hours"})
		self.assertEqual(
			set(data["hero"]),
			{
				"today_total",
				"gross_total",
				"same_weekday_last_week_total",
				"change_pct",
				"sales_count",
				"average_basket",
				"cash_in_till",
				"mpesa",
				"till",
			},
		)
		self.assertEqual(data["role"], "owner")
		self.assertEqual(data["errors"], {})
		self.assertIs(data["cached"], False)
		self.assertEqual(data["updated_at"], "14:52")
		json.dumps(data)  # plain data, ready to send

	def test_the_period_block(self):
		period = self.data()["period"]
		self.assertEqual(
			(period["key"], period["from"], period["to"], period["days"]),
			("30d", "2026-09-14", "2026-10-13", 30),
		)
		self.assertEqual((period["previous_from"], period["previous_to"]), ("2026-08-15", "2026-09-13"))
		self.assertEqual(period["days_of_data"], 30)

	def test_the_hero_is_about_today_whatever_the_period(self):
		for key in ("today", "7d", "month"):
			with self.subTest(period=key):
				hero = self.data(period=key)["hero"]
				self.assertEqual(hero["today_total"], 12450.0)
				self.assertEqual(hero["same_weekday_last_week_total"], 11000.0)
				self.assertEqual(hero["change_pct"], 13.2)
				self.assertEqual(hero["sales_count"], 9)
				self.assertEqual(hero["average_basket"], 1383.33)
				self.assertEqual(hero["gross_total"], 14442.0)
				self.assertEqual(hero["mpesa"], {"amount": 4200.0, "payments": 23})

	def test_the_hero_till_and_cash(self):
		hero = self.data()["hero"]
		self.assertEqual(
			hero["till"],
			{
				"state": "open",
				"session": "POS-OPE-2026-00010",
				"opened_on": "2026-10-13",
				"opened_at": "08:15",
			},
		)
		self.assertEqual(hero["cash_in_till"], {"amount": 10800.0, "opening_float": 2000.0})

	def test_no_open_till_means_no_cash_figure(self):
		self.stub("_open_sessions", lambda scope: [])
		hero = self.data()["hero"]
		self.assertIsNone(hero["cash_in_till"])
		self.assertEqual(hero["till"]["state"], "closed")
		self.reads["_session_cash"].assert_not_called()

	def test_the_revenue_tile_follows_the_period(self):
		tile = self.data()["tiles"]["revenue"]
		self.assertEqual(tile["value"], 34950.0)  # 12450 + 7500 + 11000 + 4000
		self.assertEqual(tile["previous"], 7000.0)
		self.assertEqual(tile["change_pct"], 399.3)
		self.assertEqual(tile["days_of_data"], 30)
		self.assertEqual(len(tile["spark"]), 29)
		self.assertEqual(tile["spark"][-1], 7500.0)

	def test_a_previous_period_with_no_sales_gives_no_percentage(self):
		self.stub(
			"_sale_day_rows",
			lambda scope, start, end: in_range(
				[s for s in SALES if s["posting_date"] > D("2026-09-13")], start, end
			),
		)
		tile = self.data()["tiles"]["revenue"]
		self.assertEqual(tile["previous"], 0.0)
		self.assertIsNone(tile["change_pct"])

	def test_a_business_with_no_sales_returns_zeros_and_empty_lists(self):
		self.stub("_sale_day_rows", lambda scope, start, end: [])
		self.stub("_line_rows", lambda scope, start, end: [])
		self.stub("_payment_rows", lambda scope, start, end: [])
		self.stub("_credit_outstanding", lambda scope, start, end: 0.0)
		self.stub("_change_total", lambda scope, start, end: 0.0)
		data = self.data()
		self.assertEqual(data["errors"], {})
		self.assertEqual(data["hero"]["today_total"], 0.0)
		self.assertEqual(data["hero"]["average_basket"], 0.0)
		self.assertIsNone(data["hero"]["change_pct"])
		self.assertEqual(data["tiles"]["revenue"]["value"], 0.0)
		self.assertEqual(data["tiles"]["revenue"]["days_of_data"], 0)
		self.assertEqual(data["charts"]["payments"], [])
		self.assertEqual(data["charts"]["top_products"], [])
		self.assertTrue(all(p["revenue"] == 0 for p in data["charts"]["sales"]))

	def test_the_net_profit_tile_uses_valuation_for_lines_with_no_recorded_cost(self):
		tile = self.data()["tiles"]["net_profit"]
		# sales 5000 + 3000 (milk's 1000 has no cost at all, so it is not counted); cost 3000 recorded + 5 x 400
		# estimated; running costs 600
		self.assertEqual(tile["value"], 2400.0)
		self.assertTrue(tile["estimated"])
		self.assertEqual(tile["uncosted_products"], 1)
		self.assertEqual(tile["previous"], 600.0)
		self.assertEqual(tile["change_pct"], 300.0)
		self.assertEqual(tile["margin_pct"], 30.0)

	def test_the_money_owed_tile(self):
		self.assertEqual(
			self.data()["tiles"]["money_owed"],
			{"amount": 24500.0, "customers": 2, "previous": None, "change_pct": None, "spark": []},
		)

	def test_the_low_stock_tile(self):
		self.assertEqual(
			self.data()["tiles"]["low_stock"],
			{"count": 1, "out_of_stock": 1, "previous": None, "change_pct": None, "spark": []},
		)

	def test_the_sales_chart_has_profit_for_an_owner(self):
		points = self.data()["charts"]["sales"]
		last = points[-1]
		self.assertEqual(last["date"], "2026-10-12")
		self.assertEqual(last["revenue"], 7500.0)
		self.assertEqual(last["profit"], 2000.0)
		self.assertEqual(last["count"], 6)
		self.assertEqual(last["previous"]["profit"], 0.0)
		self.assertNotIn("2026-10-13", [p["date"] for p in points])

	def test_the_payments_chart(self):
		got = self.data()["charts"]["payments"]
		self.assertEqual(
			got,
			[
				{"method": "Cash", "amount": 9600.0},
				{"method": "On credit", "amount": 5000.0},
				{"method": "M-Pesa", "amount": 4200.0},
			],
		)

	def test_top_products_only_count_this_period(self):
		got = self.data()["charts"]["top_products"]
		self.assertEqual(
			got,
			[
				{"item_code": "ITM-A", "name": "Soap", "revenue": 8000.0},
				{"item_code": "ITM-B", "name": "Milk", "revenue": 1000.0},
			],
		)

	def test_busiest_hours_are_averages_over_the_days_of_data(self):
		hours = self.data()["charts"]["busiest_hours"]
		self.assertEqual(len(hours), 12)
		self.assertEqual(next(h for h in hours if h["hour"] == 9)["average_sales"], 1.0)

	def test_attention_lists_the_four_most_urgent_in_order(self):
		items = self.data()["attention"]
		self.assertEqual(
			[i["type"] for i in items],
			["out_of_stock", "running_low", "customer_overdue", "supplier_bill_due"],
		)
		self.assertEqual([i["urgency"] for i in items], [2, 3, 4, 5])
		self.assertEqual(items[2]["days_overdue"], 6)

	def test_a_till_left_open_from_yesterday_comes_first(self):
		old = dict(SESSION, name="POS-OPE-2026-00009", period_start_date=datetime(2026, 10, 12, 8, 0))
		self.stub("_open_sessions", lambda scope: [dict(SESSION), old])
		items = self.data()["attention"]
		self.assertEqual(len(items), 4)
		self.assertEqual(
			items[0],
			{
				"type": "till_not_closed",
				"urgency": 1,
				"session": "POS-OPE-2026-00009",
				"opened_on": "2026-10-12",
			},
		)

	def test_a_manager_gets_the_same_as_an_owner(self):
		self.as_user("manager@shop-a.test", roles=("Sales Manager", "Sales User"))
		data = self.data()
		self.assertEqual(data["role"], "manager")
		self.assertIn("net_profit", data["tiles"])
		self.assertIn("money_owed", data["tiles"])
		self.assertIn("profit", data["charts"]["sales"][0])
		self.assertIn("customer_overdue", [i["type"] for i in data["attention"]])

	def test_the_owner_can_look_at_one_staff_member(self):
		self.data(staff="till@shop-a.test")
		scope = self.reads["_sale_day_rows"].call_args_list[0].args[0]
		self.assertEqual(scope.owner, "till@shop-a.test")

	def test_a_bad_period_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.get(period="decade")
		with self.assertRaises(frappe.ValidationError):
			self.get(period="custom")
		self.reads["_sale_day_rows"].assert_not_called()

	def test_a_custom_period(self):
		period = self.data(period="custom", from_date="2026-10-01", to_date="2026-10-10")["period"]
		self.assertEqual((period["from"], period["to"], period["days"]), ("2026-10-01", "2026-10-10", 10))


class TestTheAnswerForACashier(OverviewCase):
	USER = "till@shop-a.test"

	def setUp(self):
		super().setUp()
		self.as_user(self.USER, roles=("Sales User",))

	def test_no_profit_cost_or_owed_field_anywhere(self):
		data = self.data()
		self.assertEqual(data["role"], "cashier")
		self.assertNotIn("net_profit", data["tiles"])
		self.assertNotIn("money_owed", data["tiles"])
		for key in keys_of(data):
			with self.subTest(key=key):
				self.assertNotIn("profit", key)
				self.assertNotIn("owed", key)
				self.assertNotIn("cost", key)
				self.assertNotIn("uncosted", key)
		self.assertNotIn("net_profit", json.dumps(data))

	def test_the_things_a_cashier_still_needs_are_there(self):
		data = self.data()
		self.assertEqual(data["hero"]["today_total"], 12450.0)
		self.assertIn("revenue", data["tiles"])
		self.assertIn("low_stock", data["tiles"])
		self.assertEqual(data["errors"], {})
		self.assertEqual(len(data["charts"]["sales"]), 29)

	def test_customer_and_supplier_items_are_absent_even_when_there_are_some(self):
		types = [i["type"] for i in self.data()["attention"]]
		self.assertEqual(types, ["out_of_stock", "running_low"])
		self.reads["_overdue_rows"].assert_not_called()
		self.reads["_supplier_bill_rows"].assert_not_called()

	def test_the_money_owed_and_cost_data_is_never_read(self):
		self.data()
		self.reads["_money_owed_rows"].assert_not_called()
		self.reads["_expenses"].assert_not_called()

	def test_a_cashier_is_limited_to_their_own_sales_whoever_they_ask_for(self):
		self.data(staff="boss@shop-a.test")
		for name in ("_sale_day_rows", "_line_rows", "_payment_rows", "_open_sessions", "_hour_counts"):
			scope = self.reads[name].call_args_list[0].args[0]
			with self.subTest(read=name):
				self.assertEqual(scope.owner, self.USER)

	def test_a_cashier_without_a_staff_filter_is_limited_too(self):
		self.data()
		self.assertEqual(self.reads["_sale_day_rows"].call_args_list[0].args[0].owner, self.USER)


class TestWhoIsLetIn(OverviewCase):
	def test_a_guest_is_refused_and_nothing_is_read(self):
		self.as_user("Guest", roles=("Guest",))
		with self.assertRaises(frappe.AuthenticationError):
			self.get()
		self.reads["_sale_day_rows"].assert_not_called()
		self.assertEqual(self.cache, {})

	def test_a_store_of_another_business_is_refused(self):
		self.as_user(warehouse_owner="Shop B")
		with self.assertRaises(frappe.PermissionError):
			self.get(store="Other Store - SB")
		self.reads["_sale_day_rows"].assert_not_called()

	def test_a_store_of_your_own_business_is_allowed_and_narrows_the_reads(self):
		self.as_user(warehouse_owner="Shop A")
		self.data(store="Main Store - SA")
		self.assertEqual(self.reads["_sale_day_rows"].call_args_list[0].args[0].store, "Main Store - SA")


class TestACardThatFails(OverviewCase):
	def test_one_failing_chart_is_null_and_named_while_the_rest_returns(self):
		self.as_user()
		self.fail("_hour_counts")
		result = self.get()
		self.assertTrue(result["success"])
		data = result["data"]
		self.assertIsNone(data["charts"]["busiest_hours"])
		self.assertEqual(data["errors"], {"charts.busiest_hours": "Could not load this."})
		self.assertEqual(data["hero"]["today_total"], 12450.0)
		self.assertEqual(data["tiles"]["revenue"]["value"], 34950.0)
		self.assertTrue(data["attention"])

	def test_the_real_error_is_logged_and_never_sent_out(self):
		self.as_user()
		self.fail("_hour_counts")
		result = self.get()
		self.assertNotIn("secret", json.dumps(result, default=str))
		frappe.log_error.assert_called()

	def test_a_failing_card_does_not_stop_the_ones_after_it(self):
		self.as_user()
		self.fail("_money_owed_rows")
		self.fail("_payment_rows")
		data = self.get()["data"]
		self.assertIsNone(data["tiles"]["money_owed"])
		self.assertIsNone(data["charts"]["payments"])
		self.assertIsNone(data["hero"])  # the hero reads the same payments
		self.assertEqual(set(data["errors"]), {"tiles.money_owed", "charts.payments", "hero"})
		self.assertEqual(data["tiles"]["low_stock"]["count"], 1)
		self.assertTrue(data["charts"]["top_products"])

	def test_the_profit_cards_fail_separately_from_revenue(self):
		self.as_user()
		self.fail("_line_rows")
		data = self.get()["data"]
		self.assertIsNone(data["tiles"]["net_profit"])
		self.assertIsNone(data["charts"]["sales"])
		self.assertIsNone(data["charts"]["top_products"])
		self.assertEqual(data["tiles"]["revenue"]["value"], 34950.0)

	def test_a_failing_card_for_a_cashier_never_names_a_card_a_cashier_does_not_have(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.fail("_line_rows")
		data = self.get()["data"]
		self.assertEqual(set(data["errors"]), {"charts.top_products"})
		self.assertNotIn("net_profit", data["tiles"])

	def test_a_partial_answer_is_not_cached(self):
		self.as_user()
		self.fail("_hour_counts")
		self.get()
		self.assertEqual(self.cache, {})

	def test_only_when_everything_fails_is_the_call_not_a_success(self):
		self.as_user()
		for name in list(self.reads):
			self.fail(name)
		result = self.get()
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "Could not load the overview. Please try again.")
		self.assertNotIn("secret", json.dumps(result))
		self.assertEqual(self.cache, {})


class TestTheCache(OverviewCase):
	def test_the_answer_is_kept_and_served_again_without_recomputing(self):
		self.as_user()
		first = self.data()
		reads_after_first = self.reads["_sale_day_rows"].call_count
		again = self.data()
		self.assertIs(first["cached"], False)
		self.assertIs(again["cached"], True)
		self.assertEqual(again["hero"], first["hero"])
		self.assertEqual(self.reads["_sale_day_rows"].call_count, reads_after_first)
		self.assertEqual(len(self.cache), 1)

	def test_the_key_differs_by_role_store_period_dates_and_staff(self):
		period = calc.resolve_period("30d", None, None, TODAY)
		base = overview.cache_key("owner", "Shop A", None, None, period)
		variants = [
			overview.cache_key("manager", "Shop A", None, None, period),
			overview.cache_key("cashier", "Shop A", None, None, period),
			overview.cache_key("owner", "Shop B", None, None, period),
			overview.cache_key("owner", "Shop A", "Main", None, period),
			overview.cache_key("owner", "Shop A", None, "a@b.test", period),
			overview.cache_key("owner", "Shop A", None, None, calc.resolve_period("7d", None, None, TODAY)),
			overview.cache_key(
				"owner",
				"Shop A",
				None,
				None,
				calc.resolve_period("custom", "2026-10-01", "2026-10-05", TODAY),
			),
			overview.cache_key(
				"owner",
				"Shop A",
				None,
				None,
				calc.resolve_period("custom", "2026-10-02", "2026-10-05", TODAY),
			),
		]
		self.assertEqual(len({base, *variants}), len(variants) + 1)

	def test_an_owners_answer_is_never_served_to_a_cashier(self):
		self.as_user()
		self.data()
		self.as_user("till@shop-a.test", roles=("Sales User",))
		data = self.data()
		self.assertIs(data["cached"], False)
		self.assertNotIn("net_profit", data["tiles"])
		self.assertEqual(len(self.cache), 2)

	def test_a_cashiers_answer_is_kept_per_cashier(self):
		self.as_user("a@shop-a.test", roles=("Sales User",))
		self.data()
		self.as_user("b@shop-a.test", roles=("Sales User",))
		self.assertIs(self.data()["cached"], False)

	def test_a_manager_and_an_owner_do_not_share_an_answer(self):
		self.as_user()
		self.data()
		self.as_user("manager@shop-a.test", roles=("Stock Manager",))
		data = self.data()
		self.assertIs(data["cached"], False)
		self.assertEqual(data["role"], "manager")

	def test_the_cache_is_kept_for_sixty_seconds(self):
		self.assertEqual(overview.CACHE_SECONDS, 60)
		cache = MagicMock()
		with patch.object(frappe, "cache", create=True, return_value=cache):
			overview._cache_set("k", {"a": 1})
			overview._cache_get("k")
		cache.set_value.assert_called_once_with("k", {"a": 1}, expires_in_sec=60)
		cache.get_value.assert_called_once_with("k")

	def test_a_guest_is_refused_before_the_cache_is_looked_at(self):
		self.as_user("Guest", roles=("Guest",))
		self.cache["anything"] = {"hero": {}}
		with self.assertRaises(frappe.AuthenticationError):
			self.get()


class TestSalesStillInAnOpenTill(OverviewCase):
	"""Ticket P1-9: sales made as POS Invoices count for profit as soon as they are made, not when the till closes."""

	def setUp(self):
		super().setUp()
		self.as_user()
		pos_sales = [sale("2026-10-13", 3450, 2, 4002)]
		self.stub("_sale_day_rows", lambda scope, start, end: in_range(pos_sales, start, end))
		pos_lines = [
			line("2026-10-13", "ITM-A", 2000, 4, uncosted=4, name="Soap"),
			line("2026-10-13", "ITM-C", 1450, 3, uncosted=3, name="Tea"),
		]
		self.stub("_line_rows", lambda scope, start, end: copy.deepcopy(in_range(pos_lines, start, end)))
		self.stub("_cost_rates", lambda rows: ({"ITM-A": 300.0, "ITM-C": 200.0}, {}))
		self.stub("_expenses", lambda scope, start, end: 0.0)

	def test_profit_is_not_zero_when_there_are_sales_and_a_valuation_rate(self):
		tile = self.data()["tiles"]["net_profit"]
		self.assertEqual(tile["value"], 1650.0)  # 3450 - (4 x 300 + 3 x 200)
		self.assertNotEqual(tile["value"], 0)
		self.assertTrue(tile["estimated"])
		self.assertEqual(tile["uncosted_products"], 0)
		self.assertEqual(tile["margin_pct"], 47.8)

	def test_revenue_and_profit_come_from_the_same_sales(self):
		data = self.data()
		self.assertEqual(data["tiles"]["revenue"]["value"], 3450.0)
		self.assertEqual(data["hero"]["today_total"], 3450.0)

	def test_with_no_cost_at_all_profit_is_not_invented(self):
		self.stub("_cost_rates", lambda rows: ({}, {}))
		tile = self.data()["tiles"]["net_profit"]
		self.assertEqual(tile["value"], 0.0)  # nothing could be counted ...
		self.assertEqual(tile["uncosted_products"], 2)  # ... and the tile says why


class TestLineRowsReadBothKinds(unittest.TestCase):
	def test_sales_invoice_pos_invoice_and_packed_lines_are_all_read(self):
		scope = MagicMock()
		with (
			patch.object(overview, "_invoice_lines", side_effect=lambda s, d, a, b: [f"lines {d}"]) as lines,
			patch.object(overview, "_packed_lines", side_effect=lambda s, d, a, b: [f"packed {d}"]),
		):
			rows = overview._line_rows(scope, D("2026-10-01"), TODAY)
		self.assertEqual(
			rows, ["lines Sales Invoice", "lines POS Invoice", "packed Sales Invoice", "packed POS Invoice"]
		)
		self.assertEqual([c.args[1] for c in lines.call_args_list], ["Sales Invoice", "POS Invoice"])


class TestTheQueries(unittest.TestCase):
	def scope(self, **kwargs):
		values = {"company": "Shop A", "owner": None, "store": None}
		values.update(kwargs)
		return MagicMock(**values)

	def test_only_unmerged_pos_invoices_are_added_to_sales_invoices(self):
		conditions, values = overview._document_filters(self.scope(), "Sales Invoice", False)
		self.assertNotIn("consolidated_invoice", " ".join(conditions))
		conditions, values = overview._document_filters(self.scope(), "POS Invoice", True)
		self.assertIn("IFNULL(d.consolidated_invoice, '') = ''", conditions)
		self.assertIn("d.docstatus = 1", conditions)
		self.assertEqual(values, {"company": "Shop A"})

	def test_owner_and_store_narrow_the_query(self):
		conditions, values = overview._document_filters(
			self.scope(owner="a@b.test", store="Main"), "POS Invoice", True
		)
		self.assertIn("d.owner = %(owner)s", conditions)
		self.assertTrue(any("`tabPOS Invoice Item`" in c and "%(store)s" in c for c in conditions))
		self.assertEqual(values, {"company": "Shop A", "owner": "a@b.test", "store": "Main"})

	def test_the_sales_of_the_day_are_read_from_both_documents(self):
		rows = [{"posting_date": D("2026-10-12"), "is_return": 0, "gross": 10, "net": 9, "n": 1}]
		pos = [{"posting_date": D("2026-10-12"), "is_return": 0, "gross": 5, "net": 4, "n": 1}]
		with (
			patch.object(frappe, "db", new=MagicMock(), create=True) as db,
			patch.object(overview, "pos_invoice_days", return_value=pos) as pos_days,
		):
			db.sql.return_value = rows
			got = overview._sale_day_rows(self.scope(owner="a@b.test", store="Main"), D("2026-10-01"), TODAY)
		self.assertEqual(got, rows + pos)
		pos_days.assert_called_once_with("Shop A", D("2026-10-01"), TODAY, "a@b.test", "Main")
		self.assertIn("`tabSales Invoice`", db.sql.call_args.args[0])

	def test_the_dates_are_filled_into_both_halves_of_a_union(self):
		sql, values = overview._sale_union(
			self.scope(), "SELECT 1 AS n", "FROM `tab{doctype}` d", extra="d.is_return = 0"
		)
		self.assertIn("FROM `tabSales Invoice` d", sql)
		self.assertIn("FROM `tabPOS Invoice` d", sql)
		self.assertEqual(sql.count("UNION ALL"), 1)
		self.assertEqual(sql.count("d.posting_date BETWEEN %(from_date)s AND %(to_date)s"), 2)
		self.assertEqual(sql.count("consolidated_invoice"), 1)


class TestProductsWithoutACostPrice(OverviewCase):
	ROWS = (
		line("2026-10-12", "ITM-A", 5000, 10, recorded=3000, name="Soap"),
		line("2026-10-13", "ITM-B", 1000, 2, uncosted=2, name="Milk"),
		line("2026-10-13", "ITM-C", 900, 9, uncosted=9, name="Tea"),
		line("2026-10-13", "ITM-D", 100, 1, uncosted=1, name="Bread"),
		line("2026-10-13", "SERVICE", 500, 1, stock=0, uncosted=1, name="Delivery"),
	)

	def setUp(self):
		super().setUp()
		self.as_user()
		self.stub("_line_rows", lambda scope, start, end: copy.deepcopy(list(self.ROWS)))
		self.stub("_cost_rates", lambda rows: ({"ITM-D": 20.0}, {}))

	def test_the_tile_counts_the_products_sold_with_no_cost_at_all(self):
		self.assertEqual(self.data()["tiles"]["net_profit"]["uncosted_products"], 2)  # Milk and Tea

	def test_a_product_with_a_valuation_rate_somewhere_is_not_counted(self):
		self.stub("_cost_rates", lambda rows: ({"ITM-D": 20.0, "ITM-B": 5.0}, {("ITM-C", "W2"): 3.0}))
		self.assertEqual(self.data()["tiles"]["net_profit"]["uncosted_products"], 0)

	def test_the_list_gives_each_product_with_its_units_most_sold_first(self):
		result = overview.get_products_without_cost(from_date="2026-10-01", to_date="2026-10-13")
		self.assertTrue(result["success"])
		self.assertEqual(
			result["data"]["products"],
			[
				{"item_code": "ITM-C", "item_name": "Tea", "units_sold": 9.0},
				{"item_code": "ITM-B", "item_name": "Milk", "units_sold": 2.0},
			],
		)
		self.assertEqual(result["data"]["count"], 2)
		self.assertEqual((result["data"]["from"], result["data"]["to"]), ("2026-10-01", "2026-10-13"))

	def test_the_list_follows_the_limit_but_the_count_is_the_whole(self):
		result = overview.get_products_without_cost(limit=1)
		self.assertEqual(len(result["data"]["products"]), 1)
		self.assertEqual(result["data"]["count"], 2)

	def test_the_list_reads_the_last_thirty_days_by_default(self):
		overview.get_products_without_cost()
		_scope, start, end = self.reads["_line_rows"].call_args.args
		self.assertEqual((start, end), (D("2026-09-14"), TODAY))

	def test_a_silly_limit_is_corrected(self):
		self.assertEqual(len(overview.get_products_without_cost(limit="abc")["data"]["products"]), 2)
		self.assertEqual(len(overview.get_products_without_cost(limit=0)["data"]["products"]), 1)

	def test_a_manager_may_use_it(self):
		self.as_user("manager@shop-a.test", roles=("Purchase Manager",))
		self.stub("_line_rows", lambda scope, start, end: copy.deepcopy(list(self.ROWS)))
		self.stub("_cost_rates", lambda rows: ({}, {}))
		self.assertTrue(overview.get_products_without_cost()["success"])

	def test_a_cashier_is_refused_before_anything_is_read(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.stub("_line_rows", MagicMock())
		self.stub("_cost_rates", MagicMock())
		with self.assertRaises(frappe.PermissionError):
			overview.get_products_without_cost()
		self.reads["_line_rows"].assert_not_called()
		self.reads["_cost_rates"].assert_not_called()
		overview.resolve_company.assert_not_called()

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=("Guest",))
		self.stub("_line_rows", MagicMock())
		with self.assertRaises(frappe.AuthenticationError):
			overview.get_products_without_cost()
		self.reads["_line_rows"].assert_not_called()

	def test_bad_dates_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			overview.get_products_without_cost(from_date="2026-10-10", to_date="2026-10-01")


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	def test_the_access_check_is_the_first_statement_of_the_protected_call(self):
		import ast
		import inspect

		tree = ast.parse(inspect.getsource(overview))
		function = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_products_without_cost"
		)
		first = next(
			s for s in function.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		)
		self.assertEqual(ast.unparse(first), "role_tiers.require_access('cost_of_goods')")

	def test_both_calls_are_whitelisted(self):
		for function in (overview.get_overview, overview.get_products_without_cost):
			self.assertIn(function, frappe.whitelisted)

	def test_the_pure_module_needs_no_database(self):
		import inspect

		self.assertNotIn("import frappe", inspect.getsource(calc))


if __name__ == "__main__":
	unittest.main()
