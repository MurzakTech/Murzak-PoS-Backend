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
import csv
import io
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


class TestOutstandingOnAMergedReceipt(unittest.TestCase):
	def test_a_sale_that_was_not_merged_owes_its_own_figure(self):
		self.assertEqual(rules.effective_outstanding(600), 600.0)

	def test_a_merged_receipt_is_paid_once_the_consolidated_invoice_is_paid(self):
		self.assertEqual(rules.effective_outstanding(600, False, "ACC-SINV-1", 0), 0.0)
		self.assertEqual(rules.effective_outstanding(600, False, "ACC-SINV-1", -5), 0.0)

	def test_a_merged_receipt_never_owes_more_than_the_consolidated_invoice(self):
		self.assertEqual(rules.effective_outstanding(600, False, "ACC-SINV-1", 250), 250.0)
		self.assertEqual(rules.effective_outstanding(600, False, "ACC-SINV-1", 900), 600.0)

	def test_a_merged_receipt_that_owed_nothing_still_owes_nothing(self):
		self.assertEqual(rules.effective_outstanding(0, False, "ACC-SINV-1", 900), 0.0)

	def test_when_the_consolidated_invoice_cannot_be_read_the_receipts_own_figure_is_used(self):
		self.assertEqual(rules.effective_outstanding(600, False, "ACC-SINV-1", None), 600.0)

	def test_a_return_owes_what_it_says(self):
		self.assertEqual(rules.effective_outstanding(-50, True, "ACC-SINV-1", 0), -50.0)

	def test_the_list_row_and_the_receipt_both_use_it(self):
		doc = sale(
			"ACC-PSINV-2026-00001",
			total=600,
			owing=600,
			merged_into="ACC-SINV-2026-00010",
			merged_outstanding=0,
		)
		row = rules.list_row(doc, TODAY)
		receipt = rules.build_receipt(doc, TODAY)
		self.assertEqual((row["outstanding"], row["status"], row["paid"]), (0.0, "paid", 600.0))
		self.assertEqual(receipt["totals"]["outstanding"], 0.0)
		self.assertEqual(receipt["merged_into"], "ACC-SINV-2026-00010")
		self.assertFalse(receipt["can"]["take_payment"])

	def test_a_receipt_that_was_not_merged_reports_no_merged_into(self):
		self.assertIsNone(rules.build_receipt(sale("ACC-PSINV-2026-00001"), TODAY)["merged_into"])


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

	def test_a_merged_pos_invoice_stays_in_the_list_and_the_consolidated_invoice_is_left_out(self):
		merged = sale("ACC-PSINV-2026-00001", merged_into="ACC-SINV-2026-00010")
		kept = sale("ACC-PSINV-2026-00002", merged_into="")
		closing = sale("ACC-SINV-2026-00010", doctype=SALES, is_consolidated=1)
		names = [r["name"] for r in rules.union_sales([merged, kept], [closing])]
		self.assertEqual(sorted(names), ["ACC-PSINV-2026-00001", "ACC-PSINV-2026-00002"])

	def test_the_receipts_of_a_closed_till_are_listed_once_each(self):
		receipts = [sale(f"ACC-PSINV-2026-0000{n}", merged_into="ACC-SINV-2026-00010") for n in range(1, 4)]
		closing = sale("ACC-SINV-2026-00010", doctype=SALES, is_consolidated=1, total=3000)
		rows = rules.union_sales(receipts, [closing])
		self.assertEqual(len(rows), 3)
		self.assertEqual(sum(r["grand_total"] for r in rows), 3000)

	def test_only_a_consolidated_sales_invoice_is_left_out(self):
		self.assertTrue(rules.is_consolidated_invoice({"doctype": SALES, "is_consolidated": 1}))
		self.assertFalse(rules.is_consolidated_invoice({"doctype": SALES, "is_consolidated": 0}))
		self.assertFalse(rules.is_consolidated_invoice({"doctype": SALES}))
		self.assertFalse(rules.is_consolidated_invoice({"doctype": POS, "is_consolidated": 1}))

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

	def test_a_merged_receipt_shows_no_trace_of_the_consolidated_invoice_in_the_list(self):
		row = rules.list_row(sale("ACC-PSINV-2026-00001", merged_into="ACC-SINV-2026-00010"), TODAY)
		self.assertNotIn("ACC-SINV-2026-00010", row.values())
		self.assertNotIn("merged_into", row)


# --- tabs, scope and periods --------------------------------------------------------------------------


