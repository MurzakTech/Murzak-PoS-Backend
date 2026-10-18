# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Customers page calls (`customers_list_api`) and the rules behind them (`customers_list_calc`). They need
no database: every Frappe call is mocked, and all the figures are made up.

The first half tests the plain functions with plain rows: the request, the search, what each customer owes, the last
purchase, the short receipt number, the Owing tab, paging and the statement. The second half tests the calls: who is let in,
what a cashier does and does not receive, that the Owing tab, the summary and Home's tile read the same rows, that the
closing balance of a statement equals the balance in the list, and the shape of the queries.
"""

import ast
import inspect
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import customers_list_api as api
from murzak_pos.api import customers_list_calc as rules
from murzak_pos.api import dashboard_overview
from murzak_pos.api import overview_calc as calc

D = date.fromisoformat
TODAY = D("2026-10-13")
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


class TestTheRequest(unittest.TestCase):
	def test_the_tab_is_all_or_owing(self):
		self.assertEqual(rules.normalize_tab(None), "all")
		self.assertEqual(rules.normalize_tab(""), "all")
		self.assertEqual(rules.normalize_tab(" Owing "), "owing")
		self.assertEqual(rules.normalize_tab("ALL"), "all")

	def test_any_other_tab_is_refused_with_a_code(self):
		with self.assertRaises(rules.ListError) as raised:
			rules.normalize_tab("unpaid")
		self.assertEqual(raised.exception.code, "bad_tab")

	def test_the_status_defaults_to_active(self):
		self.assertEqual(rules.normalize_status(None), "active")
		self.assertEqual(rules.normalize_status("Disabled"), "disabled")
		self.assertEqual(rules.normalize_status("all"), "all")
		with self.assertRaises(rules.ListError) as raised:
			rules.normalize_status("gone")
		self.assertEqual(raised.exception.code, "bad_status")

	def test_the_type_is_one_of_the_three_or_none(self):
		self.assertIsNone(rules.normalize_type(None))
		self.assertIsNone(rules.normalize_type("  "))
		self.assertEqual(rules.normalize_type("company"), "Company")
		self.assertEqual(rules.normalize_type("Individual"), "Individual")
		with self.assertRaises(rules.ListError) as raised:
			rules.normalize_type("Government")
		self.assertEqual(raised.exception.code, "bad_type")

	def test_the_page_is_at_least_one(self):
		self.assertEqual(rules.clamp_page(3), 3)
		self.assertEqual(rules.clamp_page("2"), 2)
		for bad in (0, -4, "x", None):
			self.assertEqual(rules.clamp_page(bad), 1)

	def test_rows_per_page_is_never_blank_or_huge(self):
		self.assertEqual(rules.clamp_page_size(None), 20)
		self.assertEqual(rules.clamp_page_size("abc"), 20)
		self.assertEqual(rules.clamp_page_size(0), 20)
		self.assertEqual(rules.clamp_page_size(50), 50)
		self.assertEqual(rules.clamp_page_size(5000), 100)


class TestTheSearch(unittest.TestCase):
	def test_an_empty_search_means_no_search(self):
		self.assertIsNone(rules.parse_search(None))
		self.assertIsNone(rules.parse_search("   "))

	def test_a_name_is_looked_for_in_lower_case(self):
		self.assertEqual(rules.parse_search("  Wanjiru   Kamau "), {"text": "wanjiru kamau", "mobile": None})

	def test_a_phone_number_gives_the_digits_after_the_zero_or_country_code(self):
		self.assertEqual(rules.mobile_fragment("0712 345"), "712345")
		self.assertEqual(rules.mobile_fragment("+254 712 345"), "712345")
		self.assertEqual(rules.mobile_fragment("254712"), "712")
		self.assertEqual(rules.mobile_fragment("712-345"), "712345")

	def test_too_few_digits_or_letters_are_not_a_phone_number(self):
		self.assertIsNone(rules.mobile_fragment("07"))
		self.assertIsNone(rules.mobile_fragment("Shop 12345"))
		self.assertIsNone(rules.mobile_fragment(""))

	def test_a_long_search_is_cut_to_sixty_characters(self):
		self.assertEqual(len(rules.parse_search("a" * 200)["text"]), 60)

	def test_the_search_matches_the_name_the_number_or_the_mobile(self):
		row = {"name": "CUST-0007", "customer_name": "Wanjiru Kamau", "mobile_no": "+254712345678"}
		self.assertTrue(rules.matches_search(row, rules.parse_search("wanjiru")))
		self.assertTrue(rules.matches_search(row, rules.parse_search("cust-0007")))
		self.assertTrue(rules.matches_search(row, rules.parse_search("0712 345")))
		self.assertTrue(rules.matches_search(row, rules.parse_search("254712")))
		self.assertFalse(rules.matches_search(row, rules.parse_search("0733 111")))
		self.assertFalse(rules.matches_search(row, rules.parse_search("otieno")))
		self.assertTrue(rules.matches_search(row, None))

	def test_the_search_also_matches_the_email_as_the_old_list_did(self):
		row = {"name": "C-1", "customer_name": "Amina", "email_id": "amina@demo.test", "mobile_no": ""}
		self.assertTrue(rules.matches_search(row, rules.parse_search("amina@demo")))
		self.assertFalse(rules.matches_search(row, rules.parse_search("otieno@demo")))

	def test_a_mobile_saved_with_spaces_still_matches(self):
		row = {"name": "C", "customer_name": "Amina", "mobile_no": "0712 345 678"}
		self.assertTrue(rules.matches_search(row, rules.parse_search("712345")))

	def test_like_characters_in_a_search_are_made_plain(self):
		self.assertEqual(rules.escape_like("50%_off\\"), "50\\%\\_off\\\\")

	def test_the_type_filter(self):
		self.assertTrue(rules.matches_type({"customer_type": "Company"}, "Company"))
		self.assertFalse(rules.matches_type({"customer_type": "Individual"}, "Company"))
		self.assertTrue(rules.matches_type({"customer_type": "Individual"}, None))


OWED_ROWS = [
	{"customer": "CUST-1", "outstanding_amount": 3500},
	{"customer": "CUST-2", "outstanding_amount": 21000},
	{"customer": "CUST-2", "outstanding_amount": 500},
	{"customer": "CUST-3", "outstanding_amount": 0},
	{"customer": "CUST-4", "outstanding_amount": -200},
	{"customer": None, "outstanding_amount": 90},
]


class TestWhatCustomersOwe(unittest.TestCase):
	def test_each_customer_owes_the_sum_of_their_unpaid_amounts(self):
		self.assertEqual(rules.balances_by_customer(OWED_ROWS), {"CUST-1": 3500.0, "CUST-2": 21500.0})

	def test_customers_who_owe_nothing_or_are_owed_money_are_left_out(self):
		balances = rules.balances_by_customer(OWED_ROWS)
		self.assertNotIn("CUST-3", balances)
		self.assertNotIn("CUST-4", balances)

	def test_the_summary_is_exactly_the_home_tile(self):
		summary = rules.owing_summary(OWED_ROWS)
		home = calc.money_owed_summary(OWED_ROWS)
		self.assertEqual(summary, {"owing_total": home["amount"], "owing_customers": home["customers"]})

	def test_the_balances_add_up_to_the_summary_total(self):
		balances = rules.balances_by_customer(OWED_ROWS[:5])
		self.assertEqual(
			calc.money(sum(balances.values())), rules.owing_summary(OWED_ROWS[:5])["owing_total"]
		)
		self.assertEqual(len(balances), rules.owing_summary(OWED_ROWS[:5])["owing_customers"])

	def test_no_rows_means_nothing_is_owed(self):
		self.assertEqual(rules.balances_by_customer([]), {})
		self.assertEqual(rules.balances_by_customer(None), {})
		self.assertEqual(rules.owing_summary([]), {"owing_total": 0.0, "owing_customers": 0})

	def test_amounts_are_rounded_to_cents(self):
		rows = [{"customer": "C", "outstanding_amount": 10.004}, {"customer": "C", "outstanding_amount": 0.1}]
		self.assertEqual(rules.balances_by_customer(rows), {"C": 10.1})

	def test_balance_of_a_customer_who_owes_nothing_is_zero(self):
		self.assertEqual(rules.balance_of({"A": 5.5}, "A"), 5.5)
		self.assertEqual(rules.balance_of({"A": 5.5}, "B"), 0.0)


class TestTheShortReceiptNumber(unittest.TestCase):
	def test_a_pos_invoice_has_a_hash_and_four_digits(self):
		self.assertEqual(rules.short_receipt("POS Invoice", "ACC-PSINV-2026-00005"), "#0005")
		self.assertEqual(rules.short_receipt("POS Invoice", "ACC-PSINV-2026-00033"), "#0033")

	def test_a_sales_invoice_has_an_s(self):
		self.assertEqual(rules.short_receipt("Sales Invoice", "ACC-SINV-2026-00005"), "#S0005")

	def test_a_payment_has_a_p(self):
		self.assertEqual(rules.short_receipt("Payment Entry", "ACC-PAY-2026-00012"), "#P0012")

	def test_a_longer_number_is_kept_whole(self):
		self.assertEqual(rules.short_receipt("POS Invoice", "ACC-PSINV-2026-12345"), "#12345")

	def test_an_amended_copy_keeps_the_number_of_its_original(self):
		self.assertEqual(rules.short_receipt("POS Invoice", "ACC-PSINV-2026-00005-1"), "#0005")

	def test_a_name_with_no_digits_is_returned_as_it_is(self):
		self.assertEqual(rules.short_receipt("POS Invoice", "OPENING"), "OPENING")
		self.assertEqual(rules.short_receipt("POS Invoice", None), "")

	def test_it_gives_the_same_number_as_the_sales_list_rule(self):
		for name in ("ACC-PSINV-2026-00005", "ACC-PSINV-2026-00005-1", "X-9", "ACC-PSINV-2026-123456"):
			with self.subTest(name=name):
				self.assertEqual(rules.sequence_of(name), rules.sequence_of(name))
		self.assertEqual(rules.sequence_of("ACC-PSINV-2026-00005"), 5)
		self.assertIsNone(rules.sequence_of("NODIGITS"))


SALES = [
	{
		"customer": "CUST-1",
		"name": "ACC-PSINV-2026-00010",
		"doctype": "POS Invoice",
		"posting_date": D("2026-10-01"),
		"posting_time": "10:00:00",
		"grand_total": 400,
		"is_return": 0,
	},
	{
		"customer": "CUST-1",
		"name": "ACC-SINV-2026-00004",
		"doctype": "Sales Invoice",
		"posting_date": D("2026-10-05"),
		"posting_time": "09:00:00",
		"grand_total": 1500,
		"is_return": 0,
	},
	{
		"customer": "CUST-1",
		"name": "ACC-PSINV-2026-00012",
		"doctype": "POS Invoice",
		"posting_date": D("2026-10-05"),
		"posting_time": "16:30:00",
		"grand_total": 250,
		"is_return": 0,
	},
	{
		"customer": "CUST-2",
		"name": "ACC-PSINV-2026-00011",
		"doctype": "POS Invoice",
		"posting_date": D("2026-09-20"),
		"posting_time": "08:00:00",
		"grand_total": 90,
		"is_return": 0,
	},
	{
		"customer": "CUST-2",
		"name": "ACC-PSINV-2026-00013",
		"doctype": "POS Invoice",
		"posting_date": D("2026-10-09"),
		"posting_time": "12:00:00",
		"grand_total": -90,
		"is_return": 1,
	},
]


class TestTheLastPurchase(unittest.TestCase):
	def test_the_latest_sale_of_each_customer_is_found_across_both_kinds(self):
		found = rules.last_purchases(SALES)
		self.assertEqual(
			found["CUST-1"],
			{"date": "2026-10-05", "amount": 250.0, "receipt": "#0012", "id": "ACC-PSINV-2026-00012"},
		)

	def test_two_sales_on_one_day_are_told_apart_by_the_time(self):
		rows = [dict(SALES[1]), dict(SALES[2])]
		self.assertEqual(rules.last_purchases(rows)["CUST-1"]["id"], "ACC-PSINV-2026-00012")
		rows[0]["posting_time"] = "23:00:00"
		self.assertEqual(rules.last_purchases(rows)["CUST-1"]["id"], "ACC-SINV-2026-00004")

	def test_a_return_is_not_a_purchase(self):
		found = rules.last_purchases(SALES)
		self.assertEqual(found["CUST-2"]["date"], "2026-09-20")
		self.assertEqual(found["CUST-2"]["amount"], 90.0)

	def test_a_customer_with_no_purchase_has_none(self):
		self.assertEqual(rules.last_purchases([]), {})
		self.assertIsNone(rules.last_purchase_for({}, "CUST-9", True))

	def test_a_time_given_as_a_timedelta_is_read(self):
		from datetime import timedelta

		rows = [
			{
				"customer": "C",
				"name": "A-1",
				"doctype": "POS Invoice",
				"posting_date": "2026-10-01",
				"posting_time": timedelta(hours=9),
				"grand_total": 1,
			},
			{
				"customer": "C",
				"name": "A-2",
				"doctype": "POS Invoice",
				"posting_date": "2026-10-01",
				"posting_time": timedelta(hours=11),
				"grand_total": 2,
			},
		]
		self.assertEqual(rules.last_purchases(rows)["C"]["id"], "A-2")

	def test_a_row_without_a_date_or_customer_is_ignored(self):
		rows = [
			{"customer": "C", "name": "A-1", "posting_date": None, "grand_total": 5},
			{"name": "A-2", "posting_date": "2026-10-01", "grand_total": 5},
		]
		self.assertEqual(rules.last_purchases(rows), {})

	def test_a_cashier_gets_the_date_but_not_the_amount(self):
		found = rules.last_purchases(SALES)
		seen = rules.last_purchase_for(found, "CUST-1", False)
		self.assertEqual(seen["date"], "2026-10-05")
		self.assertNotIn("amount", seen)
		self.assertEqual(rules.last_purchase_for(found, "CUST-1", True)["amount"], 250.0)


RECORDS = {
	"CUST-1": {
		"name": "CUST-1",
		"customer_name": "Wanjiru Kamau",
		"customer_type": "Individual",
		"mobile_no": "0712345678",
		"email_id": "w@demo.test",
		"disabled": 0,
	},
	"CUST-2": {
		"name": "CUST-2",
		"customer_name": "Baraka Stores",
		"customer_type": "Company",
		"mobile_no": "+254733000111",
		"email_id": None,
		"disabled": 0,
	},
	"CUST-5": {
		"name": "CUST-5",
		"customer_name": "Zawadi Mwangi",
		"customer_type": "Individual",
		"mobile_no": None,
		"email_id": None,
		"disabled": 1,
	},
}


class TestTheListRows(unittest.TestCase):
	def test_a_row_for_an_owner_has_the_balance(self):
		row = rules.customer_row(RECORDS["CUST-1"], {"CUST-1": 3500.0}, rules.last_purchases(SALES), True)
		self.assertEqual(row["id"], "CUST-1")
		self.assertEqual(row["name"], "Wanjiru Kamau")
		self.assertEqual(row["type"], "Individual")
		self.assertEqual(row["mobile"], "0712345678")
		self.assertEqual(row["balance"], 3500.0)
		self.assertEqual(row["last_purchase"]["amount"], 250.0)
		self.assertFalse(row["disabled"])

	def test_a_row_for_a_cashier_has_no_balance_field_at_all(self):
		row = rules.customer_row(RECORDS["CUST-1"], {"CUST-1": 3500.0}, rules.last_purchases(SALES), False)
		self.assertNotIn("balance", row)
		self.assertNotIn("amount", row["last_purchase"])
		self.assertNotIn("3500", json.dumps(row))

	def test_a_customer_who_owes_nothing_has_a_zero_balance_for_an_owner(self):
		row = rules.customer_row(RECORDS["CUST-5"], {}, {}, True)
		self.assertEqual(row["balance"], 0.0)
		self.assertIsNone(row["last_purchase"])
		self.assertTrue(row["disabled"])
		self.assertEqual(row["mobile"], "")
		self.assertEqual(row["email"], "")

	def test_a_customer_without_a_name_is_shown_by_its_number(self):
		self.assertEqual(rules.customer_row({"name": "CUST-8"}, {}, {}, True)["name"], "CUST-8")


BALANCES = {"CUST-1": 3500.0, "CUST-2": 21500.0, "CUST-5": 3500.0}


class TestTheOwingTab(unittest.TestCase):
	def test_most_owed_first_then_by_name(self):
		records = rules.owing_records(BALANCES, RECORDS, None, None)
		self.assertEqual([r["name"] for r in records], ["CUST-2", "CUST-1", "CUST-5"])

	def test_the_total_of_the_tab_is_the_total_of_the_balances(self):
		records = rules.owing_records(BALANCES, RECORDS, None, None)
		self.assertEqual(calc.money(sum(r["balance"] for r in records)), 28500.0)

	def test_everyone_who_owes_is_listed_even_a_disabled_customer(self):
		records = rules.owing_records(BALANCES, RECORDS, None, None)
		self.assertIn("CUST-5", [r["name"] for r in records])

	def test_a_customer_record_that_cannot_be_read_is_still_listed_by_its_number(self):
		records = rules.owing_records({"CUST-9": 40.0}, {}, None, None)
		self.assertEqual(len(records), 1)
		self.assertEqual(records[0]["name"], "CUST-9")
		self.assertEqual(records[0]["balance"], 40.0)

	def test_the_search_narrows_the_tab_by_name_or_mobile(self):
		by_name = rules.owing_records(BALANCES, RECORDS, rules.parse_search("baraka"), None)
		self.assertEqual([r["name"] for r in by_name], ["CUST-2"])
		by_mobile = rules.owing_records(BALANCES, RECORDS, rules.parse_search("0733 000"), None)
		self.assertEqual([r["name"] for r in by_mobile], ["CUST-2"])

	def test_the_type_narrows_the_tab(self):
		records = rules.owing_records(BALANCES, RECORDS, None, "Individual")
		self.assertEqual([r["name"] for r in records], ["CUST-1", "CUST-5"])

	def test_the_records_passed_in_are_not_changed(self):
		before = json.dumps(RECORDS, sort_keys=True)
		rules.owing_records(BALANCES, RECORDS, None, None)
		self.assertEqual(json.dumps(RECORDS, sort_keys=True), before)


class TestPaging(unittest.TestCase):
	def test_the_second_page_of_forty_five(self):
		items = list(range(45))
		page, info = rules.page_slice(items, 2, 20)
		self.assertEqual(page, list(range(20, 40)))
		self.assertEqual(info, {"page": 2, "page_size": 20, "pages": 3, "total": 45})

	def test_the_last_page_is_short(self):
		page, info = rules.page_slice(list(range(45)), 3, 20)
		self.assertEqual(len(page), 5)
		self.assertEqual(info["pages"], 3)

	def test_a_page_past_the_end_is_the_last_page(self):
		page, info = rules.page_slice(list(range(45)), 9, 20)
		self.assertEqual(info["page"], 3)
		self.assertEqual(len(page), 5)

	def test_an_empty_list_has_one_empty_page(self):
		page, info = rules.page_slice([], 1, 20)
		self.assertEqual(page, [])
		self.assertEqual(info, {"page": 1, "page_size": 20, "pages": 1, "total": 0})

	def test_page_info_alone(self):
		self.assertEqual(rules.page_info(135, 7, 20)["pages"], 7)
		self.assertEqual(rules.page_info(135, 99, 20)["page"], 7)


class TestFeatures(unittest.TestCase):
	def test_a_shop_whose_customers_all_share_one_group_does_not_use_groups(self):
		self.assertFalse(rules.uses_values(["Individual", "Individual", "Individual"]))
		self.assertFalse(rules.uses_values([]))
		self.assertFalse(rules.uses_values([None, "", "  "]))

	def test_a_shop_with_two_different_groups_uses_them(self):
		self.assertTrue(rules.uses_values(["Individual", "Commercial"]))
		self.assertTrue(rules.uses_values(["Nairobi", None, "Mombasa"]))

	def test_a_customer_belongs_to_the_business_when_unowned_or_its_own(self):
		self.assertTrue(rules.customer_in_company(None, "Shop A"))
		self.assertTrue(rules.customer_in_company("Shop A", "Shop A"))
		self.assertFalse(rules.customer_in_company("Shop B", "Shop A"))


class TestTheStatementPeriod(unittest.TestCase):
	def test_with_no_dates_it_is_the_last_ninety_days(self):
		first, last = rules.resolve_statement_period(None, None, TODAY)
		self.assertEqual(last, TODAY)
		self.assertEqual(first, D("2026-07-16"))
		self.assertEqual((last - first).days + 1, 90)

	def test_an_end_date_in_the_future_is_cut_back_to_today(self):
		self.assertEqual(
			rules.resolve_statement_period("2026-10-01", "2027-01-01", TODAY), (D("2026-10-01"), TODAY)
		)

	def test_both_dates_are_used(self):
		self.assertEqual(
			rules.resolve_statement_period("2026-09-01", "2026-09-30", TODAY),
			(D("2026-09-01"), D("2026-09-30")),
		)

	def test_a_start_after_the_end_is_refused(self):
		with self.assertRaises(rules.StatementError) as raised:
			rules.resolve_statement_period("2026-10-05", "2026-10-01", TODAY)
		self.assertEqual(raised.exception.code, "order")

	def test_a_start_after_today_is_refused(self):
		with self.assertRaises(rules.StatementError) as raised:
			rules.resolve_statement_period("2026-11-01", None, TODAY)
		self.assertEqual(raised.exception.code, "order")

	def test_more_than_two_years_is_refused(self):
		with self.assertRaises(rules.StatementError) as raised:
			rules.resolve_statement_period("2024-01-01", "2026-10-01", TODAY)
		self.assertEqual(raised.exception.code, "too_long")

	def test_a_date_that_is_not_a_date_is_refused(self):
		with self.assertRaises(rules.StatementError) as raised:
			rules.resolve_statement_period("12/10/2026", None, TODAY)
		self.assertEqual(raised.exception.code, "bad_date")


DOCS = [
	# A credit sale of 1,500 on 5 Oct, nothing paid on the spot
	{
		"name": "ACC-SINV-2026-00004",
		"doctype": "Sales Invoice",
		"posting_date": D("2026-10-05"),
		"posting_time": "09:00:00",
		"grand_total": 1500,
		"paid": 0,
		"is_return": 0,
	},
	# A till sale of 250 on 6 Oct, paid in cash on the spot
	{
		"name": "ACC-PSINV-2026-00012",
		"doctype": "POS Invoice",
		"posting_date": D("2026-10-06"),
		"posting_time": "16:30:00",
		"grand_total": 250,
		"paid": 250,
		"is_return": 0,
	},
	# A return of 100 on 8 Oct against the credit sale (a credit note, nothing paid out)
	{
		"name": "ACC-SINV-2026-00006",
		"doctype": "Sales Invoice",
		"posting_date": D("2026-10-08"),
		"posting_time": "10:00:00",
		"grand_total": -100,
		"paid": 0,
		"is_return": 1,
	},
]
PAYS = [
	{"name": "ACC-PAY-2026-00003", "posting_date": D("2026-10-09"), "posting_time": "11:15:00", "amount": 600}
]


class TestTheStatementLines(unittest.TestCase):
	def entries(self):
		return rules.statement_entries(DOCS, PAYS)

	def test_a_credit_sale_is_a_debit(self):
		sale = self.entries()[0]
		self.assertEqual((sale["kind"], sale["debit"], sale["credit"]), ("sale", 150000, 0))

	def test_a_cash_sale_has_a_debit_and_the_same_credit_on_one_line(self):
		sale = self.entries()[1]
		self.assertEqual((sale["kind"], sale["debit"], sale["credit"]), ("sale", 25000, 25000))

	def test_a_return_is_a_credit(self):
		line = self.entries()[2]
		self.assertEqual((line["kind"], line["debit"], line["credit"]), ("return", 0, 10000))

	def test_a_return_with_cash_handed_back_has_a_debit_for_the_cash(self):
		refund = rules.statement_entries([{**DOCS[2], "paid": -100}], [])[0]
		self.assertEqual((refund["debit"], refund["credit"]), (10000, 10000))

	def test_a_payment_is_a_credit(self):
		line = self.entries()[3]
		self.assertEqual((line["kind"], line["debit"], line["credit"]), ("payment", 0, 60000))

	def test_lines_are_oldest_first(self):
		self.assertEqual(
			[e["date"] for e in self.entries()], ["2026-10-05", "2026-10-06", "2026-10-08", "2026-10-09"]
		)

	def test_two_lines_on_a_day_are_ordered_by_time(self):
		rows = [
			{**DOCS[0], "posting_time": "15:00:00", "name": "ACC-SINV-2026-00008"},
			{**DOCS[0], "posting_time": "08:00:00", "name": "ACC-SINV-2026-00009"},
		]
		self.assertEqual(
			[e["id"] for e in rules.statement_entries(rows, [])],
			["ACC-SINV-2026-00009", "ACC-SINV-2026-00008"],
		)

	def test_a_document_without_a_date_is_skipped(self):
		self.assertEqual(
			rules.statement_entries([{**DOCS[0], "posting_date": None}], [{**PAYS[0], "posting_date": ""}]),
			[],
		)


class TestTheRunningBalance(unittest.TestCase):
	def statement(self, owed, first="2026-10-01", last="2026-10-13", entries=None):
		entries = rules.statement_entries(DOCS, PAYS) if entries is None else entries
		return rules.build_statement(entries, owed, D(first), D(last))

	def test_the_balance_runs_line_by_line(self):
		# 1500 - 100 - 600 = 800 is what the customer owes now; the cash sale nets to nothing
		built = self.statement(800)
		self.assertEqual([line["balance"] for line in built["lines"]], [1500.0, 1500.0, 1400.0, 800.0])

	def test_the_opening_balance_is_worked_back_from_what_is_owed_now(self):
		built = self.statement(2300)
		self.assertEqual(built["opening_balance"], 1500.0)
		self.assertEqual(built["lines"][0]["balance"], 3000.0)

	def test_a_statement_to_today_ends_at_the_balance_in_the_list(self):
		balances = rules.balances_by_customer([{"customer": "CUST-1", "outstanding_amount": 800}])
		built = self.statement(rules.balance_of(balances, "CUST-1"))
		self.assertEqual(built["closing_balance"], rules.balance_of(balances, "CUST-1"))
		self.assertEqual(built["closing_balance"], built["current_balance"])

	def test_the_closing_balance_is_opening_plus_debits_minus_credits(self):
		built = self.statement(800)
		self.assertEqual(
			calc.money(built["opening_balance"] + built["total_debit"] - built["total_credit"]),
			built["closing_balance"],
		)

	def test_the_totals(self):
		built = self.statement(800)
		self.assertEqual(built["total_debit"], 1750.0)
		self.assertEqual(built["total_credit"], 950.0)

	def test_a_statement_that_stops_early_ends_at_that_days_balance(self):
		built = self.statement(800, last="2026-10-06")
		self.assertEqual(built["closing_balance"], 1500.0)
		self.assertEqual(len(built["lines"]), 2)
		self.assertEqual(built["current_balance"], 800.0)

	def test_a_customer_with_no_activity_has_the_same_opening_and_closing(self):
		built = self.statement(0, entries=[])
		self.assertEqual((built["opening_balance"], built["closing_balance"], built["lines"]), (0.0, 0.0, []))

	def test_a_customer_who_owes_nothing_but_has_history_still_reconciles(self):
		built = self.statement(0)
		self.assertEqual(built["closing_balance"], 0.0)
		self.assertEqual(built["opening_balance"], -800.0)

	def test_a_line_shows_the_short_receipt_number(self):
		built = self.statement(800)
		self.assertEqual(
			[line["receipt"] for line in built["lines"]], ["#S0004", "#0012", "#S0006", "#P0003"]
		)

	def test_money_is_exact_to_the_cent(self):
		entries = rules.statement_entries(
			[
				{
					"name": "ACC-SINV-2026-00001",
					"doctype": "Sales Invoice",
					"posting_date": D("2026-10-02"),
					"grand_total": 0.1,
					"paid": 0,
				},
				{
					"name": "ACC-SINV-2026-00002",
					"doctype": "Sales Invoice",
					"posting_date": D("2026-10-03"),
					"grand_total": 0.2,
					"paid": 0,
				},
			],
			[],
		)
		built = rules.build_statement(entries, 0.3, D("2026-10-01"), D("2026-10-13"))
		self.assertEqual(built["opening_balance"], 0.0)
		self.assertEqual(built["closing_balance"], 0.3)

	def test_the_period_is_in_the_answer(self):
		built = self.statement(800)
		self.assertEqual((built["from"], built["to"]), ("2026-10-01", "2026-10-13"))

	def test_entries_before_the_first_day_are_dropped(self):
		kept = rules.entries_from(rules.statement_entries(DOCS, PAYS), D("2026-10-08"))
		self.assertEqual([e["id"] for e in kept], ["ACC-SINV-2026-00006", "ACC-PAY-2026-00003"])


class TestAShortReadOfALongHistory(unittest.TestCase):
	def rows(self, count, oldest="2026-10-05"):
		first = D(oldest)
		return [{"posting_date": first.replace(day=first.day + (i % 3))} for i in range(count)]

	def test_a_read_under_the_cap_is_complete(self):
		self.assertEqual(rules.complete_from(D("2026-09-01"), self.rows(10), [], cap=2000), D("2026-09-01"))

	def test_a_read_that_hit_the_cap_starts_the_day_after_its_oldest_day(self):
		self.assertEqual(rules.complete_from(D("2026-09-01"), self.rows(2000), [], cap=2000), D("2026-10-06"))

	def test_the_latest_start_of_all_the_reads_wins(self):
		late = [{"posting_date": D("2026-10-10")}] * 3
		self.assertEqual(rules.complete_from(D("2026-09-01"), self.rows(3), late, cap=3), D("2026-10-11"))

	def test_a_period_that_starts_later_than_the_cut_is_unchanged(self):
		self.assertEqual(rules.complete_from(D("2026-10-12"), self.rows(2000), cap=2000), D("2026-10-12"))


# --- the calls ----------------------------------------------------------------------------------------


class ApiCase(unittest.TestCase):
	"""A signed-in user, every database read replaced by fixed data."""

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, user="owner@shop-a.test", roles=("System Manager",), customer_owner="Shop A"):
		db = MagicMock()
		db.get_value.return_value = customer_owner
		db.has_column.return_value = True
		self.db = db
		self.start((frappe, "session"), new=frappe._dict(user=user))
		self.start((frappe, "get_roles"), create=True, side_effect=lambda user=None: list(roles))
		self.start((frappe, "db"), new=db, create=True)
		self.start((frappe, "log_error"), create=True)
		self.start((frappe, "get_traceback"), create=True, return_value="traceback with secret db detail")
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "_now_utc"), return_value=NOW)

	def stub(self, name, value=None, function=None):
		mock = (
			self.start((api, name), side_effect=function)
			if function
			else self.start((api, name), return_value=value)
		)
		return mock


OWED = [
	{"customer": "CUST-1", "outstanding_amount": 3500},
	{"customer": "CUST-2", "outstanding_amount": 21500},
	{"customer": "CUST-5", "outstanding_amount": 3500},
]


class ListCase(ApiCase):
	def setUp(self):
		self.counts = []
		self.pages = []

	def wire(self):
		self.balance_rows = self.stub("_balance_rows", function=lambda company: [dict(r) for r in OWED])

		def count(company, status, customer_type, search):
			self.counts.append((status, customer_type, search))
			return 3 if status == "active" and not customer_type and not search else 1

		self.stub("_count_customers", function=count)

		def page(company, status, customer_type, search, offset, limit):
			self.pages.append((offset, limit))
			return [dict(RECORDS["CUST-1"]), dict(RECORDS["CUST-2"])]

		self.stub("_customer_page", function=page)
		self.stub(
			"_customer_details", function=lambda names: {n: dict(RECORDS[n]) for n in names if n in RECORDS}
		)
		self.last_rows = self.stub(
			"_last_purchase_rows",
			function=lambda company, names: [dict(r) for r in SALES if r["customer"] in names],
		)

	def get(self, **kwargs):
		return api.list_customers_overview(**kwargs)

	def data(self, **kwargs):
		result = self.get(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]


class TestTheListForAnOwner(ListCase):
	def setUp(self):
		super().setUp()
		self.as_user()
		self.wire()

	def test_the_all_tab_has_rows_with_balances_and_last_purchases(self):
		data = self.data()
		self.assertEqual([r["id"] for r in data["rows"]], ["CUST-1", "CUST-2"])
		self.assertEqual(data["rows"][0]["balance"], 3500.0)
		self.assertEqual(data["rows"][1]["balance"], 21500.0)
		self.assertEqual(data["rows"][0]["last_purchase"]["date"], "2026-10-05")
		self.assertEqual(data["role"], "owner")

	def test_the_count_is_the_exact_total_not_the_size_of_the_page(self):
		data = self.data(page_size=2)
		self.assertEqual(data["total"], 3)
		self.assertEqual(data["pages"], 2)
		self.assertEqual(data["page_size"], 2)

	def test_the_page_asks_the_database_for_its_own_slice(self):
		self.data(page=2, page_size=1)
		self.assertEqual(self.pages, [(1, 1)])

	def test_a_page_past_the_end_asks_for_the_last_page(self):
		data = self.data(page=50, page_size=2)
		self.assertEqual(data["page"], 2)
		self.assertEqual(self.pages, [(2, 2)])

	def test_the_summary_has_the_three_numbers(self):
		self.assertEqual(
			self.data()["summary"], {"customers": 3, "owing_total": 28500.0, "owing_customers": 3}
		)

	def test_the_summary_is_not_narrowed_by_the_filters(self):
		data = self.data(search="baraka", customer_type="Company")
		self.assertEqual(data["summary"]["customers"], 3)
		self.assertEqual(data["summary"]["owing_total"], 28500.0)

	def test_the_owing_tab_lists_exactly_the_customers_with_a_balance_most_owed_first(self):
		data = self.data(tab="owing")
		self.assertEqual([r["id"] for r in data["rows"]], ["CUST-2", "CUST-1", "CUST-5"])
		self.assertEqual(data["total"], 3)

	def test_the_owing_tab_total_equals_the_summary_and_the_sum_of_the_balances(self):
		data = self.data(tab="owing")
		self.assertEqual(calc.money(sum(r["balance"] for r in data["rows"])), data["summary"]["owing_total"])
		self.assertEqual(data["total"], data["summary"]["owing_customers"])

	def test_the_owing_tab_and_home_read_the_same_rows(self):
		data = self.data(tab="owing")
		home = calc.money_owed_summary(OWED)
		self.assertEqual(data["summary"]["owing_total"], home["amount"])
		self.assertEqual(data["summary"]["owing_customers"], home["customers"])

	def test_the_owing_tab_does_not_read_the_customer_table_page(self):
		self.data(tab="owing")
		self.assertEqual(self.pages, [])

	def test_the_owing_tab_pages_in_memory(self):
		data = self.data(tab="owing", page=2, page_size=2)
		self.assertEqual([r["id"] for r in data["rows"]], ["CUST-5"])
		self.assertEqual((data["total"], data["pages"]), (3, 2))

	def test_the_owing_tab_search_and_type_narrow_the_rows(self):
		data = self.data(tab="owing", search="0733")
		self.assertEqual([r["id"] for r in data["rows"]], ["CUST-2"])
		data = self.data(tab="owing", customer_type="individual")
		self.assertEqual([r["id"] for r in data["rows"]], ["CUST-1", "CUST-5"])

	def test_the_filters_reach_the_database_for_the_all_tab(self):
		self.data(search="Wanjiru", customer_type="company", status="disabled")
		status, customer_type, search = self.counts[0]
		self.assertEqual((status, customer_type, search["text"]), ("disabled", "Company", "wanjiru"))

	def test_the_last_purchases_are_read_for_the_customers_on_the_page_only(self):
		self.data()
		self.assertEqual(self.last_rows.call_args.args, ("Shop A", ["CUST-1", "CUST-2"]))

	def test_an_empty_shop_gives_an_empty_page_not_an_error(self):
		self.stub("_balance_rows", function=lambda company: [])
		self.stub("_count_customers", function=lambda *a: 0)
		self.stub("_customer_page", function=lambda *a: [])
		data = self.data()
		self.assertEqual((data["rows"], data["total"], data["pages"], data["page"]), ([], 0, 1, 1))
		self.assertEqual(data["summary"], {"customers": 0, "owing_total": 0.0, "owing_customers": 0})

	def test_a_bad_tab_status_or_type_is_refused(self):
		for kwargs in ({"tab": "unpaid"}, {"status": "gone"}, {"customer_type": "Government"}):
			with self.subTest(kwargs=kwargs), self.assertRaises(frappe.ValidationError):
				self.get(**kwargs)

	def test_a_failing_read_gives_a_plain_message_and_logs_the_real_error(self):
		self.stub(
			"_count_customers", function=lambda *a: (_ for _ in ()).throw(RuntimeError("secret db detail"))
		)
		result = self.get()
		self.assertFalse(result["success"])
		self.assertNotIn("secret", json.dumps(result))
		frappe.log_error.assert_called()


class TestTheListForACashier(ListCase):
	def setUp(self):
		super().setUp()
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.wire()

	def test_no_balance_owed_or_amount_field_appears_anywhere(self):
		data = self.data()
		text = json.dumps(data)
		for word in ("balance", "owing", "amount", "owed"):
			with self.subTest(word=word):
				self.assertNotIn(word, text)

	def test_the_customers_are_still_listed(self):
		data = self.data()
		self.assertEqual([r["id"] for r in data["rows"]], ["CUST-1", "CUST-2"])
		self.assertEqual(data["summary"], {"customers": 3})
		self.assertEqual(data["role"], "cashier")

	def test_the_balances_are_never_read(self):
		self.data()
		self.balance_rows.assert_not_called()

	def test_the_owing_tab_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.get(tab="owing")
		self.balance_rows.assert_not_called()

	def test_the_last_purchase_has_a_date_but_no_amount(self):
		row = self.data()["rows"][0]
		self.assertEqual(row["last_purchase"]["date"], "2026-10-05")
		self.assertNotIn("amount", row["last_purchase"])

	def test_a_manager_sees_the_balances(self):
		self.as_user("boss@shop-a.test", roles=("Sales Manager",))
		self.assertIn("balance", self.data()["rows"][0])


class TestWhoIsLetIn(ApiCase):
	def test_a_guest_is_refused_and_nothing_is_read(self):
		self.as_user("Guest", roles=("Guest",))
		counter = self.stub("_count_customers", 0)
		with self.assertRaises(frappe.AuthenticationError):
			api.list_customers_overview()
		counter.assert_not_called()

	def test_a_guest_cannot_open_a_customer_or_a_statement_or_the_options(self):
		self.as_user("Guest", roles=("Guest",))
		for call, args in (
			(api.get_customer_overview, ("C",)),
			(api.get_customer_statement, ("C",)),
			(api.get_customer_form_options, ()),
		):
			with self.subTest(call=call.__name__), self.assertRaises(frappe.AuthenticationError):
				call(*args)

	def test_a_customer_of_another_business_is_refused_by_the_panel_and_the_statement(self):
		self.as_user(customer_owner="Shop B")
		record = self.stub("_customer_record", {"name": "CUST-1"})
		for call in (api.get_customer_overview, api.get_customer_statement):
			with self.subTest(call=call.__name__), self.assertRaises(frappe.PermissionError):
				call("CUST-1")
		record.assert_not_called()


FULL_RECORD = {
	**RECORDS["CUST-1"],
	"customer_group": "Individual",
	"territory": "Nairobi",
	"tax_id": "A012345678Z",
	"loyalty_program": "Duka Points",
	"custom_company": "Shop A",
}


class TestThePanel(ApiCase):
	def wire(self, tier_roles=("System Manager",)):
		self.as_user(roles=tier_roles)
		self.record = self.stub("_customer_record", dict(FULL_RECORD))
		self.stub(
			"_last_purchase_rows",
			function=lambda company, names: [dict(r) for r in SALES if r["customer"] in names],
		)
		self.stub("_features", {"groups": False, "territories": True})
		self.balance = self.stub("_balance_of", 3500.0)

	def test_the_owner_gets_the_facts_the_balance_and_the_last_purchase(self):
		self.wire()
		result = api.get_customer_overview("CUST-1")
		self.assertTrue(result["success"])
		data = result["data"]
		self.assertEqual((data["id"], data["name"], data["type"]), ("CUST-1", "Wanjiru Kamau", "Individual"))
		self.assertEqual(
			(data["mobile"], data["email"], data["tax_id"]), ("0712345678", "w@demo.test", "A012345678Z")
		)
		self.assertEqual(data["balance"], 3500.0)
		self.assertEqual(
			data["last_purchase"],
			{"date": "2026-10-05", "receipt": "#0012", "id": "ACC-PSINV-2026-00012", "amount": 250.0},
		)
		self.assertEqual(data["loyalty_program"], "Duka Points")
		self.assertEqual(data["features"], {"groups": False, "territories": True})

	def test_a_cashier_gets_the_facts_but_no_balance_and_no_amount(self):
		self.wire(("Sales User",))
		data = api.get_customer_overview("CUST-1")["data"]
		self.assertNotIn("balance", data)
		self.assertNotIn("amount", data["last_purchase"])
		self.balance.assert_not_called()

	def test_a_customer_that_is_not_there_is_said_so_plainly(self):
		self.wire()
		self.stub("_customer_record", None)
		result = api.get_customer_overview("NOPE")
		self.assertFalse(result["success"])
		self.assertIn("NOPE", result["message"])

	def test_a_failing_read_gives_a_plain_message(self):
		self.wire()
		self.stub(
			"_customer_record", function=lambda *a: (_ for _ in ()).throw(RuntimeError("secret db detail"))
		)
		result = api.get_customer_overview("CUST-1")
		self.assertFalse(result["success"])
		self.assertNotIn("secret", json.dumps(result))

	def test_the_panel_balance_is_the_list_balance_for_the_same_rows(self):
		self.as_user()
		rows = [
			{"customer": "CUST-1", "outstanding_amount": 2000},
			{"customer": "CUST-1", "outstanding_amount": 1500},
		]
		self.stub("_customer_balance_rows", function=lambda company, customer: rows)
		from_panel = api._balance_of("Shop A", "CUST-1")
		from_list = rules.balance_of(rules.balances_by_customer(rows), "CUST-1")
		self.assertEqual(from_panel, from_list)
		self.assertEqual(from_panel, 3500.0)


class TestTheStatementCall(ApiCase):
	def wire(self, roles=("System Manager",), balance=800.0):
		self.as_user(roles=roles)
		self.stub("_customer_record", dict(FULL_RECORD))
		self.reads = {
			"Sales Invoice": [dict(DOCS[0]), dict(DOCS[2])],
			"POS Invoice": [dict(DOCS[1])],
		}
		self.docs = self.stub("_statement_documents", function=lambda company, customer, first: self.reads)
		self.pays = self.stub(
			"_statement_payments", function=lambda company, customer, first: [dict(p) for p in PAYS]
		)
		self.stub(
			"_last_purchase_rows",
			function=lambda company, names: [dict(r) for r in SALES if r["customer"] in names],
		)
		self.stub("_balance_of", balance)

	def test_the_statement_has_lines_with_a_running_balance(self):
		self.wire()
		data = api.get_customer_statement("CUST-1", from_date="2026-10-01")["data"]
		self.assertEqual([line["balance"] for line in data["lines"]], [1500.0, 1500.0, 1400.0, 800.0])
		self.assertEqual(data["opening_balance"], 0.0)
		self.assertEqual(data["customer"], {"id": "CUST-1", "name": "Wanjiru Kamau", "mobile": "0712345678"})

	def test_the_closing_balance_equals_the_balance_in_the_list(self):
		self.wire(balance=800.0)
		data = api.get_customer_statement("CUST-1", from_date="2026-10-01")["data"]
		self.assertEqual(data["closing_balance"], 800.0)
		self.assertEqual(data["closing_balance"], data["current_balance"])

	def test_the_statement_balance_comes_from_the_same_function_as_the_list(self):
		source = inspect.getsource(api.get_customer_statement)
		self.assertIn("_balance_of(", source)
		self.assertIn("rules.balances_by_customer", inspect.getsource(api._balance_of))
		self.assertIn("rules.balances_by_customer", inspect.getsource(api._list_page))

	def test_a_cashier_is_refused(self):
		self.wire(("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			api.get_customer_statement("CUST-1")
		self.docs.assert_not_called()
		self.pays.assert_not_called()

	def test_a_manager_may_read_it(self):
		self.wire(("Accounts Manager",))
		self.assertTrue(api.get_customer_statement("CUST-1")["success"])

	def test_with_no_dates_it_covers_ninety_days_to_today(self):
		self.wire()
		data = api.get_customer_statement("CUST-1")["data"]
		self.assertEqual((data["from"], data["to"]), ("2026-07-16", "2026-10-13"))
		self.assertEqual(self.docs.call_args.args[2], D("2026-07-16"))

	def test_a_period_that_is_not_usable_is_refused(self):
		self.wire()
		for kwargs in (
			{"from_date": "2026-10-05", "to_date": "2026-10-01"},
			{"from_date": "2020-01-01"},
			{"from_date": "nope"},
		):
			with self.subTest(kwargs=kwargs), self.assertRaises(frappe.ValidationError):
				api.get_customer_statement("CUST-1", **kwargs)

	def test_a_statement_that_stops_early_closes_at_that_days_balance(self):
		self.wire()
		data = api.get_customer_statement("CUST-1", from_date="2026-10-01", to_date="2026-10-06")["data"]
		self.assertEqual(data["closing_balance"], 1500.0)
		self.assertEqual(data["current_balance"], 800.0)

	def test_a_long_history_that_hit_the_read_cap_says_it_was_cut(self):
		self.wire()
		many = [
			{
				**DOCS[0],
				"name": f"ACC-SINV-2026-{i:05d}",
				"posting_date": D("2026-10-05") + (D("2026-10-06") - D("2026-10-05")) * (i % 2),
			}
			for i in range(rules.STATEMENT_MAX_LINES)
		]
		self.reads = {"Sales Invoice": many, "POS Invoice": []}
		data = api.get_customer_statement("CUST-1", from_date="2026-09-01")["data"]
		self.assertTrue(data["truncated"])
		self.assertEqual(data["from"], "2026-10-06")
		self.assertEqual(data["requested_from"], "2026-09-01")

	def test_a_complete_read_is_not_marked_cut(self):
		self.wire()
		self.assertFalse(api.get_customer_statement("CUST-1", from_date="2026-10-01")["data"]["truncated"])

	def test_a_missing_customer_is_said_so_plainly(self):
		self.wire()
		self.stub("_customer_record", None)
		self.assertFalse(api.get_customer_statement("NOPE")["success"])

	def test_a_failing_read_gives_a_plain_message(self):
		self.wire()
		self.stub(
			"_statement_payments", function=lambda *a: (_ for _ in ()).throw(RuntimeError("secret db detail"))
		)
		result = api.get_customer_statement("CUST-1")
		self.assertFalse(result["success"])
		self.assertNotIn("secret", json.dumps(result))


class TestTheFormOptions(ApiCase):
	def test_the_features_and_choices_are_returned(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.stub("_features", {"groups": True, "territories": False})
		self.start(
			(frappe, "get_all"),
			create=True,
			side_effect=lambda doctype, **kwargs: ["A", "B"] if doctype == "Customer Group" else ["Nairobi"],
		)
		data = api.get_customer_form_options()["data"]
		self.assertEqual(
			data,
			{
				"features": {"groups": True, "territories": False},
				"groups": ["A", "B"],
				"territories": ["Nairobi"],
			},
		)


class TestTheQueries(ApiCase):
	def setUp(self):
		self.as_user()

	def test_the_all_tab_is_limited_to_the_business_and_leaves_out_the_walk_in_customer(self):
		conditions, values = api._customer_conditions("Shop A", "active", None, None)
		self.assertIn("c.custom_company = %(company)s", conditions)
		self.assertIn("c.name != %(walk_in)s", conditions)
		self.assertIn("c.disabled = 0", conditions)
		self.assertEqual(values["company"], "Shop A")
		self.assertEqual(values["walk_in"], "Walk-in Customer")

	def test_disabled_and_all_statuses(self):
		self.assertIn("c.disabled = 1", api._customer_conditions("Shop A", "disabled", None, None)[0])
		self.assertFalse(
			any("disabled" in c for c in api._customer_conditions("Shop A", "all", None, None)[0])
		)

	def test_a_search_looks_in_the_name_the_number_and_the_mobile(self):
		conditions, values = api._customer_conditions(
			"Shop A", "active", None, rules.parse_search("0712 345")
		)
		text = " ".join(conditions)
		self.assertIn("c.customer_name LIKE %(like)s", text)
		self.assertIn("c.name LIKE %(like)s", text)
		self.assertIn("c.email_id LIKE %(like)s", text)
		self.assertIn("REPLACE(REPLACE(REPLACE(IFNULL(c.mobile_no", text)
		self.assertEqual(values["mobile"], "%712345%")

	def test_a_name_search_does_not_look_in_the_mobile(self):
		conditions, values = api._customer_conditions("Shop A", "active", None, rules.parse_search("wanjiru"))
		self.assertNotIn("mobile", " ".join(conditions))
		self.assertEqual(values["like"], "%wanjiru%")

	def test_user_text_goes_in_as_a_value_never_into_the_sql(self):
		conditions, values = api._customer_conditions(
			"Shop A", "active", "Company", rules.parse_search("x'; DROP TABLE")
		)
		self.assertNotIn("drop", " ".join(conditions).lower())
		self.assertIn("drop", values["like"])
		self.assertIn("c.customer_type = %(customer_type)s", conditions)

	def test_a_database_without_the_company_column_is_not_filtered_by_it(self):
		self.db.has_column.return_value = False
		self.assertFalse(
			any("custom_company" in c for c in api._customer_conditions("Shop A", "active", None, None)[0])
		)

	def test_one_customers_balance_uses_homes_conditions_for_both_kinds_of_sale(self):
		self.db.sql.return_value = [{"customer": "CUST-1", "outstanding_amount": 3500}]
		self.assertEqual(
			api._customer_balance_rows("Shop A", "CUST-1"),
			[{"customer": "CUST-1", "outstanding_amount": 3500}],
		)
		sql, values = self.db.sql.call_args.args[0], self.db.sql.call_args.args[1]
		self.assertIn("`tabSales Invoice`", sql)
		self.assertIn("`tabPOS Invoice`", sql)
		self.assertIn("IFNULL(d.consolidated_invoice, '') = ''", sql)
		self.assertIn("d.is_return = 0", sql)
		self.assertIn("d.outstanding_amount > 0", sql)
		self.assertEqual(sql.count("AND d.customer = %(customer)s"), 2)
		self.assertEqual(values["customer"], "CUST-1")
		self.assertEqual(values["company"], "Shop A")

	def test_the_list_reads_the_balances_with_homes_own_function(self):
		with patch.object(
			dashboard_overview, "_money_owed_rows", return_value=[{"customer": "C", "outstanding_amount": 1}]
		) as home:
			self.assertEqual(api._balance_rows("Shop A"), [{"customer": "C", "outstanding_amount": 1}])
		scope = home.call_args.args[0]
		self.assertEqual((scope.company, scope.owner, scope.store), ("Shop A", None, None))

	def test_the_last_purchase_read_counts_both_kinds_but_not_the_till_closing_invoice(self):
		self.db.sql.return_value = []
		api._last_purchase_rows("Shop A", ["CUST-1"])
		sql = [c.args[0] for c in self.db.sql.call_args_list]
		self.assertEqual(len(sql), 2)
		self.assertIn("`tabSales Invoice`", sql[0])
		self.assertIn("IFNULL(d.is_consolidated, 0) = 0", sql[0])
		self.assertIn("IFNULL(i.is_consolidated, 0) = 0", sql[0])
		self.assertIn("`tabPOS Invoice`", sql[1])
		self.assertNotIn("is_consolidated", sql[1])
		self.assertNotIn("consolidated_invoice", sql[1])
		for text in sql:
			self.assertIn("is_return = 0", text)

	def test_no_customers_means_no_last_purchase_query(self):
		self.assertEqual(api._last_purchase_rows("Shop A", []), [])
		self.db.sql.assert_not_called()

	def test_the_statement_read_leaves_out_pos_invoices_merged_at_till_close(self):
		self.db.sql.return_value = []
		api._statement_documents("Shop A", "CUST-1", D("2026-10-01"))
		sql = [c.args[0] for c in self.db.sql.call_args_list]
		self.assertNotIn("consolidated_invoice", sql[0])
		self.assertIn("IFNULL(d.consolidated_invoice, '') = ''", sql[1])
		for text in sql:
			self.assertIn("d.docstatus = 1", text)
			self.assertIn("LIMIT %(cap)s", text)

	def test_the_payments_read_takes_received_payments_against_sales_invoices(self):
		self.db.sql.return_value = []
		api._statement_payments("Shop A", "CUST-1", D("2026-10-01"))
		sql = self.db.sql.call_args.args[0]
		self.assertIn("pe.payment_type = 'Receive'", sql)
		self.assertIn("pe.party_type = 'Customer'", sql)
		self.assertIn("pe.docstatus = 1", sql)
		self.assertIn("r.reference_doctype IN ('Sales Invoice', 'POS Invoice')", sql)

	def test_features_read_the_groups_and_areas_of_the_business(self):
		self.db.sql.side_effect = [[{"v": "Individual"}, {"v": "Commercial"}], [{"v": "Nairobi"}]]
		self.assertEqual(api._features("Shop A"), {"groups": True, "territories": False})


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	CALLS = (
		"list_customers_overview",
		"get_customer_overview",
		"get_customer_statement",
		"get_customer_form_options",
	)

	def test_every_call_is_whitelisted(self):
		for name in self.CALLS:
			with self.subTest(call=name):
				self.assertIn(getattr(api, name), frappe.whitelisted)

	def test_the_statement_refuses_a_cashier_before_it_reads_anything(self):
		tree = ast.parse(inspect.getsource(api))
		function = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_customer_statement"
		)
		statements = [
			ast.unparse(s)
			for s in function.body
			if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		]
		self.assertIn("role_tiers.require_access('money_owed')", statements[:3])
		self.assertLess(
			statements.index("role_tiers.require_access('money_owed')"),
			next(i for i, s in enumerate(statements) if "_customer_record" in s or "try" in s),
		)

	def test_the_pure_module_needs_no_database_and_no_frappe(self):
		self.assertNotIn("import frappe", inspect.getsource(rules))
		self.assertNotIn("frappe.", inspect.getsource(rules))

	def test_no_em_dash_in_the_files(self):
		for module in (api, rules):
			with self.subTest(module=module.__name__):
				self.assertNotIn(chr(0x2014), inspect.getsource(module))

	def test_the_calls_only_read(self):
		source = inspect.getsource(api)
		for word in ("insert(", ".save(", "submit(", "cancel(", "delete_doc", "set_value", "db.commit"):
			with self.subTest(word=word):
				self.assertNotIn(word, source)


if __name__ == "__main__":
	unittest.main()
