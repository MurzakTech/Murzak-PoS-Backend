import unittest

from murzak_pos.api.staff_signin_calc import sign_in_report, sign_in_url, welcome_message

GOOD = {
	"exists": True,
	"enabled": True,
	"user_type": "System User",
	"roles": ["Sales User"],
	"same_business": True,
	"has_password": True,
	"locked_minutes": 0,
	"two_factor": False,
	"email_ready": True,
}


def status(report, check_id):
	return next(c["status"] for c in report["checks"] if c["id"] == check_id)


class TestSignInReport(unittest.TestCase):
	def test_a_healthy_account_is_all_clear(self):
		report = sign_in_report(GOOD)
		self.assertTrue(report["ok"])
		self.assertEqual({c["status"] for c in report["checks"]}, {"green"})
		self.assertIn("Set a new one", report["summary"])

	def test_no_account_stops_at_once(self):
		report = sign_in_report({"exists": False})
		self.assertFalse(report["ok"])
		self.assertEqual(len(report["checks"]), 1)

	def test_another_business_is_not_inspected(self):
		report = sign_in_report({**GOOD, "same_business": False})
		self.assertFalse(report["ok"])
		self.assertEqual([c["id"] for c in report["checks"]], ["account", "business"])

	def test_each_red_reason_is_named_with_what_to_do(self):
		for key, value, check in (
			("enabled", False, "enabled"),
			("has_password", False, "password"),
			("roles", [], "roles"),
		):
			report = sign_in_report({**GOOD, key: value})
			self.assertFalse(report["ok"], key)
			self.assertEqual(status(report, check), "red")
			self.assertTrue(next(c["fix"] for c in report["checks"] if c["id"] == check))

	def test_lock_two_step_and_no_email_are_worth_a_look_not_blockers(self):
		for key, value, check in (
			("locked_minutes", 4, "lock"),
			("two_factor", True, "two_factor"),
			("email_ready", False, "email"),
		):
			report = sign_in_report({**GOOD, key: value})
			self.assertTrue(report["ok"], key)
			self.assertEqual(status(report, check), "amber")

	def test_the_summary_leads_with_the_first_blocker(self):
		report = sign_in_report({**GOOD, "enabled": False, "has_password": False})
		self.assertIn("deactivated", report["summary"])


class TestSignInUrl(unittest.TestCase):
	def test_takes_the_page_the_owner_is_on(self):
		self.assertEqual(
			sign_in_url("https://shop.pos.example/", "https://other.example"), "https://shop.pos.example"
		)

	def test_falls_back_to_the_configured_address(self):
		self.assertEqual(sign_in_url(None, "https://other.example"), "https://other.example")

	def test_refuses_anything_that_is_not_a_plain_web_address(self):
		for bad in (
			"javascript:alert(1)",
			"ftp://x.example",
			"https://x.example/path/deep",
			"not a url",
			"",
			None,
		):
			self.assertEqual(sign_in_url(bad, ""), "", bad)


class TestWelcomeMessage(unittest.TestCase):
	def test_says_where_to_sign_in_and_never_holds_a_password(self):
		subject, body = welcome_message(
			"Linda mwananchi", "Amina", "amina@shop.test", "https://shop.pos.example", ["Sales User"]
		)
		self.assertIn("Linda mwananchi", subject)
		self.assertIn("https://shop.pos.example", body)
		self.assertIn("amina@shop.test", body)
		self.assertIn("Forgot password", body)
		self.assertNotIn("password:", body.lower())

	def test_what_a_person_typed_is_escaped(self):
		_subject, body = welcome_message("A<b>Shop</b>", "<script>alert(1)</script>", "x@y.test", "")
		self.assertNotIn("<script>", body)
		self.assertNotIn("<b>Shop</b>", body)
		self.assertIn("Ask your owner for the sign-in address", body)
