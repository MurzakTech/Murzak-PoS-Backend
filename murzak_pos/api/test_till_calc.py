# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""The till panel and shift-end arithmetic (till_calc.py). No database and no Frappe."""

import unittest
from datetime import date, datetime, time, timedelta

from murzak_pos.api import till_calc as calc


def lines(opening=None, payments=None, change=None, entries=None, credit=0.0, types=None):
	return calc.build_expected(opening or [], payments or [], change or [], entries or [], credit, types)


def by_mode(result):
	return {line["mode"]: line for line in result}


def cash_opening(amount=5000):
	return [{"mode_of_payment": "Cash", "opening_amount": amount}]


def pay(mode, amount, is_return=0):
	return {"is_return": is_return, "mode_of_payment": mode, "amount": amount}


class TestModeKey(unittest.TestCase):
	def test_standard_names(self):
		for name, key in [
			("Cash", "cash"),
			("M-Pesa", "mpesa"),
			("MPESA", "mpesa"),
			("Mpesa", "mpesa"),
			("Card", "card"),
			("Credit Card", "card"),
			("On credit", "credit"),
			("Credit", "credit"),
			("Customer Credit", "credit"),
			("Bank Draft", "other"),
			("Cheque", "other"),
			("Wire Transfer", "other"),
			("Airtel Money", "other"),
		]:
			with self.subTest(name=name):
				self.assertEqual(calc.classify_method(name), key)

	def test_a_card_is_never_on_credit(self):
		self.assertEqual(calc.classify_method("Credit Card"), "card")

	def test_a_cash_type_mode_with_an_odd_name_is_cash(self):
		self.assertEqual(calc.classify_method("Till float", "Cash"), "cash")

	def test_bank_draft_of_type_cash_stays_other(self):
		self.assertEqual(calc.classify_method("Bank Draft", "Cash"), "other")

	def test_empty_name_is_other(self):
		self.assertEqual(calc.classify_method(None), "other")


class TestExpectedCash(unittest.TestCase):
	def test_opening_plus_cash_sales(self):
		result = by_mode(lines(cash_opening(5000), [pay("Cash", 2350)]))
		self.assertEqual(result["Cash"]["expected"], 7350.0)
		self.assertEqual(result["Cash"]["opening"], 5000.0)
		self.assertEqual(result["Cash"]["sales"], 2350.0)

	def test_cash_refunds_come_off(self):
		result = by_mode(lines(cash_opening(5000), [pay("Cash", 2350), pay("Cash", -500, is_return=1)]))
		self.assertEqual(result["Cash"]["refunds"], 500.0)
		self.assertEqual(result["Cash"]["expected"], 6850.0)

	def test_change_given_comes_out_of_cash(self):
		result = by_mode(
			lines(cash_opening(1000), [pay("Cash", 2000)], change=[{"is_return": 0, "change": 350}])
		)
		self.assertEqual(result["Cash"]["sales"], 1650.0)
		self.assertEqual(result["Cash"]["expected"], 2650.0)

	def test_change_on_a_return_is_ignored(self):
		result = by_mode(
			lines(cash_opening(1000), [pay("Cash", 100)], change=[{"is_return": 1, "change": 50}])
		)
		self.assertEqual(result["Cash"]["expected"], 1100.0)

	def test_cash_paid_out_comes_off(self):
		entries = [{"payment_type": "Pay", "mode_of_payment": "Cash", "amount": 300}]
		result = by_mode(lines(cash_opening(1000), [pay("Cash", 500)], entries=entries))
		self.assertEqual(result["Cash"]["paid_out"], 300.0)
		self.assertEqual(result["Cash"]["expected"], 1200.0)

	def test_a_debt_settled_in_cash_goes_in(self):
		entries = [{"payment_type": "Receive", "mode_of_payment": "Cash", "amount": 400}]
		result = by_mode(lines(cash_opening(1000), entries=entries))
		self.assertEqual(result["Cash"]["paid_in"], 400.0)
		self.assertEqual(result["Cash"]["expected"], 1400.0)

	def test_a_refund_larger_than_sales_is_not_negative_refunds(self):
		result = by_mode(lines(cash_opening(0), [pay("Cash", 100, is_return=1)]))
		self.assertEqual(result["Cash"]["refunds"], 0.0)

	def test_the_sum_adds_up(self):
		entries = [
			{"payment_type": "Receive", "mode_of_payment": "Cash", "amount": 40},
			{"payment_type": "Pay", "mode_of_payment": "Cash", "amount": 15},
		]
		line = by_mode(
			lines(
				cash_opening(100),
				[pay("Cash", 1000), pay("Cash", -200, is_return=1)],
				change=[{"is_return": 0, "change": 60}],
				entries=entries,
			)
		)["Cash"]
		self.assertEqual(
			line["expected"],
			round(line["opening"] + line["sales"] + line["paid_in"] - line["refunds"] - line["paid_out"], 2),
		)
		self.assertEqual(line["expected"], 100 + 940 + 40 - 200 - 15)

	def test_other_modes_are_not_touched_by_change(self):
		result = by_mode(
			lines(
				cash_opening(0),
				[pay("Cash", 100), pay("M-Pesa", 500)],
				change=[{"is_return": 0, "change": 20}],
			)
		)
		self.assertEqual(result["M-Pesa"]["expected"], 500.0)
		self.assertEqual(result["Cash"]["expected"], 80.0)

	def test_a_session_with_nothing_sold_expects_its_opening_cash(self):
		self.assertEqual(by_mode(lines(cash_opening(2500)))["Cash"]["expected"], 2500.0)

	def test_amounts_are_rounded_to_cents(self):
		result = by_mode(lines(cash_opening(0.1), [pay("Cash", 0.2)]))
		self.assertEqual(result["Cash"]["expected"], 0.3)

	def test_a_mode_only_in_the_sales_still_gets_a_line(self):
		result = by_mode(lines(cash_opening(0), [pay("M-Pesa", 800)]))
		self.assertEqual(result["M-Pesa"]["opening"], 0.0)
		self.assertEqual(result["M-Pesa"]["expected"], 800.0)

	def test_cash_in_till_is_the_cash_lines(self):
		self.assertEqual(
			calc.cash_in_till(lines(cash_opening(100), [pay("Cash", 50), pay("M-Pesa", 900)])), 150.0
		)

	def test_cash_in_till_is_none_without_a_cash_line(self):
		self.assertIsNone(calc.cash_in_till(lines([], [pay("M-Pesa", 900)])))

	def test_two_cash_modes_share_one_change(self):
		types = {"Till float": "Cash"}
		opening = [
			{"mode_of_payment": "Cash", "opening_amount": 10},
			{"mode_of_payment": "Till float", "opening_amount": 5},
		]
		result = lines(opening, [pay("Cash", 100)], change=[{"is_return": 0, "change": 30}], types=types)
		self.assertEqual(calc.cash_in_till(result), 85.0)