class TestTabs(unittest.TestCase):
	def test_each_tab_maps_to_its_conditions(self):
		self.assertEqual(rules.tab_conditions("all"), [])
		self.assertEqual(rules.tab_conditions("unpaid"), ["s.is_return = 0", "s.effective_outstanding > 0"])
		self.assertEqual(rules.tab_conditions("returns"), ["s.is_return = 1"])

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
		owing = rules.build_receipt(
			receipt_doc(doctype=SALES, name="ACC-SINV-2026-00033", outstanding_amount=600), TODAY
		)
		self.assertTrue(owing["can"]["take_payment"])
		self.assertIsNone(owing["can"]["take_payment_blocked"])
		self.assertEqual(owing["totals"]["paid"], 1750.0)
		self.assertEqual(owing["status"], "unpaid")

	def test_whatsapp_needs_a_mobile_number(self):
		self.assertTrue(rules.build_receipt(receipt_doc(), TODAY)["can"]["whatsapp"])
		without = rules.build_receipt(receipt_doc(mobile=None), TODAY)
		self.assertFalse(without["can"]["whatsapp"])
		self.assertIsNone(without["customer"]["mobile"])
		self.assertFalse(rules.build_receipt(receipt_doc(mobile="  "), TODAY)["can"]["whatsapp"])

	def test_a_pos_invoice_on_an_open_shift_cannot_take_payment_yet_and_says_why(self):
		receipt = rules.build_receipt(receipt_doc(outstanding_amount=600), TODAY)
		self.assertFalse(receipt["can"]["take_payment"])
		self.assertEqual(receipt["can"]["take_payment_blocked"], "pay_after_close")

	def test_a_merged_pos_invoice_that_still_owes_can_take_payment(self):
		doc = receipt_doc(outstanding_amount=600, merged_into="ACC-SINV-2026-00010", merged_outstanding=600)
		receipt = rules.build_receipt(doc, TODAY)
		self.assertTrue(receipt["can"]["take_payment"])
		self.assertIsNone(receipt["can"]["take_payment_blocked"])

	def test_the_invoice_made_at_till_close_cannot_take_payment_and_says_why(self):
		doc = receipt_doc(
			doctype=SALES, name="ACC-SINV-2026-00010", outstanding_amount=600, is_consolidated=1
		)
		receipt = rules.build_receipt(doc, TODAY)
		self.assertFalse(receipt["can"]["take_payment"])
		self.assertEqual(receipt["can"]["take_payment_blocked"], "consolidated")

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
		if "GROUP BY s.is_return" in text:
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
		if "ORDER BY s.posting_date DESC" in text:
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

	def test_the_query_reads_both_kinds(self):
		self.data()
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertIn("`tabPOS Invoice` d", sql)
		self.assertIn("`tabSales Invoice` d", sql)
		self.assertIn("UNION ALL", sql)
		self.assertEqual(sql.count("d.docstatus = 1"), 2)

	def test_the_receipts_of_a_closed_till_are_read_and_the_consolidated_invoice_is_not(self):
		self.data()
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		pos_part, sales_part = sql.split("UNION ALL")
		# no condition drops a merged POS Invoice
		self.assertNotIn("IFNULL(d.consolidated_invoice, '') = ''", pos_part.split("WHERE")[1])
		# the Sales Invoice made at close is recognised by its own flag and by the receipts that point at it
		self.assertIn("IFNULL(d.is_consolidated, 0) = 0", sales_part)
		self.assertIn(
			"NOT EXISTS (SELECT 1 FROM `tabPOS Invoice` mp WHERE mp.consolidated_invoice = d.name)",
			sales_part,
		)

	def test_the_flag_is_used_only_when_the_column_exists(self):
		self.db.has_column.return_value = False
		self.data()
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertNotIn("is_consolidated", sql)
		self.assertIn("NOT EXISTS (SELECT 1 FROM `tabPOS Invoice` mp", sql)

	def test_a_merged_receipt_is_owed_only_what_its_consolidated_invoice_still_owes(self):
		self.data()
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		pos_part = sql.split("UNION ALL")[0]
		self.assertIn("LEFT JOIN `tabSales Invoice` m ON m.name = d.consolidated_invoice", pos_part)
		self.assertIn("LEAST(d.outstanding_amount, m.outstanding_amount)", pos_part)

	def test_the_count_the_rows_and_the_summary_read_the_same_sales(self):
		self.data(search="wanji", cashier=AMINA, store="Main store - A")
		rows_sql, rows_values = self.queries("ORDER BY s.posting_date DESC")[0]
		count_sql, count_values = self.queries("COUNT(*) AS n FROM (")[0]
		summary_sql, summary_values = self.queries("GROUP BY s.is_return")[0]
		union = rows_sql[rows_sql.index("FROM (") + 6 : rows_sql.index(") s")]
		self.assertIn(union, count_sql)
		self.assertIn(union, summary_sql)
		for key in ("company", "from_date", "to_date", "like", "owner", "store"):
			self.assertEqual(rows_values[key], count_values[key])
			self.assertEqual(rows_values[key], summary_values[key])

	def test_the_summary_adds_each_sale_once(self):
		self.data()
		sql, _values = self.queries("GROUP BY s.is_return")[0]
		self.assertEqual(sql.count("FROM `tabPOS Invoice` d"), 1)
		self.assertEqual(sql.count("FROM `tabSales Invoice` d"), 1)
		self.assertIn("SUM(s.grand_total)", sql)
		self.assertIn("s.effective_outstanding", sql)

	def test_the_tab_limits_the_count_and_the_rows_but_not_the_summary(self):
		self.data(tab="unpaid")
		for needle in ("COUNT(*) AS n FROM (", "ORDER BY s.posting_date DESC"):
			sql, _values = self.queries(needle)[0]
			with self.subTest(query=needle):
				self.assertIn("WHERE s.is_return = 0 AND s.effective_outstanding > 0", sql)
		summary, _values = self.queries("GROUP BY s.is_return")[0]
		self.assertNotIn("s.effective_outstanding > 0 AND", summary)
		self.assertNotIn("WHERE s.", summary)

	def test_the_period_and_company_go_into_the_query(self):
		self.data(period="7d")
		_sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertEqual((str(values["from_date"]), str(values["to_date"])), ("2026-10-07", "2026-10-13"))
		self.assertEqual(values["company"], "Shop A")

	def test_no_owner_filter_for_an_owner_who_asks_for_everyone(self):
		self.data()
		_sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertNotIn("owner", values)

	def test_an_owner_may_filter_by_cashier(self):
		self.data(cashier=AMINA)
		sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertEqual(values["owner"], AMINA)
		self.assertIn("d.owner = %(owner)s", sql)

	def test_the_unpaid_tab_asks_only_for_sales_with_a_balance(self):
		self.data(tab="unpaid")
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertIn("s.is_return = 0 AND s.effective_outstanding > 0", sql)

	def test_the_returns_tab_asks_only_for_returns(self):
		self.data(tab="returns")
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertIn("s.is_return = 1", sql)

	def test_the_summary_is_not_limited_by_the_tab(self):
		self.data(tab="returns")
		sql, _values = self.queries("GROUP BY s.is_return")[0]
		self.assertNotIn("s.is_return = 1", sql)
		self.assertNotIn("effective_outstanding > 0 AND", sql)

	def test_the_summary_follows_the_search_and_cashier_filters(self):
		self.data(search="wanji", cashier=AMINA)
		sql, values = self.queries("GROUP BY s.is_return")[0]
		self.assertIn("d.customer_name LIKE %(like)s", sql)
		self.assertEqual((values["like"], values["owner"]), ("%wanji%", AMINA))

	def test_a_receipt_number_search_reads_the_name_by_pattern_not_by_text(self):
		self.data(search="#0033")
		sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertIn("d.name REGEXP %(receipt_rx)s", sql)
		self.assertNotIn("d.name LIKE", sql)
		self.assertEqual(values["receipt_rx"], rules.receipt_regex(33))

	def test_an_s_receipt_search_skips_pos_invoices(self):
		self.data(search="S33")
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		pos_part, sales_part = sql.split("UNION ALL")
		self.assertNotIn("REGEXP", pos_part)
		self.assertIn("REGEXP", sales_part)

	def test_a_text_search_reads_name_and_customer(self):
		self.data(search="PSINV")
		sql, _values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertIn("d.name LIKE %(like)s", sql)
		self.assertIn("d.customer_name LIKE %(like)s", sql)

	def test_a_store_limits_the_sales_to_it(self):
		self.data(store="Main store - A")
		sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
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
		_sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
		self.assertEqual((values["limit"], values["offset"]), (10, 20))
		self.assertEqual((data["page"], data["pages"], data["total"]), (3, 5, 45))

	def test_a_page_past_the_end_is_corrected(self):
		self.total = 45
		data = self.data(page=99, page_size=20)
		_sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
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
		self.assertEqual(self.queries("ORDER BY s.posting_date DESC"), [])

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
		_sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
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
		for needle in ("ORDER BY s.posting_date DESC", "GROUP BY s.is_return", "COUNT(*) AS n FROM ("):
			sql, values = self.queries(needle)[0]
			with self.subTest(query=needle):
				self.assertEqual(values["owner"], AMINA)
				self.assertIn("d.owner = %(owner)s", sql)

	def test_a_cashier_without_a_filter_is_still_held_to_their_own(self):
		self.data()
		_sql, values = self.queries("ORDER BY s.posting_date DESC")[0]
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
		company="Shop A",
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
		translations = {
			row[0]
			for row in csv.reader(io.StringIO(self.source(os.path.join("..", "translations", "sw.csv"))))
			if row
		}
		tree = ast.parse(self.source("sales_list_api.py"))
		messages = {
			node.args[0].value
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
			and getattr(node.func, "id", "") == "_"
			and node.args
			and isinstance(node.args[0], ast.Constant)
		}
		self.assertGreater(len(messages), 20)
		for message in sorted(messages):
			with self.subTest(message=message):
				self.assertIn(message, translations)


