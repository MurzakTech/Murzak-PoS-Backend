# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Suppliers page calls (`suppliers_list_api`) and the rules behind them (`suppliers_list_calc`). They need
no database: every Frappe call is mocked, and all the figures are made up.

The first half tests the plain functions with plain rows: the request, the search, what the shop owes, the next bill due
(a late bill first), the Owed filter, the summary and paging. The second half tests the calls: who is let in, what a cashier
does and does not receive, that the list, the Owed filter, the summary and the panel read the same figures, that
Home's rule for "due soon" and this page's agree, and the shape of the queries.
"""

import ast
import inspect
import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import overview_calc as calc
from murzak_pos.api import suppliers_list_api as api
from murzak_pos.api import suppliers_list_calc as rules

D = date.fromisoformat
TODAY = D("2026-10-13")
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)


def bill(name, supplier, amount, due, bill_no=""):
	return {
		"name": name,
		"supplier": supplier,
		"outstanding_amount": amount,
		"due_date": due,
		"bill_no": bill_no,
	}


BILLS = [
	bill("PINV-1", "SUP-1", 12000, "2026-10-10", "A-100"),  # three days late
	bill("PINV-2", "SUP-1", 8000, "2026-10-20", "A-101"),
	bill("PINV-3", "SUP-2", 5000.5, "2026-10-15"),  # due in two days
	bill("PINV-4", "SUP-3", 40000, "2026-11-30"),
	bill("PINV-5", "SUP-4", 0, "2026-10-01"),  # paid
	bill("PINV-6", "SUP-5", 2500, None),  # no due date
]

RECORDS = {
	"SUP-1": {
		"name": "SUP-1",
		"supplier_name": "Bidco Distributors",
		"supplier_type": "Company",
		"supplier_group": "Goods",
		"tax_id": "P051234567A",
		"country": "Kenya",
		"default_currency": "KES",
		"disabled": 0,
		"is_internal_supplier": 0,
	},
	"SUP-2": {
		"name": "SUP-2",
		"supplier_name": "Mama Njeri Eggs",
		"supplier_type": "Individual",
		"supplier_group": "Farm",
		"tax_id": "",
		"country": "Kenya",
		"default_currency": "KES",
		"disabled": 0,
		"is_internal_supplier": 0,
	},
	"SUP-3": {
		"name": "SUP-3",
		"supplier_name": "Zawadi Packaging",
		"supplier_type": "Company",
		"supplier_group": "Goods",
		"tax_id": "P059999999Z",
		"country": "Kenya",
		"default_currency": "USD",
		"disabled": 1,
		"is_internal_supplier": 0,
	},
	"SUP-4": {
		"name": "SUP-4",
		"supplier_name": "Asha Flour Mill",
		"supplier_type": "Company",
		"supplier_group": "Goods",
		"tax_id": "",
		"country": "",
		"default_currency": "",
		"disabled": 0,
		"is_internal_supplier": 1,
	},
	"SUP-5": {
		"name": "SUP-5",
		"supplier_name": "No Date Traders",
		"supplier_type": "Company",
		"supplier_group": "Goods",
		"tax_id": "",
		"country": "Kenya",
		"default_currency": "KES",
		"disabled": 0,
		"is_internal_supplier": 0,
	},
}


def records():
	return [dict(r) for r in RECORDS.values()]


class TestTheRequest(unittest.TestCase):
	def test_the_owed_filter_is_off_by_default(self):
		for off in (None, False, "", "0", "false", "all", "no", 0):
			with self.subTest(value=off):
				self.assertFalse(rules.normalize_owed(off))

	def test_the_owed_filter_reads_the_words_a_web_request_sends(self):
		for on in (True, "1", "true", "True", "yes", "owed", 1, " TRUE "):
			with self.subTest(value=on):
				self.assertTrue(rules.normalize_owed(on))

	def test_any_other_owed_value_is_refused_with_a_code(self):
		with self.assertRaises(rules.ListError) as raised:
			rules.normalize_owed("maybe")
		self.assertEqual(raised.exception.code, "bad_owed")

	def test_the_status_defaults_to_active(self):
		self.assertEqual(rules.normalize_status(None), "active")
		self.assertEqual(rules.normalize_status("Disabled"), "disabled")
		self.assertEqual(rules.normalize_status("all"), "all")

	def test_the_owed_filter_shows_everyone_owed_unless_a_status_is_chosen(self):
		self.assertEqual(rules.normalize_status(None, True), "all")
		self.assertEqual(rules.normalize_status("", True), "all")
		self.assertEqual(rules.normalize_status("active", True), "active")

	def test_an_unknown_status_is_refused_with_a_code(self):
		with self.assertRaises(rules.ListError) as raised:
			rules.normalize_status("gone")
		self.assertEqual(raised.exception.code, "bad_status")

	def test_the_page_is_at_least_one(self):
		self.assertEqual(rules.clamp_page(3), 3)
		self.assertEqual(rules.clamp_page("2"), 2)
		for bad in (0, -4, "x", None):
			self.assertEqual(rules.clamp_page(bad), 1)

	def test_rows_per_page_is_never_blank_or_huge(self):
		self.assertEqual(rules.clamp_page_size(None), 20)
		self.assertEqual(rules.clamp_page_size("abc"), 20)
		self.assertEqual(rules.clamp_page_size(0), 20)
		self.assertEqual(rules.clamp_page_size(-5), 20)
		self.assertEqual(rules.clamp_page_size(50), 50)
		self.assertEqual(rules.clamp_page_size(5000), 100)

	def test_a_supplier_group_is_trimmed_or_none(self):
		self.assertIsNone(rules.clean_group(None))
		self.assertIsNone(rules.clean_group("   "))
		self.assertEqual(rules.clean_group(" Goods "), "Goods")


class TestTheSearch(unittest.TestCase):
	def test_an_empty_search_means_no_search(self):
		self.assertIsNone(rules.parse_search(None))
		self.assertIsNone(rules.parse_search("   "))

	def test_a_search_is_lower_case_with_one_space_between_words(self):
		self.assertEqual(rules.parse_search("  Mama   NJERI "), {"text": "mama njeri"})

	def test_a_search_is_cut_to_sixty_characters(self):
		self.assertEqual(len(rules.parse_search("a" * 200)["text"]), 60)

	def test_the_name_the_number_and_the_tax_id_are_searched(self):
		record = RECORDS["SUP-1"]
		for text in ("bidco", "sup-1", "p0512"):
			with self.subTest(text=text):
				self.assertTrue(rules.matches_search(record, rules.parse_search(text)))
		self.assertFalse(rules.matches_search(record, rules.parse_search("eggs")))

	def test_no_search_matches_everything(self):
		self.assertTrue(rules.matches_search(RECORDS["SUP-2"], None))

	def test_a_missing_tax_id_does_not_break_the_search(self):
		self.assertFalse(
			rules.matches_search({"name": "X", "supplier_name": None, "tax_id": None}, {"text": "q"})
		)

	def test_the_group_filter(self):
		self.assertTrue(rules.matches_group(RECORDS["SUP-2"], "Farm"))
		self.assertFalse(rules.matches_group(RECORDS["SUP-2"], "Goods"))
		self.assertTrue(rules.matches_group(RECORDS["SUP-2"], None))

	def test_the_status_filter(self):
		self.assertTrue(rules.matches_status(RECORDS["SUP-1"], "active"))
		self.assertFalse(rules.matches_status(RECORDS["SUP-3"], "active"))
		self.assertTrue(rules.matches_status(RECORDS["SUP-3"], "disabled"))
		self.assertTrue(rules.matches_status(RECORDS["SUP-3"], "all"))


class TestDueStates(unittest.TestCase):
	def test_before_today_is_overdue(self):
		self.assertEqual(rules.bill_state(D("2026-10-12"), TODAY), "overdue")

	def test_today_and_three_days_ahead_are_soon(self):
		self.assertEqual(rules.bill_state(D("2026-10-13"), TODAY), "soon")
		self.assertEqual(rules.bill_state(D("2026-10-16"), TODAY), "soon")

	def test_four_days_ahead_is_later(self):
		self.assertEqual(rules.bill_state(D("2026-10-17"), TODAY), "later")

	def test_no_due_date_has_no_state(self):
		self.assertIsNone(rules.bill_state(None, TODAY))

	def test_due_soon_is_overdue_or_soon(self):
		self.assertTrue(rules.is_due_soon(D("2026-09-01"), TODAY))
		self.assertTrue(rules.is_due_soon(D("2026-10-16"), TODAY))
		self.assertFalse(rules.is_due_soon(D("2026-10-17"), TODAY))
		self.assertFalse(rules.is_due_soon(None, TODAY))

	def test_the_window_is_the_one_home_uses(self):
		self.assertEqual(calc.BILL_WARNING_DAYS, 3)


class TestWhatTheShopOwes(unittest.TestCase):
	def setUp(self):
		self.figures = rules.figures_by_supplier(BILLS, TODAY)

	def test_owed_is_the_sum_of_a_suppliers_unpaid_bills(self):
		self.assertEqual(rules.owed_of(self.figures, "SUP-1"), 20000.0)
		self.assertEqual(rules.owed_of(self.figures, "SUP-2"), 5000.5)

	def test_a_paid_bill_is_not_owed(self):
		self.assertNotIn("SUP-4", self.figures)
		self.assertEqual(rules.owed_of(self.figures, "SUP-4"), 0.0)

	def test_a_supplier_never_billed_owes_nothing(self):
		self.assertEqual(rules.owed_of(self.figures, "SUP-99"), 0.0)

	def test_a_return_or_a_negative_balance_does_not_count(self):
		figures = rules.figures_by_supplier([bill("PINV-9", "SUP-1", -500, "2026-10-01")], TODAY)
		self.assertEqual(figures, {})

	def test_a_bill_with_no_supplier_is_dropped(self):
		self.assertEqual(rules.figures_by_supplier([bill("PINV-9", None, 500, "2026-10-01")], TODAY), {})

	def test_bad_amounts_are_ignored(self):
		rows = [bill("PINV-9", "SUP-1", "abc", "2026-10-01"), bill("PINV-8", "SUP-1", None, "2026-10-01")]
		self.assertEqual(rules.figures_by_supplier(rows, TODAY), {})

	def test_an_unreadable_due_date_counts_as_no_date(self):
		figures = rules.figures_by_supplier([bill("PINV-9", "SUP-1", 100, "soon")], TODAY)
		self.assertEqual(rules.owed_of(figures, "SUP-1"), 100.0)
		self.assertIsNone(rules.next_due(figures["SUP-1"]["bills"]))

	def test_owed_is_added_in_cents_so_pennies_do_not_drift(self):
		rows = [bill(f"PINV-{i}", "SUP-1", 0.1, "2026-10-20") for i in range(10)]
		self.assertEqual(rules.owed_of(rules.figures_by_supplier(rows, TODAY), "SUP-1"), 1.0)

	def test_a_string_amount_from_the_database_is_read(self):
		figures = rules.figures_by_supplier([bill("PINV-9", "SUP-1", "1250.50", "2026-10-20")], TODAY)
		self.assertEqual(rules.owed_of(figures, "SUP-1"), 1250.5)

	def test_only_limits_to_some_suppliers(self):
		figures = rules.figures_by_supplier(BILLS, TODAY, only={"SUP-2"})
		self.assertEqual(set(figures), {"SUP-2"})

	def test_the_suppliers_owed_are_the_ones_with_figures(self):
		self.assertEqual(rules.owed_suppliers(self.figures), {"SUP-1", "SUP-2", "SUP-3", "SUP-5"})

	def test_no_rows_gives_no_figures(self):
		self.assertEqual(rules.figures_by_supplier(None, TODAY), {})
		self.assertEqual(rules.figures_by_supplier([], TODAY), {})


class TestTheNextBillDue(unittest.TestCase):
	def setUp(self):
		self.figures = rules.figures_by_supplier(BILLS, TODAY)

	def test_a_late_bill_comes_before_a_later_one(self):
		due = rules.next_due(self.figures["SUP-1"]["bills"])
		self.assertEqual(due["invoice"], "PINV-1")
		self.assertEqual(due["date"], "2026-10-10")
		self.assertEqual(due["amount"], 12000.0)
		self.assertEqual(due["state"], "overdue")
		self.assertEqual(due["days"], -3)

	def test_the_amount_is_that_bills_amount_not_the_total(self):
		self.assertEqual(rules.next_due(self.figures["SUP-1"]["bills"])["amount"], 12000.0)
		self.assertEqual(rules.owed_of(self.figures, "SUP-1"), 20000.0)

	def test_a_bill_due_in_two_days_is_soon(self):
		due = rules.next_due(self.figures["SUP-2"]["bills"])
		self.assertEqual((due["state"], due["days"]), ("soon", 2))

	def test_a_bill_far_off_is_later(self):
		due = rules.next_due(self.figures["SUP-3"]["bills"])
		self.assertEqual(due["state"], "later")

	def test_the_bill_number_travels_with_it(self):
		self.assertEqual(rules.next_due(self.figures["SUP-1"]["bills"])["bill_no"], "A-100")

	def test_a_bill_with_no_due_date_is_never_the_next_due(self):
		self.assertIsNone(rules.next_due(self.figures["SUP-5"]["bills"]))
		self.assertEqual(rules.owed_of(self.figures, "SUP-5"), 2500.0)

	def test_a_dated_bill_beats_an_undated_one(self):
		rows = [bill("PINV-1", "S", 100, None), bill("PINV-2", "S", 50, "2027-01-01")]
		due = rules.next_due(rules.figures_by_supplier(rows, TODAY)["S"]["bills"])
		self.assertEqual(due["invoice"], "PINV-2")

	def test_two_bills_due_the_same_day_pick_the_bigger(self):
		rows = [bill("PINV-1", "S", 100, "2026-10-14"), bill("PINV-2", "S", 900, "2026-10-14")]
		self.assertEqual(
			rules.next_due(rules.figures_by_supplier(rows, TODAY)["S"]["bills"])["invoice"], "PINV-2"
		)

	def test_no_bills_means_no_next_due(self):
		self.assertIsNone(rules.next_due([]))

	def test_the_bills_are_listed_soonest_first(self):
		order = [b["invoice"] for b in self.figures["SUP-1"]["bills"]]
		self.assertEqual(order, ["PINV-1", "PINV-2"])

	def test_the_due_soon_count_has_late_and_soon_bills_only(self):
		self.assertEqual(self.figures["SUP-1"]["due_soon"], 1)
		self.assertEqual(self.figures["SUP-2"]["due_soon"], 1)
		self.assertEqual(self.figures["SUP-3"]["due_soon"], 0)
		self.assertEqual(self.figures["SUP-5"]["due_soon"], 0)


class TestTheRows(unittest.TestCase):
	def setUp(self):
		self.figures = rules.figures_by_supplier(BILLS, TODAY)

	def test_an_owner_row_has_the_money_fields(self):
		row = rules.supplier_row(RECORDS["SUP-1"], self.figures, True)
		self.assertEqual(row["owed"], 20000.0)
		self.assertEqual(row["next_due"]["date"], "2026-10-10")
		self.assertEqual(row["bills_due_soon"], 1)
		self.assertEqual(row["name"], "Bidco Distributors")
		self.assertEqual(row["type"], "Company")
		self.assertEqual(row["currency"], "KES")
		self.assertFalse(row["disabled"])

	def test_a_cashier_row_has_no_money_field_at_all(self):
		row = rules.supplier_row(RECORDS["SUP-1"], self.figures, False)
		for key in ("owed", "next_due", "bills_due_soon"):
			self.assertNotIn(key, row)
		self.assertNotIn("20000", json.dumps(row))

	def test_a_supplier_owed_nothing_has_zero_and_no_next_due(self):
		row = rules.supplier_row(RECORDS["SUP-4"], self.figures, True)
		self.assertEqual((row["owed"], row["next_due"], row["bills_due_soon"]), (0.0, None, 0))

	def test_a_missing_name_falls_back_to_the_number(self):
		row = rules.supplier_row({"name": "SUP-7", "supplier_name": ""}, {}, False)
		self.assertEqual(row["name"], "SUP-7")

	def test_disabled_is_a_real_boolean(self):
		self.assertTrue(rules.supplier_row(RECORDS["SUP-3"], {}, False)["disabled"])
		self.assertFalse(rules.supplier_row({"name": "X", "disabled": "0"}, {}, False)["disabled"] is True)


class TestThePanelRules(unittest.TestCase):
	def setUp(self):
		self.figures = rules.figures_by_supplier(BILLS, TODAY)

	def test_the_panel_lists_the_unpaid_bills_soonest_first(self):
		data = rules.panel(RECORDS["SUP-1"], self.figures, True)
		self.assertEqual([b["invoice"] for b in data["bills"]], ["PINV-1", "PINV-2"])
		self.assertEqual(data["bills"][0]["outstanding"], 12000.0)
		self.assertEqual(data["bills"][0]["due_date"], "2026-10-10")
		self.assertEqual(data["bills"][0]["state"], "overdue")
		self.assertEqual(data["bill_count"], 2)

	def test_the_panel_owed_equals_the_sum_of_its_bills(self):
		data = rules.panel(RECORDS["SUP-1"], self.figures, True)
		self.assertEqual(data["owed"], round(sum(b["outstanding"] for b in data["bills"]), 2))

	def test_the_panel_says_how_many_bills_when_the_list_is_cut(self):
		rows = [bill(f"PINV-{i}", "S", 10, "2026-12-01") for i in range(30)]
		figures = rules.figures_by_supplier(rows, TODAY)
		data = rules.panel({"name": "S"}, figures, True)
		self.assertEqual(len(data["bills"]), 20)
		self.assertEqual(data["bill_count"], 30)
		self.assertEqual(data["owed"], 300.0)

	def test_a_cashier_panel_has_the_facts_and_nothing_owed(self):
		data = rules.panel(RECORDS["SUP-1"], self.figures, False)
		for key in ("owed", "next_due", "bills", "bill_count", "bills_due_soon"):
			self.assertNotIn(key, data)
		self.assertEqual(data["tax_id"], "P051234567A")
		self.assertEqual(data["group"], "Goods")

	def test_the_panel_has_the_internal_flag(self):
		self.assertTrue(rules.panel(RECORDS["SUP-4"], self.figures, False)["internal"])
		self.assertFalse(rules.panel(RECORDS["SUP-1"], self.figures, False)["internal"])

	def test_a_supplier_owed_nothing_has_an_empty_bill_list(self):
		data = rules.panel(RECORDS["SUP-4"], self.figures, True)
		self.assertEqual(
			(data["owed"], data["next_due"], data["bills"], data["bill_count"]), (0.0, None, [], 0)
		)


class TestTheOwedFilterAndTheSummary(unittest.TestCase):
	def setUp(self):
		self.figures = rules.figures_by_supplier(BILLS, TODAY)

	def select(self, **kwargs):
		args = {"search": None, "group": None, "status": "all", "owed": False}
		args.update(kwargs)
		return rules.select_records(
			records(),
			rules.parse_search(args["search"]),
			args["group"],
			args["status"],
			args["owed"],
			self.figures,
		)

	def test_the_owed_filter_lists_only_suppliers_owed(self):
		self.assertEqual({r["name"] for r in self.select(owed=True)}, {"SUP-1", "SUP-2", "SUP-3", "SUP-5"})

	def test_the_owed_filter_puts_the_biggest_debt_first(self):
		self.assertEqual([r["name"] for r in self.select(owed=True)], ["SUP-3", "SUP-1", "SUP-2", "SUP-5"])

	def test_the_plain_list_is_by_name(self):
		names = [r["supplier_name"] for r in self.select()]
		self.assertEqual(names, sorted(names, key=str.lower))

	def test_the_owed_total_equals_the_sum_of_the_owed_rows(self):
		rows = [rules.supplier_row(r, self.figures, True) for r in self.select(owed=True)]
		total = rules.summary(self.figures, 4, True)["owed_total"]
		self.assertEqual(round(sum(r["owed"] for r in rows), 2), total)

	def test_the_owed_count_equals_the_summary_suppliers_owed(self):
		self.assertEqual(len(self.select(owed=True)), rules.summary(self.figures, 4, True)["suppliers_owed"])

	def test_the_summary_has_four_numbers_for_an_owner(self):
		summary = rules.summary(self.figures, 4, True)
		self.assertEqual(summary["suppliers"], 4)
		self.assertEqual(summary["owed_total"], 20000 + 5000.5 + 40000 + 2500)
		self.assertEqual(summary["suppliers_owed"], 4)
		self.assertEqual(summary["bills_due_soon"], 2)

	def test_the_summary_has_only_the_count_for_a_cashier(self):
		self.assertEqual(rules.summary({}, 4, False), {"suppliers": 4})

	def test_a_filter_narrows_the_owed_list_too(self):
		self.assertEqual([r["name"] for r in self.select(owed=True, group="Farm")], ["SUP-2"])
		self.assertEqual([r["name"] for r in self.select(owed=True, search="zawadi")], ["SUP-3"])

	def test_the_owed_filter_with_a_status_hides_the_other_status(self):
		names = {r["name"] for r in self.select(owed=True, status="active")}
		self.assertNotIn("SUP-3", names)

	def test_search_finds_a_supplier_by_part_of_the_name(self):
		self.assertEqual([r["name"] for r in self.select(search="njeri")], ["SUP-2"])

	def test_search_with_no_match_gives_nothing(self):
		self.assertEqual(self.select(search="zzzz"), [])

	def test_the_summary_due_soon_equals_homes_rule(self):
		"""Home lists the unpaid bills due within 3 days or late; this page counts the same bills."""
		home = calc.supplier_bill_items([dict(b) for b in BILLS], TODAY)
		self.assertEqual(rules.summary(self.figures, 4, True)["bills_due_soon"], len(home))

	def test_each_suppliers_due_soon_matches_homes_items(self):
		home = calc.supplier_bill_items([dict(b) for b in BILLS], TODAY)
		by_invoice = {i["invoice"] for i in home}
		mine = {
			b["invoice"]
			for f in self.figures.values()
			for b in f["bills"]
			if b["state"] in ("overdue", "soon")
		}
		self.assertEqual(by_invoice, mine)


class TestPaging(unittest.TestCase):
	def test_twenty_five_items_make_two_pages(self):
		items = list(range(45))
		page, info = rules.page_slice(items, 1, 20)
		self.assertEqual(len(page), 20)
		self.assertEqual(info, {"page": 1, "page_size": 20, "pages": 3, "total": 45})

	def test_the_last_page_is_short(self):
		page, info = rules.page_slice(list(range(45)), 3, 20)
		self.assertEqual(len(page), 5)
		self.assertEqual(info["page"], 3)

	def test_a_page_past_the_end_clamps_to_the_last_page(self):
		page, info = rules.page_slice(list(range(45)), 99, 20)
		self.assertEqual(info["page"], 3)
		self.assertEqual(len(page), 5)

	def test_an_empty_list_has_one_page(self):
		page, info = rules.page_slice([], 4, 20)
		self.assertEqual((page, info["page"], info["pages"], info["total"]), ([], 1, 1, 0))


# --- the calls --------------------------------------------------------------------------------------


class ApiCase(unittest.TestCase):
	"""A signed-in user, every database read replaced by fixed data."""

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, user="owner@shop-a.test", roles=("System Manager",), supplier_owner="Shop A"):
		db = MagicMock()
		db.get_value.return_value = supplier_owner
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
		self.start((api.supplier_api, "_visible_supplier_names"), return_value=set(RECORDS))

	def stub(self, name, value=None, function=None):
		if function:
			return self.start((api, name), side_effect=function)
		return self.start((api, name), return_value=value)


class ListCase(ApiCase):
	def wire(self):
		self.bill_calls = []

		def bills(company, supplier=None):
			self.bill_calls.append((company, supplier))
			return [dict(b) for b in BILLS if supplier is None or b["supplier"] == supplier]

		self.stub("_bill_rows", function=bills)
		self.stub("_supplier_records", function=lambda company: records())

	def get(self, **kwargs):
		return api.list_suppliers_overview(**kwargs)

	def data(self, **kwargs):
		result = self.get(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]


class TestTheListForAnOwner(ListCase):
	def setUp(self):
		self.as_user()
		self.wire()

	def test_the_default_list_is_the_active_suppliers_by_name(self):
		data = self.data()
		self.assertEqual([r["id"] for r in data["rows"]], ["SUP-4", "SUP-1", "SUP-2", "SUP-5"])
		self.assertEqual(data["total"], 4)
		self.assertEqual(data["role"], "owner")

	def test_the_page_info_is_exact(self):
		data = self.data()
		self.assertEqual((data["page"], data["page_size"], data["pages"]), (1, 20, 1))

	def test_rows_per_page_is_applied(self):
		data = self.data(page_size=2)
		self.assertEqual(len(data["rows"]), 2)
		self.assertEqual((data["pages"], data["total"]), (2, 4))

	def test_the_second_page_continues_the_list(self):
		first = self.data(page_size=2)["rows"]
		second = self.data(page_size=2, page=2)["rows"]
		self.assertEqual(len(first + second), 4)
		self.assertEqual(len({r["id"] for r in first + second}), 4)

	def test_a_page_past_the_end_gives_the_last_page(self):
		data = self.data(page_size=2, page=9)
		self.assertEqual(data["page"], 2)

	def test_the_owed_filter_lists_everyone_owed_including_a_disabled_supplier(self):
		data = self.data(owed="true")
		self.assertEqual([r["id"] for r in data["rows"]], ["SUP-3", "SUP-1", "SUP-2", "SUP-5"])
		self.assertEqual(data["total"], 4)

	def test_the_owed_total_equals_the_summary_and_the_sum_of_the_rows(self):
		data = self.data(owed=True)
		self.assertEqual(data["total"], data["summary"]["suppliers_owed"])
		self.assertEqual(round(sum(r["owed"] for r in data["rows"]), 2), data["summary"]["owed_total"])

	def test_the_owed_total_equals_the_sum_across_pages(self):
		pages = [self.data(owed=True, page_size=1, page=n)["rows"] for n in range(1, 5)]
		owed = [r["owed"] for rows in pages for r in rows]
		self.assertEqual(round(sum(owed), 2), self.data()["summary"]["owed_total"])

	def test_the_summary_does_not_change_with_the_filters(self):
		plain = self.data()["summary"]
		self.assertEqual(self.data(owed=True)["summary"], plain)
		self.assertEqual(self.data(search="njeri")["summary"], plain)

	def test_the_summary_counts_active_suppliers(self):
		self.assertEqual(self.data()["summary"]["suppliers"], 4)

	def test_a_search_narrows_the_list_and_the_total(self):
		data = self.data(search="bidco")
		self.assertEqual([r["id"] for r in data["rows"]], ["SUP-1"])
		self.assertEqual(data["total"], 1)

	def test_the_supplier_group_filter(self):
		self.assertEqual([r["id"] for r in self.data(supplier_group="Farm")["rows"]], ["SUP-2"])

	def test_the_disabled_status(self):
		self.assertEqual([r["id"] for r in self.data(status="disabled")["rows"]], ["SUP-3"])

	def test_an_empty_result_is_a_success_with_no_rows(self):
		data = self.data(search="nobody")
		self.assertEqual((data["rows"], data["total"], data["pages"]), ([], 0, 1))

	def test_the_rows_carry_owed_and_next_due(self):
		row = self.data(owed=True)["rows"][1]
		self.assertEqual(row["id"], "SUP-1")
		self.assertEqual(row["owed"], 20000.0)
		self.assertEqual(row["next_due"]["invoice"], "PINV-1")

	def test_the_bills_are_read_once_for_the_whole_page(self):
		self.data()
		self.assertEqual(self.bill_calls, [("Shop A", None)])

	def test_a_bad_status_is_a_friendly_refusal(self):
		with self.assertRaises(frappe.ValidationError):
			self.get(status="gone")

	def test_a_bad_owed_value_is_a_friendly_refusal(self):
		with self.assertRaises(frappe.ValidationError):
			self.get(owed="maybe")

	def test_a_manager_sees_the_money(self):
		self.as_user("boss@shop-a.test", roles=("Purchase Manager",))
		self.assertIn("owed", self.data()["rows"][0])

	def test_a_bill_of_a_supplier_the_business_does_not_see_is_left_out_of_every_figure(self):
		self.stub(
			"_bill_rows",
			function=lambda company, supplier=None: [dict(b) for b in BILLS]
			+ [bill("PINV-X", "SUP-OTHER", 99999, "2026-10-01")],
		)
		data = self.data(owed=True)
		self.assertEqual(data["summary"]["owed_total"], 67500.5)
		self.assertEqual(round(sum(r["owed"] for r in data["rows"]), 2), 67500.5)

	def test_a_read_that_fails_gives_a_friendly_message_without_detail(self):
		self.stub("_supplier_records", function=lambda company: (_ for _ in ()).throw(RuntimeError("secret")))
		result = self.get()
		self.assertFalse(result["success"])
		self.assertNotIn("secret", json.dumps(result))
		self.assertNotIn("traceback", json.dumps(result))


class TestTheListForACashier(ListCase):
	def setUp(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.wire()

	def test_no_money_field_appears_anywhere(self):
		text = json.dumps(self.data())
		for word in ("owed", "next_due", "bills_due_soon", "outstanding", "amount"):
			with self.subTest(word=word):
				self.assertNotIn(word, text)

	def test_the_suppliers_are_still_listed(self):
		data = self.data()
		self.assertEqual(len(data["rows"]), 4)
		self.assertEqual(data["summary"], {"suppliers": 4})
		self.assertEqual(data["role"], "cashier")

	def test_the_bills_are_never_read(self):
		self.data()
		self.assertEqual(self.bill_calls, [])

	def test_the_owed_filter_is_refused_and_nothing_is_read(self):
		with self.assertRaises(frappe.PermissionError):
			self.get(owed=True)
		self.assertEqual(self.bill_calls, [])


class TestWhoIsLetIn(ApiCase):
	def test_a_guest_is_refused_and_nothing_is_read(self):
		self.as_user("Guest", roles=("Guest",))
		reader = self.stub("_supplier_records", [])
		with self.assertRaises(frappe.AuthenticationError):
			api.list_suppliers_overview()
		reader.assert_not_called()

	def test_a_guest_cannot_open_a_supplier(self):
		self.as_user("Guest", roles=("Guest",))
		with self.assertRaises(frappe.AuthenticationError):
			api.get_supplier_overview("SUP-1")

	def test_a_supplier_of_another_business_is_refused_by_the_panel(self):
		self.as_user(supplier_owner="Shop B")
		bills = self.stub("_bill_rows", [])
		with self.assertRaises(frappe.PermissionError):
			api.get_supplier_overview("SUP-1")
		bills.assert_not_called()


class TestThePanel(ApiCase):
	def wire(self, roles=("System Manager",)):
		self.as_user(roles=roles)
		self.bill_calls = []

		def bills(company, supplier=None):
			self.bill_calls.append((company, supplier))
			return [dict(b) for b in BILLS if supplier is None or b["supplier"] == supplier]

		self.stub("_bill_rows", function=bills)
		self.db.get_value.side_effect = lambda doctype, name, fields=None, as_dict=False: (
			dict(RECORDS[name]) if isinstance(fields, list) and name in RECORDS else "Shop A"
		)

	def get(self, supplier="SUP-1"):
		return api.get_supplier_overview(supplier)

	def test_an_owner_gets_the_facts_the_balance_and_the_bills(self):
		self.wire()
		result = self.get()
		self.assertTrue(result["success"], result)
		data = result["data"]
		self.assertEqual(data["name"], "Bidco Distributors")
		self.assertEqual(data["tax_id"], "P051234567A")
		self.assertEqual(data["owed"], 20000.0)
		self.assertEqual(data["next_due"]["invoice"], "PINV-1")
		self.assertEqual([b["invoice"] for b in data["bills"]], ["PINV-1", "PINV-2"])

	def test_only_this_suppliers_bills_are_read(self):
		self.wire()
		self.get()
		self.assertEqual(self.bill_calls, [("Shop A", "SUP-1")])

	def test_the_panel_balance_equals_the_list_balance(self):
		self.wire()
		self.stub("_supplier_records", function=lambda company: records())
		listed = {r["id"]: r for r in api.list_suppliers_overview(owed=True)["data"]["rows"]}
		for supplier in listed:
			with self.subTest(supplier=supplier):
				self.assertEqual(self.get(supplier)["data"]["owed"], listed[supplier]["owed"])
				self.assertEqual(self.get(supplier)["data"]["next_due"], listed[supplier]["next_due"])

	def test_a_cashier_gets_the_facts_and_no_money(self):
		self.wire(("Sales User",))
		data = self.get()["data"]
		text = json.dumps(data)
		for word in ("owed", "next_due", "bills", "outstanding"):
			self.assertNotIn(word, text)
		self.assertEqual(data["name"], "Bidco Distributors")
		self.assertEqual(self.bill_calls, [])

	def test_a_supplier_that_does_not_exist_is_said_plainly(self):
		self.wire()
		result = self.get("SUP-404")
		self.assertFalse(result["success"])
		self.assertIn("SUP-404", result["message"])

	def test_a_supplier_the_business_does_not_see_is_not_found(self):
		self.wire()
		self.start((api.supplier_api, "_visible_supplier_names"), return_value={"SUP-2"})
		self.assertFalse(self.get("SUP-1")["success"])

	def test_the_administrator_with_no_limit_can_open_any_supplier(self):
		self.wire()
		self.start((api.supplier_api, "_visible_supplier_names"), return_value=None)
		self.assertTrue(self.get("SUP-1")["success"])


class TestTheQueries(ApiCase):
	def setUp(self):
		self.as_user()
		self.sql = self.db.sql
		self.sql.return_value = []

	def test_the_bill_query_uses_homes_conditions(self):
		api._bill_rows("Shop A")
		text = self.sql.call_args[0][0]
		for condition in (
			"docstatus = 1",
			"is_return = 0",
			"outstanding_amount > 0",
			"company = %(company)s",
		):
			self.assertIn(condition, text)
		self.assertIn("`tabPurchase Invoice`", text)

	def test_the_bill_query_is_capped_and_ordered(self):
		api._bill_rows("Shop A")
		text = self.sql.call_args[0][0]
		self.assertIn("LIMIT %(cap)s", text)
		self.assertIn("ORDER BY due_date", text)

	def test_a_supplier_is_passed_as_a_value_never_in_the_text(self):
		api._bill_rows("Shop A", "SUP-1'; DROP TABLE x; --")
		text, values = self.sql.call_args[0][:2]
		self.assertNotIn("DROP", text)
		self.assertEqual(values["supplier"], "SUP-1'; DROP TABLE x; --")

	def test_the_company_is_passed_as_a_value(self):
		api._bill_rows("Shop A")
		self.assertEqual(self.sql.call_args[0][1]["company"], "Shop A")

	def test_no_visible_suppliers_means_no_read(self):
		self.start((api.supplier_api, "_visible_supplier_names"), return_value=set())
		get_all = self.start((frappe, "get_all"), create=True, return_value=[])
		self.assertEqual(api._supplier_records("Shop A"), [])
		get_all.assert_not_called()

	def test_the_suppliers_read_asks_only_for_visible_ones(self):
		get_all = self.start((frappe, "get_all"), create=True, return_value=[])
		api._supplier_records("Shop A")
		filters = get_all.call_args.kwargs["filters"]
		self.assertEqual(sorted(filters["name"][1]), sorted(RECORDS))


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	CALLS = ("list_suppliers_overview", "get_supplier_overview")

	def test_every_call_is_whitelisted(self):
		for name in self.CALLS:
			with self.subTest(call=name):
				self.assertIn(getattr(api, name), frappe.whitelisted)

	def test_the_owed_filter_refuses_a_cashier_before_it_reads_anything(self):
		tree = ast.parse(inspect.getsource(api))
		function = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "list_suppliers_overview"
		)
		statements = [
			ast.unparse(s)
			for s in function.body
			if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		]
		joined = "\n".join(statements)
		self.assertIn("role_tiers.require_access('money_owed')", joined)
		self.assertLess(joined.index("require_access"), joined.index("_list_page"))

	def test_the_panel_checks_the_record_before_it_reads_anything(self):
		tree = ast.parse(inspect.getsource(api))
		function = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_supplier_overview"
		)
		first_calls = [ast.unparse(s) for s in function.body[:3]]
		self.assertIn("ensure_record_access('Supplier', supplier)", first_calls)

	def test_the_pure_module_needs_no_database_and_no_frappe(self):
		self.assertNotIn("import frappe", inspect.getsource(rules))
		self.assertNotIn("frappe.", inspect.getsource(rules))

	def test_the_rules_import_homes_constants_instead_of_copying_them(self):
		self.assertIn("calc.BILL_WARNING_DAYS", inspect.getsource(rules))

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