class TestCredit(unittest.TestCase):
	def test_a_credit_sale_is_not_cash(self):
		result = by_mode(lines(cash_opening(1000), [pay("Cash", 200)], credit=700))
		self.assertEqual(result["Cash"]["expected"], 1200.0)
		self.assertEqual(result["On credit"]["expected"], 0.0)
		self.assertFalse(result["On credit"]["counts_cash"])
		self.assertEqual(result["On credit"]["sales"], 700.0)

	def test_no_credit_line_when_nothing_was_sold_on_credit(self):
		self.assertNotIn("On credit", by_mode(lines(cash_opening(1000))))

	def test_credit_is_last(self):
		result = lines(cash_opening(1), [pay("Cash", 1), pay("M-Pesa", 1), pay("Bank Draft", 1)], credit=5)
		self.assertEqual(result[-1]["key"], "credit")

	def test_a_credit_mode_in_the_payments_is_folded_into_one_line(self):
		result = lines(cash_opening(0), [pay("Credit", 300)])
		credit = [line for line in result if line["key"] == "credit"]
		self.assertEqual(len(credit), 1)
		self.assertEqual(credit[0]["expected"], 0.0)
		self.assertEqual(credit[0]["sales"], 300.0)

	def test_credit_does_not_change_the_cash_in_till(self):
		self.assertEqual(calc.cash_in_till(lines(cash_opening(1000), credit=9999)), 1000.0)

	def test_every_other_line_counts_cash(self):
		result = lines(cash_opening(1), [pay("M-Pesa", 1), pay("Card", 1)], credit=3)
		self.assertEqual([line["counts_cash"] for line in result], [True, True, True, False])


class TestOrdering(unittest.TestCase):
	def test_cash_then_mpesa_then_card_then_other_then_credit(self):
		result = lines(
			[{"mode_of_payment": "Cheque", "opening_amount": 0}],
			[pay("Card", 1), pay("M-Pesa", 1), pay("Cash", 1), pay("Airtel Money", 1)],
			credit=2,
		)
		self.assertEqual(
			[line["key"] for line in result], ["cash", "mpesa", "card", "other", "other", "credit"]
		)

	def test_other_modes_are_sorted_by_name(self):
		result = lines([], [pay("Zeta", 1), pay("Alpha", 1)])
		self.assertEqual([line["mode"] for line in result], ["Alpha", "Zeta"])