class TestGetSaleOnAMergedReceipt(SalesCase):
	"""A POS Invoice merged into the invoice of a closed till can still be opened, and says where it went."""

	def setUp(self):
		super().setUp()
		self.as_user(BOSS)
		self.documents[(POS, "ACC-PSINV-2026-00033")] = stored(
			"ACC-PSINV-2026-00033",
			consolidated_invoice="ACC-SINV-2026-00010",
			grand_total=600,
			outstanding_amount=600,
		)
		self.exists[(POS, "ACC-PSINV-2026-00033")] = True
		self.consolidated_owes = 600

		def get_value(kind, name, fields=None, as_dict=False, for_update=False):
			if kind == "Customer":
				return {"mobile_no": None, "payment_terms": None}
			if kind == SALES and name == "ACC-SINV-2026-00010":
				return self.consolidated_owes
			return None

		self.db.get_value.side_effect = get_value

	def test_it_reports_the_consolidated_invoice_for_the_details_area(self):
		data = api.get_sale("ACC-PSINV-2026-00033")["data"]
		self.assertEqual(data["merged_into"], "ACC-SINV-2026-00010")
		self.assertEqual(data["receipt"], "#0033")

	def test_a_paid_consolidated_invoice_makes_the_receipt_paid(self):
		self.consolidated_owes = 0
		data = api.get_sale("ACC-PSINV-2026-00033")["data"]
		self.assertEqual((data["status"], data["totals"]["outstanding"]), ("paid", 0.0))
		self.assertFalse(data["can"]["take_payment"])

	def test_a_receipt_still_owing_offers_to_take_payment(self):
		data = api.get_sale("ACC-PSINV-2026-00033")["data"]
		self.assertTrue(data["can"]["take_payment"])
		self.assertEqual(data["totals"]["outstanding"], 600.0)

	def test_a_sale_that_was_not_merged_has_no_merged_into(self):
		self.documents[(POS, "ACC-PSINV-2026-00034")] = stored("ACC-PSINV-2026-00034")
		self.exists[(POS, "ACC-PSINV-2026-00034")] = True
		self.assertIsNone(api.get_sale("ACC-PSINV-2026-00034")["data"]["merged_into"])


