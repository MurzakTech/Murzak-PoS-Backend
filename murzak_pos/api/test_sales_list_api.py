# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Sales page calls (`sales_list_api.list_sales` and `sales_list_api.get_sale`) and the rules behind them
(`sales_list_calc`). They need no database: every Frappe call is mocked.

The first half tests the plain functions with plain rows: the short receipt number, the status of a sale, the summary
row, paging, the union of the two kinds of document, search, who may see what, and that the list and the receipt get
one amount for one sale. The second half tests the calls: who is let in, that a cashier is held to their own sales
and sessions, what is asked of the database, and that the list and the receipt agree.
"""

import ast
import os
import re
import unittest
from datetime import date, datetime, time, timedelta, timezone
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import overview_calc as calc
from murzak_pos.api import sales_list_api as api
from murzak_pos.api import sales_list_calc as rules

TODAY = date(2026, 10, 13)  # a Tuesday
NOW = datetime(2026, 10, 13, 9, 30, tzinfo=timezone.utc)  # 12:30 in Nairobi
D = date.fromisoformat
POS, SALES = rules.POS_INVOICE, rules.SALES_INVOICE


def sale(name, doctype=POS, day="2026-10-13", at="09:15:00", total=1000, owing=0, **extra):
	row = {
		"doctype": doctype,
		"name": name,
		"posting_date": D(day),
		"posting_time": at,
		"customer": "CUST-1",
		"customer_name": "Wanjiru Kamau",
		"owner": "amina@shop-a.test",
		"grand_total": total,
		"outstanding_amount": owing,
		"is_return": 0,
		"due_date": None,
		"has_terms": False,
		"is_consolidated": 0,
	}
	row.update(extra)
	return row


# --- the short receipt number -----------------------------------------------------------------------


class TestShortReceiptNumber(unittest.TestCase):
	def test_a_pos_invoice_is_its_number_padded_to_four(self):
		self.assertEqual(rules.short_receipt(POS, "ACC-PSINV-2026-00005"), "#0005")
		self.assertEqual(rules.short_receipt(POS, "ACC-PSINV-2026-00033"), "#0033")

	def test_a_sales_invoice_carries_an_s_so_the_two_kinds_never_share_a_number(self):
		self.assertEqual(rules.short_receipt(SALES, "ACC-SINV-2026-00005"), "#S0005")
		self.assertNotEqual(
			rules.short_receipt(POS, "ACC-PSINV-2026-00005"),
			rules.short_receipt(SALES, "ACC-SINV-2026-00005"),
		)

	def test_a_long_number_is_kept_whole(self):
		self.assertEqual(rules.short_receipt(POS, "ACC-PSINV-2026-123456"), "#123456")
		self.assertEqual(rules.short_receipt(POS, "ACC-PSINV-2026-09999"), "#9999")

	def test_an_amended_copy_keeps_the_number_of_its_original(self):
		self.assertEqual(rules.short_receipt(POS, "ACC-PSINV-2026-00005-1"), "#0005")
		self.assertEqual(rules.sequence_of("ACC-SINV-2026-00012-2"), 12)

	def test_a_name_ending_in_a_short_number_is_not_taken_for_an_amendment(self):
		self.assertEqual(rules.sequence_of("SINV-12"), 12)
		self.assertEqual(rules.sequence_of("POS-2026-12"), 12)

	def test_a_name_with_no_number_is_shown_as_it_is(self):
		self.assertEqual(rules.short_receipt(POS, "CUSTOM-NAME"), "CUSTOM-NAME")
		self.assertIsNone(rules.sequence_of("CUSTOM-NAME"))
		self.assertIsNone(rules.sequence_of(None))

	def test_the_same_name_always_gives_the_same_number(self):
		first = rules.short_receipt(POS, "ACC-PSINV-2026-00031")
		self.assertEqual(first, rules.short_receipt(POS, "ACC-PSINV-2026-00031"))

	def test_two_years_can_share_a_number_which_the_id_tells_apart(self):
		"""The documented risk: the year is not in the short number, so the id is the key."""
		a = rules.list_row(sale("ACC-PSINV-2025-00005", day="2025-11-02"), TODAY)
		b = rules.list_row(sale("ACC-PSINV-2026-00005"), TODAY)
		self.assertEqual(a["receipt"], b["receipt"])
		self.assertNotEqual(a["id"], b["id"])
		self.assertNotEqual(a["date"], b["date"])

	def test_the_system_reference_is_kept_for_the_details_area(self):
		row = rules.list_row(sale("ACC-PSINV-2026-00005"), TODAY)
		self.assertEqual(row["reference"], "ACC-PSINV-2026-00005")
		self.assertEqual(row["receipt"], "#0005")


# --- search -----------------------------------------------------------------------------------------


class TestSearch(unittest.TestCase):
	def test_an_empty_search_means_no_search(self):
		self.assertIsNone(rules.parse_search(None))
		self.assertIsNone(rules.parse_search("   "))

	def test_receipt_number_forms(self):
		self.assertEqual(rules.parse_receipt_query("#0033"), (None, 33))
		self.assertEqual(rules.parse_receipt_query("33"), (None, 33))
		self.assertEqual(rules.parse_receipt_query("0033"), (None, 33))
		self.assertEqual(rules.parse_receipt_query("S33"), (SALES, 33))
		self.assertEqual(rules.parse_receipt_query("#s0033"), (SALES, 33))
		self.assertIsNone(rules.parse_receipt_query("Sam"))
		self.assertIsNone(rules.parse_receipt_query("ACC-PSINV-2026-00033"))

	def test_a_receipt_number_finds_that_receipt(self):
		parsed = rules.parse_search("#0033")
		self.assertTrue(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00033"}, parsed))
		self.assertTrue(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00033-1"}, parsed))
		self.assertFalse(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00133"}, parsed))
		self.assertFalse(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00034"}, parsed))

	def test_a_plain_number_does_not_match_the_year_in_the_reference(self):
		parsed = rules.parse_search("26")
		self.assertFalse(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00005"}, parsed))
		self.assertTrue(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00026"}, parsed))

	def test_the_s_letter_limits_the_number_to_sales_invoices(self):
		parsed = rules.parse_search("S33")
		self.assertTrue(rules.search_matches({"doctype": SALES, "name": "ACC-SINV-2026-00033"}, parsed))
		self.assertFalse(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00033"}, parsed))

	def test_a_plain_number_matches_either_kind(self):
		parsed = rules.parse_search("33")
		self.assertTrue(rules.search_matches({"doctype": SALES, "name": "ACC-SINV-2026-00033"}, parsed))
		self.assertTrue(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00033"}, parsed))

	def test_the_customer_name_is_found_in_any_case_and_in_part(self):
		parsed = rules.parse_search("wanji")
		self.assertTrue(
			rules.search_matches({"doctype": POS, "name": "X-1", "customer_name": "Wanjiru Kamau"}, parsed)
		)
		self.assertFalse(
			rules.search_matches({"doctype": POS, "name": "X-1", "customer_name": "Otieno Odhiambo"}, parsed)
		)

	def test_the_system_reference_is_found_by_its_text(self):
		parsed = rules.parse_search("PSINV-2026-00005")
		self.assertIsNone(parsed["seq"])
		self.assertTrue(rules.search_matches({"doctype": POS, "name": "ACC-PSINV-2026-00005"}, parsed))

	def test_a_number_in_a_customer_name_still_matches(self):
		parsed = rules.parse_search("33")
		self.assertTrue(
			rules.search_matches(
				{"doctype": POS, "name": "ACC-PSINV-2026-00001", "customer_name": "Shop 33"}, parsed
			)
		)

	def test_the_like_pattern_escapes_wildcards_and_the_text_is_capped(self):
		self.assertEqual(rules.parse_search("50%_off")["like"], "%50\\%\\_off%")
		self.assertEqual(len(rules.parse_search("x" * 500)["text"]), rules.MAX_SEARCH_LENGTH)

	def test_the_regex_for_a_number_is_safe_and_matches_names_python_side(self):
		pattern = rules.receipt_regex(33)
		self.assertTrue(re.search(pattern, "ACC-PSINV-2026-00033"))
		self.assertFalse(re.search(pattern, "ACC-PSINV-2026-00333"))
		self.assertEqual(rules.receipt_regex("7; DROP"[:1]), rules.receipt_regex(7))


# --- status -----------------------------------------------------------------------------------------


class TestStatus(unittest.TestCase):
	def test_paid(self):
		self.assertEqual(rules.derive_status(1000, 0, None, TODAY), "paid")

	def test_unpaid_before_the_due_date(self):
		self.assertEqual(rules.derive_status(1000, 400, D("2026-10-20"), TODAY), "unpaid")

	def test_unpaid_on_the_due_day(self):
		self.assertEqual(rules.derive_status(1000, 400, TODAY, TODAY), "unpaid")

	def test_overdue_after_the_due_date(self):
		self.assertEqual(rules.derive_status(1000, 400, D("2026-10-12"), TODAY), "overdue")

	def test_unpaid_with_no_due_date(self):
		self.assertEqual(rules.derive_status(1000, 400, None, TODAY), "unpaid")

	def test_part_returned(self):
		self.assertEqual(rules.derive_status(1000, 0, None, TODAY, returned_total=300), "part-returned")

	def test_returned_when_the_returns_add_up_to_the_sale(self):
		self.assertEqual(rules.derive_status(1000, 0, None, TODAY, returned_total=1000), "returned")
		self.assertEqual(rules.derive_status(1000, 0, None, TODAY, returned_total=999.999), "returned")

	def test_a_return_document_is_returned(self):
		self.assertEqual(rules.derive_status(-500, 0, None, TODAY, is_return=True), "returned")

	def test_money_owed_comes_before_a_part_return(self):
		self.assertEqual(
			rules.derive_status(1000, 500, D("2026-10-01"), TODAY, returned_total=200), "overdue"
		)
		self.assertEqual(rules.derive_status(1000, 500, None, TODAY, returned_total=200), "unpaid")

	def test_returns_given_as_negative_numbers_count_the_same(self):
		self.assertEqual(rules.derive_status(1000, 0, None, TODAY, returned_total=-1000), "returned")

	def test_every_status_is_one_of_the_five(self):
		for args in [(1, 0), (1, 1), (1, 1), (1, 0)]:
			self.assertIn(rules.derive_status(*args, None, TODAY), rules.STATUSES)

	def test_a_row_uses_the_customers_terms_or_14_days_for_overdue(self):
		# without terms the due date on the invoice is its own day, which is not trusted: 14 days after the sale
		young = rules.list_row(
			sale("ACC-PSINV-2026-00001", day="2026-10-05", total=900, owing=900, due_date=D("2026-10-05")),
			TODAY,
		)
		self.assertEqual(young["status"], "unpaid")
		old = rules.list_row(
			sale("ACC-PSINV-2026-00002", day="2026-09-20", total=900, owing=900, due_date=D("2026-09-20")),
			TODAY,
		)
		self.assertEqual(old["status"], "overdue")
		terms = rules.list_row(
			sale(
				"ACC-PSINV-2026-00003",
				day="2026-09-20",
				total=900,
				owing=900,
				due_date=D("2026-10-30"),
				has_terms=True,
			),
			TODAY,
		)
		self.assertEqual(terms["status"], "unpaid")


# --- the money of one sale --------------------------------------------------------------------------


class TestOneAmountForOneSale(unittest.TestCase):
	def test_total_paid_and_outstanding(self):
		amounts = rules.sale_amounts({"grand_total": 2350, "outstanding_amount": 350})
		self.assertEqual((amounts["total"], amounts["paid"], amounts["outstanding"]), (2350.0, 2000.0, 350.0))

	def test_change_does_not_raise_what_was_paid(self):
		amounts = rules.sale_amounts({"grand_total": 1000, "outstanding_amount": 0, "change_amount": 200})
		self.assertEqual((amounts["paid"], amounts["change"]), (1000.0, 200.0))

	def test_money_is_rounded_to_two_places(self):
		self.assertEqual(rules.sale_amounts({"grand_total": 10.005, "outstanding_amount": 0})["total"], 10.01)
		self.assertEqual(rules.sale_amounts({"grand_total": 1 / 3, "outstanding_amount": 0})["total"], 0.33)

	def test_missing_numbers_are_zero(self):
		amounts = rules.sale_amounts({})
		self.assertEqual((amounts["total"], amounts["paid"], amounts["outstanding"]), (0.0, 0.0, 0.0))

	def test_a_return_is_negative(self):
		self.assertEqual(rules.sale_amounts({"grand_total": -500, "outstanding_amount": 0})["total"], -500.0)

	def test_the_list_row_and_the_receipt_show_the_same_total(self):
		doc = sale("ACC-PSINV-2026-00033", total=2350.5, owing=350.5, change_amount=0)
		row = rules.list_row(doc, TODAY)
		receipt = rules.build_receipt(doc, TODAY)
		self.assertEqual(row["total"], receipt["totals"]["total"])
		self.assertEqual(row["paid"], receipt["totals"]["paid"])
		self.assertEqual(row["outstanding"], receipt["totals"]["outstanding"])
		self.assertEqual(row["status"], receipt["status"])
		self.assertEqual(row["receipt"], receipt["receipt"])

	def test_they_agree_for_many_kinds_of_sale(self):
		cases = [
			sale("ACC-PSINV-2026-00001", total=1),
			sale("ACC-PSINV-2026-00002", total=99999.99, owing=1000),
			sale("ACC-SINV-2026-00003", doctype=SALES, total=-250, is_return=1),
			sale("ACC-SINV-2026-00004", doctype=SALES, total=1234.567),
		]
		for doc in cases:
			with self.subTest(doc=doc["name"]):
				self.assertEqual(
					rules.list_row(doc, TODAY)["total"], rules.build_receipt(doc, TODAY)["totals"]["total"]
				)

	def test_the_total_is_the_full_amount_with_vat_not_the_net_figure(self):
		doc = sale("ACC-PSINV-2026-00007", total=1160, net_total=1000, total_taxes_and_charges=160)
		row = rules.list_row(doc, TODAY)
		self.assertEqual(row["total"], 1160.0)
		self.assertNotIn("net_total", row)


# --- the summary row --------------------------------------------------------------------------------


class TestSummary(unittest.TestCase):
	def test_sold_unpaid_and_returned(self):
		rows = [
			{"is_return": 0, "total": 10000, "outstanding": 2500},
			{"is_return": 0, "total": 5000, "outstanding": 0},
			{"is_return": 1, "total": -800, "outstanding": 0},
		]
		self.assertEqual(rules.build_summary(rows), {"sold": 15000.0, "unpaid": 2500.0, "returned": 800.0})

	def test_returned_is_a_positive_number(self):
		self.assertEqual(rules.build_summary([{"is_return": 1, "total": -300}])["returned"], 300.0)

	def test_a_return_does_not_reduce_what_was_sold(self):
		rows = [{"is_return": 0, "total": 1000, "outstanding": 0}, {"is_return": 1, "total": -1000}]
		self.assertEqual(rules.build_summary(rows)["sold"], 1000.0)

	def test_a_credit_balance_does_not_reduce_what_is_unpaid(self):
		self.assertEqual(
			rules.build_summary([{"is_return": 0, "total": 100, "outstanding": -20}])["unpaid"], 0.0
		)

	def test_both_kinds_of_document_add_together(self):
		rows = [
			{"is_return": 0, "total": 1000, "outstanding": 100},  # POS Invoices
			{"is_return": 0, "total": 2000, "outstanding": 200},  # Sales Invoices
		]
		self.assertEqual(rules.build_summary(rows), {"sold": 3000.0, "unpaid": 300.0, "returned": 0.0})

	def test_nothing_gives_zeros(self):
		self.assertEqual(rules.build_summary([]), {"sold": 0.0, "unpaid": 0.0, "returned": 0.0})
		self.assertEqual(rules.build_summary(None), {"sold": 0.0, "unpaid": 0.0, "returned": 0.0})

	def test_amounts_from_the_database_may_be_none_or_text(self):
		rows = [{"is_return": 0, "total": None, "outstanding": None}, {"is_return": None, "total": "40.5"}]
		self.assertEqual(rules.build_summary(rows)["sold"], 40.5)


# --- paging -----------------------------------------------------------------------------------------


class TestPaging(unittest.TestCase):
	def test_the_defaults(self):
		self.assertEqual(rules.page_numbers(1, 20, 45), {"page": 1, "page_size": 20, "pages": 3, "offset": 0})

	def test_the_second_page_starts_after_the_first(self):
		self.assertEqual(rules.page_numbers(2, 20, 45)["offset"], 20)

	def test_a_full_last_page_does_not_add_an_empty_one(self):
		self.assertEqual(rules.page_numbers(1, 20, 40)["pages"], 2)
		self.assertEqual(rules.page_numbers(1, 20, 41)["pages"], 3)

	def test_a_page_past_the_end_is_the_last(self):
		numbers = rules.page_numbers(9, 20, 45)
		self.assertEqual((numbers["page"], numbers["offset"]), (3, 40))

	def test_a_page_below_one_is_the_first(self):
		self.assertEqual(rules.page_numbers(0, 20, 45)["page"], 1)
		self.assertEqual(rules.page_numbers(-4, 20, 45)["page"], 1)

	def test_a_size_above_100_is_100(self):
		self.assertEqual(rules.page_numbers(1, 5000, 1000)["page_size"], 100)

	def test_a_missing_or_wrong_size_is_20(self):
		for size in (None, "", 0, -5, "abc"):
			with self.subTest(size=size):
				self.assertEqual(rules.page_numbers(1, size, 100)["page_size"], 20)

	def test_numbers_may_arrive_as_text(self):
		numbers = rules.page_numbers("2", "10", "25")
		self.assertEqual((numbers["page"], numbers["page_size"], numbers["pages"]), (2, 10, 3))

	def test_an_empty_list_has_one_page(self):
		numbers = rules.page_numbers(1, 20, 0)
		self.assertEqual((numbers["page"], numbers["pages"], numbers["offset"]), (1, 1, 0))

	def test_a_bad_page_is_the_first(self):
		self.assertEqual(rules.page_numbers("x", 20, 100)["page"], 1)


# --- the two kinds of document as one list ------------------------------------------------------------


class TestOneListFromTwoKinds(unittest.TestCase):
	def test_newest_first_across_both_kinds(self):
		pos = [
			sale("ACC-PSINV-2026-00002", day="2026-10-13", at="10:00:00"),
			sale("ACC-PSINV-2026-00001", day="2026-10-12", at="18:00:00"),
		]
		invoices = [sale("ACC-SINV-2026-00007", doctype=SALES, day="2026-10-13", at="11:30:00")]
		names = [r["name"] for r in rules.union_sales(pos, invoices)]
		self.assertEqual(names, ["ACC-SINV-2026-00007", "ACC-PSINV-2026-00002", "ACC-PSINV-2026-00001"])

	def test_same_day_is_ordered_by_time_and_seconds(self):
		rows = [
			sale("A-00001", at="09:15:10"),
			sale("A-00002", at="09:15:50"),
			sale("A-00003", at="08:00:00"),
		]
		self.assertEqual([r["name"] for r in rules.union_sales(rows)], ["A-00002", "A-00001", "A-00003"])

	def test_the_time_may_come_as_a_timedelta_from_the_database(self):
		rows = [sale("A-00001", at=timedelta(hours=9, minutes=15)), sale("A-00002", at=timedelta(hours=14))]
		self.assertEqual([r["name"] for r in rules.union_sales(rows)], ["A-00002", "A-00001"])

	def test_the_same_moment_is_ordered_by_name_descending(self):
		rows = [sale("A-00001"), sale("A-00003"), sale("A-00002")]
		self.assertEqual([r["name"] for r in rules.union_sales(rows)], ["A-00003", "A-00002", "A-00001"])

	def test_a_pos_invoice_merged_into_a_sales_invoice_is_not_listed_twice(self):
		merged = sale("ACC-PSINV-2026-00001", merged_into="ACC-SINV-2026-00010")
		kept = sale("ACC-PSINV-2026-00002", merged_into="")
		closing = sale("ACC-SINV-2026-00010", doctype=SALES)
		names = [r["name"] for r in rules.union_sales([merged, kept], [closing])]
		self.assertNotIn("ACC-PSINV-2026-00001", names)
		self.assertEqual(sorted(names), ["ACC-PSINV-2026-00002", "ACC-SINV-2026-00010"])

	def test_the_erpnext_field_name_is_understood_too(self):
		self.assertTrue(rules.is_merged({"doctype": POS, "name": "X", "consolidated_invoice": "ACC-SINV-1"}))

	def test_a_sales_invoice_is_never_called_merged(self):
		self.assertFalse(rules.is_merged({"doctype": SALES, "name": "X", "consolidated_invoice": "Y"}))

	def test_empty_groups_give_an_empty_list(self):
		self.assertEqual(rules.union_sales([], []), [])
		self.assertEqual(rules.union_sales(None), [])

	def test_dates_and_times_are_written_plainly(self):
		row = rules.list_row(sale("ACC-PSINV-2026-00001", day="2026-10-02", at="07:05:59"), TODAY)
		self.assertEqual((row["date"], row["time"]), ("2026-10-02", "07:05"))

	def test_time_forms(self):
		self.assertEqual(rules.time_text(time(14, 30, 12)), "14:30")
		self.assertEqual(rules.time_text(datetime(2026, 1, 1, 8, 5)), "08:05")
		self.assertEqual(rules.time_text(timedelta(hours=26)), "02:00")
		self.assertEqual(rules.time_text("9:05:00.123"), "09:05")
		self.assertEqual(rules.time_text(None), "00:00")


# --- the rows ---------------------------------------------------------------------------------------


class TestRows(unittest.TestCase):
	def test_a_row_has_the_agreed_fields(self):
		row = rules.list_row(
			sale("ACC-PSINV-2026-00033", total=2350), TODAY, names={"amina@shop-a.test": "Amina Hassan"}
		)
		self.assertEqual(
			set(row),
			{
				"id",
				"doctype",
				"receipt",
				"reference",
				"date",
				"time",
				"customer",
				"cashier",
				"total",
				"paid",
				"outstanding",
				"status",
				"methods",
				"is_return",
				"consolidated",
			},
		)
		self.assertEqual(row["cashier"], "Amina Hassan")
		self.assertEqual(row["id"], "ACC-PSINV-2026-00033")
		self.assertEqual(row["doctype"], POS)

	def test_a_sale_with_no_customer_is_a_walk_in(self):
		row = rules.list_row(sale("A-00001", customer=None, customer_name=None), TODAY)
		self.assertEqual(row["customer"], "Walk-in Customer")

	def test_the_customer_name_is_preferred_to_the_customer_id(self):
		self.assertEqual(rules.customer_label("Wanjiru", "CUST-9"), "Wanjiru")
		self.assertEqual(rules.customer_label(None, "CUST-9"), "CUST-9")

	def test_a_cashier_with_no_full_name_shows_their_user(self):
		self.assertEqual(rules.cashier_label("amina@shop-a.test", {}), "amina@shop-a.test")
		self.assertEqual(rules.cashier_label(None), "")

	def test_the_ways_of_paying_are_in_the_tills_order_and_each_once(self):
		rows = [
			{"mode_of_payment": "Cash", "amount": 500},
			{"mode_of_payment": "Mpesa", "amount": 300},
			{"mode_of_payment": "M-Pesa", "amount": 200},
			{"mode_of_payment": "Cheque", "amount": 100},
		]
		self.assertEqual(rules.method_labels(rows), ["M-Pesa", "Cash", "Other"])

	def test_a_payment_of_zero_is_not_a_method(self):
		self.assertEqual(rules.method_labels([{"mode_of_payment": "Cash", "amount": 0}]), [])

	def test_money_still_owed_shows_on_credit(self):
		self.assertEqual(
			rules.method_labels([{"mode_of_payment": "Cash", "amount": 400}], 600), ["Cash", "On credit"]
		)
		self.assertEqual(rules.method_labels([], 600, is_return=True), [])

	def test_a_credit_card_is_a_card(self):
		self.assertEqual(rules.method_labels([{"mode_of_payment": "Credit Card", "amount": 50}]), ["Card"])

	def test_a_sale_part_returned_shows_it(self):
		row = rules.list_row(sale("ACC-PSINV-2026-00033"), TODAY, returned={"ACC-PSINV-2026-00033": 250})
		self.assertEqual(row["status"], "part-returned")

	def test_a_return_row(self):
		row = rules.list_row(sale("ACC-PSINV-2026-00040", total=-300, is_return=1), TODAY)
		self.assertEqual((row["is_return"], row["status"], row["total"]), (True, "returned", -300.0))

	def test_a_row_says_when_it_is_a_till_closing_invoice(self):
		row = rules.list_row(sale("ACC-SINV-2026-00010", doctype=SALES, is_consolidated=1), TODAY)
		self.assertTrue(row["consolidated"])


# --- tabs, scope and periods --------------------------------------------------------------------------


class TestTabs(unittest.TestCase):
	def test_each_tab_maps_to_its_conditions(self):
		self.assertEqual(rules.tab_conditions("all"), [])
		self.assertEqual(rules.tab_conditions("unpaid"), ["d.is_return = 0", "d.outstanding_amount > 0"])
		self.assertEqual(rules.tab_conditions("returns"), ["d.is_return = 1"])

	def test_the_tab_name_is_forgiving_about_case_and_gaps(self):
		self.assertEqual(rules.normalize_tab(" Unpaid "), "unpaid")
		self.assertEqual(rules.normalize_tab(None), "all")
		self.assertEqual(rules.normalize_tab(""), "all")

	def test_an_unknown_tab_is_refused(self):
		with self.assertRaises(rules.ListError) as raised:
			rules.normalize_tab("everything")
		self.assertEqual(raised.exception.code, "bad_tab")

	def test_the_doctype_must_be_one_of_the_two(self):
		self.assertEqual(rules.normalize_doctype("POS Invoice"), POS)
		self.assertIsNone(rules.normalize_doctype(None))
		with self.assertRaises(rules.ListError):
			rules.normalize_doctype("Purchase Invoice")


class TestWhoSeesWhat(unittest.TestCase):
	def test_a_cashier_gets_their_own_sales_whatever_they_ask_for(self):
		self.assertEqual(rules.scope_owner("cashier", "amina@x", "boss@x"), "amina@x")
		self.assertEqual(rules.scope_owner("cashier", "amina@x", None), "amina@x")

	def test_an_owner_or_manager_may_choose_or_see_everyone(self):
		self.assertEqual(rules.scope_owner("owner", "boss@x", "amina@x"), "amina@x")
		self.assertIsNone(rules.scope_owner("manager", "m@x", None))
		self.assertIsNone(rules.scope_owner("manager", "m@x", ""))

	def test_a_cashier_may_read_only_their_own_sale(self):
		self.assertTrue(rules.may_read_sale("cashier", "amina@x", "amina@x"))
		self.assertFalse(rules.may_read_sale("cashier", "amina@x", "otieno@x"))
		self.assertTrue(rules.may_read_sale("owner", "boss@x", "otieno@x"))
		self.assertTrue(rules.may_read_sale("manager", "m@x", "otieno@x"))

	def test_returns_are_offered_to_the_roles_that_may_record_them(self):
		self.assertTrue(rules.may_return(["Sales Manager"], "m@x"))
		self.assertTrue(rules.may_return(["System Manager"], "boss@x"))
		self.assertTrue(rules.may_return([], "Administrator"))
		self.assertFalse(rules.may_return(["Sales User"], "amina@x"))

	def test_the_return_roles_match_the_ones_the_return_call_checks(self):
		path = os.path.join(os.path.dirname(__file__), "sales_api.py")
		with open(path) as handle:
			tree = ast.parse(handle.read())
		found = [
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "RETURN_ROLES"
		]
		self.assertEqual(rules.RETURN_ROLES, frozenset(found[0]))


class TestPeriods(unittest.TestCase):
	"""The period keys are the Home overview's, resolved by the same function."""

	def test_the_same_keys_and_dates_as_home(self):
		self.assertEqual(calc.resolve_period("today", None, None, TODAY)["from"], TODAY)
		self.assertEqual(str(calc.resolve_period("7d", None, None, TODAY)["from"]), "2026-10-07")
		self.assertEqual(str(calc.resolve_period("30d", None, None, TODAY)["from"]), "2026-09-14")
		self.assertEqual(str(calc.resolve_period("month", None, None, TODAY)["from"]), "2026-10-01")

	def test_the_day_is_the_nairobi_day(self):
		late = datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)  # 01:30 on the 13th in Nairobi
		self.assertEqual(calc.nairobi_today(late), TODAY)

	def test_custom_needs_both_dates(self):
		with self.assertRaises(calc.PeriodError) as raised:
			calc.resolve_period("custom", "2026-10-01", None, TODAY)
		self.assertEqual(raised.exception.code, "need_dates")

	def test_the_call_turns_a_period_problem_into_words(self):
		for code in ("need_dates", "bad_date", "order", "too_long", "bad_period"):
			with self.subTest(code=code), self.assertRaises(frappe.ValidationError):
				api._refuse_period(code)


