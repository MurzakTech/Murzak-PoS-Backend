# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the rules of the Purchases page and the guided Receive goods step. No database is needed."""

import unittest
from datetime import date

from murzak_pos.api import purchases_list_calc as rules

ORDER, INVOICE, RECEIPT = rules.ORDER, rules.INVOICE, rules.RECEIPT


def order_lines():
	return [
		rules.line_view(
			{
				"item_code": "A",
				"item_name": "Sugar",
				"qty": 10,
				"received_qty": 4,
				"rate": 100,
				"amount": 1000,
				"warehouse": "Stores",
			}
		),
		rules.line_view(
			{
				"item_code": "B",
				"item_name": "Rice",
				"qty": 5,
				"received_qty": 0,
				"rate": 200,
				"amount": 1000,
				"warehouse": "Stores",
			}
		),
	]


class TestShortNumber(unittest.TestCase):
	def test_an_order_is_o_and_four_digits(self):
		self.assertEqual(rules.short_number(ORDER, "PUR-ORD-2026-00012"), "#O0012")

	def test_a_bill_is_b(self):
		self.assertEqual(rules.short_number(INVOICE, "ACC-PINV-2026-00007"), "#B0007")

	def test_a_return_is_r(self):
		self.assertEqual(rules.short_number(INVOICE, "ACC-PINV-2026-00009", True), "#R0009")

	def test_goods_received_is_g(self):
		self.assertEqual(rules.short_number(RECEIPT, "MAT-PRE-2026-00003"), "#G0003")

	def test_an_amended_name_keeps_the_original_number(self):
		self.assertEqual(rules.short_number(ORDER, "PUR-ORD-2026-00012-1"), "#O0012")

	def test_a_name_with_no_number_is_returned_as_it_is(self):
		self.assertEqual(rules.short_number(ORDER, "OLD-ONE"), "OLD-ONE")

	def test_a_large_number_is_not_cut(self):
		self.assertEqual(rules.short_number(ORDER, "PUR-ORD-2026-123456"), "#O123456")


class TestSearch(unittest.TestCase):
	def test_empty_search_is_none(self):
		self.assertIsNone(rules.parse_search("   "))

	def test_text_becomes_a_like_pattern_with_wildcards_escaped(self):
		self.assertEqual(rules.parse_search("50%_off")["like"], "%50\\%\\_off%")

	def test_a_plain_number_is_a_short_number_for_any_kind(self):
		parsed = rules.parse_search("12")
		self.assertEqual((parsed["seq"], parsed["letter"]), (12, None))

	def test_a_number_with_a_letter_names_the_kind(self):
		parsed = rules.parse_search("#b0012")
		self.assertEqual((parsed["seq"], parsed["letter"]), (12, "B"))

	def test_a_supplier_name_is_not_a_number(self):
		self.assertIsNone(rules.parse_search("Bidco")["seq"])

	def test_a_long_search_is_cut(self):
		self.assertEqual(len(rules.parse_search("x" * 200)["text"]), rules.MAX_SEARCH_LENGTH)

	def test_a_supplier_matches_by_name(self):
		row = {"doctype": ORDER, "name": "PUR-ORD-2026-00001", "supplier_name": "Bidco Africa"}
		self.assertTrue(rules.search_matches(row, rules.parse_search("bidco")))

	def test_a_bill_matches_by_the_suppliers_bill_number(self):
		row = {"doctype": INVOICE, "name": "ACC-PINV-2026-00001", "bill_no": "INV-778"}
		self.assertTrue(rules.search_matches(row, rules.parse_search("778")))
		self.assertTrue(rules.search_matches(row, rules.parse_search("INV-778")))

	def test_a_short_number_matches_the_end_of_the_name(self):
		row = {"doctype": ORDER, "name": "PUR-ORD-2026-00012"}
		self.assertTrue(rules.search_matches(row, rules.parse_search("#O0012")))
		self.assertFalse(rules.search_matches(row, rules.parse_search("#O0013")))

	def test_a_short_number_does_not_match_the_year(self):
		row = {"doctype": ORDER, "name": "PUR-ORD-2026-00012"}
		self.assertFalse(rules.search_matches(row, rules.parse_search("2026")))

	def test_a_letter_must_match_the_kind(self):
		bill = {"doctype": INVOICE, "name": "ACC-PINV-2026-00012", "is_return": 0}
		self.assertFalse(rules.search_matches(bill, rules.parse_search("O12")))
		self.assertTrue(rules.search_matches(bill, rules.parse_search("B12")))

	def test_the_r_letter_matches_a_return_only(self):
		debit = {"doctype": INVOICE, "name": "ACC-PINV-2026-00012", "is_return": 1}
		self.assertTrue(rules.search_matches(debit, rules.parse_search("R12")))
		self.assertFalse(rules.search_matches(debit, rules.parse_search("B12")))