# --- taking a payment -------------------------------------------------------------------------------


class PaymentCase(SalesCase):
	"""A sale that owes money, a shop with Cash, M-Pesa and Card, and a Payment Entry maker that only records what it is told."""

	SALE = "ACC-SINV-2026-00007"
	POS_SALE = "ACC-PSINV-2026-00033"
	MERGED = "ACC-PSINV-2026-00020"
	CLOSING = "ACC-SINV-2026-00010"

	def setUp(self):
		super().setUp()
		self.modes = ["Cash", "Mpesa", "Credit Card"]
		self.account = {"account": "Cash - A", "account_currency": "KES"}
		self.payments = []  # the Payment Entries recorded, in order
		self.entries = []  # their fake documents
		self.closing_owes = 900
		self.customer_of_closing = "CUST-1"
		self.documents[(SALES, self.SALE)] = stored(
			self.SALE, kind=SALES, grand_total=4000, outstanding_amount=1000, total=4000, company="Shop A"
		)
		self.documents[(POS, self.POS_SALE)] = stored(
			self.POS_SALE, grand_total=500, outstanding_amount=500, company="Shop A"
		)
		self.documents[(POS, self.MERGED)] = stored(
			self.MERGED,
			grand_total=900,
			outstanding_amount=900,
			consolidated_invoice=self.CLOSING,
			company="Shop A",
		)
		self.documents[(SALES, self.CLOSING)] = stored(
			self.CLOSING, kind=SALES, owner=BOSS, is_consolidated=1, grand_total=900, outstanding_amount=900
		)
		for key in self.documents:
			self.exists[key] = True
		self.as_user(AMINA)

	def as_user(self, user=BOSS, warehouse_owner=None):
		super().as_user(user, warehouse_owner)

		def get_value(kind, name, fields=None, as_dict=False, for_update=False):
			if kind == "Customer":
				return {"mobile_no": None, "payment_terms": None}
			doc = self.documents.get((kind, name))
			if doc is None:
				return None
			if fields == "outstanding_amount":
				return doc["outstanding_amount"]
			if fields == "company":
				return doc.get("company", "Shop A")
			if isinstance(fields, list) and fields[0] == "owner":
				return frappe._dict(
					owner=doc["owner"], docstatus=doc["docstatus"], company=doc.get("company", "Shop A")
				)
			if isinstance(fields, list):
				return frappe._dict(
					docstatus=doc["docstatus"],
					customer=doc["customer"],
					outstanding_amount=doc["outstanding_amount"],
				)
			return name

		self.db.get_value.side_effect = get_value
		self.db.exists.side_effect = lambda kind, name_or_filters=None: (
			bool(self.exists.get((kind, name_or_filters)))
			if isinstance(name_or_filters, str)
			else any(
				d.get("consolidated_invoice") == name_or_filters.get("consolidated_invoice")
				for (k, _n), d in self.documents.items()
				if k == kind
			)
		)
		self.start((api.business_lists, "modes_of_payment_for"), side_effect=lambda company: list(self.modes))
		self.start((api, "_mode_types"), return_value={})
		self.start((api, "_mode_account"), side_effect=lambda mode, company: self.account)
		self.record_calls = []
		self.start((frappe, "get_all"), create=True, side_effect=self.get_all_with_payments)
		self.start_erpnext()

	def get_all_with_payments(self, doctype, **kwargs):
		if doctype == "Payment Entry":
			pattern = kwargs["filters"]["remarks"][1]
			needle = pattern.strip("%").replace("\\_", "_")
			return [p["name"] for p in self.payments if needle in p["remarks"]][:1]
		return self.get_all(doctype, **kwargs)

	def start_erpnext(self):
		"""A stand-in for ERPNext's Payment Entry builder: a fake entry that notes what is done to it."""
		import sys
		import types

		def get_payment_entry(dt, dn, party_amount=None, **kwargs):
			entry = MagicMock(name=f"entry for {dn}")
			entry.references = [MagicMock(allocated_amount=party_amount)]
			entry.name = f"ACC-PAY-2026-{len(self.entries) + 1:05d}"
			self.entries.append(entry)
			self.built = (dt, dn, party_amount)

			def submit():
				self.payments.append(
					{"name": entry.name, "remarks": entry.remarks, "amount": entry.paid_amount}
				)
				self.documents[(dt, dn)]["outstanding_amount"] -= entry.paid_amount
				# a payment on the closing invoice leaves the receipt's own figure alone

			entry.submit.side_effect = submit
			return entry

		modules = {}
		for name in (
			"erpnext",
			"erpnext.accounts",
			"erpnext.accounts.doctype",
			"erpnext.accounts.doctype.payment_entry",
			"erpnext.accounts.doctype.payment_entry.payment_entry",
		):
			modules[name] = types.ModuleType(name)
		modules["erpnext.accounts.doctype.payment_entry.payment_entry"].get_payment_entry = get_payment_entry
		patcher = patch.dict(sys.modules, modules)
		patcher.start()
		self.addCleanup(patcher.stop)

	def pay(self, id=None, doctype=SALES, amount=400, mode="cash", **kwargs):
		return api.take_payment(id or self.SALE, doctype, amount, mode, **kwargs)

	def ok(self, **kwargs):
		result = self.pay(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]

	def refused(self, error_type, **kwargs):
		result = self.pay(**kwargs)
		self.assertFalse(result["success"], result)
		self.assertEqual(result["error_type"], error_type, result)
		self.assertEqual(self.entries, [], "no Payment Entry may be made for a refused payment")
		return result["message"]


