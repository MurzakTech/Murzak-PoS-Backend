import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import shop_type_api as api

set_shop_type = getattr(api.set_shop_type, "__wrapped__", api.set_shop_type)
NOW = datetime(2026, 11, 7, 12, 0)


def run(
	industry,
	*,
	current=None,
	last=None,
	active=True,
	has_field=True,
	people=("boss@shop.test", "till@shop.test"),
):
	db = MagicMock()
	db.has_column.return_value = has_field
	db.get_value.side_effect = lambda doctype, *a, **k: (
		("retail" if active else None) if doctype == "POS Industry" else current
	)
	with (
		patch.object(frappe, "db", db, create=True),
		patch.object(frappe, "session", MagicMock(user="boss@shop.test"), create=True),
		patch.object(api.role_tiers, "require_access"),
		patch.object(api, "resolve_company", return_value="Linda"),
		patch.object(api, "now_datetime", return_value=NOW),
		patch.object(api, "_last_change", return_value=last),
		patch.object(api, "_payload", return_value={}),
		patch.object(api.frappe, "get_all", return_value=list(people)),
		patch.object(api.frappe, "get_doc") as get_doc,
	):
		result = set_shop_type(industry)
	return result, db, get_doc


class TestSetShopType(unittest.TestCase):
	def test_the_first_choice_is_applied_to_the_whole_team_and_recorded(self):
		result, db, get_doc = run("retail")
		self.assertTrue(result["success"])
		changed = {call.args[0:2] for call in db.set_value.call_args_list}
		self.assertEqual(changed, {("User", "boss@shop.test"), ("User", "till@shop.test")})
		content = get_doc.call_args.args[0]["content"]
		self.assertEqual(content, "Shop type set to retail")
		get_doc.return_value.insert.assert_called_once()

	def test_a_type_that_is_not_on_the_list_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			run("made-up", active=False)

	def test_the_same_type_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			run("retail", current="retail")

	def test_a_change_inside_the_waiting_period_is_refused_and_changes_nothing(self):
		with self.assertRaises(frappe.ValidationError):
			run("retail", current="pharmacy", last=NOW - timedelta(days=3))

	def test_a_change_after_the_waiting_period_is_allowed(self):
		result, _db, _doc = run("retail", current="pharmacy", last=NOW - timedelta(days=60))
		self.assertTrue(result["success"])

	def test_a_site_without_the_field_refuses(self):
		with self.assertRaises(frappe.ValidationError):
			run("retail", has_field=False)