class TestRequest(unittest.TestCase):
	def test_the_tab_defaults_to_all(self):
		self.assertEqual(rules.normalize_tab(None), "all")

	def test_a_known_tab_is_kept(self):
		self.assertEqual(rules.normalize_tab("Returns"), "returns")

	def test_an_unknown_tab_is_refused(self):
		with self.assertRaises(rules.ListError):
			rules.normalize_tab("sessions")

	def test_the_orders_tab_holds_only_orders(self):
		self.assertEqual(rules.tab_parts("orders"), {"orders": True, "bills": False, "returns": False})

	def test_the_returns_tab_holds_only_returns(self):
		self.assertEqual(rules.tab_parts("returns"), {"orders": False, "bills": False, "returns": True})

	def test_all_holds_the_three_parts(self):
		self.assertEqual(rules.tab_parts("all"), {"orders": True, "bills": True, "returns": True})

	def test_a_doctype_must_be_one_of_the_two(self):
		with self.assertRaises(rules.ListError):
			rules.normalize_doctype("Sales Invoice")
		self.assertIsNone(rules.normalize_doctype(""))
		self.assertEqual(rules.normalize_doctype(INVOICE), INVOICE)

	def test_page_numbers_clamp_a_page_past_the_end(self):
		self.assertEqual(rules.page_numbers(9, 20, 45)["page"], 3)

	def test_page_numbers_default_a_bad_size_to_20(self):
		self.assertEqual(rules.page_numbers(1, "x", 100)["page_size"], 20)

	def test_page_numbers_cap_the_size_at_100(self):
		self.assertEqual(rules.page_numbers(1, 500, 1000)["page_size"], 100)

	def test_an_empty_list_has_one_page(self):
		self.assertEqual(rules.page_numbers(1, 20, 0)["pages"], 1)

	def test_the_offset_follows_the_page(self):
		self.assertEqual(rules.page_numbers(2, 20, 45)["offset"], 20)


class TestStatus(unittest.TestCase):
	def test_a_cancelled_document_is_cancelled(self):
		self.assertEqual(rules.derive_status(ORDER, 2), "cancelled")

	def test_a_draft_is_a_draft(self):
		self.assertEqual(rules.derive_status(INVOICE, 0), "draft")

	def test_an_order_with_nothing_received_is_ordered(self):
		self.assertEqual(rules.derive_status(ORDER, 1, False, "To Receive and Bill", 0, 0), "ordered")

	def test_an_order_part_received_is_still_ordered(self):
		self.assertEqual(rules.derive_status(ORDER, 1, False, "To Receive and Bill", 40, 0), "ordered")

	def test_an_order_fully_received_but_not_billed_is_received(self):
		self.assertEqual(rules.derive_status(ORDER, 1, False, "To Bill", 100, 0), "received")

	def test_an_order_received_and_billed_is_billed(self):
		self.assertEqual(rules.derive_status(ORDER, 1, False, "Completed", 100, 100), "billed")

	def test_a_closed_order_counts_as_received(self):
		self.assertEqual(rules.derive_status(ORDER, 1, False, "Closed", 30, 0), "received")

	def test_a_bill_with_money_owed_is_billed(self):
		self.assertEqual(rules.derive_status(INVOICE, 1, False, None, 0, 0, 500), "billed")

	def test_a_bill_with_nothing_owed_is_paid(self):
		self.assertEqual(rules.derive_status(INVOICE, 1, False, None, 0, 0, 0), "paid")

	def test_a_return_is_returned(self):
		self.assertEqual(rules.derive_status(INVOICE, 1, True, None, 0, 0, 0), "returned")

	def test_part_received_is_true_only_between_none_and_all(self):
		self.assertTrue(rules.part_received(ORDER, 1, 40))
		self.assertFalse(rules.part_received(ORDER, 1, 0))
		self.assertFalse(rules.part_received(ORDER, 1, 100))
		self.assertFalse(rules.part_received(INVOICE, 1, 40))

	def test_every_status_is_one_of_the_seven(self):
		for doctype in (ORDER, INVOICE):
			for docstatus in (0, 1, 2):
				for returned in (False, True):
					for received in (0, 50, 100):
						status = rules.derive_status(doctype, docstatus, returned, "To Bill", received, 0, 10)
						self.assertIn(status, rules.STATUSES)


