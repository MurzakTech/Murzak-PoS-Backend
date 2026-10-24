# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Sign-up over the web is closed: each business gets its own site from murzaktech.tech.

Needs no database: every Frappe call is mocked.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import auth, auth_api, signup_policy, verification_api


class PolicyCase(unittest.TestCase):
	def given(self, web=True, conf=None, user="Guest", companies_on_site=3, my_companies=()):
		local = MagicMock()
		local.response = {}
		if not web:
			del local.request
		db = MagicMock()
		db.count.return_value = companies_on_site
		for p in (
			patch.object(frappe, "local", local, create=True),
			patch.object(frappe, "conf", frappe._dict(conf or {}), create=True),
			patch.object(frappe, "session", frappe._dict(user=user), create=True),
			patch.object(frappe, "db", db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(my_companies)),
		):
			p.start()
			self.addCleanup(p.stop)
		return local


class TestSignUp(PolicyCase):
	def test_a_web_sign_up_is_refused_and_sent_to_the_website(self):
		local = self.given()
		reply = signup_policy.signup_closed_reply()
		self.assertEqual((reply["success"], reply["code"]), (False, "SIGNUP_CLOSED"))
		self.assertEqual(reply["signup_url"], "https://murzaktech.tech")
		self.assertEqual(local.response["http_status_code"], 403)

	def test_the_provisioning_script_on_the_server_is_never_refused(self):
		self.given(web=False)
		self.assertIsNone(signup_policy.signup_closed_reply())

	def test_a_site_can_open_sign_up_and_choose_where_people_are_sent(self):
		self.given(conf={"murzak_pos_signup_open": 1})
		self.assertIsNone(signup_policy.signup_closed_reply())
		self.given(conf={"murzak_pos_signup_url": "https://murzaktech.tech/pos"})
		self.assertEqual(signup_policy.signup_closed_reply()["signup_url"], "https://murzaktech.tech/pos")

	def test_both_sign_up_calls_refuse_before_creating_anyone(self):
		self.given()
		with patch.object(frappe, "new_doc", create=True) as new_doc:
			for module in (auth_api, auth):
				with self.subTest(module=module.__name__):
					reply = module.register_user(
						email="new@shop.test", first_name="New", last_name="Owner", password="Str0ng!pass"
					)
					self.assertEqual(reply["code"], "SIGNUP_CLOSED")
		new_doc.assert_not_called()

	def test_sign_up_codes_are_not_sent(self):
		self.given()
		with patch.object(frappe, "sendmail", create=True) as sendmail:
			self.assertEqual(
				verification_api.send_email_verification("new@shop.test")["code"], "SIGNUP_CLOSED"
			)
			self.assertEqual(verification_api.send_phone_verification("0712345678")["code"], "SIGNUP_CLOSED")
		sendmail.assert_not_called()

	def test_nobody_can_ask_whether_an_email_has_an_account_while_sign_up_is_closed(self):
		self.given()
		self.assertEqual(auth_api.check_email("someone@shop.test")["code"], "SIGNUP_CLOSED")
		frappe.db.exists.assert_not_called()

	def test_the_sign_up_page_can_check_an_email_when_sign_up_is_open(self):
		self.given(conf={"murzak_pos_signup_open": 1})
		frappe.db.exists.return_value = None
		self.assertEqual(auth_api.check_email("someone@shop.test"), {"exists": None})


class TestNewBusiness(PolicyCase):
	def test_someone_with_no_business_cannot_add_one_to_a_site_that_has_businesses(self):
		self.given(user="stranger@x.test")
		with self.assertRaises(frappe.PermissionError):
			signup_policy.refuse_new_business_if_closed()

	def test_a_shops_own_site_gets_its_first_business(self):
		self.given(user="owner@shop.test", companies_on_site=0)
		signup_policy.refuse_new_business_if_closed()

	def test_a_business_already_here_may_add_another_and_scripts_pass(self):
		self.given(user="owner@a.test", my_companies=("A",))
		signup_policy.refuse_new_business_if_closed()
		self.given(user="stranger@x.test", web=False)
		signup_policy.refuse_new_business_if_closed()
		self.given(user="Administrator")
		signup_policy.refuse_new_business_if_closed()

	def test_creating_a_company_asks_first(self):
		from murzak_pos.api import onboarding_api

		self.given(user="stranger@x.test")
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			self.assertRaises(frappe.PermissionError),
		):
			onboarding_api.create_company(company_name="Gamma", abbr="GM")
		new_doc.assert_not_called()


if __name__ == "__main__":
	unittest.main()
