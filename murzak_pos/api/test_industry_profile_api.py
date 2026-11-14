import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import industry_profile_api as api

set_outlet_industry = getattr(api.set_outlet_industry, "__wrapped__", api.set_outlet_industry)


def outlet(company="Duka Ltd", industry=None, is_group=0):
	return frappe._dict(name="Main - DL", company=company, is_group=is_group, custom_pos_industry=industry)


class Base(unittest.TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.patches = [
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "session", frappe._dict(user="boss@duka.test"), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api, "resolve_company", side_effect=lambda c=None: "Duka Ltd"),
			patch.object(api, "_outlet_has_field", return_value=True),
			patch.object(api, "_shop_type", return_value=None),
			patch.object(api, "_business_kind", return_value=None),
			patch.object(api, "_industry_code", side_effect=lambda name: name),
			patch.object(api.role_tiers, "get_tier", return_value="owner"),
			patch.object(api.role_tiers, "require_access"),
		]
		for p in self.patches:
			p.start()

	def tearDown(self):
		for p in reversed(self.patches):
			p.stop()


class TestProfileFor(Base):
	def test_an_outlet_with_its_own_industry_uses_it(self):
		self.db.get_value.return_value = outlet(industry="REST")
		data = api.profile_for("Duka Ltd", "Main - DL")
		self.assertEqual((data["key"], data["source"], data["outlet"]), ("restaurant", "outlet", "Main - DL"))
		self.assertTrue(data["can_change"])

	def test_an_outlet_without_one_follows_the_business(self):
		self.db.get_value.return_value = outlet(industry=None)
		with patch.object(api, "_business_kind", return_value="pharmacy"):
			data = api.profile_for("Duka Ltd", "Main - DL")
		self.assertEqual((data["key"], data["source"]), ("pharmacy", "business_kind"))

	def test_another_business_outlet_is_refused(self):
		self.db.get_value.return_value = outlet(company="Other Ltd")
		with self.assertRaises(frappe.PermissionError):
			api.profile_for("Duka Ltd", "Main - DL")

	def test_a_cashier_reads_it_but_cannot_change_it(self):
		self.db.get_value.return_value = outlet(industry="PHARMACY")
		with patch.object(api.role_tiers, "get_tier", return_value="cashier"):
			self.assertFalse(api.profile_for("Duka Ltd", "Main - DL")["can_change"])


class TestSetOutletIndustry(Base):
	def setUp(self):
		super().setUp()
		self.get_doc = patch.object(api.frappe, "get_doc").start()
		self.addCleanup(patch.stopall)

	def answers(self, row, active=True):
		def get_value(doctype, *args, **kwargs):
			if doctype == "Warehouse":
				return row
			if doctype == "POS Industry":
				return "REST" if active else None
			return None

		self.db.get_value.side_effect = get_value

	def test_the_owner_sets_an_outlet_and_the_change_is_recorded(self):
		self.answers(outlet())
		result = set_outlet_industry("Main - DL", "REST")
		self.db.set_value.assert_called_once_with("Warehouse", "Main - DL", "custom_pos_industry", "REST")
		self.assertEqual(self.get_doc.call_args.args[0]["content"], "Outlet industry set to REST")
		self.assertTrue(result["success"])

	def test_an_empty_choice_makes_the_outlet_follow_the_business_again(self):
		self.answers(outlet(industry="REST"))
		set_outlet_industry("Main - DL", "  ")
		self.db.set_value.assert_called_once_with("Warehouse", "Main - DL", "custom_pos_industry", None)

	def test_an_inactive_or_unknown_industry_is_refused(self):
		self.answers(outlet(), active=False)
		with self.assertRaises(frappe.ValidationError):
			set_outlet_industry("Main - DL", "MADE-UP")
		self.db.set_value.assert_not_called()

	def test_a_group_of_stores_is_refused(self):
		self.answers(outlet(is_group=1))
		with self.assertRaises(frappe.ValidationError):
			set_outlet_industry("Main - DL", "REST")

	def test_only_the_owner_may_change_it(self):
		with patch.object(api.role_tiers, "require_access", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				set_outlet_industry("Main - DL", "REST")
		self.db.set_value.assert_not_called()

	def test_a_site_without_the_field_says_so(self):
		with patch.object(api, "_outlet_has_field", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				set_outlet_industry("Main - DL", "REST")


class TestBusinessHas(Base):
	def test_a_pharmacy_counter_in_a_general_business_counts(self):
		with patch.object(api.frappe, "get_all", return_value=["PHARMACY"]):
			self.assertTrue(api.business_has("Duka Ltd", "antibiotics_prescription_only"))

	def test_a_failure_never_stops_the_caller(self):
		with (
			patch.object(api.frappe, "get_all", side_effect=RuntimeError("no db")),
			patch.object(api.frappe, "log_error") as log_error,
			patch.object(api.frappe, "get_traceback", return_value=""),
		):
			self.assertFalse(api.business_has("Duka Ltd", "tables"))
		log_error.assert_called_once()


if __name__ == "__main__":
	unittest.main()