class TestCountedInput(unittest.TestCase):
	def test_nothing_is_an_empty_list(self):
		for empty in (None, "", []):
			self.assertEqual(calc.parse_counted(empty), [])

	def test_a_list(self):
		self.assertEqual(
			calc.parse_counted([{"mode": "Cash", "amount": "1200.5"}]), [{"mode": "Cash", "amount": 1200.5}]
		)

	def test_json_text(self):
		self.assertEqual(
			calc.parse_counted('[{"mode": "Cash", "amount": 10}]'), [{"mode": "Cash", "amount": 10.0}]
		)

	def test_bad_text_is_refused(self):
		with self.assertRaises(calc.TillError) as raised:
			calc.parse_counted("not json")
		self.assertEqual(raised.exception.code, "bad_counted")

	def test_a_missing_amount_is_refused(self):
		for bad in (
			[{"mode": "Cash"}],
			[{"mode": "Cash", "amount": ""}],
			[{"mode": "Cash", "amount": "abc"}],
			[{"amount": 5}],
			["x"],
		):
			with self.subTest(bad=bad), self.assertRaises(calc.TillError):
				calc.parse_counted(bad)

	def test_negative_is_refused(self):
		with self.assertRaises(calc.TillError) as raised:
			calc.parse_counted([{"mode": "Cash", "amount": -1}])
		self.assertEqual(raised.exception.code, "negative_count")

	def test_the_same_mode_twice_is_refused(self):
		with self.assertRaises(calc.TillError) as raised:
			calc.parse_counted([{"mode": "Cash", "amount": 1}, {"mode": "cash", "amount": 2}])
		self.assertEqual(raised.exception.code, "duplicate_mode")

	def test_not_a_number_is_refused(self):
		with self.assertRaises(calc.TillError):
			calc.parse_counted([{"mode": "Cash", "amount": "nan"}])

	def test_match_by_name_or_kind(self):
		result = lines(cash_opening(100), [pay("M-Pesa", 5)])
		self.assertEqual(calc.match_counted(result, [{"mode": "cash", "amount": 90}]), {"Cash": 90.0})
		self.assertEqual(calc.match_counted(result, [{"mode": "mpesa", "amount": 5}]), {"M-Pesa": 5.0})

	def test_an_unknown_mode_is_refused(self):
		result = lines(cash_opening(100))
		with self.assertRaises(calc.TillError) as raised:
			calc.match_counted(result, [{"mode": "Gold", "amount": 1}])
		self.assertEqual(raised.exception.code, "unknown_mode")

	def test_on_credit_cannot_be_counted(self):
		result = lines(cash_opening(100), credit=50)
		with self.assertRaises(calc.TillError):
			calc.match_counted(result, [{"mode": "On credit", "amount": 50}])

	def test_the_same_line_named_twice_two_ways_is_refused(self):
		result = lines(cash_opening(100))
		with self.assertRaises(calc.TillError):
			calc.match_counted(result, [{"mode": "Cash", "amount": 1}, {"mode": "cash", "amount": 1}])