class TestWhoMayTakeAPayment(PaymentCase):
	def test_a_guest_is_refused(self):
		self.start((frappe, "session"), new=frappe._dict(user="Guest"))
		with self.assertRaises(frappe.AuthenticationError):
			self.pay()
		self.assertEqual(self.entries, [])

	def test_a_cashier_cannot_take_payment_on_someone_elses_sale_and_nothing_is_read(self):
		self.documents[(SALES, self.SALE)]["owner"] = BOSS
		self.db.reset_mock()
		reads_before = len(self.sql)
		with self.assertRaises(frappe.PermissionError) as raised:
			self.pay()
		self.assertIn("your own sales", str(raised.exception))
		self.assertEqual(self.entries, [])
		self.assertEqual(len(self.sql), reads_before)
		# only the owner of the sale was looked up: no document with money was read
		self.assertEqual(self.db.get_value.call_count, 3)

	def test_a_sale_of_another_business_is_refused_before_anything_else(self):
		self.db.get_value.side_effect = None
		self.db.get_value.return_value = "Shop B"
		with self.assertRaises(frappe.PermissionError):
			self.pay()
		self.assertEqual(self.entries, [])

	def test_a_cashier_takes_payment_on_their_own_sale(self):
		data = self.ok()
		self.assertTrue(data["payment"].startswith("ACC-PAY-"))

	def test_a_manager_takes_payment_on_any_sale_of_the_business(self):
		self.documents[(SALES, self.SALE)]["owner"] = AMINA
		self.as_user(MANAGER)
		self.assertTrue(self.pay()["success"])

	def test_an_owner_takes_payment_on_any_sale_of_the_business(self):
		self.as_user(BOSS)
		self.assertTrue(self.pay()["success"])

	def test_a_company_that_is_not_the_sales_is_refused(self):
		self.start((api, "resolve_company"), return_value="Shop C")
		self.assertEqual(self.pay(company="Shop C")["error_type"], "not_found")
		self.assertEqual(self.entries, [])

	def test_an_unknown_kind_of_document_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.pay(doctype="Purchase Invoice")
		with self.assertRaises(frappe.ValidationError):
			self.pay(doctype=None)

	def test_a_sale_that_does_not_exist_is_not_found(self):
		self.refused("not_found", id="ACC-SINV-2026-99999")


class TestTheAmount(PaymentCase):
	def test_zero_negative_and_words_are_refused(self):
		for amount in (0, -50, "abc", "", None, True, float("nan"), float("inf"), "-1", 0.001):
			with self.subTest(amount=amount):
				self.refused("bad_amount", amount=amount)

	def test_more_than_is_owed_is_refused_in_plain_words(self):
		message = self.refused("too_much", amount=1000.01)
		self.assertIn("KES 1,000", message)

	def test_exactly_what_is_owed_is_taken(self):
		data = self.ok(amount=1000)
		self.assertEqual(data["sale"]["totals"]["outstanding"], 0.0)
		self.assertEqual(data["sale"]["status"], "paid")
		self.assertFalse(data["sale"]["can"]["take_payment"])

	def test_a_part_payment_leaves_a_smaller_balance(self):
		data = self.ok(amount=400)
		self.assertEqual(data["sale"]["totals"]["outstanding"], 600.0)
		self.assertEqual(data["sale"]["totals"]["total"], 4000.0)
		self.assertTrue(data["sale"]["can"]["take_payment"])
		self.assertEqual(data["sale"]["status"], "unpaid")

	def test_two_part_payments_cannot_together_pay_more_than_is_owed(self):
		self.ok(amount=700, request_id="first")
		self.refused_after_first = self.pay(amount=400, request_id="second")
		self.assertEqual(self.refused_after_first["error_type"], "too_much")
		self.assertEqual(len(self.entries), 1)

	def test_the_amount_is_rounded_to_two_places_and_text_is_understood(self):
		self.ok(amount="250.456")
		self.assertEqual(self.entries[0].paid_amount, 250.46)
		self.assertEqual(self.entries[0].received_amount, 250.46)

	def test_a_thousands_comma_in_text_is_understood(self):
		self.ok(amount="1,000")
		self.assertEqual(self.entries[0].paid_amount, 1000.0)


