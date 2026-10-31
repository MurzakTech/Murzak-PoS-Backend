# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for keeping each business's suppliers to itself: who sees which supplier, that a new supplier is
tagged with its company, and the one-time patch that tags the suppliers that already exist.

No database is needed. Every Frappe lookup is mocked, so each test says plainly what the database
"contains" for the case in hand.
"""

import json
import os
import types
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import supplier_api
from murzak_pos.patches import backfill_supplier_company as backfill

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Who bought from whom, as the purchase documents say
PURCHASES = {
	("Purchase Order", "Shop A"): ["Acme", "Both Ltd"],
	("Purchase Receipt", "Shop A"): ["Acme"],
	("Purchase Invoice", "Shop A"): [],
	("Purchase Order", "Shop B"): ["Both Ltd", "Zeta"],
	("Purchase Receipt", "Shop B"): [],
	("Purchase Invoice", "Shop B"): [],
}
# The owning company recorded on each supplier ("" means none, an older supplier)
OWNERS = {"Acme": "Shop A", "Both Ltd": "", "Zeta": "Shop B", "Nobody Ltd": "", "Legacy Co": ""}


class SupplierCase(unittest.TestCase):
	def as_user(self, user="buyer@shop-a.test", companies=("Shop A",), has_field=True, roles=("Business Owner",)):
		def get_all(doctype, filters=None, pluck=None, **kwargs):
			if doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice"):
				return list(PURCHASES.get((doctype, filters["company"]), []))
			if doctype == "Supplier":
				custom_company = filters.get("custom_company")
				if custom_company == ["is", "not set"]:  # asked among the names given
					return [n for n in filters["name"][1] if not OWNERS.get(n)]
				return [name for name, owner in OWNERS.items() if owner == custom_company]
			return []

		db = MagicMock()
		db.has_column.return_value = has_field
		db.exists.return_value = True
		db.get_value.side_effect = lambda doctype, name=None, field=None, *a, **k: (
			OWNERS.get(name) if doctype == "Supplier" and field == "custom_company" else None
		)
		self.db = db
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_all", create=True, side_effect=get_all),
			patch.object(frappe, "log_error", create=True),
			patch.object(frappe, "logger", create=True),  # there is no log folder outside a real site
			patch(
				"frappe.defaults.get_global_default", return_value="Supplier Name"
			),  # how the site names suppliers
			patch(
				"murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)
			),
			patch.object(supplier_api, "get_user_companies", return_value=set(companies)),
		]
		frappe.local.response = {}  # where a call sets the HTTP status
		for p in stack:
			p.start()
			self.addCleanup(p.stop)
		self.get_all = frappe.get_all


class TestWhichSuppliersACompanySees(SupplierCase):
	def test_a_company_sees_its_own_suppliers(self):
		self.as_user()
		self.assertIn("Acme", supplier_api._visible_supplier_names("Shop A"))

	def test_it_never_sees_another_companys_supplier(self):
		self.as_user()
		visible = supplier_api._visible_supplier_names("Shop A")
		self.assertNotIn("Zeta", visible)

	def test_an_older_supplier_with_no_owner_is_seen_by_each_company_that_bought_from_it(self):
		for company in ("Shop A", "Shop B"):
			self.as_user(companies=(company,))
			self.assertIn("Both Ltd", supplier_api._visible_supplier_names(company), company)

	def test_an_older_supplier_nobody_bought_from_is_seen_by_no_company(self):
		self.as_user()
		for company in ("Shop A", "Shop B"):
			visible = supplier_api._visible_supplier_names(company)
			self.assertNotIn("Nobody Ltd", visible)
			self.assertNotIn("Legacy Co", visible)

	def test_the_administrator_with_no_company_is_not_limited(self):
		self.as_user("Administrator", companies=())
		self.assertIsNone(supplier_api._visible_supplier_names(None))

	def test_before_the_owner_field_exists_only_purchases_decide(self):
		self.as_user(has_field=False)
		self.assertEqual(supplier_api._visible_supplier_names("Shop A"), {"Acme", "Both Ltd"})

	def test_a_company_with_no_suppliers_sees_an_empty_set_not_everything(self):
		self.as_user(companies=("Shop C",))
		self.assertEqual(supplier_api._visible_supplier_names("Shop C"), set())


class TestListingSuppliers(SupplierCase):
	def list_suppliers(self, **request):
		self.as_user(**{k: v for k, v in request.items() if k in ("user", "companies")})
		request = {k: v for k, v in request.items() if k not in ("user", "companies")}
		seen = {}

		def get_all(doctype, filters=None, pluck=None, **kwargs):
			if doctype == "Supplier" and pluck is None:
				seen["filters"] = filters
				return (
					[{"name": n} for n in filters["name"][1]] if "name" in filters else [{"name": "everyone"}]
				)
			return self.get_all(doctype, filters=filters, pluck=pluck, **kwargs)

		with patch.object(frappe, "get_all", create=True, side_effect=get_all):
			reply = supplier_api.get_suppliers(**request)
		return reply, seen

	def test_the_list_is_limited_to_the_suppliers_the_company_may_see(self):
		reply, seen = self.list_suppliers()
		self.assertTrue(reply["success"])
		self.assertEqual(sorted(seen["filters"]["name"][1]), ["Acme", "Both Ltd"])

	def test_naming_another_businesss_company_is_refused_and_nothing_is_listed(self):
		reply, seen = self.list_suppliers(company="Shop B")
		self.assertFalse(reply["success"])
		self.assertIn("access to company Shop B", reply["message"])
		self.assertNotIn("filters", seen)

	def test_a_company_with_no_suppliers_gets_an_empty_list_without_asking_for_suppliers(self):
		reply, seen = self.list_suppliers(companies=("Shop C",))
		self.assertEqual((reply["success"], reply["data"], reply["count"]), (True, [], 0))
		self.assertNotIn("filters", seen)

	def test_the_administrator_with_no_company_sees_every_supplier(self):
		reply, seen = self.list_suppliers(user="Administrator", companies=())
		self.assertEqual(reply["data"], [{"name": "everyone"}])
		self.assertNotIn("name", seen["filters"])

	def test_the_search_and_group_filters_still_apply(self):
		reply, seen = self.list_suppliers(supplier_group="Dairy")
		self.assertEqual(seen["filters"]["supplier_group"], "Dairy")
		self.assertEqual(seen["filters"]["disabled"], 0)


class TestOneSupplierAtATime(SupplierCase):
	def refused(self, name, **user):
		self.as_user(**user)
		try:
			supplier_api._ensure_supplier_visible(name)
		except frappe.PermissionError:
			return True
		return False

	def test_a_company_may_open_its_own_supplier(self):
		self.assertFalse(self.refused("Acme"))

	def test_another_businesss_supplier_is_refused(self):
		self.assertTrue(self.refused("Zeta"))
		self.assertTrue(self.refused("Acme", user="boss@shop-b.test", companies=("Shop B",)))

	def test_an_older_supplier_is_open_to_a_company_that_bought_from_it_only(self):
		self.as_user()
		self.db.exists.side_effect = lambda doctype, filters=None: (
			doctype == "Supplier" or (isinstance(filters, dict) and filters["supplier"] == "Both Ltd")
		)
		supplier_api._ensure_supplier_visible("Both Ltd")  # bought from: allowed
		with self.assertRaises(frappe.PermissionError):
			supplier_api._ensure_supplier_visible("Nobody Ltd")  # never bought from: refused

	def test_the_administrator_may_open_any_supplier(self):
		self.assertFalse(self.refused("Zeta", user="Administrator", companies=()))

	def test_a_supplier_that_does_not_exist_is_left_for_the_call_to_report(self):
		self.as_user()
		self.db.exists.return_value = False
		supplier_api._ensure_supplier_visible("Ghost")

	def test_the_details_call_refuses_another_businesss_supplier(self):
		self.as_user()
		reply = supplier_api.get_supplier_details(name="Zeta")
		self.assertFalse(reply["success"])
		self.assertIn("belongs to another business", reply["message"])
		self.assertNotIn("data", reply)

	def test_the_update_call_refuses_another_businesss_supplier_before_loading_it(self):
		self.as_user()
		with (
			patch.object(frappe, "get_doc", create=True) as get_doc,
			self.assertRaises(frappe.PermissionError),
		):
			supplier_api.update_supplier(name="Zeta", tax_id="P0000")
		get_doc.assert_not_called()


class FakeSupplier:
	def __init__(self):
		self.name = "New Co"
		self.inserted = False

	def insert(self, **kwargs):
		self.inserted = True


class TestNewSuppliersAreTagged(SupplierCase):
	def create(self, has_field=True, **request):
		self.as_user(has_field=has_field)
		self.db.exists.side_effect = lambda doctype, filters=None: doctype != "Supplier"
		self.db.get_value.side_effect = lambda *a, **k: "Local"
		doc = FakeSupplier()
		with patch.object(frappe, "new_doc", create=True, return_value=doc):
			reply = supplier_api.create_supplier(supplier_name="New Co", **request)
		return doc, reply

	def test_a_new_supplier_belongs_to_the_callers_company(self):
		doc, reply = self.create()
		self.assertEqual(doc.custom_company, "Shop A")
		self.assertTrue(doc.inserted)
		self.assertTrue(reply["success"])

	def test_a_company_named_in_the_request_must_be_the_callers(self):
		doc, _reply = self.create(company="Shop A")
		self.assertEqual(doc.custom_company, "Shop A")
		self.as_user()
		self.db.exists.side_effect = lambda doctype, filters=None: doctype != "Supplier"
		with patch.object(frappe, "new_doc", create=True, return_value=FakeSupplier()) as new_doc:
			with self.assertRaises(frappe.PermissionError):
				supplier_api.create_supplier(supplier_name="New Co", company="Shop B")
		new_doc.assert_not_called()

	def test_before_the_owner_field_exists_nothing_is_tagged_and_creation_still_works(self):
		doc, reply = self.create(has_field=False)
		self.assertFalse(hasattr(doc, "custom_company"))
		self.assertTrue(reply["success"])


class TestTheBackfillPatch(unittest.TestCase):
	def decide(self, bought_by, owner=None, owner_company=None, only_company=None):
		with (
			patch.object(frappe, "db", MagicMock(), create=True) as db,
			patch.object(frappe, "defaults", MagicMock(), create=True) as defaults,
		):
			db.get_value.side_effect = lambda doctype, name, field: (
				owner if (doctype, field) == ("Supplier", "owner") else owner_company
			)
			defaults.get_user_default.return_value = None
			return backfill.decide_company("Supplier X", set(bought_by), only_company)

	def test_a_supplier_bought_from_by_one_company_belongs_to_it(self):
		self.assertEqual(self.decide(["Shop A"]), "Shop A")

	def test_a_supplier_bought_from_by_several_companies_stays_untagged_even_on_a_one_company_site(self):
		self.assertIsNone(self.decide(["Shop A", "Shop B"], only_company="Shop A"))

	def test_a_supplier_with_no_purchases_belongs_to_its_creators_company(self):
		self.assertEqual(self.decide([], owner="buyer@shop-a.test", owner_company="Shop A"), "Shop A")

	def test_one_the_administrator_made_falls_back_to_the_only_company(self):
		self.assertEqual(self.decide([], owner="Administrator", only_company="Solo Ltd"), "Solo Ltd")

	def test_one_the_administrator_made_on_a_site_with_several_companies_stays_untagged(self):
		self.assertIsNone(self.decide([], owner="Administrator"))

	def test_the_patch_creates_the_field_then_tags_only_what_the_evidence_settles(self):
		rows = [
			types.SimpleNamespace(supplier="Acme", company="Shop A"),
			types.SimpleNamespace(supplier="Both Ltd", company="Shop A"),
			types.SimpleNamespace(supplier="Both Ltd", company="Shop B"),
		]

		def sql(query, as_dict=False):
			return rows if "Purchase Order" in query else []

		with (
			patch.object(backfill, "create_custom_fields") as create,
			patch.object(frappe, "db", MagicMock(), create=True) as db,
			patch.object(frappe, "defaults", MagicMock(), create=True),
			patch.object(
				frappe,
				"get_all",
				create=True,
				side_effect=lambda doctype, **k: ["Acme", "Both Ltd", "Unknown"]
				if doctype == "Supplier"
				else ["Shop A", "Shop B"],
			),
		):
			db.sql.side_effect = sql
			db.get_value.return_value = None
			backfill.execute()
		create.assert_called_once()
		self.assertEqual(create.call_args.args[0]["Supplier"][0]["fieldname"], "custom_company")
		tagged = {call.args[1]: call.args[3] for call in db.set_value.call_args_list}
		self.assertEqual(tagged, {"Acme": "Shop A"})
		self.assertTrue(
			all(call.kwargs == {"update_modified": False} for call in db.set_value.call_args_list)
		)

	def test_the_patch_does_nothing_more_when_every_supplier_already_has_an_owner(self):
		with (
			patch.object(backfill, "create_custom_fields"),
			patch.object(frappe, "get_all", create=True, return_value=[]),
			patch.object(frappe, "db", MagicMock(), create=True) as db,
		):
			backfill.execute()
		db.set_value.assert_not_called()


class TestTheFieldIsShipped(unittest.TestCase):
	def test_the_custom_field_file_matches_the_patch(self):
		path = os.path.join(REPO, "murzak_pos", "murzak_pos", "custom", "supplier.json")
		with open(path) as handle:
			shipped = json.load(handle)["custom_fields"][0]
		self.assertEqual((shipped["dt"], shipped["fieldname"]), ("Supplier", backfill.FIELD["fieldname"]))
		self.assertEqual((shipped["fieldtype"], shipped["options"]), ("Link", "Company"))
		self.assertEqual(shipped["name"], "Supplier-custom_company")

	def test_the_patch_is_listed_to_run_after_the_tables_are_updated(self):
		with open(os.path.join(REPO, "murzak_pos", "patches.txt")) as handle:
			lines = [line.strip() for line in handle]
		self.assertIn("murzak_pos.patches.backfill_supplier_company", lines)
		self.assertGreater(
			lines.index("murzak_pos.patches.backfill_supplier_company"), lines.index("[post_model_sync]")
		)


if __name__ == "__main__":
	unittest.main()