class TestDifference(unittest.TestCase):
	def figures(self, counted_cash, **kwargs):
		result = lines(cash_opening(5000), [pay("Cash", 2350)], **kwargs)
		return calc.close_figures(result, {"Cash": counted_cash} if counted_cash is not None else {})

	def test_short(self):
		f = self.figures(7250)
		self.assertEqual((f["difference"], f["word"], f["by"]), (-100.0, "short", 100.0))

	def test_over(self):
		f = self.figures(7400)
		self.assertEqual((f["difference"], f["word"], f["by"]), (50.0, "over", 50.0))

	def test_matches(self):
		f = self.figures(7350)
		self.assertEqual((f["difference"], f["word"], f["by"]), (0.0, "matches", 0.0))

	def test_difference_is_counted_minus_expected(self):
		f = self.figures(7000)
		self.assertEqual(f["lines"][0]["difference"], 7000 - 7350)

	def test_a_tiny_difference_matches(self):
		self.assertEqual(calc.difference_word(0.001), "matches")
		self.assertEqual(calc.difference_word(-0.004), "matches")
		self.assertEqual(calc.difference_word(-0.01), "short")

	def test_no_cash_count_leaves_the_result_open(self):
		f = self.figures(None)
		self.assertTrue(f["needs_cash_count"])
		self.assertIsNone(f["difference"])
		self.assertIsNone(f["word"])
		self.assertIsNone(f["lines"][0]["counted"])

	def test_cash_counted_as_zero_is_a_count(self):
		f = self.figures(0)
		self.assertFalse(f["needs_cash_count"])
		self.assertEqual(f["difference"], -7350.0)

	def test_non_cash_defaults_to_expected(self):
		result = lines(cash_opening(100), [pay("M-Pesa", 800)])
		f = calc.close_figures(result, {"Cash": 100})
		mpesa = next(x for x in f["lines"] if x["key"] == "mpesa")
		self.assertEqual((mpesa["counted"], mpesa["difference"]), (800.0, 0.0))
		self.assertEqual(f["word"], "matches")

	def test_a_counted_non_cash_amount_is_used(self):
		result = lines(cash_opening(100), [pay("M-Pesa", 800)])
		f = calc.close_figures(result, {"Cash": 100, "M-Pesa": 750})
		self.assertEqual(f["difference"], -50.0)
		self.assertEqual(f["total_expected"], 900.0)
		self.assertEqual(f["total_counted"], 850.0)

	def test_credit_is_not_in_the_count(self):
		result = lines(cash_opening(100), credit=500)
		f = calc.close_figures(result, {"Cash": 100})
		self.assertEqual([x["key"] for x in f["lines"]], ["cash"])
		self.assertEqual(f["total_expected"], 100.0)

	def test_a_session_without_cash_needs_no_cash_count(self):
		result = lines([], [pay("M-Pesa", 800)])
		f = calc.close_figures(result, {})
		self.assertFalse(f["needs_cash_count"])
		self.assertEqual(f["word"], "matches")

	def test_labels_are_carried_through(self):
		result = lines(cash_opening(1))
		result[0]["label"] = "Cash"
		self.assertEqual(calc.close_figures(result, {"Cash": 1})["lines"][0]["label"], "Cash")


class TestClosingEntryRows(unittest.TestCase):
	def figures(self):
		result = lines(cash_opening(100), [pay("M-Pesa", 800)])
		return calc.close_figures(result, {"Cash": 90})["lines"]

	def test_rows_that_exist_are_changed(self):
		changes, additions = calc.reconciliation_changes(["Cash", "M-Pesa"], self.figures())
		self.assertEqual(
			changes["Cash"], {"closing_amount": 90.0, "difference": -10.0, "expected_amount": 100.0}
		)
		self.assertEqual(additions, [])

	def test_a_mode_erpnext_did_not_list_is_added(self):
		changes, additions = calc.reconciliation_changes(["Cash"], self.figures())
		self.assertNotIn("M-Pesa", changes)
		self.assertEqual(additions[0]["mode_of_payment"], "M-Pesa")
		self.assertEqual(additions[0]["opening_amount"], 0)
		self.assertEqual(additions[0]["closing_amount"], 800.0)

	def test_uncounted_lines_are_not_written(self):
		result = calc.close_figures(lines(cash_opening(100)), {})["lines"]
		self.assertEqual(calc.reconciliation_changes(["Cash"], result), ({}, []))


class TestWhoAndWhich(unittest.TestCase):
	def test_the_cashier_who_opened_it_may_close(self):
		self.assertTrue(calc.may_close("a@x", "a@x", "cashier"))

	def test_another_cashier_may_not(self):
		self.assertFalse(calc.may_close("a@x", "b@x", "cashier"))

	def test_a_manager_or_owner_may(self):
		self.assertTrue(calc.may_close("a@x", "b@x", "manager"))
		self.assertTrue(calc.may_close("a@x", "b@x", "owner"))

	def test_nobody_signed_in_may_not(self):
		self.assertFalse(calc.may_close("a@x", None, None))
		self.assertFalse(calc.may_close("a@x", "b@x", None))

	def sessions(self):
		return [
			{"name": "S3", "user": "boss@x"},
			{"name": "S2", "user": "amina@x"},
			{"name": "S1", "user": "juma@x"},
		]

	def test_a_cashier_gets_only_their_own(self):
		self.assertEqual(calc.pick_session(self.sessions(), "amina@x", "cashier")["name"], "S2")
		self.assertIsNone(calc.pick_session(self.sessions(), "zed@x", "cashier"))

	def test_a_manager_gets_their_own_first_then_the_newest(self):
		self.assertEqual(calc.pick_session(self.sessions(), "juma@x", "manager")["name"], "S1")
		self.assertEqual(calc.pick_session(self.sessions(), "zed@x", "manager")["name"], "S3")

	def test_no_sessions_gives_none(self):
		self.assertIsNone(calc.pick_session([], "a@x", "owner"))