class TestListRow(unittest.TestCase):
	def raw(self, **extra):
		row = {
			"doctype": INVOICE,
			"name": "ACC-PINV-2026-00007",
			"supplier": "SUP-1",
			"supplier_name": "Bidco",
			"posting_date": date(2026, 10, 12),
			"grand_total": 1160.0,
			"docstatus": 1,
			"status": "Unpaid",
			"is_return": 0,
			"outstanding_amount": 1160.0,
			"due_date": date(2026, 11, 11),
			"bill_no": "INV-9",
		}
		row.update(extra)
		return row

	def test_a_bill_row_has_number_supplier_date_total_status(self):
		row = rules.list_row(self.raw())
		self.assertEqual(
			(row["number"], row["supplier"], row["date"], row["total"], row["status"]),
			("#B0007", "Bidco", "2026-10-12", 1160.0, "billed"),
		)

	def test_a_bill_row_carries_what_is_owed_and_the_due_date(self):
		row = rules.list_row(self.raw())
		self.assertEqual(
			(row["outstanding"], row["due_date"], row["bill_no"]), (1160.0, "2026-11-11", "INV-9")
		)

	def test_a_paid_bill_owes_nothing(self):
		self.assertEqual(rules.list_row(self.raw(outstanding_amount=0))["outstanding"], 0.0)

	def test_a_return_shows_its_total_as_a_positive_number(self):
		row = rules.list_row(self.raw(is_return=1, grand_total=-300.0))
		self.assertEqual(
			(row["total"], row["status"], row["number"], row["outstanding"], row["due_date"]),
			(300.0, "returned", "#R0007", 0.0, ""),
		)

	def test_an_order_row_has_no_due_date_and_owes_nothing(self):
		row = rules.list_row(
			self.raw(
				doctype=ORDER,
				name="PUR-ORD-2026-00003",
				status="To Receive and Bill",
				per_received=0,
				per_billed=0,
				outstanding_amount=0,
			)
		)
		self.assertEqual(
			(row["number"], row["status"], row["due_date"], row["outstanding"]),
			("#O0003", "ordered", "", 0.0),
		)

	def test_the_supplier_id_is_used_when_there_is_no_name(self):
		self.assertEqual(rules.list_row(self.raw(supplier_name=None))["supplier"], "SUP-1")

	def test_a_row_never_shows_the_system_reference_as_its_number(self):
		self.assertNotIn("ACC-", rules.list_row(self.raw())["number"])


class TestSummary(unittest.TestCase):
	def test_the_three_numbers(self):
		self.assertEqual(
			rules.build_summary(3, 12500.456, -800),
			{"orders_open": 3, "bills_unpaid": 12500.46, "returned": 800.0},
		)

	def test_missing_numbers_are_zero(self):
		self.assertEqual(
			rules.build_summary(None, None, None), {"orders_open": 0, "bills_unpaid": 0.0, "returned": 0.0}
		)


