# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the rules behind the Expenses page (`expenses_calc`). They need no database and no Frappe calls.

The last class proves acceptance check AC-30: the debit line of an entry made by create_expense is counted by the same
rule Home uses for Running costs, so Net profit falls by the amount.
"""

import base64
import unittest
from datetime import date
from typing import ClassVar

from murzak_pos.api import dashboard_api
from murzak_pos.api import expenses_calc as rules
from murzak_pos.api.profit_calc import summarize_profit

TODAY = date(2026, 10, 17)


def png(extra=b""):
	return b"\x89PNG\r\n\x1a\n" + b"\x00" * 16 + extra


def data_url(content, kind="image/png"):
	return f"data:{kind};base64," + base64.b64encode(content).decode()


class TestAmount(unittest.TestCase):
	def test_plain_numbers_and_text_are_read(self):
		self.assertEqual(rules.clean_amount(1200), (1200.0, None))
		self.assertEqual(rules.clean_amount("1200"), (1200.0, None))
		self.assertEqual(rules.clean_amount("1,200.50"), (1200.5, None))
		self.assertEqual(rules.clean_amount("KES 1,200"), (1200.0, None))
		self.assertEqual(rules.clean_amount(0.1 + 0.2), (0.3, None))

	def test_two_decimals_at_most(self):
		self.assertEqual(rules.clean_amount("10.456"), (10.46, None))

	def test_nothing_is_refused(self):
		for empty in (None, "", "  "):
			self.assertEqual(rules.clean_amount(empty), (None, "no_amount"))

	def test_zero_and_negative_are_refused(self):
		for bad in (0, "0", -5, "-5", "0.001", 0.004):
			self.assertEqual(rules.clean_amount(bad), (None, "not_above_zero"), bad)

	def test_text_that_is_not_a_number_is_refused(self):
		for bad in ("abc", "12x", "1.2.3", True, "nan", "inf", float("nan"), float("inf")):
			self.assertEqual(rules.clean_amount(bad)[0], None, bad)
			self.assertIn(rules.clean_amount(bad)[1], ("bad_amount", "not_above_zero"))

	def test_a_huge_amount_is_refused(self):
		self.assertEqual(rules.clean_amount(rules.MAX_AMOUNT), (float(rules.MAX_AMOUNT), None))
		self.assertEqual(rules.clean_amount(rules.MAX_AMOUNT + 1), (None, "too_big"))


class TestDate(unittest.TestCase):
	def test_today_and_earlier_are_fine(self):
		self.assertEqual(rules.clean_day("2026-10-17", TODAY), (date(2026, 10, 17), None))
		self.assertEqual(rules.clean_day("2025-01-01", TODAY), (date(2025, 1, 1), None))

	def test_tomorrow_is_refused(self):
		self.assertEqual(rules.clean_day("2026-10-18", TODAY), (None, "future_date"))

	def test_missing_and_bad_dates(self):
		self.assertEqual(rules.clean_day(None, TODAY), (None, "no_date"))
		self.assertEqual(rules.clean_day("", TODAY), (None, "no_date"))
		self.assertEqual(rules.clean_day("17/10/2026", TODAY), (None, "bad_date"))
		self.assertEqual(rules.clean_day("2026-13-40", TODAY), (None, "bad_date"))


class TestNote(unittest.TestCase):
	def test_a_note_is_tidied(self):
		self.assertEqual(
			rules.clean_note("  Charcoal \n for  the kitchen "), ("Charcoal for the kitchen", None)
		)
		self.assertEqual(rules.clean_note(None), ("", None))
		self.assertEqual(rules.clean_note("<b>Rent</b>"), ("Rent", None))

	def test_brackets_cannot_imitate_the_marker(self):
		note, why = rules.clean_note("fake [expense:abc12345]")
		self.assertIsNone(why)
		self.assertFalse(rules.parse_remark(note)["is_expense"])

	def test_one_hundred_and_forty_characters_are_allowed_and_one_more_is_not(self):
		self.assertEqual(rules.clean_note("a" * 140), ("a" * 140, None))
		self.assertEqual(rules.clean_note("a" * 141), ("", "note_too_long"))

	def test_the_length_counts_after_tidying(self):
		raw = "x   " * 70  # 280 characters typed, 139 once the extra spaces are gone
		self.assertEqual(rules.clean_note(raw), (" ".join(["x"] * 70), None))


class TestPaging(unittest.TestCase):
	def test_defaults_and_limits(self):
		self.assertEqual(
			rules.page_numbers(None, None, 45), {"page": 1, "page_size": 20, "pages": 3, "offset": 0}
		)
		self.assertEqual(rules.page_numbers(2, 20, 45)["offset"], 20)
		self.assertEqual(rules.page_numbers(1, 500, 45)["page_size"], 100)
		self.assertEqual(rules.page_numbers(1, 0, 45)["page_size"], 20)

	def test_a_page_past_the_end_is_the_last_and_an_empty_list_has_one_page(self):
		self.assertEqual(rules.page_numbers(9, 20, 45)["page"], 3)
		self.assertEqual(rules.page_numbers(1, 20, 0), {"page": 1, "page_size": 20, "pages": 1, "offset": 0})
		self.assertEqual(rules.page_numbers("x", "y", 5)["page"], 1)


class TestSearchAndNames(unittest.TestCase):
	def test_like_characters_are_taken_literally(self):
		self.assertEqual(rules.like_pattern("50%_off"), "%50\\%\\_off%")

	def test_search_is_tidied_and_cut(self):
		self.assertEqual(rules.clean_search("  rent   shop "), "rent shop")
		self.assertEqual(len(rules.clean_search("x" * 200)), rules.MAX_SEARCH_LENGTH)

	def test_the_company_tag_is_removed(self):
		self.assertEqual(rules.strip_abbr("Office Rent - AB", "AB"), "Office Rent")
		self.assertEqual(rules.strip_abbr("Office Rent - AB", "ZZ"), "Office Rent - AB")
		self.assertEqual(rules.strip_abbr(None, "AB"), "")


class TestPaidFromChoice(unittest.TestCase):
	def test_the_three_choices(self):
		self.assertEqual(rules.clean_paid_from("cash"), "cash")
		self.assertEqual(rules.clean_paid_from("Cash"), "cash")
		self.assertEqual(rules.clean_paid_from("M-Pesa"), "mpesa")
		self.assertEqual(rules.clean_paid_from("mpesa"), "mpesa")
		self.assertEqual(rules.clean_paid_from("Bank"), "bank")

	def test_anything_else_is_refused(self):
		for bad in (None, "", "card", "credit", "Cash - AB", "cheque"):
			self.assertIsNone(rules.clean_paid_from(bad), bad)

	def test_modes_of_payment_are_sorted_into_the_choices(self):
		self.assertEqual(rules.classify_mode("Cash", "Cash"), "cash")
		self.assertEqual(rules.classify_mode("Petty Cash", "Cash"), "cash")
		self.assertEqual(rules.classify_mode("M-Pesa", "Bank"), "mpesa")
		self.assertEqual(rules.classify_mode("MPESA Till", "General"), "mpesa")
		self.assertEqual(rules.classify_mode("Bank Draft", "Bank"), "bank")
		self.assertEqual(rules.classify_mode("Wire Transfer", "Bank"), "bank")
		self.assertIsNone(rules.classify_mode("Credit Card", "Bank"))
		self.assertIsNone(rules.classify_mode("Card", "Bank"))
		self.assertIsNone(rules.classify_mode("On credit", "General"))
		self.assertIsNone(rules.classify_mode("Gift voucher", "General"))
		self.assertIsNone(rules.classify_mode("", "Cash"))


def account(name, **extra):
	base = {
		"company": "Acme",
		"is_group": 0,
		"disabled": 0,
		"freeze_account": "No",
		"account_currency": "KES",
	}
	base.update(extra)
	return base


class TestPaidFromOptions(unittest.TestCase):
	def options(self, modes, accounts):
		return {o["key"]: o for o in rules.paid_from_options(modes, accounts, "Acme", "KES")}

	def test_each_choice_with_an_account_is_available(self):
		out = self.options(
			[
				{"name": "Cash", "type": "Cash", "account": "Cash - A"},
				{"name": "M-Pesa", "type": "Bank", "account": "M-Pesa - A"},
				{"name": "Bank Draft", "type": "Bank", "account": "KCB - A"},
			],
			{"Cash - A": account("c"), "M-Pesa - A": account("m"), "KCB - A": account("b")},
		)
		self.assertTrue(all(o["available"] for o in out.values()))
		self.assertEqual(out["cash"]["account"], "Cash - A")
		self.assertEqual(out["mpesa"]["mode_of_payment"], "M-Pesa")
		self.assertEqual(out["bank"]["account"], "KCB - A")
		self.assertIsNone(out["cash"]["reason"])

	def test_a_shop_without_an_mpesa_mode_cannot_pay_from_it(self):
		out = self.options(
			[{"name": "Cash", "type": "Cash", "account": "Cash - A"}], {"Cash - A": account("c")}
		)
		self.assertFalse(out["mpesa"]["available"])
		self.assertEqual(out["mpesa"]["reason"], "no_mode")
		self.assertIsNone(out["mpesa"]["account"])
		self.assertEqual(out["bank"]["reason"], "no_mode")

	def test_a_mode_with_no_account_for_this_company(self):
		out = self.options([{"name": "M-Pesa", "type": "Bank", "account": None}], {})
		self.assertEqual(out["mpesa"]["reason"], "no_account")

	def test_an_account_that_cannot_be_used(self):
		for bad in (
			account("x", company="Other"),
			account("x", is_group=1),
			account("x", disabled=1),
			account("x", freeze_account="Yes"),
			account("x", account_currency="USD"),
		):
			out = self.options([{"name": "Cash", "type": "Cash", "account": "Cash - A"}], {"Cash - A": bad})
			self.assertFalse(out["cash"]["available"])
			self.assertEqual(out["cash"]["reason"], "account_unusable")
		out = self.options([{"name": "Cash", "type": "Cash", "account": "Cash - A"}], {})
		self.assertEqual(out["cash"]["reason"], "account_unusable")

	def test_an_account_without_a_currency_is_fine(self):
		out = self.options(
			[{"name": "Cash", "type": "Cash", "account": "Cash - A"}],
			{"Cash - A": account("c", account_currency=None)},
		)
		self.assertTrue(out["cash"]["available"])

	def test_the_order_is_cash_then_mpesa_then_bank(self):
		self.assertEqual(
			[o["key"] for o in rules.paid_from_options([], {}, "Acme", "KES")], ["cash", "mpesa", "bank"]
		)
		self.assertEqual(
			[o["label"] for o in rules.paid_from_options([], {}, "Acme", "KES")], ["Cash", "M-Pesa", "Bank"]
		)

	def test_the_exact_name_wins_and_a_broken_account_is_skipped(self):
		modes = [
			{"name": "Petty Cash", "type": "Cash", "account": "Petty - A"},
			{"name": "Cash", "type": "Cash", "account": "Cash - A"},
		]
		out = self.options(modes, {"Petty - A": account("p"), "Cash - A": account("c")})
		self.assertEqual(out["cash"]["mode_of_payment"], "Cash")
		out = self.options(modes, {"Petty - A": account("p"), "Cash - A": account("c", disabled=1)})
		self.assertEqual(out["cash"]["mode_of_payment"], "Petty Cash")

	def test_bank_prefers_a_name_with_bank_in_it(self):
		modes = [
			{"name": "Cheque", "type": "Bank", "account": "Ch - A"},
			{"name": "Bank Transfer", "type": "Bank", "account": "Bk - A"},
		]
		out = self.options(modes, {"Ch - A": account("c"), "Bk - A": account("b")})
		self.assertEqual(out["bank"]["mode_of_payment"], "Bank Transfer")

	def test_the_credit_account_is_named_by_its_choice_on_a_row(self):
		options = rules.paid_from_options(
			[{"name": "M-Pesa", "type": "Bank", "account": "M-Pesa - A"}],
			{"M-Pesa - A": account("m")},
			"Acme",
			"KES",
		)
		self.assertEqual(rules.paid_from_of("M-Pesa - A", options, "A"), {"key": "mpesa", "label": "M-Pesa"})
		self.assertEqual(rules.paid_from_of("Safe - A", options, "A"), {"key": "other", "label": "Safe"})


def expense_account(name="Office Rent - A", **extra):
	base = {
		"name": name,
		"account_name": name.split(" - ")[0],
		"root_type": "Expense",
		"account_type": "Expense Account",
		"is_group": 0,
		"disabled": 0,
		"parent_account": "Indirect Expenses - A",
	}
	base.update(extra)
	return base


class TestCategories(unittest.TestCase):
	def test_an_ordinary_expense_account_is_a_category(self):
		self.assertTrue(rules.is_category(expense_account()))
		self.assertTrue(rules.is_category(expense_account(account_type=None)))
		self.assertTrue(rules.is_category(expense_account(account_type="Depreciation")))

	def test_cost_of_goods_stock_adjustment_and_stock_received_but_not_billed_are_not(self):
		for kind in (
			"Cost of Goods Sold",
			"Stock Adjustment",
			"Stock Received But Not Billed",
			"Expenses Included In Valuation",
			"Expenses Included In Asset Valuation",
			"Round Off",
		):
			self.assertFalse(rules.is_category(expense_account(account_type=kind)), kind)

	def test_the_companys_own_cost_of_goods_and_adjustment_accounts_are_not(self):
		cogs = expense_account("Goods Bought - A")
		self.assertTrue(rules.is_category(cogs))
		self.assertFalse(rules.is_category(cogs, excluded_accounts=["Goods Bought - A"]))

	def test_by_name_when_the_type_was_not_set(self):
		for name in ("Cost of Goods Sold - A", "Stock Adjustment - A", "Stock Received But Not Billed - A"):
			self.assertFalse(rules.is_category(expense_account(name, account_type=None)), name)

	def test_groups_disabled_frozen_and_other_roots_are_not(self):
		self.assertFalse(rules.is_category(expense_account(is_group=1)))
		self.assertFalse(rules.is_category(expense_account(disabled=1)))
		self.assertFalse(rules.is_category(expense_account(freeze_account="Yes")))
		self.assertFalse(rules.is_category(expense_account(root_type="Income")))
		self.assertFalse(rules.is_category(expense_account(root_type="Asset")))
		self.assertFalse(rules.is_category(None))

	def test_views_are_sorted_with_plain_labels_and_groups(self):
		views = rules.category_views(
			[
				expense_account("Rent - A"),
				expense_account("Airtime - A"),
				expense_account("COGS", account_type="Cost of Goods Sold"),
			],
			[],
			"A",
		)
		self.assertEqual([v["label"] for v in views], ["Airtime", "Rent"])
		self.assertEqual(views[0], {"id": "Airtime - A", "label": "Airtime", "group": "Indirect Expenses"})


class TestMarker(unittest.TestCase):
	def test_the_request_id(self):
		self.assertEqual(rules.clean_request_id(None), (None, True))
		self.assertEqual(rules.clean_request_id(""), (None, True))
		self.assertEqual(rules.clean_request_id("abc-12345"), ("abc-12345", True))
		self.assertEqual(rules.clean_request_id("short"), (None, False))
		self.assertEqual(rules.clean_request_id("has space in it"), (None, False))
		self.assertEqual(rules.clean_request_id("x]y" * 5), (None, False))

	def test_the_remark_carries_the_note_and_the_marker(self):
		self.assertEqual(rules.remark("Charcoal", "req-12345"), "Charcoal [expense:req-12345]")
		self.assertEqual(rules.remark("", "req-12345"), "[expense:req-12345]")
		self.assertEqual(rules.remark("Charcoal", None), "Charcoal [expense]")

	def test_the_remark_can_be_read_back(self):
		self.assertEqual(
			rules.parse_remark("Charcoal [expense:req-12345]"),
			{"is_expense": True, "request_id": "req-12345", "note": "Charcoal"},
		)
		self.assertEqual(
			rules.parse_remark("Charcoal [expense]"),
			{"is_expense": True, "request_id": None, "note": "Charcoal"},
		)
		self.assertEqual(rules.parse_remark("[expense:abc12345]")["note"], "")
		self.assertEqual(
			rules.parse_remark("Paid the plumber"),
			{"is_expense": False, "request_id": None, "note": "Paid the plumber"},
		)
		self.assertFalse(rules.parse_remark(None)["is_expense"])

	def test_the_search_pattern_finds_one_request_only(self):
		self.assertEqual(rules.marker_pattern("req-12345"), "%[expense:req-12345]%")
		self.assertEqual(rules.marker_pattern("a_b.12345"), "%[expense:a\\_b.12345]%")

	def test_the_other_note_is_short_and_plain(self):
		self.assertEqual(rules.other_note("Rent for <b>March</b>", "x"), "Rent for March")
		self.assertEqual(rules.other_note("", "Against Supplier Invoice X"), "Against Supplier Invoice X")
		self.assertEqual(rules.other_note(None, None), "")
		self.assertEqual(len(rules.other_note("a" * 300, None)), 140)


class TestReceipt(unittest.TestCase):
	def test_no_receipt_is_fine(self):
		for nothing in (None, "", {}):
			self.assertEqual(rules.clean_receipt(nothing), (None, None))

	def test_a_photo_is_read_from_a_data_url_or_an_object(self):
		for given in (
			data_url(png()),
			{"content": data_url(png())},
			{"content": base64.b64encode(png()).decode()},
		):
			photo, why = rules.clean_receipt(given)
			self.assertIsNone(why)
			self.assertEqual(photo["ext"], "png")
			self.assertEqual(photo["content_type"], "image/png")
			self.assertEqual(photo["content"], png())
			self.assertEqual(photo["size"], len(png()))

	def test_each_picture_type(self):
		samples = {
			"jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 10,
			"png": png(),
			"gif": b"GIF89a" + b"\x00" * 10,
			"webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ",
		}
		for ext, content in samples.items():
			self.assertEqual(rules.clean_receipt(data_url(content))[0]["ext"], ext)

	def test_the_file_type_is_judged_by_its_bytes_not_its_label(self):
		self.assertEqual(
			rules.clean_receipt(data_url(b"%PDF-1.4 hello", "image/png")), (None, "receipt_not_image")
		)
		self.assertEqual(
			rules.clean_receipt(data_url(b"<svg></svg>", "image/svg+xml")), (None, "receipt_not_image")
		)
		self.assertEqual(
			rules.clean_receipt(data_url(b"MZ\x90\x00", "image/jpeg")), (None, "receipt_not_image")
		)

	def test_five_megabytes_is_the_limit(self):
		exactly = png(b"\x00" * (rules.MAX_RECEIPT_BYTES - len(png())))
		self.assertEqual(len(exactly), rules.MAX_RECEIPT_BYTES)
		self.assertIsNone(rules.clean_receipt(data_url(exactly))[1])
		self.assertEqual(rules.clean_receipt(data_url(exactly + b"\x00")), (None, "receipt_too_big"))

	def test_a_huge_text_is_refused_before_it_is_decoded(self):
		self.assertEqual(rules.clean_receipt("A" * (rules.MAX_RECEIPT_BYTES * 2)), (None, "receipt_too_big"))

	def test_text_that_is_not_a_picture_at_all(self):
		for bad in (
			"not base64 !!",
			"data:image/png,rawtext",
			42,
			{"content": 5},
			{"content": ""},
			"data:image/png;base64,",
		):
			self.assertEqual(rules.clean_receipt(bad), (None, "bad_receipt"), bad)

	def test_the_data_url_for_showing_a_photo(self):
		url, kind = rules.receipt_data_url(png())
		self.assertEqual(kind, "image/png")
		self.assertTrue(url.startswith("data:image/png;base64,"))
		self.assertIsNone(rules.receipt_data_url(b"%PDF"))


class TestRows(unittest.TestCase):
	def raw(self, **extra):
		base = {
			"voucher_type": "Journal Entry",
			"voucher_no": "ACC-JV-2026-00007",
			"account": "Office Rent - A",
			"account_name": "Office Rent",
			"posting_date": date(2026, 10, 16),
			"amount": 1500,
			"remarks": "Note: Rent",
			"user_remark": "Rent for October [expense:req-12345]",
		}
		base.update(extra)
		return base

	def test_an_expense_made_here(self):
		options = rules.paid_from_options(
			[{"name": "Cash", "type": "Cash", "account": "Cash - A"}],
			{"Cash - A": account("c")},
			"Acme",
			"KES",
		)
		row = rules.row_view(
			self.raw(),
			credit_account="Cash - A",
			options=options,
			has_receipt=True,
			added_by="Amina",
			abbr="A",
		)
		self.assertEqual(row["kind"], "expense")
		self.assertEqual(row["date"], "2026-10-16")
		self.assertEqual(row["amount"], 1500.0)
		self.assertEqual(row["note"], "Rent for October")
		self.assertEqual(row["category"], {"id": "Office Rent - A", "label": "Office Rent"})
		self.assertEqual(row["paid_from"], {"key": "cash", "label": "Cash"})
		self.assertTrue(row["has_receipt"])
		self.assertEqual(row["added_by"], "Amina")
		self.assertEqual(row["reference"], "ACC-JV-2026-00007")
		self.assertEqual(row["id"], "ACC-JV-2026-00007")

	def test_another_entry_that_home_counts(self):
		row = rules.row_view(
			self.raw(
				voucher_type="Purchase Invoice",
				voucher_no="ACC-PINV-2026-00003",
				user_remark=None,
				remarks="Power bill",
			),
			credit_account="Cash - A",
			has_receipt=True,
			added_by="Amina",
		)
		self.assertEqual(row["kind"], "other")
		self.assertEqual(row["source"], "Supplier bill")
		self.assertEqual(row["note"], "Power bill")
		self.assertIsNone(row["paid_from"])
		self.assertFalse(row["has_receipt"])
		self.assertIsNone(row["added_by"])

	def test_a_journal_entry_made_by_hand_is_not_one_of_ours(self):
		row = rules.row_view(self.raw(user_remark="Year end adjustment"))
		self.assertEqual(row["kind"], "other")
		self.assertEqual(row["source"], "Journal entry")
		self.assertEqual(row["note"], "Year end adjustment")

	def test_a_refund_shows_as_a_negative_amount(self):
		self.assertEqual(rules.row_view(self.raw(amount=-200.0, user_remark=None))["amount"], -200.0)

	def test_source_names(self):
		self.assertEqual(rules.source_label("Payment Entry"), "Payment")
		self.assertEqual(rules.source_label("Weird Doc"), "Other entry")
		self.assertEqual(rules.source_label(None), "Other entry")

	def test_the_summary(self):
		self.assertEqual(
			rules.summary_view(4500.5, 3, {"id": "Rent - A", "label": "Rent", "amount": 3000}),
			{
				"total": 4500.5,
				"count": 3,
				"top_category": {"id": "Rent - A", "label": "Rent", "amount": 3000.0},
			},
		)
		self.assertEqual(rules.summary_view(0, 0, None), {"total": 0.0, "count": 0, "top_category": None})


class TestTheEntryLines(unittest.TestCase):
	def test_debit_the_category_and_credit_the_paid_from_account(self):
		lines = rules.journal_lines(1500.0, "Office Rent - A", "Cash - A", "Main - A")
		self.assertEqual(
			lines,
			[
				{
					"account": "Office Rent - A",
					"debit_in_account_currency": 1500.0,
					"credit_in_account_currency": 0,
					"cost_center": "Main - A",
				},
				{"account": "Cash - A", "debit_in_account_currency": 0, "credit_in_account_currency": 1500.0},
			],
		)

	def test_the_entry_balances(self):
		lines = rules.journal_lines(99.99, "X", "Y")
		self.assertEqual(
			sum(line["debit_in_account_currency"] for line in lines),
			sum(line["credit_in_account_currency"] for line in lines),
		)
		self.assertNotIn("cost_center", lines[0])


class TestHomeRuleIsMirrored(unittest.TestCase):
	"""The rule in this file is a copy of Home's; these tests fail if Home's changes and this one does not."""

	def test_the_excluded_types_are_the_ones_home_leaves_out(self):
		self.assertEqual(rules.HOME_EXCLUDED_TYPES, tuple(dashboard_api.NON_OPERATING_EXPENSE_TYPES))

	def test_every_type_home_leaves_out_is_also_kept_out_of_the_categories(self):
		for kind in dashboard_api.NON_OPERATING_EXPENSE_TYPES:
			self.assertIn(kind, rules.CATEGORY_EXCLUDED_TYPES)

	def test_the_rule(self):
		self.assertTrue(rules.counts_as_running_cost("Expense", "Expense Account", "Rent - A", ["COGS - A"]))
		self.assertTrue(rules.counts_as_running_cost("Expense", None, "Rent - A"))
		self.assertTrue(rules.counts_as_running_cost("Expense", "", "Rent - A"))
		self.assertFalse(rules.counts_as_running_cost("Expense", "Cost of Goods Sold", "COGS - A"))
		self.assertFalse(rules.counts_as_running_cost("Expense", "Expense Account", "COGS - A", ["COGS - A"]))
		self.assertFalse(rules.counts_as_running_cost("Asset", None, "Cash - A"))
		self.assertFalse(rules.counts_as_running_cost("Income", None, "Sales - A"))


class TestAC30NetProfitFallsByTheAmount(unittest.TestCase):
	"""
	AC-30: after an expense, Net profit for the period falls by the amount.

	Home's Net profit is gross profit minus the Running costs, and Running costs are the ledger lines on expense accounts
	that pass `counts_as_running_cost`. An expense made by create_expense posts exactly the lines of `journal_lines`, so:
	the debit line passes the rule, the credit line (an asset account) does not, and Running costs rise by the amount.
	"""

	ACCOUNTS: ClassVar = {
		"Office Rent - A": {"root_type": "Expense", "account_type": "Expense Account"},
		"Cash - A": {"root_type": "Asset", "account_type": "Cash"},
		"M-Pesa - A": {"root_type": "Asset", "account_type": "Bank"},
	}
	SALES: ClassVar = [{"net_amount": 100000, "is_stock_item": 1, "recorded_cost": 62000, "uncosted_qty": 0}]

	def net(self, running_costs):
		return summarize_profit(self.SALES, running_costs)["netProfit"]

	def test_the_debit_line_is_counted_by_the_rule_home_uses(self):
		debit, credit = rules.journal_lines(2500.0, "Office Rent - A", "Cash - A")
		meta = self.ACCOUNTS["Office Rent - A"]
		self.assertTrue(
			rules.counts_as_running_cost(meta["root_type"], meta["account_type"], debit["account"], [])
		)
		self.assertEqual(debit["debit_in_account_currency"] - debit["credit_in_account_currency"], 2500.0)
		cash = self.ACCOUNTS["Cash - A"]
		self.assertFalse(
			rules.counts_as_running_cost(cash["root_type"], cash["account_type"], credit["account"], [])
		)

	def test_running_costs_rise_by_the_amount(self):
		lines = rules.journal_lines(2500.0, "Office Rent - A", "M-Pesa - A")
		self.assertEqual(rules.running_costs_effect(lines, self.ACCOUNTS, []), 2500.0)

	def test_net_profit_falls_by_the_amount(self):
		before_costs = 15000.0
		lines = rules.journal_lines(2500.0, "Office Rent - A", "Cash - A")
		after_costs = before_costs + rules.running_costs_effect(lines, self.ACCOUNTS, [])
		self.assertEqual(self.net(before_costs), 23000.0)  # the Build Pack's worked example
		self.assertEqual(self.net(after_costs), 20500.0)
		self.assertEqual(round(self.net(before_costs) - self.net(after_costs), 2), 2500.0)

	def test_every_category_offered_is_one_home_counts(self):
		accounts = [
			expense_account("Rent - A"),
			expense_account("Airtime - A", account_type=None),
			expense_account("Power - A", account_type="Depreciation"),
		]
		for view in rules.category_views(accounts, [], "A"):
			meta = next(a for a in accounts if a["name"] == view["id"])
			self.assertTrue(
				rules.counts_as_running_cost(meta["root_type"], meta["account_type"], meta["name"], [])
			)

	def test_no_account_that_home_leaves_out_can_be_chosen_so_the_amount_cannot_go_missing(self):
		for kind in dashboard_api.NON_OPERATING_EXPENSE_TYPES:
			self.assertEqual(
				rules.category_views([expense_account("Odd - A", account_type=kind)], [], "A"), []
			)


if __name__ == "__main__":
	unittest.main()