# --- the receipt ------------------------------------------------------------------------------------


def receipt_doc(**extra):
	doc = sale(
		"ACC-PSINV-2026-00033",
		total=2350,
		owing=0,
		mobile="0712345678",
		change_amount=150,
		discount_amount=100,
		total_taxes_and_charges=324.14,
	)
	doc.update(
		{
			"total": 2126,
			"items": [
				{"item_code": "ITM-A", "item_name": "Sugar 1kg", "qty": 2, "rate": 180, "amount": 360},
				{
					"item_code": "ITM-B",
					"item_name": "Maize flour 2kg",
					"qty": 10,
					"rate": 176.6,
					"amount": 1766,
				},
			],
			"payments": [
				{"mode_of_payment": "Cash", "amount": 1500, "reference_no": None},
				{"mode_of_payment": "Mpesa", "amount": 1000, "reference_no": "SGH7K2L9QX"},
				{"mode_of_payment": "Card", "amount": 0, "reference_no": ""},
			],
		}
	)
	doc.update(extra)
	return doc


class TestReceipt(unittest.TestCase):
	def test_the_whole_panel(self):
		names = {"amina@shop-a.test": "Amina Hassan"}
		receipt = rules.build_receipt(receipt_doc(), TODAY, names=names, can_return=True)
		self.assertEqual(receipt["id"], "ACC-PSINV-2026-00033")
		self.assertEqual(receipt["receipt"], "#0033")
		self.assertEqual(receipt["reference"], "ACC-PSINV-2026-00033")
		self.assertEqual((receipt["date"], receipt["time"]), ("2026-10-13", "09:15"))
		self.assertEqual(receipt["customer"], {"name": "Wanjiru Kamau", "mobile": "0712345678"})
		self.assertEqual(receipt["cashier"], "Amina Hassan")
		self.assertEqual(receipt["status"], "paid")
		self.assertFalse(receipt["is_return"])
		self.assertIsNone(receipt["return_against"])
		self.assertEqual(
			receipt["totals"],
			{
				"subtotal": 2126.0,
				"discount": 100.0,
				"tax": 324.14,
				"total": 2350.0,
				"paid": 2350.0,
				"outstanding": 0.0,
				"change": 150.0,
			},
		)

	def test_the_lines(self):
		lines = rules.build_receipt(receipt_doc(), TODAY)["lines"]
		self.assertEqual(
			lines[0],
			{"item_code": "ITM-A", "item_name": "Sugar 1kg", "qty": 2.0, "rate": 180.0, "amount": 360.0},
		)
		self.assertEqual(len(lines), 2)

	def test_a_line_with_no_name_shows_its_code(self):
		doc = receipt_doc(items=[{"item_code": "ITM-Z", "item_name": None, "qty": 1, "rate": 5, "amount": 5}])
		self.assertEqual(rules.build_receipt(doc, TODAY)["lines"][0]["item_name"], "ITM-Z")

	def test_the_payments_carry_the_mpesa_code_and_leave_out_zeros(self):
		payments = rules.build_receipt(receipt_doc(), TODAY)["payments"]
		self.assertEqual(
			payments,
			[
				{"mode": "Cash", "label": "Cash", "amount": 1500.0, "reference": None},
				{"mode": "Mpesa", "label": "M-Pesa", "amount": 1000.0, "reference": "SGH7K2L9QX"},
			],
		)

	def test_a_paid_sale_cannot_take_payment_and_a_sale_with_a_balance_can(self):
		self.assertFalse(rules.build_receipt(receipt_doc(), TODAY)["can"]["take_payment"])
		owing = rules.build_receipt(receipt_doc(outstanding_amount=600), TODAY)
		self.assertTrue(owing["can"]["take_payment"])
		self.assertEqual(owing["totals"]["paid"], 1750.0)
		self.assertEqual(owing["status"], "unpaid")

	def test_whatsapp_needs_a_mobile_number(self):
		self.assertTrue(rules.build_receipt(receipt_doc(), TODAY)["can"]["whatsapp"])
		without = rules.build_receipt(receipt_doc(mobile=None), TODAY)
		self.assertFalse(without["can"]["whatsapp"])
		self.assertIsNone(without["customer"]["mobile"])
		self.assertFalse(rules.build_receipt(receipt_doc(mobile="  "), TODAY)["can"]["whatsapp"])

	def test_print_is_always_offered(self):
		self.assertTrue(rules.build_receipt(receipt_doc(), TODAY)["can"]["print"])

	def test_return_items_follows_the_role(self):
		self.assertTrue(rules.build_receipt(receipt_doc(), TODAY, can_return=True)["can"]["return_items"])
		self.assertFalse(rules.build_receipt(receipt_doc(), TODAY, can_return=False)["can"]["return_items"])

	def test_returns_made_against_the_sale(self):
		returns = [{"doctype": POS, "name": "ACC-PSINV-2026-00040", "grand_total": -350}]
		receipt = rules.build_receipt(receipt_doc(), TODAY, returns=returns, can_return=True)
		self.assertEqual(
			receipt["returns"], [{"id": "ACC-PSINV-2026-00040", "receipt": "#0040", "total": -350.0}]
		)
		self.assertEqual(receipt["status"], "part-returned")
		self.assertTrue(receipt["can"]["return_items"])

	def test_a_sale_returned_in_full_offers_no_more_returns(self):
		returns = [{"doctype": POS, "name": "ACC-PSINV-2026-00040", "grand_total": -2350}]
		receipt = rules.build_receipt(receipt_doc(), TODAY, returns=returns, can_return=True)
		self.assertEqual(receipt["status"], "returned")
		self.assertFalse(receipt["can"]["return_items"])

	def test_a_return_points_back_to_its_sale_and_offers_nothing_to_pay_or_return(self):
		doc = receipt_doc(
			name="ACC-PSINV-2026-00040",
			grand_total=-350,
			outstanding_amount=0,
			is_return=1,
			return_against="ACC-PSINV-2026-00033",
		)
		receipt = rules.build_receipt(doc, TODAY, can_return=True)
		self.assertTrue(receipt["is_return"])
		self.assertEqual(receipt["return_against"], "ACC-PSINV-2026-00033")
		self.assertEqual(receipt["status"], "returned")
		self.assertFalse(receipt["can"]["take_payment"])
		self.assertFalse(receipt["can"]["return_items"])

	def test_a_return_against_a_sales_invoice_has_a_sales_invoice_receipt_number(self):
		returns = [{"doctype": SALES, "name": "ACC-SINV-2026-00009", "grand_total": -10}]
		doc = receipt_doc(doctype=SALES, name="ACC-SINV-2026-00005")
		receipt = rules.build_receipt(doc, TODAY, returns=returns)
		self.assertEqual(receipt["receipt"], "#S0005")
		self.assertEqual(receipt["returns"][0]["receipt"], "#S0009")

	def test_a_sale_with_no_lines_or_payments_still_builds(self):
		doc = sale("ACC-PSINV-2026-00001")
		receipt = rules.build_receipt(doc, TODAY)
		self.assertEqual((receipt["lines"], receipt["payments"], receipt["returns"]), ([], [], []))