class TestLines(unittest.TestCase):
	def test_still_to_arrive_is_ordered_minus_received(self):
		line = order_lines()[0]
		self.assertEqual((line["ordered"], line["received"], line["to_arrive"]), (10.0, 4.0, 6.0))

	def test_still_to_arrive_is_never_negative(self):
		self.assertEqual(rules.line_view({"item_code": "A", "qty": 5, "received_qty": 7})["to_arrive"], 0.0)

	def test_a_bill_line_has_nothing_left_to_arrive(self):
		self.assertEqual(
			rules.bill_line_view({"item_code": "A", "qty": 3, "rate": 10, "amount": 30})["to_arrive"], 0.0
		)

	def test_can_receive_needs_a_submitted_open_order_with_something_left(self):
		self.assertTrue(rules.can_receive(1, "To Receive and Bill", order_lines()))
		self.assertFalse(rules.can_receive(0, "Draft", order_lines()))
		self.assertFalse(rules.can_receive(1, "Closed", order_lines()))
		self.assertFalse(rules.can_receive(1, "On Hold", order_lines()))
		self.assertFalse(
			rules.can_receive(
				1, "To Receive and Bill", [rules.line_view({"item_code": "A", "qty": 2, "received_qty": 2})]
			)
		)

	def test_the_first_unpaid_bill_is_the_one_due_soonest(self):
		bills = [
			{"id": "X2", "outstanding": 10, "due_date": "2026-11-20", "date": "2026-10-01"},
			{"id": "X1", "outstanding": 10, "due_date": "2026-11-05", "date": "2026-10-02"},
			{"id": "X3", "outstanding": 0, "due_date": "2026-10-01", "date": "2026-09-01"},
		]
		self.assertEqual(rules.first_unpaid_bill(bills)["id"], "X1")

	def test_no_unpaid_bill_gives_none(self):
		self.assertIsNone(rules.first_unpaid_bill([{"id": "X", "outstanding": 0}]))
		self.assertIsNone(rules.first_unpaid_bill([]))

	def test_a_return_is_never_the_bill_to_pay(self):
		self.assertIsNone(rules.first_unpaid_bill([{"id": "R", "outstanding": 50, "is_return": True}]))


class TestQuantityRules(unittest.TestCase):
	def plan(self, requested):
		return rules.plan_receipt(order_lines(), requested)

	def test_a_good_request_is_kept(self):
		plan = self.plan([{"item_code": "A", "qty_received": 6}, {"item_code": "B", "qty_received": "2.5"}])
		self.assertIsNone(plan["error"])
		self.assertEqual(plan["items"], [{"item_code": "A", "qty": 6.0}, {"item_code": "B", "qty": 2.5}])

	def test_no_items_is_refused(self):
		self.assertEqual(self.plan([])["error"]["code"], "no_items")
		self.assertEqual(self.plan(None)["error"]["code"], "no_items")
		self.assertEqual(self.plan({"item_code": "A"})["error"]["code"], "no_items")

	def test_an_item_that_is_not_on_the_order_is_refused(self):
		error = self.plan([{"item_code": "Z", "qty_received": 1}])["error"]
		self.assertEqual((error["code"], error["item_code"]), ("not_on_order", "Z"))

	def test_an_item_named_twice_is_refused(self):
		error = self.plan([{"item_code": "A", "qty_received": 1}, {"item_code": "A", "qty_received": 1}])[
			"error"
		]
		self.assertEqual(error["code"], "named_twice")

	def test_zero_is_refused(self):
		self.assertEqual(self.plan([{"item_code": "A", "qty_received": 0}])["error"]["code"], "bad_qty")

	def test_a_negative_quantity_is_refused(self):
		self.assertEqual(self.plan([{"item_code": "A", "qty_received": -1}])["error"]["code"], "bad_qty")

	def test_a_word_is_refused(self):
		self.assertEqual(self.plan([{"item_code": "A", "qty_received": "many"}])["error"]["code"], "bad_qty")

	def test_nothing_typed_is_refused(self):
		self.assertEqual(self.plan([{"item_code": "A", "qty_received": None}])["error"]["code"], "bad_qty")
		self.assertEqual(self.plan([{"item_code": "A"}])["error"]["code"], "bad_qty")

	def test_a_true_value_is_not_a_quantity(self):
		self.assertEqual(self.plan([{"item_code": "A", "qty_received": True}])["error"]["code"], "bad_qty")

	def test_more_than_is_left_is_refused_and_says_how_much_is_left(self):
		error = self.plan([{"item_code": "A", "qty_received": 6.5}])["error"]
		self.assertEqual((error["code"], error["left"]), ("too_much", 6.0))

	def test_exactly_what_is_left_is_allowed(self):
		self.assertIsNone(self.plan([{"item_code": "A", "qty_received": 6}])["error"])

	def test_a_line_without_an_item_code_is_refused(self):
		self.assertEqual(self.plan([{"qty_received": 1}])["error"]["code"], "no_item_code")
		self.assertEqual(self.plan(["A"])["error"]["code"], "no_item_code")

	def test_clean_qty_accepts_numbers_and_numeric_text(self):
		self.assertEqual(rules.clean_qty("1,000.5"), 1000.5)
		self.assertEqual(rules.clean_qty(3), 3.0)

	def test_clean_qty_refuses_infinity_and_nan(self):
		self.assertIsNone(rules.clean_qty(float("inf")))
		self.assertIsNone(rules.clean_qty(float("nan")))

	def test_a_tiny_quantity_that_rounds_to_zero_is_refused(self):
		self.assertIsNone(rules.clean_qty("0.0001"))

	def test_what_is_left_after_a_partial_delivery(self):
		items = [{"item_code": "A", "qty": 2.0}]
		self.assertEqual(
			rules.still_to_arrive(order_lines(), items),
			[{"item_code": "A", "to_arrive": 4.0}, {"item_code": "B", "to_arrive": 5.0}],
		)

	def test_nothing_is_left_when_every_line_is_received_in_full(self):
		items = [{"item_code": "A", "qty": 6.0}, {"item_code": "B", "qty": 5.0}]
		self.assertEqual(rules.still_to_arrive(order_lines(), items), [])

	def test_duplicate_items_on_an_order_are_found(self):
		lines = [*order_lines(), rules.line_view({"item_code": "A", "qty": 1})]
		self.assertEqual(rules.duplicate_items(lines), ["A"])
		self.assertEqual(rules.duplicate_items(order_lines()), [])


