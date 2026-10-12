# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for sales people and commission. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_person_api as api


class MoneyCase(unittest.TestCase):
	def test_commission_is_a_percentage_of_the_amount(self):
		self.assertEqual(api.commission(2000, 5), 100)
		self.assertEqual(api.commission(0, 5), 0)

	def test_a_rate_outside_zero_to_hundred_is_refused(self):
		for rate in (-1, 100.5):
			with self.assertRaises(frappe.ValidationError):
				api.checked_rate(rate)
		self.assertEqual(api.checked_rate("2.5"), 2.5)


class SummaryCase(unittest.TestCase):
	def rows(self):
		return [
			{
				"sales_person": "Jane",
				"invoice": "S1",
				"is_return": 0,
				"allocated_amount": 1000,
				"commission_rate": 5,
			},
			{
				"sales_person": "Jane",
				"invoice": "S2",
				"is_return": 0,
				"allocated_amount": 3000,
				"commission_rate": "5",
			},
			{
				"sales_person": "Jane",
				"invoice": "R1",
				"is_return": 1,
				"allocated_amount": -500,
				"commission_rate": 5,
			},
			{
				"sales_person": "Joe",
				"invoice": "S3",
				"is_return": 0,
				"allocated_amount": 800,
				"commission_rate": 0,
			},
		]

	def test_returns_reduce_the_net_sales_and_the_commission(self):
		jane = next(r for r in api.summarise(self.rows()) if r["sales_person"] == "Jane")
		self.assertEqual(jane["sales"], 4000)
		self.assertEqual(jane["returns"], 500)
		self.assertEqual(jane["net_sales"], 3500)
		self.assertEqual(jane["commission"], 175)
		self.assertEqual(jane["invoices"], 3)

	def test_people_are_listed_best_seller_first(self):
		self.assertEqual([r["sales_person"] for r in api.summarise(self.rows())], ["Jane", "Joe"])

	def test_a_person_with_no_commission_rate_earns_nothing(self):
		joe = next(r for r in api.summarise(self.rows()) if r["sales_person"] == "Joe")
		self.assertEqual(joe["commission"], 0)

	def test_nothing_sold_is_an_empty_report(self):
		self.assertEqual(api.summarise([]), [])


def _db(found=None, linked=None):
	db = MagicMock()

	def get_value(doctype, filters, fieldname=None, as_dict=False):
		if isinstance(filters, str):
			return found
		return linked

	db.get_value.side_effect = get_value
	return db


class CreditingCase(unittest.TestCase):
	def doc(self):
		doc = MagicMock()
		doc.company = "Acme"
		return doc

	def person(self, company="Acme", enabled=1, rate=5):
		return frappe._dict(name="Jane", custom_company=company, commission_rate=rate, enabled=enabled)

	def test_a_named_person_is_credited_with_the_whole_sale(self):
		doc = self.doc()
		with patch.object(api.frappe, "db", _db(found=self.person())):
			api.attach(doc, "Jane")
		doc.append.assert_called_once_with(
			"sales_team", {"sales_person": "Jane", "allocated_percentage": 100, "commission_rate": 5}
		)
		self.assertEqual(doc.commission_rate, 5)

	def test_a_person_of_another_business_is_refused(self):
		with patch.object(api.frappe, "db", _db(found=self.person(company="Other"))):
			with self.assertRaises(frappe.ValidationError):
				api.attach(self.doc(), "Jane")

	def test_a_person_switched_off_is_refused(self):
		with patch.object(api.frappe, "db", _db(found=self.person(enabled=0))):
			with self.assertRaises(frappe.ValidationError):
				api.attach(self.doc(), "Jane")

	def test_an_unknown_person_is_refused(self):
		with patch.object(api.frappe, "db", _db(found=None)):
			with self.assertRaises(frappe.ValidationError):
				api.attach(self.doc(), "Nobody")

	def test_a_cashier_linked_to_a_person_is_credited_automatically(self):
		doc = self.doc()
		with (
			patch.object(api.frappe, "db", _db(linked=self.person())),
			patch.object(api.frappe, "session", MagicMock(user="jane@x")),
		):
			api.attach(doc)
		doc.append.assert_called_once()

	def test_a_cashier_with_no_link_leaves_the_sale_alone(self):
		doc = self.doc()
		with (
			patch.object(api.frappe, "db", _db()),
			patch.object(api.frappe, "session", MagicMock(user="cashier@x")),
		):
			api.attach(doc)
		doc.append.assert_not_called()

	def test_a_return_copies_the_sales_team_of_the_sale(self):
		doc = self.doc()
		rows = [frappe._dict(sales_person="Jane", allocated_percentage=100, commission_rate=5)]
		with patch.object(api.frappe, "get_all", return_value=rows):
			api.copy_sales_team("SINV-1", doc)
		doc.append.assert_called_once()
		self.assertEqual(doc.commission_rate, 5)


class PermissionCase(unittest.TestCase):
	def test_a_cashier_cannot_add_or_change_sales_people_or_see_the_report(self):
		with (
			patch.object(api.frappe, "get_roles", return_value=["Sales User"]),
			patch.object(api.frappe, "session", MagicMock(user="cashier@x")),
		):
			with self.assertRaises(frappe.PermissionError):
				api.create_sales_person("Jane", 5, company="Acme")
			with self.assertRaises(frappe.PermissionError):
				api.update_sales_person("Jane", 6)
			with self.assertRaises(frappe.PermissionError):
				api.get_commission_report("Acme")

	def test_a_name_is_required(self):
		with (
			patch.object(api.frappe, "get_roles", return_value=["Sales Manager"]),
			patch.object(api.frappe, "session", MagicMock(user="m@x")),
			patch.object(api, "resolve_company", return_value="Acme"),
		):
			with self.assertRaises(frappe.ValidationError):
				api.create_sales_person("  ", 5)