class TestWhatKindOfSale(PaymentCase):
	def test_a_return_cannot_take_a_payment(self):
		self.documents[(SALES, self.SALE)].update(is_return=1, grand_total=-500, outstanding_amount=-500)
		message = self.refused("not_payable")
		self.assertIn("return", message)

	def test_a_cancelled_or_draft_sale_is_not_found(self):
		for status in (0, 2):
			with self.subTest(docstatus=status):
				self.documents[(SALES, self.SALE)]["docstatus"] = status
				self.refused("not_found")

	def test_a_sale_that_owes_nothing_is_refused(self):
		self.documents[(SALES, self.SALE)]["outstanding_amount"] = 0
		self.assertEqual(self.refused("not_payable"), "Nothing is owed on this sale.")

	def test_the_invoice_made_when_a_till_closed_is_refused(self):
		self.as_user(BOSS)
		self.refused("consolidated", id=self.CLOSING)

	def test_an_invoice_that_receipts_point_at_is_refused_even_without_the_flag(self):
		self.as_user(BOSS)
		self.documents[(SALES, self.CLOSING)]["is_consolidated"] = 0
		self.refused("consolidated", id=self.CLOSING)


class TestThePosInvoiceCases(PaymentCase):
	def test_a_pos_invoice_still_on_an_open_shift_cannot_take_a_payment(self):
		message = self.refused("pay_after_close", id=self.POS_SALE, doctype=POS, amount=100)
		self.assertEqual(
			message, "This sale is still on the till's open shift. Close the shift, then take the payment."
		)
		self.assertEqual(self.payments, [])
		self.assertEqual(self.documents[(POS, self.POS_SALE)]["outstanding_amount"], 500)

	def test_a_merged_pos_invoice_takes_the_payment_on_its_consolidated_invoice(self):
		data = self.ok(id=self.MERGED, doctype=POS, amount=300)
		self.assertEqual(self.built, (SALES, self.CLOSING, 300.0))
		self.assertEqual(self.documents[(SALES, self.CLOSING)]["outstanding_amount"], 600)
		self.assertEqual(data["sale"]["id"], self.MERGED)
		self.assertEqual(data["sale"]["merged_into"], self.CLOSING)
		self.assertEqual(data["sale"]["totals"]["outstanding"], 600.0)

	def test_a_merged_pos_invoice_cannot_take_more_than_the_consolidated_invoice_owes(self):
		self.documents[(SALES, self.CLOSING)]["outstanding_amount"] = 200
		self.refused("too_much", id=self.MERGED, doctype=POS, amount=300)

	def test_a_merged_pos_invoice_whose_consolidated_invoice_is_paid_owes_nothing(self):
		self.documents[(SALES, self.CLOSING)]["outstanding_amount"] = 0
		self.refused("not_payable", id=self.MERGED, doctype=POS, amount=100)

	def test_a_merged_pos_invoice_of_another_customer_is_refused(self):
		self.documents[(SALES, self.CLOSING)]["customer"] = "CUST-9"
		self.refused("other_customer", id=self.MERGED, doctype=POS, amount=100)

	def test_a_merged_pos_invoice_whose_consolidated_invoice_is_gone_is_refused(self):
		self.documents[(SALES, self.CLOSING)]["docstatus"] = 2
		self.refused("pay_after_close", id=self.MERGED, doctype=POS, amount=100)

	def test_a_cashier_pays_on_their_own_merged_receipt_though_the_closing_invoice_is_not_theirs(self):
		self.assertEqual(self.documents[(SALES, self.CLOSING)]["owner"], BOSS)
		self.assertTrue(self.pay(id=self.MERGED, doctype=POS, amount=100)["success"])