class TestStockReport(unittest.TestCase):
	def test_stock_afterwards_is_stock_before_plus_the_quantity_received(self):
		items = [{"item_code": "A", "qty": 6.0}]
		report = rules.stock_report(items, {"A": 10.0}, {"A": 16.0})
		self.assertEqual(report, [{"item_code": "A", "received": 6.0, "before": 10.0, "after": 16.0}])
		self.assertTrue(rules.stock_matches(report))

	def test_a_report_that_does_not_add_up_is_noticed(self):
		report = rules.stock_report([{"item_code": "A", "qty": 6.0}], {"A": 10.0}, {"A": 15.0})
		self.assertFalse(rules.stock_matches(report))

	def test_an_item_that_holds_no_stock_is_left_out(self):
		items = [{"item_code": "A", "qty": 1.0}, {"item_code": "S", "qty": 1.0}]
		report = rules.stock_report(items, {"A": 0}, {"A": 1}, {"A"})
		self.assertEqual([r["item_code"] for r in report], ["A"])

	def test_an_item_with_no_stock_yet_starts_at_zero(self):
		report = rules.stock_report([{"item_code": "N", "qty": 3.0}], {}, {"N": 3.0})
		self.assertEqual((report[0]["before"], report[0]["after"]), (0.0, 3.0))

	def test_an_item_in_two_warehouses_adds_up(self):
		self.assertEqual(rules.sum_by_item([("A", 4), ("A", 6), ("B", 1)]), {"A": 10.0, "B": 1.0})


class TestRequestId(unittest.TestCase):
	def test_no_id_is_allowed(self):
		self.assertEqual(rules.clean_request_id(None), (None, True))

	def test_a_normal_id_is_kept(self):
		self.assertEqual(rules.clean_request_id("recv-lq3k9-ab12cd34"), ("recv-lq3k9-ab12cd34", True))

	def test_a_bad_id_is_refused(self):
		self.assertEqual(rules.clean_request_id("a b; drop"), (None, False))
		self.assertEqual(rules.clean_request_id("x" * 80), (None, False))
		self.assertEqual(rules.clean_request_id("short"), (None, False))

	def test_the_marker_names_the_request_and_the_order(self):
		self.assertEqual(rules.request_marker("rq-12345678", "PO-1"), "[recv-req:rq-12345678:PO-1]")

	def test_the_pattern_wraps_the_marker_for_like(self):
		self.assertTrue(
			rules.marker_pattern("rq-12345678", "PO_1").startswith("%[recv-req:rq-12345678:PO\\_1]")
		)

	def test_the_remark_carries_the_marker_only_with_a_request(self):
		self.assertIn("[recv-req:", rules.receipt_remark("PO-1", "rq-12345678"))
		self.assertNotIn("[recv-req:", rules.receipt_remark("PO-1", None))
		self.assertIn("[recv-req:", rules.invoice_remark("PO-1", "rq-12345678"))


