# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the shop's brand. They need no database: every Frappe call is mocked.

What they pin down: a business with nothing saved has the defaults; only an owner may change the brand;
nobody can read or change another business's brand; every field is checked (accent and style from a list,
the logo only a public upload picture, text without markup and within its length); a field that is not
sent keeps its saved value; and the call that changes anything checks for an owner before it touches the
database.
"""

import ast
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import brand_api

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager")
CASHIER_ROLES = ("Sales User",)


class BrandCase(unittest.TestCase):
	def as_user(
		self,
		user="owner@shop-a.test",
		roles=OWNER_ROLES,
		companies=("Shop A",),
		stored=None,
		file_exists=True,
	):
		self.stored = dict(stored or {})
		db = MagicMock()
		db.exists.return_value = file_exists
		db.get_default.side_effect = lambda key, *a, **k: self.stored.get(key)
		db.set_default.side_effect = lambda key, value, *a, **k: self.stored.__setitem__(key, value)
		self.db = db
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestReading(BrandCase):
	def test_a_business_that_never_chose_has_the_defaults(self):
		self.as_user()
		data = brand_api.get_brand()["data"]
		self.assertEqual(data["company"], "Shop A")
		self.assertEqual({k: v for k, v in data.items() if k != "company"}, brand_api.defaults())
		self.assertEqual(data["accent"], "indigo")
		self.assertEqual(data["style"], "thermal80")

	def test_a_cashier_may_read_their_own_businesss_brand(self):
		stored = {brand_api._key("Shop A"): json.dumps({"tagline": "Fresh daily"})}
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES, stored=stored)
		self.assertEqual(brand_api.get_brand()["data"]["tagline"], "Fresh daily")

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			brand_api.get_brand()

	def test_another_businesss_brand_cannot_be_read(self):
		self.as_user(companies=("Shop A",))
		with self.assertRaises(frappe.PermissionError):
			brand_api.get_brand(company="Shop B")

	def test_a_damaged_saved_value_falls_back_field_by_field(self):
		for stored in ("not json", "[1,2]", json.dumps({"accent": "hotpink", "tagline": "Good"})):
			with self.subTest(stored=stored):
				self.as_user(stored={brand_api._key("Shop A"): stored})
				data = brand_api.get_brand()["data"]
				self.assertEqual(data["accent"], "indigo")
		self.assertEqual(data["tagline"], "Good")


class TestChanging(BrandCase):
	def test_an_owner_saves_and_reads_back(self):
		self.as_user()
		sent = {
			"logo": "/files/logo.png",
			"accent": "teal",
			"tagline": "Fresh every day",
			"footer": "Goods once sold can be exchanged within 7 days",
			"style": "a4",
			"social": "myshop",
			"website": "https://www.myshop.co.ke/",
		}
		saved = brand_api.set_brand(json.dumps(sent))["data"]
		self.assertEqual(saved["social"], "@myshop")
		self.assertEqual(saved["website"], "www.myshop.co.ke")
		again = brand_api.get_brand()["data"]
		self.assertEqual(again, saved)

	def test_a_field_that_is_not_sent_keeps_its_saved_value(self):
		self.as_user()
		brand_api.set_brand({"tagline": "Fresh", "accent": "red"})
		brand_api.set_brand({"footer": "Thanks"})
		data = brand_api.get_brand()["data"]
		self.assertEqual((data["tagline"], data["accent"], data["footer"]), ("Fresh", "red", "Thanks"))

	def test_saving_twice_gives_the_same_result(self):
		self.as_user()
		first = brand_api.set_brand({"tagline": "Fresh", "style": "thermal58"})
		second = brand_api.set_brand({"tagline": "Fresh", "style": "thermal58"})
		self.assertEqual(first, second)

	def test_a_logo_can_be_cleared(self):
		self.as_user()
		brand_api.set_brand({"logo": "/files/logo.png"})
		self.assertEqual(brand_api.set_brand({"logo": ""})["data"]["logo"], "")

	def test_a_cashier_cannot_change_it_and_nothing_is_written(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			brand_api.set_brand({"tagline": "x"})
		self.db.set_default.assert_not_called()

	def test_a_guest_cannot_change_it(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			brand_api.set_brand({"tagline": "x"})
		self.db.set_default.assert_not_called()

	def test_an_owner_cannot_change_another_businesss_brand(self):
		self.as_user(companies=("Shop A",))
		with self.assertRaises(frappe.PermissionError):
			brand_api.set_brand({"tagline": "x"}, company="Shop B")
		self.db.set_default.assert_not_called()

	def test_one_business_never_changes_another(self):
		self.as_user(companies=("Shop A", "Shop B"))
		brand_api.set_brand({"tagline": "A only"}, company="Shop A")
		self.assertEqual(brand_api.get_brand(company="Shop B")["data"]["tagline"], "")

	def test_a_logo_that_was_never_uploaded_is_refused(self):
		self.as_user(file_exists=False)
		with self.assertRaises(frappe.ValidationError):
			brand_api.set_brand({"logo": "/files/ghost.png"})
		self.db.set_default.assert_not_called()


class TestChecks(BrandCase):
	def refused(self, sent):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			brand_api.set_brand(sent)
		self.db.set_default.assert_not_called()

	def test_an_accent_off_the_list_is_refused(self):
		for accent in ("hotpink", "#ff0000", "", "Indigo; x", None):
			with self.subTest(accent=accent):
				self.refused({"accent": accent})

	def test_every_listed_accent_is_accepted_in_any_case(self):
		for accent in brand_api.ACCENTS:
			with self.subTest(accent=accent):
				self.as_user()
				self.assertEqual(brand_api.set_brand({"accent": accent.upper()})["data"]["accent"], accent)

	def test_a_background_off_the_list_is_refused_and_the_default_is_waves(self):
		for backdrop in ("fireworks", "#fff", "", None):
			with self.subTest(backdrop=backdrop):
				self.refused({"backdrop": backdrop})
		self.assertEqual(brand_api.defaults()["backdrop"], "waves")

	def test_every_listed_background_is_accepted_in_any_case(self):
		for backdrop in brand_api.BACKDROPS:
			with self.subTest(backdrop=backdrop):
				self.as_user()
				self.assertEqual(
					brand_api.set_brand({"backdrop": backdrop.upper()})["data"]["backdrop"], backdrop
				)

	def test_a_style_off_the_list_is_refused(self):
		self.refused({"style": "thermal100"})

	def test_the_logo_must_be_a_public_upload_picture(self):
		for logo in (
			"https://evil.test/logo.png",
			"/private/files/logo.png",
			"/files/../secret.png",
			"/files/logo.svg",
			"/files/logo.png.exe",
			"javascript:alert(1)",
			"/files/" + "a" * 250 + ".png",
		):
			with self.subTest(logo=logo):
				self.refused({"logo": logo})

	def test_text_length_and_markup(self):
		self.refused({"tagline": "x" * (brand_api.MAX_TAGLINE + 1)})
		self.refused({"footer": "x" * (brand_api.MAX_FOOTER + 1)})
		self.refused({"tagline": "<b>Hi</b>"})

	def test_text_at_the_limit_is_kept_and_tidied(self):
		self.as_user()
		data = brand_api.set_brand({"tagline": "  Fresh \n\t daily  ", "footer": "y" * brand_api.MAX_FOOTER})[
			"data"
		]
		self.assertEqual(data["tagline"], "Fresh daily")
		self.assertEqual(len(data["footer"]), brand_api.MAX_FOOTER)

	def test_handles_and_websites(self):
		for bad in (
			{"social": "my shop"},
			{"social": "@" + "a" * 40},
			{"website": "not a site"},
			{"website": "javascript:x.com"},
		):
			with self.subTest(bad=bad):
				self.refused(bad)

	def test_an_unknown_field_or_unreadable_body_is_refused(self):
		self.refused({"password": "x"})
		self.refused("{not json")
		self.refused("[1]")


class TestKey(unittest.TestCase):
	def test_each_business_has_its_own_key(self):
		self.assertNotEqual(brand_api._key("Shop A"), brand_api._key("Shop B"))

	def test_a_very_long_company_name_still_fits_and_stays_unique(self):
		a = brand_api._key("A" * 200)
		self.assertLessEqual(len(a), brand_api.MAX_KEY_LENGTH)
		self.assertNotEqual(a, brand_api._key("A" * 199 + "B"))

	def test_the_brand_key_is_not_the_new_look_key(self):
		self.assertNotEqual(brand_api.KEY_PREFIX, "murzak_new_look")


class TestGuardIsNotForgotten(unittest.TestCase):
	def test_the_call_that_changes_the_brand_checks_for_an_owner_first(self):
		path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_api.py")
		with open(path) as handle:
			source = handle.read()
		node = next(
			n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "set_brand"
		)
		body = ast.get_source_segment(source, node)
		call = body.find("require_manager()")
		touching = [body.find(word) for word in ("frappe.db", "resolve_company(")]
		self.assertNotEqual(call, -1)
		self.assertTrue(all(call < position for position in touching if position != -1))
		self.assertIn('methods=["POST"]', ast.unparse(node.decorator_list[0]).replace("'", '"'))
