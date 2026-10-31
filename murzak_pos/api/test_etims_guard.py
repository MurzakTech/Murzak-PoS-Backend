# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the guard that keeps the Kenya tax app (eTIMS) out of supplier bills for a business that does not use eTIMS.

The reported failure was "AttributeError: 'PurchaseInvoice' object has no attribute 'branch'" on every supplier bill, from
the tax app's own check of the bill. The tax app is not installed where these tests run, so a stand-in module with the
same step names and the same failure is used. No database is needed.
"""

import types
import unittest

from murzak_pos import hooks
from murzak_pos.overrides import etims_guard

PURCHASE = "kenya_compliance.kenya_compliance.overrides.server.purchase_invoice"
SALES = "kenya_compliance.kenya_compliance.overrides.server.sales_invoice"
UTILS = "kenya_compliance.kenya_compliance.utils"


class Doc(types.SimpleNamespace):
	"""A document that, like a real one, raises AttributeError for a field it does not have (frappe._dict would give None)."""

	def get(self, field, default=None):
		return getattr(self, field, default)


def doc(doctype="Purchase Invoice", company="Shop A", **fields):
	return Doc(doctype=doctype, company=company, **fields)


class FakeTaxApp:
	"""Stand-ins for the tax app's four steps. They record their calls, and validate fails as the real one did."""

	def __init__(self):
		self.calls = []
		modules = {name: types.ModuleType(name) for name in (PURCHASE, SALES, UTILS)}
		self.modules = modules

		def record(label):
			def step(document, method=None):
				self.calls.append((label, document.doctype))

			step.__name__ = label
			return step

		def validate(document, method=None):
			document.branch  # a bill with no branch field raises AttributeError, as the real step did
			self.calls.append(("validate", document.doctype))

		modules[PURCHASE].validate = validate
		modules[PURCHASE].on_submit = record("on_submit")
		modules[SALES].before_cancel = record("before_cancel")
		modules[UTILS].before_save_ = record("before_save_")

	def import_module(self, name):
		if name not in self.modules:
			raise ImportError(name)
		return self.modules[name]


class TestTheGuard(unittest.TestCase):
	def setUp(self):
		self.app = FakeTaxApp()
		self.uses_etims = {"Shop A": False, "Shop E": True}
		self.installed = etims_guard.install(
			self.app.import_module, lambda company: self.uses_etims.get(company, False)
		)

	def run_steps(self, document):
		self.app.modules[UTILS].before_save_(document, "before_save")
		self.app.modules[PURCHASE].validate(document, "validate")
		self.app.modules[PURCHASE].on_submit(document, "on_submit")
		self.app.modules[SALES].before_cancel(document, "before_cancel")

	def test_all_four_steps_are_guarded(self):
		self.assertEqual(self.installed, 4)

	def test_a_supplier_bill_of_a_business_without_etims_no_longer_fails_and_the_tax_app_does_nothing(self):
		self.run_steps(doc())  # this raised AttributeError ('branch') before
		self.assertEqual(self.app.calls, [])

	def test_a_business_that_uses_etims_still_goes_through_the_tax_app_exactly_as_before(self):
		bill = doc(company="Shop E", branch="00")
		self.run_steps(bill)
		self.assertEqual(
			[c[0] for c in self.app.calls], ["before_save_", "validate", "on_submit", "before_cancel"]
		)

	def test_a_business_that_uses_etims_but_lacks_a_branch_still_gets_the_tax_apps_own_failure(self):
		with self.assertRaises(AttributeError):
			self.app.modules[PURCHASE].validate(doc(company="Shop E"), "validate")

	def test_a_sales_invoice_always_goes_to_the_tax_app_because_the_guard_is_for_supplier_bills_only(self):
		sale = doc("Sales Invoice", branch="00")
		self.app.modules[UTILS].before_save_(sale, "before_save")
		self.app.modules[SALES].before_cancel(sale, "before_cancel")
		self.assertEqual([c[0] for c in self.app.calls], ["before_save_", "before_cancel"])

	def test_an_item_always_goes_to_the_tax_app(self):
		self.app.modules[UTILS].before_save_(doc("Item"), "before_save")
		self.assertEqual(self.app.calls, [("before_save_", "Item")])

	def test_it_asks_about_the_bills_own_business(self):
		asked = []
		app = FakeTaxApp()
		etims_guard.install(app.import_module, lambda company: asked.append(company) or False)
		app.modules[PURCHASE].on_submit(doc(company="Shop Z"), "on_submit")
		self.assertEqual(asked, ["Shop Z"])

	def test_the_steps_keep_their_names(self):
		self.assertEqual(self.app.modules[PURCHASE].on_submit.__name__, "on_submit")
		self.assertEqual(self.app.modules[UTILS].before_save_.__name__, "before_save_")

	def test_installing_again_does_not_wrap_a_step_twice(self):
		again = etims_guard.install(self.app.import_module, lambda company: False)
		self.assertEqual(again, 0)
		self.run_steps(doc(company="Shop E", branch="00"))
		self.assertEqual(len(self.app.calls), 4)  # once each, not twice

	def test_without_the_tax_app_nothing_happens(self):
		def missing(name):
			raise ImportError(name)

		self.assertEqual(etims_guard.install(missing, lambda company: False), 0)

	def test_a_tax_app_without_one_of_the_steps_is_left_alone(self):
		app = FakeTaxApp()
		del app.modules[PURCHASE].on_submit
		self.assertEqual(etims_guard.install(app.import_module, lambda company: False), 3)


class TestItIsLoadedBeforeAnyStepRuns(unittest.TestCase):
	def test_the_purchase_invoice_class_is_registered_in_the_hooks(self):
		self.assertEqual(
			hooks.override_doctype_class["Purchase Invoice"],
			"murzak_pos.overrides.purchase_invoice_class.PurchaseInvoice",
		)

	def test_the_class_loads_the_guard_and_is_erpnexts_purchase_invoice(self):
		from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import (
			PurchaseInvoice as ERPNextPurchaseInvoice,
		)

		from murzak_pos.overrides import purchase_invoice_class

		self.assertTrue(issubclass(purchase_invoice_class.PurchaseInvoice, ERPNextPurchaseInvoice))
		self.assertIs(purchase_invoice_class.etims_guard, etims_guard)


if __name__ == "__main__":
	unittest.main()
