# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Which business a person belongs to, and that they cannot put themselves inside another one.

Needs no database: every Frappe call is mocked.
"""

import ast
import inspect
import os
import unittest
from unittest.mock import MagicMock, call, patch

import frappe

from murzak_pos.api import company_binding_guard, payment_gateway_common, session_defaults

SHOPS = ["Alpha Hardware", "Beta Pharmacy"]


class Site:
	"""A site with two businesses. `user` is bound by the things passed in, and by nothing else."""

	def __init__(
		self,
		user="someone@example.com",
		custom_company=None,
		permissions=(),
		owned=(),
		employee_company=None,
		default=None,
		global_default="Alpha Hardware",
	):
		self.user, self.custom_company, self.permissions, self.owned = (
			user,
			custom_company,
			permissions,
			owned,
		)
		self.employee_company, self.default, self.global_default = employee_company, default, global_default

	def get_all(self, doctype, filters=None, **kwargs):
		if doctype == "Company":
			return list(self.owned) if filters else list(SHOPS)
		if doctype == "User Permission":
			return [frappe._dict(for_value=company) for company in self.permissions]
		return []

	def get_value(self, doctype, name=None, fieldname=None, *args, **kwargs):
		if doctype == "User" and fieldname == "custom_company":
			return self.custom_company
		if doctype == "Employee":
			return self.employee_company
		return None

	def __enter__(self):
		db = MagicMock()
		db.has_column.return_value = True
		db.exists.return_value = True
		db.get_value.side_effect = self.get_value
		db.get_single_value.return_value = self.global_default
		defaults = MagicMock()
		defaults.get_user_default.side_effect = lambda key, user=None: self.default
		self.patches = [
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_all", side_effect=self.get_all, create=True),
			patch.object(frappe, "defaults", defaults, create=True),
			patch.object(frappe, "session", frappe._dict(user=self.user), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		]
		for p in self.patches:
			p.start()
		return self

	def __exit__(self, *exc):
		for p in reversed(self.patches):
			p.stop()


class TestWhoBelongsWhere(unittest.TestCase):
	def companies(self, **kwargs):
		with Site(**kwargs):
			return payment_gateway_common.get_user_companies()

	def test_someone_who_just_signed_up_belongs_to_no_business(self):
		self.assertEqual(self.companies(), set())

	def test_nor_with_no_site_default_company(self):
		self.assertEqual(self.companies(global_default=None), set())

	def test_a_default_company_they_set_themselves_does_not_make_them_belong(self):
		self.assertEqual(self.companies(default="Beta Pharmacy"), set())
		self.assertEqual(
			self.companies(custom_company="Alpha Hardware", default="Beta Pharmacy"), {"Alpha Hardware"}
		)

	def test_profile_company_permission_and_created_company_count(self):
		self.assertEqual(self.companies(custom_company="Alpha Hardware"), {"Alpha Hardware"})
		self.assertEqual(self.companies(permissions=["Beta Pharmacy"]), {"Beta Pharmacy"})
		self.assertEqual(self.companies(owned=["Beta Pharmacy"]), {"Beta Pharmacy"})

	def test_an_employee_record_counts_only_for_someone_bound_to_nothing_else(self):
		self.assertEqual(self.companies(employee_company="Beta Pharmacy"), {"Beta Pharmacy"})
		self.assertEqual(
			self.companies(custom_company="Alpha Hardware", employee_company="Beta Pharmacy"),
			{"Alpha Hardware"},
		)


class TestChoosingTheCompany(unittest.TestCase):
	def test_the_default_is_used_only_when_it_is_one_of_theirs(self):
		with Site(default="Beta Pharmacy", global_default=None):
			allowed = {"Alpha Hardware", "Beta Pharmacy"}
			self.assertEqual(payment_gateway_common.preferred_company(allowed), "Beta Pharmacy")
			self.assertEqual(
				payment_gateway_common.preferred_company({"Alpha Hardware", "Gamma"}), "Alpha Hardware"
			)

	def test_nothing_is_chosen_for_someone_who_belongs_nowhere(self):
		with Site(default="Beta Pharmacy"):
			self.assertIsNone(payment_gateway_common.preferred_company(set()))

	def test_a_newcomer_naming_a_business_is_refused(self):
		with Site(default="Beta Pharmacy"), self.assertRaises(frappe.PermissionError):
			payment_gateway_common.resolve_company("Beta Pharmacy")

	def test_a_newcomer_naming_none_is_told_a_company_is_needed(self):
		with Site(default="Beta Pharmacy"), self.assertRaises(frappe.ValidationError):
			payment_gateway_common.resolve_company()

	def test_the_administrator_still_gets_a_company(self):
		with Site(user="Administrator", global_default=None):
			self.assertEqual(payment_gateway_common.resolve_company(), "Alpha Hardware")


class TestSessionDefaults(unittest.TestCase):
	def test_another_business_cannot_be_set_as_the_default(self):
		with (
			Site(custom_company="Alpha Hardware"),
			patch(
				"frappe.core.doctype.session_default_settings.session_default_settings.set_session_default_values"
			) as frappe_call,
			self.assertRaises(frappe.PermissionError),
		):
			session_defaults.set_session_default_values({"company": "Beta Pharmacy"})
		frappe_call.assert_not_called()

	def test_their_own_business_and_other_defaults_go_through(self):
		with (
			Site(custom_company="Alpha Hardware"),
			patch(
				"frappe.core.doctype.session_default_settings.session_default_settings.set_session_default_values",
				return_value="success",
			) as frappe_call,
		):
			values = {"company": "Alpha Hardware", "price_list": "Standard Selling"}
			self.assertEqual(session_defaults.set_session_default_values(values), "success")
		frappe_call.assert_called_once_with(values)

	def test_clearing_and_the_administrator_are_never_refused(self):
		self.assertEqual(session_defaults.refused_companies({"company": ""}, "a@b.test", set()), [])
		self.assertEqual(session_defaults.refused_companies({"Company": "X"}, "Administrator", set()), [])
		self.assertEqual(session_defaults.refused_companies({"Company": "X"}, "a@b.test", {"Y"}), ["X"])

	def test_frappes_call_is_pointed_at_the_checked_one(self):
		from murzak_pos import hooks

		frappe_path = (
			"frappe.core.doctype.session_default_settings.session_default_settings.set_session_default_values"
		)
		self.assertEqual(
			hooks.override_whitelisted_methods[frappe_path],
			"murzak_pos.api.session_defaults.set_session_default_values",
		)


def make_doc(new=False, changed=(), trusted=False, **values):
	doc = MagicMock()
	doc.is_new.return_value = new
	doc.has_value_changed.side_effect = lambda field: field in changed
	doc.flags = frappe._dict(ignore_permissions=trusted)
	doc.get.side_effect = values.get
	doc.get_doc_before_save.return_value = frappe._dict(allow=values.get("before_allow")) if not new else None
	doc.allow = values.get("allow")
	return doc


class TestMembershipCannotBeRewritten(unittest.TestCase):
	def setUp(self):
		for p in (
			patch.object(frappe, "session", frappe._dict(user="owner@alpha.test"), create=True),
			patch.object(frappe, "flags", frappe._dict(), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_a_profile_company_changed_through_the_generic_api_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			company_binding_guard.guard_user_company(make_doc(changed=("custom_company",)))
		with self.assertRaises(frappe.PermissionError):
			company_binding_guard.guard_user_company(make_doc(new=True, custom_company="Beta Pharmacy"))

	def test_the_apps_own_checked_change_and_other_edits_pass(self):
		company_binding_guard.guard_user_company(make_doc(changed=("custom_company",), trusted=True))
		company_binding_guard.guard_user_company(make_doc(changed=("first_name",)))

	def test_company_permissions_can_only_be_changed_by_the_app(self):
		with self.assertRaises(frappe.PermissionError):
			company_binding_guard.guard_user_permission(make_doc(new=True, allow="Company"))
		with self.assertRaises(frappe.PermissionError):
			company_binding_guard.guard_user_permission(make_doc(allow="Warehouse", before_allow="Company"))
		company_binding_guard.guard_user_permission(make_doc(new=True, allow="Company", trusted=True))
		company_binding_guard.guard_user_permission(make_doc(new=True, allow="Warehouse"))

	def test_the_owner_of_a_business_cannot_be_rewritten(self):
		with self.assertRaises(frappe.PermissionError):
			company_binding_guard.guard_company_owner(make_doc(changed=("owner",)))
		company_binding_guard.guard_company_owner(make_doc(changed=("company_name",)))
		company_binding_guard.guard_company_owner(make_doc(new=True, changed=("owner",)))

	def test_the_administrator_and_migrations_pass(self):
		with patch.object(frappe, "session", frappe._dict(user="Administrator"), create=True):
			company_binding_guard.guard_user_permission(make_doc(new=True, allow="Company"))
		with patch.object(frappe, "flags", frappe._dict(in_patch=True), create=True):
			company_binding_guard.guard_user_company(make_doc(changed=("custom_company",)))

	def test_the_hooks_are_registered(self):
		from murzak_pos import hooks

		events = hooks.doc_events
		self.assertIn("company_binding_guard.guard_user_company", events["User"]["validate"])
		self.assertIn("company_binding_guard.guard_user_permission", events["User Permission"]["validate"])
		self.assertIn("company_binding_guard.guard_user_permission", events["User Permission"]["on_trash"])
		self.assertIn("company_binding_guard.guard_company_owner", events["Company"]["validate"])


class TestSignUpGivesNoRoles(unittest.TestCase):
	API = os.path.dirname(os.path.abspath(__file__))

	def body(self, filename, function):
		with open(os.path.join(self.API, filename)) as handle:
			source = handle.read()
		for node in ast.parse(source).body:
			if isinstance(node, ast.FunctionDef) and node.name == function:
				return ast.get_source_segment(source, node)
		self.fail(f"{filename} has no {function}")

	def test_neither_sign_up_call_hands_out_roles(self):
		for filename in ("auth_api.py", "auth.py"):
			with self.subTest(filename=filename):
				body = self.body(filename, "register_user")
				self.assertNotIn("STANDARD_BUSINESS_ROLES", body)
				self.assertNotIn("assign_all_business_roles", body)
				self.assertNotIn('"roles"', body)

	def test_creating_a_business_binds_the_person_before_giving_roles(self):
		from murzak_pos.api import onboarding_api

		order = MagicMock()
		with (
			patch("murzak_pos.api.staff_api.set_company_permission", order.bind),
			patch("murzak_pos.api.auth_api.assign_all_business_roles", order.roles),
			patch.object(frappe, "clear_cache", create=True),
		):
			onboarding_api._make_owner("new@owner.test", "Gamma Bakery")
		self.assertEqual(
			order.mock_calls,
			[call.bind("new@owner.test", "Gamma Bakery", "new@owner.test"), call.roles("new@owner.test")],
		)
		self.assertIn(
			"_make_owner(frappe.session.user, company.name)", inspect.getsource(onboarding_api.create_company)
		)

	def test_the_profile_call_picks_only_a_business_the_person_belongs_to(self):
		for filename in ("auth_api.py", "auth.py"):
			with self.subTest(filename=filename):
				body = self.body(filename, "get_current_user")
				self.assertIn("preferred_company(get_user_companies(user), user)", body)


class TestCleanUpPatch(unittest.TestCase):
	def run_patch(self, users, defaults, roles, companies, operators=()):
		from murzak_pos.patches import unbind_users_from_other_businesses as patch_module

		def get_all(doctype, filters=None, **kwargs):
			if doctype == "User":
				return list(users)
			if doctype == "DefaultValue":
				return [frappe._dict(parent=u, defkey=k, defvalue=v) for u, k, v in defaults]
			return []

		docs = {}

		def get_doc(doctype, name):
			docs[name] = MagicMock(flags=frappe._dict())
			return docs[name]

		db = MagicMock()
		db.exists.return_value = True
		defaults_api = MagicMock()
		with (
			patch.object(frappe, "get_all", side_effect=get_all, create=True),
			patch.object(frappe, "get_doc", side_effect=get_doc, create=True),
			patch.object(frappe, "get_roles", side_effect=lambda user: roles.get(user, []), create=True),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "defaults", defaults_api, create=True),
			patch.object(
				frappe, "conf", frappe._dict(murzak_platform_operators=list(operators)), create=True
			),
			patch(
				"murzak_pos.api.payment_gateway_common.get_user_companies",
				side_effect=lambda user: set(companies.get(user, ())),
			),
			patch("builtins.print"),
		):
			patch_module.execute()
		return defaults_api, docs

	def test_a_default_company_that_is_not_theirs_is_cleared_or_replaced(self):
		defaults_api, _docs = self.run_patch(
			users=["a@x.test", "b@x.test", "c@x.test"],
			defaults=[
				("a@x.test", "Company", "Beta"),
				("b@x.test", "company", "Beta"),
				("c@x.test", "Company", "Alpha"),
			],
			roles={},
			companies={"a@x.test": ["Alpha"], "c@x.test": ["Alpha"]},
		)
		defaults_api.clear_user_default.assert_has_calls(
			[call("Company", "a@x.test"), call("company", "b@x.test")], any_order=True
		)
		self.assertEqual(defaults_api.clear_user_default.call_count, 2)
		defaults_api.set_user_default.assert_called_once_with("Company", "Alpha", "a@x.test")

	def test_sign_up_roles_are_removed_only_from_people_bound_to_no_business(self):
		from murzak_pos.api.auth_api import STANDARD_BUSINESS_ROLES

		every = list(STANDARD_BUSINESS_ROLES)
		_defaults, docs = self.run_patch(
			users=["stranger@x.test", "owner@x.test", "ops@murzak.test", "cashier@x.test"],
			defaults=[],
			roles={
				"stranger@x.test": every,
				"owner@x.test": every,
				"ops@murzak.test": every,
				"cashier@x.test": ["Sales User"],
			},
			companies={"owner@x.test": ["Alpha"]},
			operators=["ops@murzak.test"],
		)
		self.assertEqual(sorted(docs), ["stranger@x.test"])
		docs["stranger@x.test"].remove_roles.assert_called_once_with(*sorted(every))


if __name__ == "__main__":
	unittest.main()