class TestBillFields(unittest.TestCase):
	def test_no_date_is_allowed(self):
		self.assertEqual(rules.clean_day(None), (None, True))

	def test_a_date_is_read(self):
		self.assertEqual(rules.clean_day("2026-10-13"), (date(2026, 10, 13), True))

	def test_text_that_is_not_a_date_is_refused(self):
		self.assertEqual(rules.clean_day("yesterday"), (None, False))

	def test_a_due_date_before_the_bill_date_is_refused(self):
		self.assertEqual(rules.check_dates(date(2026, 10, 13), date(2026, 10, 1)), "due_before_bill")
		self.assertIsNone(rules.check_dates(date(2026, 10, 13), date(2026, 10, 13)))
		self.assertIsNone(rules.check_dates(None, date(2026, 10, 1)))

	def test_a_bill_number_is_tidied_and_cut(self):
		self.assertEqual(rules.clean_bill_no("  INV   778  "), "INV 778")
		self.assertEqual(len(rules.clean_bill_no("x" * 200)), rules.MAX_BILL_NO_LENGTH)

	def test_the_default_due_date_is_30_days_on(self):
		self.assertEqual(rules.default_due_date(date(2026, 10, 1)), date(2026, 10, 31))
		self.assertIsNone(rules.default_due_date(None))


class TestNotes(unittest.TestCase):
	def test_a_note_is_tidied(self):
		self.assertEqual(rules.clean_note("  Paid   half\n\n  in cash  "), "Paid half\nin cash")

	def test_tags_are_removed(self):
		self.assertEqual(rules.clean_note("<b>Fragile</b> goods"), "Fragile goods")

	def test_a_note_is_cut_at_500_characters(self):
		self.assertEqual(len(rules.clean_note("x" * 900)), rules.MAX_NOTE_LENGTH)

	def test_nothing_is_an_empty_note(self):
		self.assertEqual(rules.clean_note(None), "")
		self.assertEqual(rules.clean_note("   "), "")


class TestDirectBill(unittest.TestCase):
	def test_good_lines_are_kept(self):
		plan = rules.plan_direct_bill([{"item_code": "A", "qty": 2, "rate": "10.50"}])
		self.assertEqual(plan["items"], [{"item_code": "A", "qty": 2.0, "rate": 10.5}])

	def test_no_lines_is_refused(self):
		self.assertEqual(rules.plan_direct_bill([])["error"]["code"], "no_items")

	def test_a_zero_quantity_is_refused(self):
		self.assertEqual(
			rules.plan_direct_bill([{"item_code": "A", "qty": 0, "rate": 1}])["error"]["code"], "bad_qty"
		)

	def test_a_negative_cost_is_refused(self):
		self.assertEqual(
			rules.plan_direct_bill([{"item_code": "A", "qty": 1, "rate": -1}])["error"]["code"], "bad_rate"
		)

	def test_a_missing_cost_is_zero(self):
		self.assertEqual(rules.plan_direct_bill([{"item_code": "A", "qty": 1}])["items"][0]["rate"], 0.0)

	def test_the_same_item_twice_is_added_up(self):
		plan = rules.plan_direct_bill(
			[{"item_code": "A", "qty": 1, "rate": 5}, {"item_code": "A", "qty": 2, "rate": 6}]
		)
		self.assertEqual(plan["items"], [{"item_code": "A", "qty": 3.0, "rate": 6.0}])

	def test_the_total_of_the_lines(self):
		self.assertEqual(rules.lines_total([{"qty": 2, "rate": 10.5}, {"qty": 1, "rate": 3}]), 24.0)

	def test_a_payment_must_be_above_zero_and_not_above_the_bill(self):
		self.assertIsNone(rules.check_payment(100, None))
		self.assertIsNone(rules.check_payment(100, 100))
		self.assertEqual(rules.check_payment(100, 0), "bad_paid")
		self.assertEqual(rules.check_payment(100, "abc"), "bad_paid")
		self.assertEqual(rules.check_payment(100, 100.5), "paid_too_much")

	def test_the_direct_marker_wraps_the_request(self):
		self.assertEqual(rules.direct_marker("rq-12345678"), "[bill-req:rq-12345678]")
		self.assertTrue(rules.direct_pattern("rq-12345678").endswith("]%"))

	def test_money_reads_as_kes(self):
		self.assertEqual(rules.money_text(1200), "KES 1,200")
		self.assertEqual(rules.money_text(1200.5), "KES 1,200.50")


if __name__ == "__main__":
	unittest.main()