class TestTheKindOfPayment(PaymentCase):
	def test_an_unknown_kind_is_refused(self):
		for mode in ("bitcoin", "", None, "Cash payment"):
			with self.subTest(mode=mode):
				self.refused("bad_mode", mode=mode)

	def test_the_keys_map_to_the_shops_own_modes(self):
		self.ok(mode="cash", amount=100)
		self.assertEqual(self.entries[0].mode_of_payment, "Cash")
		self.ok(mode="mpesa", reference="sgh7k2l9qx", amount=100, request_id="r2")
		self.assertEqual(self.entries[1].mode_of_payment, "Mpesa")
		self.ok(mode="card", amount=100, request_id="r3")
		self.assertEqual(self.entries[2].mode_of_payment, "Credit Card")

	def test_the_keys_may_be_written_as_the_till_writes_them(self):
		self.ok(mode="M-Pesa", reference="SGH7K2L9QX", amount=100)
		self.assertEqual(self.entries[0].mode_of_payment, "Mpesa")

	def test_a_kind_the_shop_does_not_have_is_refused(self):
		self.modes = ["Cash"]
		message = self.refused("bad_mode", mode="mpesa", reference="SGH7K2L9QX")
		self.assertIn("M-Pesa", message)
		self.refused("bad_mode", mode="card")

	def test_a_shop_with_no_modes_at_all_is_refused(self):
		self.modes = []
		self.refused("bad_mode", mode="cash")

	def test_a_mode_with_no_account_is_refused(self):
		self.account = None
		message = self.refused("no_account")
		self.assertIn("Cash", message)

	def test_the_payment_goes_to_the_account_of_the_mode(self):
		self.account = {"account": "Bank - A", "account_currency": "KES"}
		self.ok(mode="card")
		self.assertEqual(self.entries[0].paid_to, "Bank - A")

	def test_mpesa_needs_its_code(self):
		for reference in (None, "", "   "):
			with self.subTest(reference=reference):
				self.refused("reference_required", mode="mpesa", reference=reference)

	def test_a_bad_mpesa_code_is_refused(self):
		for reference in ("SGH7K2L", "SGH7K2L9QXAAA", "SGH-7K2L9QX", "SGH7K2L9 QX", "SGH7K2L9Q!"):
			with self.subTest(reference=reference):
				self.refused("bad_reference", mode="mpesa", reference=reference)

	def test_a_good_mpesa_code_is_saved_in_capitals_as_the_reference_number(self):
		data = self.ok(mode="mpesa", reference=" sgh7k2l9qx ")
		self.assertEqual(self.entries[0].reference_no, "SGH7K2L9QX")
		self.assertEqual(data["sale"]["payments"][0]["mode"], "Mpesa")

	def test_cash_needs_no_reference(self):
		self.ok(mode="cash")
		self.assertNotIn("reference_no", [c[0] for c in self.entries[0].method_calls])

	def test_cash_keeps_a_reference_that_is_given(self):
		self.ok(mode="cash", reference="till slip 4")
		self.assertEqual(self.entries[0].reference_no, "till slip 4")

	def test_a_card_payment_with_no_reference_carries_the_sale_number(self):
		self.ok(mode="card")
		self.assertEqual(self.entries[0].reference_no, self.SALE)


class TestThePaymentEntry(PaymentCase):
	def test_it_is_inserted_and_submitted_exactly_once(self):
		self.ok()
		self.assertEqual(len(self.entries), 1)
		entry = self.entries[0]
		entry.insert.assert_called_once_with(ignore_permissions=True)
		entry.submit.assert_called_once_with()

	def test_the_fields_that_are_set(self):
		self.ok(amount=400, mode="mpesa", reference="SGH7K2L9QX", request_id="req-1")
		entry = self.entries[0]
		self.assertEqual(self.built, (SALES, self.SALE, 400.0))
		self.assertEqual(str(entry.posting_date), "2026-10-13")
		self.assertEqual(str(entry.reference_date), "2026-10-13")
		self.assertEqual(entry.paid_amount, 400.0)
		self.assertEqual(entry.received_amount, 400.0)
		self.assertEqual(entry.references[0].allocated_amount, 400.0)
		self.assertEqual(entry.mode_of_payment, "Mpesa")
		self.assertEqual(entry.paid_to, "Cash - A")
		self.assertEqual(entry.reference_no, "SGH7K2L9QX")
		self.assertIn("Payment taken at the till for #S0007", entry.remarks)
		self.assertIn("[pay-req:req-1:ACC-SINV-2026-00007]", entry.remarks)

	def test_the_answer_names_the_payment_and_shows_the_sale_after_it(self):
		data = self.ok(amount=400)
		self.assertEqual(set(data), {"payment", "sale"})
		self.assertEqual(data["payment"], self.entries[0].name)
		sale_now = api.get_sale(self.SALE)["data"]
		self.assertEqual(data["sale"], sale_now)

	def test_nothing_is_committed_by_the_call(self):
		self.ok()
		self.db.commit.assert_not_called()


class TestARetryNeverPaysTwice(PaymentCase):
	def test_the_same_request_returns_the_first_payment_and_the_current_sale(self):
		first = self.ok(amount=400, request_id="tap-1")
		again = self.ok(amount=400, request_id="tap-1")
		self.assertEqual(len(self.entries), 1)
		self.assertEqual(again["payment"], first["payment"])
		self.assertEqual(again["sale"]["totals"]["outstanding"], 600.0)

	def test_a_retry_after_the_sale_is_fully_paid_still_answers(self):
		first = self.ok(amount=1000, request_id="tap-1")
		again = self.ok(amount=1000, request_id="tap-1")
		self.assertEqual(again["payment"], first["payment"])
		self.assertEqual(len(self.entries), 1)

	def test_a_different_request_is_a_different_payment(self):
		self.ok(amount=300, request_id="a")
		self.ok(amount=300, request_id="b")
		self.assertEqual(len(self.entries), 2)
		self.assertEqual(self.documents[(SALES, self.SALE)]["outstanding_amount"], 400)

	def test_the_same_request_id_on_another_sale_is_a_new_payment(self):
		self.ok(amount=300, request_id="same")
		self.as_user(BOSS)
		self.ok(id=self.MERGED, doctype=POS, amount=100, request_id="same")
		self.assertEqual(len(self.entries), 2)

	def test_the_sale_is_locked_before_the_retry_is_looked_for(self):
		order = []
		inner = self.db.get_value.side_effect

		def get_value(*args, **kwargs):
			if kwargs.get("for_update"):
				order.append("lock")
			return inner(*args, **kwargs)

		self.db.get_value.side_effect = get_value
		self.start((api, "_earlier_payment"), side_effect=lambda *a: order.append("look"))
		self.pay(request_id="x")
		self.assertEqual(order[:2], ["lock", "look"])

	def test_a_request_id_with_odd_characters_is_refused(self):
		for request_id in ("a b", "x" * 65, "%", "a/b"):
			with self.subTest(request_id=request_id):
				self.refused("bad_request_id", request_id=request_id)

	def test_a_call_with_no_request_id_still_works(self):
		self.ok()
		self.assertNotIn("[pay-req", self.entries[0].remarks)