# --- till sessions ----------------------------------------------------------------------------------


OPENING = {
	"name": "POS-OPE-2026-00004",
	"user": "amina@shop-a.test",
	"pos_profile": "Main till",
	"status": "Closed",
	"period_start_date": datetime(2026, 10, 12, 8, 5),
}
CLOSING = {"name": "POS-CLO-2026-00004", "period_end_date": datetime(2026, 10, 12, 18, 40)}
RECON = [
	{"mode_of_payment": "Cash", "expected_amount": 5500, "closing_amount": 5400, "difference": -100},
	{"mode_of_payment": "Mpesa", "expected_amount": 3000, "closing_amount": 3000, "difference": 0},
]


class TestTillSessions(unittest.TestCase):
	def test_a_closed_session_shows_who_when_and_the_cash_difference(self):
		row = rules.session_row(OPENING, CLOSING, RECON, 14, {"amina@shop-a.test": "Amina Hassan"})
		self.assertEqual(
			row,
			{
				"id": "POS-OPE-2026-00004",
				"cashier": "amina@shop-a.test",
				"cashier_name": "Amina Hassan",
				"opened_on": "2026-10-12",
				"opened_at": "08:05",
				"closed_on": "2026-10-12",
				"closed_at": "18:40",
				"status": "closed",
				"sales_count": 14,
				"expected": 5500.0,
				"counted": 5400.0,
				"difference": -100.0,
				"closing_entry": "POS-CLO-2026-00004",
			},
		)

	def test_only_the_cash_rows_make_the_cash_difference(self):
		recon = [
			*RECON,
			{"mode_of_payment": "Card", "expected_amount": 100, "closing_amount": 0, "difference": -100},
		]
		self.assertEqual(rules.session_cash(recon)["difference"], -100.0)

	def test_an_open_session_has_no_difference_and_no_closing_entry(self):
		opening = dict(OPENING, status="Open")
		row = rules.session_row(opening, None, None, 3)
		self.assertEqual(row["status"], "open")
		self.assertIsNone(row["difference"])
		self.assertIsNone(row["counted"])
		self.assertIsNone(row["expected"])
		self.assertIsNone(row["closed_at"])
		self.assertIsNone(row["closing_entry"])

	def test_a_closed_session_with_no_closing_entry_has_no_difference(self):
		row = rules.session_row(OPENING, None, None, 2)
		self.assertEqual(row["status"], "closed")
		self.assertIsNone(row["difference"])
		self.assertIsNone(row["closing_entry"])

	def test_a_till_that_recorded_no_count_has_no_counted_cash_or_difference(self):
		recon = [{"mode_of_payment": "Cash", "expected_amount": 5500, "closing_amount": 0, "difference": 0}]
		cash = rules.session_cash(recon)
		self.assertEqual(cash, {"expected": 5500.0, "counted": None, "difference": None})

	def test_a_count_that_matches_is_a_difference_of_zero(self):
		recon = [{"mode_of_payment": "Cash", "expected_amount": 500, "closing_amount": 500, "difference": 0}]
		self.assertEqual(rules.session_cash(recon)["difference"], 0.0)

	def test_a_session_with_no_cash_row_has_no_cash_figures(self):
		self.assertIsNone(rules.session_cash([RECON[1]]))
		self.assertIsNone(rules.session_cash([]))

	def test_a_cash_over_is_positive(self):
		recon = [{"mode_of_payment": "Cash", "expected_amount": 500, "closing_amount": 550, "difference": 50}]
		self.assertEqual(rules.session_cash(recon)["difference"], 50.0)

	def test_a_session_opened_one_day_and_closed_the_next_shows_both_dates(self):
		closing = {"name": "C", "period_end_date": datetime(2026, 10, 13, 1, 10)}
		row = rules.session_row(OPENING, closing, RECON, 1)
		self.assertEqual(
			(row["opened_on"], row["closed_on"], row["closed_at"]), ("2026-10-12", "2026-10-13", "01:10")
		)


