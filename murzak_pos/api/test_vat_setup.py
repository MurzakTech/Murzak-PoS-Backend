# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for VAT set-up: product VAT classes, the POS Profile default and the tax added to a sale.
No database is needed; every Frappe call is replaced.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_api, vat_setup


class Doc(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class VatClassCase(unittest.TestCase):
	def test_class_names_are_tolerant_of_case_and_spacing(self):
		self.assertEqual(vat_setup.clean_vat_class(" Zero-Rated "), "zero_rated")
		self.assertEqual(vat_setup.clean_vat_class("EXEMPT"), "exempt")

	def test_no_class_given_means_none(self):
		self.assertIsNone(vat_setup.clean_vat_class(None))
		self.assertIsNone(vat_setup.clean_vat_class("  "))

	def test_unknown_class_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			vat_setup.clean_vat_class("luxury")

	def test_each_class_maps_to_its_etims_taxation_type(self):
		self.assertEqual(vat_setup.etims_code_for("standard"), "B")
		self.assertEqual(vat_setup.etims_code_for("zero_rated"), "C")
		self.assertEqual(vat_setup.etims_code_for("exempt"), "A")

	def test_rates(self):
		self.assertEqual(vat_setup.VAT_CLASSES["standard"][1], 16)
		self.assertEqual(vat_setup.VAT_CLASSES["zero_rated"][1], 0)
		self.assertEqual(vat_setup.VAT_CLASSES["exempt"][1], 0)

	def test_no_class_gives_no_template(self):
		self.assertIsNone(vat_setup.item_tax_template_for("Acme", None))


class PosProfileCase(unittest.TestCase):
	def test_needs_a_template_only_when_registered_and_missing(self):
		self.assertTrue(vat_setup.pos_profile_needs_vat_template(True, None))
		self.assertFalse(vat_setup.pos_profile_needs_vat_template(True, "Kenya VAT 16% - A"))
		self.assertFalse(vat_setup.pos_profile_needs_vat_template(False, None))

	def test_unregistered_business_is_left_alone(self):
		profile = Doc(company="Acme", taxes_and_charges=None)
		with patch.object(vat_setup, "is_vat_registered", return_value=False):
			vat_setup.validate_pos_profile(profile)
		self.assertIsNone(profile.taxes_and_charges)

	def test_registered_business_gets_the_vat_template(self):
		profile = Doc(company="Acme", taxes_and_charges=None)
		with (
			patch.object(vat_setup, "is_vat_registered", return_value=True),
			patch.object(
				vat_setup, "ensure_vat_setup", return_value={"sales_taxes_template": "Kenya VAT 16% - A"}
			),
		):
			vat_setup.validate_pos_profile(profile)
		self.assertEqual(profile.taxes_and_charges, "Kenya VAT 16% - A")

	def test_registered_business_keeps_the_template_it_chose(self):
		profile = Doc(company="Acme", taxes_and_charges="Other - A")
		with patch.object(vat_setup, "is_vat_registered", return_value=True):
			vat_setup.validate_pos_profile(profile)
		self.assertEqual(profile.taxes_and_charges, "Other - A")

	def test_registered_business_without_any_template_cannot_save(self):
		profile = Doc(company="Acme", taxes_and_charges=None)
		with (
			patch.object(vat_setup, "is_vat_registered", return_value=True),
			patch.object(vat_setup, "ensure_vat_setup", return_value={"sales_taxes_template": None}),
		):
			with self.assertRaises(frappe.ValidationError):
				vat_setup.validate_pos_profile(profile)


class SaleTaxCase(unittest.TestCase):
	def sale(self, taxes=None):
		return SimpleNamespace(
			taxes=taxes or [],
			taxes_and_charges=None,
			set_taxes=MagicMock(),
			get=lambda key, default=None: (taxes or []) if key == "taxes" else default,
		)

	def apply(self, doc, profile_template, registered=False, profile="POS-1"):
		with (
			patch.object(sales_api.frappe, "db", MagicMock()) as db,
			patch.object(vat_setup, "is_vat_registered", return_value=registered),
			patch.object(
				vat_setup, "ensure_vat_setup", return_value={"sales_taxes_template": "Registered VAT - A"}
			),
		):
			db.get_value.return_value = profile_template
			sales_api._apply_sales_taxes(doc, "Acme", profile)

	def test_the_profile_template_is_used(self):
		doc = self.sale()
		self.apply(doc, "Kenya VAT 16% - A")
		self.assertEqual(doc.taxes_and_charges, "Kenya VAT 16% - A")
		doc.set_taxes.assert_called_once()

	def test_a_registered_business_with_a_bare_profile_still_gets_vat(self):
		doc = self.sale()
		self.apply(doc, None, registered=True)
		self.assertEqual(doc.taxes_and_charges, "Registered VAT - A")

	def test_no_template_anywhere_means_no_tax(self):
		doc = self.sale()
		self.apply(doc, None, registered=False)
		doc.set_taxes.assert_not_called()

	def test_a_sale_that_already_has_tax_rows_is_untouched(self):
		doc = self.sale(taxes=[object()])
		self.apply(doc, "Kenya VAT 16% - A")
		doc.set_taxes.assert_not_called()


if __name__ == "__main__":
	unittest.main()