class TestHeldCount(unittest.TestCase):
	rows = ({"warehouse": "A"}, {"warehouse": "B"}, {"warehouse": None}, {"warehouse": ""})

	def test_all_without_a_store(self):
		self.assertEqual(calc.count_held(self.rows), 4)

	def test_a_store_counts_its_own_and_store_less_bills(self):
		self.assertEqual(calc.count_held(self.rows, "A"), 3)

	def test_none(self):
		self.assertEqual(calc.count_held([], "A"), 0)
		self.assertEqual(calc.count_held(None), 0)


class TestRecentSales(unittest.TestCase):
	def rows(self, n):
		return [
			{
				"name": f"ACC-PSINV-2026-{i:05d}",
				"creation": datetime(2026, 10, 13, 8, i % 60),
				"posting_time": timedelta(hours=8, minutes=i),
				"customer": "CUST-1",
				"customer_name": "Walk-in Customer",
				"grand_total": 100 * i,
				"is_return": 0,
				"outstanding_amount": 0,
			}
			for i in range(1, n + 1)
		]

	def test_limited_to_five_newest_first(self):
		result = calc.recent_sales(self.rows(9))
		self.assertEqual(len(result), 5)
		self.assertEqual([r["receipt"] for r in result], ["#0009", "#0008", "#0007", "#0006", "#0005"])

	def test_fewer_than_five(self):
		self.assertEqual(len(calc.recent_sales(self.rows(2))), 2)

	def test_empty(self):
		self.assertEqual(calc.recent_sales([]), [])

	def test_the_fields(self):
		first = calc.recent_sales(self.rows(1))[0]
		self.assertEqual(
			first,
			{
				"id": "ACC-PSINV-2026-00001",
				"receipt": "#0001",
				"time": "08:01",
				"customer": "Walk-in Customer",
				"total": 100.0,
				"status": "paid",
			},
		)

	def test_status_words(self):
		self.assertEqual(calc.sale_status(0, 0), "paid")
		self.assertEqual(calc.sale_status(0, 250), "unpaid")
		self.assertEqual(calc.sale_status(1, 0), "returned")

	def test_the_customer_falls_back_to_the_customer_code(self):
		row = self.rows(1)[0]
		row["customer_name"] = None
		self.assertEqual(calc.recent_sales([row])[0]["customer"], "CUST-1")

	def test_a_limit_of_zero_gives_nothing(self):
		self.assertEqual(calc.recent_sales(self.rows(3), 0), [])


class TestSmallReaders(unittest.TestCase):
	def test_short_receipt(self):
		self.assertEqual(calc.short_receipt("ACC-PSINV-2026-00033"), "#0033")
		self.assertEqual(calc.short_receipt("SINV-0005"), "#0005")
		self.assertEqual(calc.short_receipt("ACC-SINV-2026-123456"), "#123456")
		self.assertEqual(calc.short_receipt("OFFLINE"), "OFFLINE")
		self.assertEqual(calc.short_receipt(None), "")

	def test_clock(self):
		self.assertEqual(calc.clock(timedelta(hours=14, minutes=5)), "14:05")
		self.assertEqual(calc.clock(time(9, 3, 1)), "09:03")
		self.assertEqual(calc.clock(datetime(2026, 10, 13, 7, 45)), "07:45")
		self.assertEqual(calc.clock("14:05:09.123"), "14:05")
		self.assertIsNone(calc.clock(None))
		self.assertIsNone(calc.clock("soon"))

	def test_day_of(self):
		self.assertEqual(calc.day_of(datetime(2026, 10, 13, 7, 45)), "2026-10-13")
		self.assertEqual(calc.day_of(date(2026, 10, 13)), "2026-10-13")
		self.assertEqual(calc.day_of("2026-10-13 07:45:00"), "2026-10-13")
		self.assertIsNone(calc.day_of(None))

	def test_as_bool(self):
		for yes in (True, 1, "true", "True", "1", "yes"):
			self.assertTrue(calc.as_bool(yes), yes)
		for no in (False, 0, None, "false", "False", "0", "", "no"):
			self.assertFalse(calc.as_bool(no), no)

	def test_money_never_negative_zero(self):
		self.assertEqual(str(calc.money(-0.0001)), "0.0")

	def test_grouped_other_names(self):
		self.assertTrue(calc.is_grouped_other("Bank Draft"))
		self.assertTrue(calc.is_grouped_other("Wire Transfer"))
		self.assertFalse(calc.is_grouped_other("Airtel Money"))


if __name__ == "__main__":
	unittest.main()
