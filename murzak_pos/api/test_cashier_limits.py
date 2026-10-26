# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""A cashier's limits on discounts and sale dates. Needs no database."""

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import cashier_limits, cashier_rules, role_tiers


def line(code="SOAP", qty=1, list_price=100, rate=100, shop=0):
	return {
		"item_code": code,
		"qty": qty,
		"list_price": list_price,
		"rate": rate,
		"shop_discount_percent": shop,
	}


class TestDiscountRules(unittest.TestCase):
	def test_up_to_the_limit_is_fine(self):
		self.assertEqual(cashier_rules.discount_problems([line(rate=90)], 0, 10), [])
		self.assertEqual(cashier_rules.discount_problems([line(qty=2)], 20, 10), [])

	def test_a_line_more_than_the_limit_below_its_price_is_refused(self):
		self.assertEqual(cashier_rules.discount_problems([line(rate=89)], 0, 10), ["SOAP", "sale"])

	def test_discounts_cannot_be_stacked_past_the_limit(self):
		# 8% off the line and another 8% off the whole sale
		self.assertEqual(cashier_rules.discount_problems([line(rate=92)], 7.36, 10), ["sale"])

	def test_the_shops_own_discount_does_not_count(self):
		self.assertEqual(cashier_rules.discount_problems([line(rate=70, shop=25)], 0, 10), [])
		self.assertEqual(cashier_rules.discount_problems([line(rate=60, shop=25)], 0, 10), ["SOAP", "sale"])

	def test_a_line_with_no_list_price_or_a_higher_price_is_left_alone(self):
		self.assertEqual(cashier_rules.discount_problems([line(list_price=0, rate=1)], 0, 10), [])
		self.assertEqual(cashier_rules.discount_problems([line(rate=150)], 0, 10), [])


class TestFullPriceCeiling(unittest.TestCase):
	def line(self, **over):
		return {"item_code": "SOAP", "qty": 1, "rate": 100, "discount_percentage": 0, **over}

	def test_up_to_and_including_the_whole_price_is_allowed(self):
		self.assertEqual(cashier_rules.full_price_problems([self.line()], 0), [])
		self.assertEqual(cashier_rules.full_price_problems([self.line(rate=0, discount_percentage=100)], 0), [])
		self.assertEqual(cashier_rules.full_price_problems([self.line()], 100, 100), [])

	def test_a_line_discounted_past_100_percent_or_priced_below_zero_is_refused(self):
		self.assertEqual(cashier_rules.full_price_problems([self.line(discount_percentage=150)], 0), ["SOAP"])
		self.assertEqual(cashier_rules.full_price_problems([self.line(rate=-795)], 0), ["SOAP"])

	def test_a_sale_discount_past_the_lines_or_past_100_percent_is_refused(self):
		self.assertEqual(cashier_rules.full_price_problems([self.line()], 101), ["sale"])
		self.assertEqual(cashier_rules.full_price_problems([self.line()], 0, 150), ["sale"])


class TestDateRules(unittest.TestCase):
	TODAY = date(2026, 10, 24)

	def test_today_and_up_to_a_week_back_are_fine(self):
		for day in (24, 20, 17):
			self.assertIsNone(cashier_rules.date_problem(date(2026, 10, day), self.TODAY))

	def test_the_future_and_more_than_a_week_back_are_refused(self):
		self.assertEqual(cashier_rules.date_problem(date(2026, 10, 25), self.TODAY), "future")
		self.assertEqual(cashier_rules.date_problem(date(2026, 10, 16), self.TODAY), "too_old")


def sale(posting_date="2026-10-24", rate=100, discount_amount=0, **flags):
	item = frappe._dict(
		item_code="SOAP", qty=1, price_list_rate=100, rate=rate, pricing_rules="", warehouse="Main"
	)
	doc = frappe._dict(
		company="Shop A", posting_date=posting_date, items=[item], discount_amount=discount_amount
	)
	doc.update(flags)
	return doc


class TestTheCheckOnSave(unittest.TestCase):
	def given(self, tier=role_tiers.CASHIER):
		db = MagicMock()
		db.get_default.return_value = None
		db.get_value.return_value = "Bath soap"
		for p in (
			patch.object(frappe, "session", frappe._dict(user="till@shop.test"), create=True),
			patch.object(frappe, "flags", frappe._dict(), create=True),
			patch.object(frappe, "db", db, create=True),
			patch.object(role_tiers, "get_tier", return_value=tier),
			patch.object(cashier_limits, "nowdate", return_value="2026-10-24"),
			patch.object(cashier_limits, "_batch_rule_percent", return_value=0.0),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_a_cashier_over_the_limit_is_refused(self):
		self.given()
		with self.assertRaises(frappe.ValidationError) as refused:
			cashier_limits.enforce_on_sale(sale(rate=80))
		self.assertIn("Bath soap", str(refused.exception))
		with self.assertRaises(frappe.ValidationError):
			cashier_limits.enforce_on_sale(sale(discount_amount=15))

	def test_a_cashier_within_the_limit_passes(self):
		self.given()
		cashier_limits.enforce_on_sale(sale(rate=95))

	def test_dates_outside_the_window_are_refused_for_a_cashier(self):
		self.given()
		for day in ("2026-10-25", "2026-10-10"):
			with self.subTest(day=day), self.assertRaises(frappe.ValidationError):
				cashier_limits.enforce_on_sale(sale(posting_date=day))

	def test_owners_and_managers_may_go_beyond(self):
		for tier in (role_tiers.OWNER, role_tiers.MANAGER):
			with self.subTest(tier=tier):
				self.given(tier)
				cashier_limits.enforce_on_sale(sale(rate=10, posting_date="2026-09-01"))

	def test_returns_and_shift_closing_invoices_are_not_checked(self):
		self.given()
		cashier_limits.enforce_on_sale(sale(rate=10, is_return=1))
		cashier_limits.enforce_on_sale(sale(rate=10, is_consolidated=1))

	def test_the_owner_can_change_the_limit(self):
		self.given()
		frappe.db.get_default.return_value = "25"
		cashier_limits.enforce_on_sale(sale(rate=80))

	def test_nobody_may_take_more_than_the_whole_price_off(self):
		for tier in (role_tiers.CASHIER, role_tiers.MANAGER, role_tiers.OWNER):
			with self.subTest(tier=tier):
				self.given(tier)
				too_much = sale(rate=0)
				too_much["items"][0].discount_percentage = 150
				with self.assertRaises(frappe.ValidationError) as refused:
					cashier_limits.enforce_on_sale(too_much)
				self.assertIn("100%", str(refused.exception))
				with self.assertRaises(frappe.ValidationError):
					cashier_limits.enforce_on_sale(sale(discount_amount=150, additional_discount_percentage=150))

	def test_an_owner_may_still_give_an_item_away(self):
		self.given(role_tiers.OWNER)
		free = sale(rate=0)
		free["items"][0].discount_percentage = 100
		cashier_limits.enforce_on_sale(free)

	def test_the_ceiling_leaves_returns_alone(self):
		self.given(role_tiers.OWNER)
		cashier_limits.enforce_on_sale(sale(rate=-50, is_return=1))

	def test_the_hooks_are_registered(self):
		from murzak_pos import hooks

		for doctype in ("Sales Invoice", "POS Invoice"):
			self.assertEqual(
				hooks.doc_events[doctype]["validate"], "murzak_pos.api.cashier_limits.enforce_on_sale"
			)


if __name__ == "__main__":
	unittest.main()