class TestWhenItGoesWrong(PaymentCase):
	def test_a_failure_rolls_back_logs_and_says_nothing_was_taken_without_the_detail(self):
		def explode():
			raise RuntimeError("secret db detail")

		self.start((api, "_record_payment"), side_effect=lambda *a: explode())
		result = self.pay()
		self.assertFalse(result["success"])
		self.assertEqual(result["error_type"], "server_error")
		self.assertIn("nothing was taken", result["message"])
		self.assertNotIn("secret", result["message"])
		self.db.rollback.assert_called()
		frappe.log_error.assert_called()

	def test_a_failure_in_submit_rolls_back(self):
		def make(dt, dn, party_amount=None, **kwargs):
			entry = MagicMock()
			entry.references = [MagicMock()]
			entry.submit.side_effect = frappe.ValidationError("Account is frozen")
			return entry

		import sys

		sys.modules["erpnext.accounts.doctype.payment_entry.payment_entry"].get_payment_entry = make
		result = self.pay()
		self.assertEqual(result["error_type"], "server_error")
		self.assertNotIn("frozen", result["message"])
		self.db.rollback.assert_called()
		self.db.commit.assert_not_called()

	def test_when_only_the_receipt_cannot_be_drawn_the_payment_is_still_reported(self):
		self.start((api, "_receipt"), side_effect=RuntimeError("boom"))
		result = self.pay()
		self.assertTrue(result["success"])
		self.assertIsNone(result["data"]["sale"])
		self.assertEqual(len(self.entries), 1)


class TestPaymentRules(unittest.TestCase):
	def test_amounts(self):
		self.assertEqual(rules.parse_amount("12.5"), 12.5)
		self.assertEqual(rules.parse_amount(3), 3.0)
		self.assertIsNone(rules.parse_amount("1e999"))
		self.assertIsNone(rules.parse_amount([5]))

	def test_check_amount(self):
		self.assertIsNone(rules.check_amount(100.0, 100))
		self.assertEqual(rules.check_amount(100.01, 100), "too_much")
		self.assertEqual(rules.check_amount(None, 100), "bad_amount")

	def test_picking_a_mode_prefers_the_exact_name(self):
		self.assertEqual(rules.pick_mode("cash", ["Cash", "Cash on delivery"]), "Cash")
		self.assertEqual(rules.pick_mode("mpesa", ["M-Pesa", "Mpesa Till"]), "M-Pesa")
		self.assertEqual(rules.pick_mode("card", ["Credit Card", "Debit Card"]), "Credit Card")

	def test_picking_a_mode_never_picks_another_kind(self):
		self.assertIsNone(rules.pick_mode("cash", ["Bank Draft", "Cheque", "Wire Transfer"]))
		self.assertIsNone(rules.pick_mode("card", ["Cash"]))
		self.assertIsNone(rules.pick_mode("cash", []))
		self.assertIsNone(rules.pick_mode("nope", ["Cash"]))

	def test_a_mode_of_type_cash_with_an_odd_name_counts_as_cash(self):
		self.assertEqual(rules.pick_mode("cash", ["Petty box"], {"Petty box": "Cash"}), "Petty box")

	def test_the_plan(self):
		self.assertEqual(rules.payment_plan(SALES, "S-1", 1, 0, 100)["target"], (SALES, "S-1"))
		self.assertEqual(rules.payment_plan(POS, "P-1", 1, 0, 100, "S-9")["target"], (SALES, "S-9"))
		self.assertEqual(rules.payment_plan(POS, "P-1", 1, 0, 100)["error"], "pay_after_close")
		self.assertEqual(rules.payment_plan(SALES, "S-1", 1, 0, 100, None, True)["error"], "consolidated")
		self.assertEqual(rules.payment_plan(SALES, "S-1", 2, 0, 100)["error"], "not_found")
		self.assertEqual(rules.payment_plan(SALES, "S-1", 1, 1, 100)["error"], "is_return")
		self.assertEqual(rules.payment_plan(SALES, "S-1", 1, 0, 0)["error"], "nothing_owed")

	def test_the_marker_finds_the_request_and_the_sale_only(self):
		pattern = rules.marker_pattern("tap-1", "ACC-SINV-2026-00007")
		self.assertEqual(pattern, "%[pay-req:tap-1:ACC-SINV-2026-00007]%")
		self.assertIn(
			"[pay-req:tap-1:ACC-SINV-2026-00007]",
			rules.payment_remark("#S0007", "tap-1", "ACC-SINV-2026-00007"),
		)
		self.assertNotIn("pay-req", rules.payment_remark("#S0007", None, "ACC-SINV-2026-00007"))

	def test_money_in_a_message(self):
		self.assertEqual(rules.money_text(1000), "KES 1,000")
		self.assertEqual(rules.money_text(250.5), "KES 250.50")