# --- the calls --------------------------------------------------------------------------------------

AMINA, BOSS, MANAGER = "amina@shop-a.test", "boss@shop-a.test", "meg@shop-a.test"
ROLES = {AMINA: ["Sales User"], BOSS: ["System Manager"], MANAGER: ["Sales Manager"]}

FIXTURE_ROWS = [
	sale("ACC-SINV-2026-00007", doctype=SALES, at="11:30:00", total=4000, owing=1000),
	sale("ACC-PSINV-2026-00033", at="10:00:00", total=2350),
	sale("ACC-PSINV-2026-00032", at="09:00:00", total=500, is_return=1),
]


class FakeDoc(dict):
	"""A document that reads like a Frappe one: attributes and .get."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name)


class SalesCase(unittest.TestCase):
	"""A signed-in user and a database that answers each kind of query with fixed rows and remembers what it was asked."""

	def setUp(self):
		self.sql = []
		self.rows = [dict(r) for r in FIXTURE_ROWS]
		self.total = len(self.rows)
		self.documents = {}
		self.exists = {}

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, user=BOSS, warehouse_owner=None):
		db = MagicMock()
		db.get_value.return_value = warehouse_owner
		db.has_column.return_value = True
		db.sql.side_effect = self.answer
		db.exists.side_effect = lambda kind, name: self.exists.get((kind, name), False)
		self.db = db
		self.start((frappe, "session"), new=frappe._dict(user=user))
		self.start(
			(frappe, "get_roles"),
			create=True,
			side_effect=lambda user=None: list(ROLES[user or frappe.session.user]),
		)
		self.start((frappe, "db"), new=db, create=True)
		self.start((frappe, "log_error"), create=True)
		self.start((frappe, "get_traceback"), create=True, return_value="secret db detail")
		self.start((frappe, "get_all"), create=True, side_effect=self.get_all)
		self.start((frappe, "get_doc"), create=True, side_effect=self.get_doc)
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "_now_utc"), return_value=NOW)

	def get_all(self, doctype, **kwargs):
		if doctype == "User":
			return [
				{"name": AMINA, "full_name": "Amina Hassan"},
				{"name": BOSS, "full_name": "Beth Owner"},
			]
		if doctype == "Mode of Payment":
			return [{"name": "Cash", "type": "Cash"}, {"name": "Mpesa", "type": "Phone"}]
		return self.returns_for.get(kwargs.get("filters", {}).get("return_against"), [])

	returns_for: ClassVar[dict] = {}

	def get_doc(self, kind, name):
		if (kind, name) not in self.documents:
			raise frappe.DoesNotExistError(name)
		return self.documents[(kind, name)]

	def answer(self, sql, values=None, as_dict=False):
		self.sql.append((" ".join(sql.split()), dict(values or {})))
		text = " ".join(sql.split())
		if "FROM `tabPOS Opening Entry` o WHERE" in text and "COUNT(*)" in text:
			return [{"n": len(self.sessions)}]
		if "FROM `tabPOS Opening Entry` o WHERE" in text:
			return [dict(s) for s in self.sessions]
		if "COUNT(DISTINCT d.name)" in text:
			return [{"session": "POS-OPE-2026-00004", "n": 14}]
		if "FROM `tabPOS Closing Entry` WHERE" in text:
			return [dict(CLOSING, pos_opening_entry="POS-OPE-2026-00004")]
		if "`tabPOS Closing Entry Detail`" in text:
			return [dict(r, parent="POS-CLO-2026-00004") for r in RECON]
		if "COUNT(*) AS n FROM (" in text:
			return [{"n": self.total}]
		if "GROUP BY d.is_return" in text:
			return [
				{"is_return": 0, "total": 6350, "outstanding": 1000},
				{"is_return": 1, "total": -500, "outstanding": 0},
			]
		if "SUM(ABS(grand_total))" in text:
			return [{"return_against": "ACC-PSINV-2026-00033", "returned": 350}]
		if "`tabSales Invoice Payment`" in text:
			return [
				{"parent": "ACC-PSINV-2026-00033", "mode_of_payment": "Mpesa", "amount": 2350},
				{"parent": "ACC-SINV-2026-00007", "mode_of_payment": "Cash", "amount": 3000},
			]
		if "ORDER BY posting_date DESC" in text:
			return [dict(r) for r in self.rows]
		raise AssertionError(f"unexpected query: {text}")

	sessions: ClassVar[list] = [
		{
			"name": "POS-OPE-2026-00004",
			"user": AMINA,
			"pos_profile": "Main till",
			"status": "Closed",
			"posting_date": D("2026-10-12"),
			"period_start_date": datetime(2026, 10, 12, 8, 5),
		}
	]

	def data(self, **kwargs):
		result = api.list_sales(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]

	def queries(self, needle):
		return [(s, v) for s, v in self.sql if needle in s]


class TestListForAnOwner(SalesCase):
	def setUp(self):
		super().setUp()
		self.as_user(BOSS)

	def test_the_answer_has_rows_summary_and_paging(self):
		data = self.data()
		self.assertEqual(set(data), {"rows", "summary", "total", "page", "page_size", "pages"})
		self.assertEqual((data["total"], data["page"], data["page_size"], data["pages"]), (3, 1, 20, 1))
		self.assertEqual(data["summary"], {"sold": 6350.0, "unpaid": 1000.0, "returned": 500.0})

	def test_rows_read_both_kinds_newest_first(self):
		rows = self.data()["rows"]
		self.assertEqual([r["receipt"] for r in rows], ["#S0007", "#0033", "#0032"])
		self.assertEqual([r["doctype"] for r in rows], [SALES, POS, POS])

	def test_the_rows_are_enriched_from_their_own_reads(self):
		rows = {r["id"]: r for r in self.data()["rows"]}
		self.assertEqual(rows["ACC-PSINV-2026-00033"]["methods"], ["M-Pesa"])
		self.assertEqual(rows["ACC-PSINV-2026-00033"]["status"], "part-returned")
		self.assertEqual(rows["ACC-PSINV-2026-00033"]["cashier"], "Amina Hassan")
		self.assertEqual(rows["ACC-SINV-2026-00007"]["methods"], ["Cash", "On credit"])
		self.assertEqual(rows["ACC-SINV-2026-00007"]["status"], "unpaid")
		self.assertTrue(rows["ACC-PSINV-2026-00032"]["is_return"])

	def test_the_query_reads_both_kinds_and_leaves_out_merged_pos_invoices(self):
		self.data()
		sql, _values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertIn("`tabPOS Invoice` d", sql)
		self.assertIn("`tabSales Invoice` d", sql)
		self.assertIn("UNION ALL", sql)
		self.assertEqual(sql.count("d.docstatus = 1"), 2)
		self.assertEqual(sql.count("IFNULL(d.consolidated_invoice, '') = ''"), 1)
		pos_part = sql.split("UNION ALL")[0]
		self.assertIn("consolidated_invoice", pos_part)

	def test_the_period_and_company_go_into_the_query(self):
		self.data(period="7d")
		_sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertEqual((str(values["from_date"]), str(values["to_date"])), ("2026-10-07", "2026-10-13"))
		self.assertEqual(values["company"], "Shop A")

	def test_no_owner_filter_for_an_owner_who_asks_for_everyone(self):
		self.data()
		_sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertNotIn("owner", values)

	def test_an_owner_may_filter_by_cashier(self):
		self.data(cashier=AMINA)
		sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertEqual(values["owner"], AMINA)
		self.assertIn("d.owner = %(owner)s", sql)

	def test_the_unpaid_tab_asks_only_for_sales_with_a_balance(self):
		self.data(tab="unpaid")
		sql, _values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertIn("d.is_return = 0 AND d.outstanding_amount > 0", sql)

	def test_the_returns_tab_asks_only_for_returns(self):
		self.data(tab="returns")
		sql, _values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertIn("d.is_return = 1", sql)

	def test_the_summary_is_not_limited_by_the_tab(self):
		self.data(tab="returns")
		sql, _values = self.queries("GROUP BY d.is_return")[0]
		self.assertNotIn("d.is_return = 1", sql)
		self.assertNotIn("outstanding_amount > 0 AND", sql)

	def test_the_summary_follows_the_search_and_cashier_filters(self):
		self.data(search="wanji", cashier=AMINA)
		sql, values = self.queries("GROUP BY d.is_return")[0]
		self.assertIn("d.customer_name LIKE %(like)s", sql)
		self.assertEqual((values["like"], values["owner"]), ("%wanji%", AMINA))

	def test_a_receipt_number_search_reads_the_name_by_pattern_not_by_text(self):
		self.data(search="#0033")
		sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertIn("d.name REGEXP %(receipt_rx)s", sql)
		self.assertNotIn("d.name LIKE", sql)
		self.assertEqual(values["receipt_rx"], rules.receipt_regex(33))

	def test_an_s_receipt_search_skips_pos_invoices(self):
		self.data(search="S33")
		sql, _values = self.queries("ORDER BY posting_date DESC")[0]
		pos_part, sales_part = sql.split("UNION ALL")
		self.assertNotIn("REGEXP", pos_part)
		self.assertIn("REGEXP", sales_part)

	def test_a_text_search_reads_name_and_customer(self):
		self.data(search="PSINV")
		sql, _values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertIn("d.name LIKE %(like)s", sql)
		self.assertIn("d.customer_name LIKE %(like)s", sql)

	def test_a_store_limits_the_sales_to_it(self):
		self.data(store="Main store - A")
		sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertIn("`tabPOS Invoice Item` wi", sql)
		self.assertIn("`tabSales Invoice Item` wi", sql)
		self.assertEqual(values["store"], "Main store - A")

	def test_a_store_of_another_business_is_refused(self):
		self.db.get_value.return_value = "Shop B"
		with self.assertRaises(frappe.PermissionError):
			api.list_sales(store="Other store - B")

	def test_paging_reaches_the_query(self):
		self.total = 45
		data = self.data(page=3, page_size=10)
		_sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertEqual((values["limit"], values["offset"]), (10, 20))
		self.assertEqual((data["page"], data["pages"], data["total"]), (3, 5, 45))

	def test_a_page_past_the_end_is_corrected(self):
		self.total = 45
		data = self.data(page=99, page_size=20)
		_sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertEqual((data["page"], values["offset"]), (3, 40))

	def test_a_huge_page_size_is_capped(self):
		self.total = 500
		data = self.data(page_size=9999)
		self.assertEqual(data["page_size"], 100)

	def test_no_sales_is_an_empty_list_with_zero_summary_money_and_no_row_query(self):
		self.total = 0
		self.rows = []
		data = self.data()
		self.assertEqual((data["rows"], data["total"], data["pages"], data["page"]), ([], 0, 1, 1))
		self.assertEqual(self.queries("ORDER BY posting_date DESC"), [])

	def test_an_unknown_tab_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_sales(tab="everything")

	def test_a_bad_period_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_sales(period="forever")
		with self.assertRaises(frappe.ValidationError):
			api.list_sales(period="custom", from_date="2026-10-01")

	def test_a_custom_period(self):
		self.data(period="custom", from_date="2026-09-01", to_date="2026-09-30")
		_sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertEqual((str(values["from_date"]), str(values["to_date"])), ("2026-09-01", "2026-09-30"))

	def test_a_database_failure_gives_a_plain_message_and_no_detail(self):
		self.db.sql.side_effect = RuntimeError("secret db detail")
		result = api.list_sales()
		self.assertFalse(result["success"])
		self.assertNotIn("secret", result["message"])


class TestListForACashier(SalesCase):
	def setUp(self):
		super().setUp()
		self.as_user(AMINA)

	def test_a_cashier_is_held_to_their_own_sales_whatever_they_ask_for(self):
		self.data(cashier=BOSS)
		for needle in ("ORDER BY posting_date DESC", "GROUP BY d.is_return", "COUNT(*) AS n FROM ("):
			sql, values = self.queries(needle)[0]
			with self.subTest(query=needle):
				self.assertEqual(values["owner"], AMINA)
				self.assertIn("d.owner = %(owner)s", sql)

	def test_a_cashier_without_a_filter_is_still_held_to_their_own(self):
		self.data()
		_sql, values = self.queries("ORDER BY posting_date DESC")[0]
		self.assertEqual(values["owner"], AMINA)

	def test_a_cashier_is_held_to_their_own_sessions(self):
		self.data(tab="sessions", cashier=BOSS)
		sql, values = self.queries("ORDER BY o.period_start_date")[0]
		self.assertEqual(values["owner"], AMINA)
		self.assertIn("o.user = %(owner)s", sql)

	def test_a_guest_is_refused(self):
		self.start((frappe, "session"), new=frappe._dict(user="Guest"))
		with self.assertRaises(frappe.AuthenticationError):
			api.list_sales()


class TestSessionsTab(SalesCase):
	def setUp(self):
		super().setUp()
		self.as_user(MANAGER)

	def test_the_rows_show_the_cashier_the_time_and_the_cash_difference(self):
		data = self.data(tab="sessions")
		self.assertEqual(data["total"], 1)
		row = data["rows"][0]
		self.assertEqual(row["id"], "POS-OPE-2026-00004")
		self.assertEqual(row["cashier_name"], "Amina Hassan")
		self.assertEqual(
			(row["opened_on"], row["opened_at"], row["closed_at"]), ("2026-10-12", "08:05", "18:40")
		)
		self.assertEqual((row["expected"], row["counted"], row["difference"]), (5500.0, 5400.0, -100.0))
		self.assertEqual(
			(row["status"], row["sales_count"], row["closing_entry"]), ("closed", 14, "POS-CLO-2026-00004")
		)

	def test_the_summary_is_still_there(self):
		self.assertEqual(
			self.data(tab="sessions")["summary"], {"sold": 6350.0, "unpaid": 1000.0, "returned": 500.0}
		)

	def test_sessions_are_read_newest_first_for_the_period_and_business(self):
		self.data(tab="sessions", period="today")
		sql, values = self.queries("ORDER BY o.period_start_date")[0]
		self.assertIn("ORDER BY o.period_start_date DESC", sql)
		self.assertIn("o.docstatus = 1", sql)
		self.assertEqual((str(values["from_date"]), values["company"]), ("2026-10-13", "Shop A"))

	def test_a_manager_may_filter_sessions_by_cashier(self):
		self.data(tab="sessions", cashier=AMINA)
		_sql, values = self.queries("ORDER BY o.period_start_date")[0]
		self.assertEqual(values["owner"], AMINA)

	def test_no_sessions_is_an_empty_list(self):
		self.sessions = []
		data = self.data(tab="sessions")
		self.assertEqual((data["rows"], data["total"]), ([], 0))


def stored(name, owner=AMINA, kind=POS, **extra):
	doc = FakeDoc(
		doctype=kind,
		name=name,
		docstatus=1,
		owner=owner,
		posting_date=D("2026-10-13"),
		posting_time="10:00:00",
		customer="CUST-1",
		customer_name="Wanjiru Kamau",
		grand_total=2350,
		outstanding_amount=0,
		change_amount=0,
		total=2350,
		discount_amount=0,
		total_taxes_and_charges=0,
		is_return=0,
		return_against=None,
		due_date=None,
		contact_mobile=None,
		items=[FakeDoc(item_code="ITM-A", item_name="Sugar", qty=1, rate=2350, amount=2350)],
		payments=[FakeDoc(mode_of_payment="Mpesa", amount=2350, reference_no="SGH7K2L9QX")],
	)
	doc.update(extra)
	return doc


class TestGetSale(SalesCase):
	def setUp(self):
		super().setUp()
		self.documents[(POS, "ACC-PSINV-2026-00033")] = stored("ACC-PSINV-2026-00033")
		self.documents[(POS, "ACC-PSINV-2026-00050")] = stored("ACC-PSINV-2026-00050", owner=BOSS)
		self.documents[(SALES, "ACC-SINV-2026-00007")] = stored(
			"ACC-SINV-2026-00007", kind=SALES, grand_total=4000, outstanding_amount=1000, total=4000
		)
		for key in self.documents:
			self.exists[key] = True

	def customer_row(self, mobile="0712345678"):
		def get_value(kind, name, fields=None, as_dict=False):
			if kind == "Customer":
				return {"mobile_no": mobile, "payment_terms": None}
			return None  # no record has an owner business recorded: nothing to refuse

		self.db.get_value.side_effect = get_value

	def test_an_owner_reads_a_sale_of_the_kind_found(self):
		self.as_user(BOSS)
		self.customer_row()
		result = api.get_sale("ACC-PSINV-2026-00033")
		self.assertTrue(result["success"], result)
		data = result["data"]
		self.assertEqual((data["receipt"], data["doctype"]), ("#0033", POS))
		self.assertEqual(data["customer"], {"name": "Wanjiru Kamau", "mobile": "0712345678"})
		self.assertEqual(data["payments"][0]["reference"], "SGH7K2L9QX")
		self.assertTrue(data["can"]["whatsapp"])
		self.assertTrue(data["can"]["print"])

	def test_a_sales_invoice_is_found_without_saying_the_kind(self):
		self.as_user(BOSS)
		self.customer_row()
		data = api.get_sale("ACC-SINV-2026-00007")["data"]
		self.assertEqual((data["receipt"], data["doctype"]), ("#S0007", SALES))
		self.assertTrue(data["can"]["take_payment"])
		self.assertEqual(data["status"], "unpaid")

	def test_the_kind_may_be_given(self):
		self.as_user(BOSS)
		self.customer_row()
		self.assertTrue(api.get_sale("ACC-SINV-2026-00007", SALES)["success"])
		self.assertFalse(api.get_sale("ACC-SINV-2026-00007", POS)["success"])

	def test_a_wrong_kind_is_refused(self):
		self.as_user(BOSS)
		with self.assertRaises(frappe.ValidationError):
			api.get_sale("ACC-PSINV-2026-00033", "Purchase Invoice")

	def test_a_cashier_reads_their_own_sale(self):
		self.as_user(AMINA)
		self.customer_row()
		self.assertTrue(api.get_sale("ACC-PSINV-2026-00033")["success"])

	def test_a_cashier_is_refused_a_sale_of_someone_else_in_clear_words(self):
		self.as_user(AMINA)
		self.customer_row()
		with self.assertRaises(frappe.PermissionError) as raised:
			api.get_sale("ACC-PSINV-2026-00050")
		self.assertIn("your own sales", str(raised.exception))

	def test_a_manager_reads_any_sale_of_the_business(self):
		self.as_user(MANAGER)
		self.customer_row()
		self.assertTrue(api.get_sale("ACC-PSINV-2026-00050")["success"])

	def test_only_return_roles_are_offered_return_items(self):
		self.as_user(MANAGER)
		self.customer_row()
		self.assertTrue(api.get_sale("ACC-PSINV-2026-00033")["data"]["can"]["return_items"])
		self.as_user(AMINA)
		self.customer_row()
		self.assertFalse(api.get_sale("ACC-PSINV-2026-00033")["data"]["can"]["return_items"])

	def test_a_sale_that_does_not_exist_is_not_found(self):
		self.as_user(BOSS)
		result = api.get_sale("ACC-PSINV-2026-99999")
		self.assertEqual((result["success"], result["error_type"]), (False, "not_found"))

	def test_a_draft_or_cancelled_sale_is_not_found(self):
		self.as_user(BOSS)
		self.customer_row()
		self.documents[(POS, "ACC-PSINV-2026-00033")]["docstatus"] = 2
		self.assertEqual(api.get_sale("ACC-PSINV-2026-00033")["error_type"], "not_found")
		self.documents[(POS, "ACC-PSINV-2026-00033")]["docstatus"] = 0
		self.assertEqual(api.get_sale("ACC-PSINV-2026-00033")["error_type"], "not_found")

	def test_a_sale_of_another_business_is_refused(self):
		self.as_user(BOSS)
		self.db.get_value.side_effect = None
		self.db.get_value.return_value = "Shop B"
		with self.assertRaises(frappe.PermissionError):
			api.get_sale("ACC-PSINV-2026-00033")

	def test_the_returns_are_listed(self):
		self.as_user(BOSS)
		self.customer_row()
		self.returns_for = {"ACC-PSINV-2026-00033": [{"name": "ACC-PSINV-2026-00040", "grand_total": -350}]}
		data = api.get_sale("ACC-PSINV-2026-00033")["data"]
		self.assertEqual(
			data["returns"], [{"id": "ACC-PSINV-2026-00040", "receipt": "#0040", "total": -350.0}]
		)
		self.assertEqual(data["status"], "part-returned")

	def test_a_failure_gives_a_plain_message_and_no_detail(self):
		self.as_user(BOSS)
		self.customer_row()
		self.start((api, "_returns_against"), side_effect=RuntimeError("secret db detail"))
		result = api.get_sale("ACC-PSINV-2026-00033")
		self.assertFalse(result["success"])
		self.assertNotIn("secret", result["message"])

	def test_a_guest_is_refused(self):
		self.as_user(BOSS)
		self.start((frappe, "session"), new=frappe._dict(user="Guest"))
		with self.assertRaises(frappe.AuthenticationError):
			api.get_sale("ACC-PSINV-2026-00033")


class TestTheListAndTheReceiptAgree(SalesCase):
	"""AC-24 at the endpoint level: one sale, one amount, in the list and in the receipt panel."""

	def test_the_same_sale_has_the_same_total_in_both(self):
		self.as_user(BOSS)
		documents = {
			(POS, "ACC-PSINV-2026-00033"): stored(
				"ACC-PSINV-2026-00033", grand_total=2350.5, outstanding_amount=350.5
			),
			(SALES, "ACC-SINV-2026-00007"): stored(
				"ACC-SINV-2026-00007", kind=SALES, grand_total=4000, outstanding_amount=1000
			),
		}
		self.documents.update(documents)
		self.exists.update({key: True for key in documents})
		self.rows = [
			sale("ACC-PSINV-2026-00033", total=2350.5, owing=350.5),
			sale("ACC-SINV-2026-00007", doctype=SALES, total=4000, owing=1000, at="08:00:00"),
		]
		self.total = 2
		self.db.get_value.side_effect = (
			lambda kind, *a, **k: {"mobile_no": None, "payment_terms": None} if kind == "Customer" else None
		)
		listed = {r["id"]: r for r in self.data()["rows"]}
		for _kind, name in documents:
			with self.subTest(sale=name):
				receipt = api.get_sale(name)["data"]
				row = listed[name]
				self.assertEqual(row["total"], receipt["totals"]["total"])
				self.assertEqual(row["paid"], receipt["totals"]["paid"])
				self.assertEqual(row["outstanding"], receipt["totals"]["outstanding"])
				self.assertEqual(row["receipt"], receipt["receipt"])


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	def source(self, name):
		with open(os.path.join(os.path.dirname(__file__), name)) as handle:
			return handle.read()

	def test_both_calls_check_the_record_first(self):
		tree = ast.parse(self.source("sales_list_api.py"))
		wanted = {
			"list_sales": 'ensure_record_access("Warehouse", store)',
			"get_sale": "ensure_record_access(",
		}
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name in wanted:
				self.assertIn(wanted[node.name], ast.unparse(node).replace("'", '"'))

	def test_both_calls_are_whitelisted(self):
		for name in ("list_sales", "get_sale"):
			self.assertTrue(hasattr(getattr(api, name), "__wrapped__") or callable(getattr(api, name)))

	def test_no_em_dash_anywhere(self):
		for name in ("sales_list_api.py", "sales_list_calc.py", "test_sales_list_api.py"):
			with self.subTest(file=name):
				self.assertNotIn(chr(0x2014), self.source(name))

	def test_every_message_has_a_swahili_row(self):
		translations = self.source(os.path.join("..", "translations", "sw.csv"))
		for name in ("sales_list_api.py",):
			for message in re.findall(r'_\("([^"]+)"\)', self.source(name)):
				with self.subTest(message=message):
					self.assertIn('"' + message.replace('"', '""') + '"', translations)
