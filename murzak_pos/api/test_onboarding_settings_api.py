# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for reading the POS profile and reading and changing the eTIMS settings. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import onboarding_api
from murzak_pos.api import onboarding_settings_api as api


class PosProfileCase(unittest.TestCase):
	def get(self, names, mine, row=None):
		def get_all(doctype, **kwargs):
			return names if doctype == "POS Profile" else mine

		with (
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "get_all", side_effect=get_all),
			patch.object(api.frappe, "db", MagicMock()) as db,
			patch.object(api.frappe, "get_meta", return_value=MagicMock(has_field=lambda field: True)),
			patch.object(api.frappe, "session", MagicMock(user="cashier@acme.test")),
		):
			db.get_value.return_value = row if row is not None else {"name": names[0] if names else None}
			result = api.get_pos_profile("Acme")
		return result, db

	def test_a_business_with_no_profile_gets_an_empty_answer(self):
		result, _db = self.get([], [])
		self.assertEqual(result, {"pos_profile": None})

	def test_the_profile_that_names_the_user_wins(self):
		_result, db = self.get(["First", "Mine"], ["Mine"])
		self.assertEqual(db.get_value.call_args.args[1], "Mine")

	def test_without_one_naming_the_user_the_oldest_is_used(self):
		_result, db = self.get(["First", "Second"], [])
		self.assertEqual(db.get_value.call_args.args[1], "First")

	def test_a_setting_this_version_of_erpnext_lacks_is_not_asked_for(self):
		with (
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(
				api.frappe,
				"get_all",
				side_effect=lambda doctype, **k: ["P"] if doctype == "POS Profile" else [],
			),
			patch.object(api.frappe, "db", MagicMock()) as db,
			patch.object(
				api.frappe,
				"get_meta",
				return_value=MagicMock(has_field=lambda field: field != "update_stock"),
			),
			patch.object(api.frappe, "session", MagicMock(user="u")),
		):
			db.get_value.return_value = {"name": "P"}
			api.get_pos_profile("Acme")
		self.assertNotIn("update_stock", db.get_value.call_args.args[2])
		self.assertIn("warehouse", db.get_value.call_args.args[2])

	def test_the_tick_boxes_come_back_as_true_or_false(self):
		row = {"name": "P", "update_stock": 1, "allow_discount_change": 0, "disabled": 0}
		result, _db = self.get(["P"], [], row)
		profile = result["pos_profile"]
		self.assertIs(profile["update_stock"], True)
		self.assertIs(profile["allow_discount_change"], False)
		self.assertIs(profile["disabled"], False)


class EtimsReadCase(unittest.TestCase):
	def read(self, row):
		with (
			patch.object(api, "require_any_role") as check,
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_settings_row", return_value=row),
		):
			return api.get_etims_settings("Acme"), check

	def test_only_managers_may_read_the_settings(self):
		_result, check = self.read(None)
		self.assertEqual(check.call_args.args[0], api.ETIMS_ROLES)
		self.assertIn("Accounts Manager", api.ETIMS_ROLES)
		self.assertNotIn("Sales User", api.ETIMS_ROLES)

	def test_no_record_gives_an_empty_answer(self):
		result, _check = self.read(None)
		self.assertEqual(result, {"etims_settings": None})

	def test_tick_boxes_come_back_as_true_or_false_and_no_secret_fields_are_read(self):
		result, _check = self.read({"name": "S", "sandbox": 1, "is_active": 0, "tin": "P000000000A"})
		shown = result["etims_settings"]
		self.assertEqual((shown["sandbox"], shown["is_active"]), (True, False))
		for secret in ("client_secret", "auth_password", "communication_key"):
			self.assertNotIn(secret, api.ETIMS_READ_FIELDS)


class EtimsUpdateCase(unittest.TestCase):
	def update(self, row="found", after=None, tax_id="P000000000A", **kwargs):
		record = MagicMock()
		record.get.side_effect = lambda field: {
			"tin": "P000000000A",
			"dvcsrlno": "D1",
			"sandbox": 1,
			"is_active": 1,
		}[field]
		first = (
			{"name": "S", "tin": "P000000000A", "dvcsrlno": "D1", "sandbox": 1, "is_active": 1}
			if row == "found"
			else row
		)
		second = after or first
		rows = iter([first, second])
		with (
			patch.object(api, "require_any_role") as check,
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_settings_row", side_effect=lambda *a, **k: next(rows)),
			patch.object(api.frappe, "get_doc", return_value=record),
			patch.object(api.frappe, "db", MagicMock(get_value=MagicMock(return_value=tax_id))),
		):
			result = api.update_etims_settings("Acme", **kwargs)
		return result, record, check

	def test_only_managers_may_change_the_settings(self):
		_result, _record, check = self.update()
		self.assertEqual(check.call_args.args[0], api.ETIMS_ROLES)

	def test_a_business_with_no_settings_is_told_to_use_the_desk(self):
		with self.assertRaises(frappe.DoesNotExistError) as error:
			self.update(row=None)
		self.assertIn("Desk", str(error.exception))

	def test_a_changed_value_is_set_and_the_record_saved(self):
		after = {"name": "S", "tin": "P000000000A", "dvcsrlno": "D2", "sandbox": 1, "is_active": 1}
		result, record, _check = self.update(after=after, dvcsrlno="D2")
		record.set.assert_called_once_with("dvcsrlno", "D2")
		record.save.assert_called_once()
		self.assertEqual(result["changed"], ["dvcsrlno"])

	def test_the_pin_cannot_be_changed_here_because_it_comes_from_the_company(self):
		result, record, _check = self.update(tin="P111111111B")
		record.save.assert_not_called()
		self.assertEqual(result["ignored"], ["tin"])

	def test_a_company_with_no_tax_id_is_told_to_add_one_before_a_save(self):
		with self.assertRaises(frappe.ValidationError) as error:
			self.update(dvcsrlno="D2", tax_id=None)
		self.assertIn("Tax ID", str(error.exception))

	def test_a_value_that_is_already_right_does_not_save(self):
		result, record, _check = self.update(dvcsrlno="D1", sandbox=True)
		record.save.assert_not_called()
		self.assertEqual(result["changed"], [])

	def test_a_switch_the_kenya_app_puts_back_is_not_reported_as_changed(self):
		result, record, _check = self.update(is_active=0)
		record.save.assert_called_once()
		self.assertEqual(result["changed"], [])

	def test_ticks_sent_as_text_are_understood(self):
		_result, record, _check = self.update(sandbox="false")
		record.set.assert_called_once_with("sandbox", 0)

	def test_keys_the_screen_sends_that_are_not_kept_here_are_listed_as_ignored(self):
		result, record, _check = self.update(client_secret="x", server_url="http://a", auth_password="")
		record.save.assert_not_called()
		self.assertEqual(result["ignored"], ["client_secret", "server_url"])


class ReachabilityCase(unittest.TestCase):
	def test_the_screens_find_the_calls_where_they_look(self):
		for name in ("get_pos_profile", "get_etims_settings", "update_etims_settings"):
			self.assertIs(getattr(onboarding_api, name), getattr(api, name))


if __name__ == "__main__":
	unittest.main()
