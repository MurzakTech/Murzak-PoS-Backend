# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Replacing the authenticator app needs a current code from the old one. Needs no database.

Turning two-step sign-in off already asked for a current code "so a borrowed signed-in screen cannot remove it".
Starting a new setup did not, and confirming it simply replaced the stored secret: someone on a borrowed screen
could put the secret in their own app and take the second step away from its owner. These tests replay that.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import login_guard
from murzak_pos.api import two_factor_api as api

OWNER = "owner@shop.test"
OWNERS_APP = login_guard.new_secret()  # the secret in the owner's authenticator app
THIEFS_APP = login_guard.new_secret()  # the secret in the thief's own app


def code_now(secret):
	return login_guard.code_at(secret, time.time())


class FakeCache:
	"""Just enough of frappe.cache() for the lockout counters."""

	def __init__(self):
		self.values = {}

	def get_value(self, key):
		return self.values.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.values[key] = value

	def delete_value(self, key):
		self.values.pop(key, None)


class TwoStepSetupCase(unittest.TestCase):
	def setUp(self):
		self.secrets = {}  # (user, key) -> secret
		self.cache = FakeCache()
		patches = [
			patch.object(frappe, "session", MagicMock(user=OWNER), create=True),
			patch.object(frappe, "cache", lambda: self.cache),
			patch.object(
				api, "_get_secret", side_effect=lambda user, key=api.SECRET_KEY: self.secrets.get((user, key))
			),
			patch.object(api, "_set_secret", side_effect=self._set),
			patch.object(api, "_address", return_value="10.0.0.9"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def _set(self, user, secret, key=api.SECRET_KEY):
		if secret:
			self.secrets[(user, key)] = secret
		else:
			self.secrets.pop((user, key), None)

	def owner_has_two_step_on(self):
		self.secrets[(OWNER, api.SECRET_KEY)] = OWNERS_APP

	def active_secret(self):
		return self.secrets.get((OWNER, api.SECRET_KEY))

	def pending_secret(self):
		return self.secrets.get((OWNER, api.PENDING_KEY))


class TestTurningItOn(TwoStepSetupCase):
	def test_with_two_step_off_a_setup_starts_without_a_code(self):
		reply = api.start_two_factor_setup()
		self.assertTrue(reply["success"])
		self.assertEqual(self.pending_secret(), reply["data"]["secret"])
		self.assertIsNone(self.active_secret())

	def test_confirming_with_the_new_apps_code_turns_it_on(self):
		pending = api.start_two_factor_setup()["data"]["secret"]
		reply = api.confirm_two_factor_setup(otp=code_now(pending))
		self.assertTrue(reply["success"])
		self.assertEqual(self.active_secret(), pending)
		self.assertIsNone(self.pending_secret())


class TestReplacingTheApp(TwoStepSetupCase):
	def setUp(self):
		super().setUp()
		self.owner_has_two_step_on()

	def test_the_attack_on_a_borrowed_screen_no_longer_works(self):
		"""Start, put the secret in your own app, confirm. This is exactly what used to work."""
		reply = api.start_two_factor_setup()  # no code: the thief does not have the owner's phone
		self.assertFalse(reply["success"])
		self.assertNotIn("data", reply)
		self.assertIsNone(self.pending_secret(), "no secret was handed out")

		# They cannot skip ahead and confirm either: nothing is pending
		confirm = api.confirm_two_factor_setup(otp=code_now(THIEFS_APP))
		self.assertFalse(confirm["success"])
		self.assertEqual(self.active_secret(), OWNERS_APP, "the owner's app is still the one that counts")

	def test_a_wrong_code_does_not_start_a_replacement(self):
		reply = api.start_two_factor_setup(otp="000000")
		self.assertFalse(reply["success"])
		self.assertIsNone(self.pending_secret())
		self.assertEqual(self.active_secret(), OWNERS_APP)

	def test_the_old_apps_current_code_starts_a_replacement(self):
		reply = api.start_two_factor_setup(otp=code_now(OWNERS_APP))
		self.assertTrue(reply["success"])
		new = reply["data"]["secret"]
		self.assertEqual(self.active_secret(), OWNERS_APP, "nothing changes until the new app proves itself")
		self.assertTrue(api.confirm_two_factor_setup(otp=code_now(new))["success"])
		self.assertEqual(self.active_secret(), new)

	def test_a_code_from_the_thiefs_own_app_does_not_help_them(self):
		reply = api.start_two_factor_setup(otp=code_now(THIEFS_APP))
		self.assertFalse(reply["success"])
		self.assertEqual(self.active_secret(), OWNERS_APP)

	def test_wrong_codes_here_count_toward_the_sign_in_lockout(self):
		"""Otherwise the six digits could be guessed here as often as you like."""
		for _attempt in range(login_guard.MAX_FAILURES):
			api.start_two_factor_setup(otp="000000")
		with self.assertRaises(frappe.AuthenticationError):
			api.start_two_factor_setup(
				otp=code_now(OWNERS_APP)
			)  # even the right code is refused while locked
		self.assertIsNone(self.pending_secret())

	def test_a_right_code_does_not_leave_a_failure_behind(self):
		api.start_two_factor_setup(otp="000000")
		api.start_two_factor_setup(otp=code_now(OWNERS_APP))
		self.assertEqual(self.cache.get_value(login_guard.failure_key(OWNER, "10.0.0.9")), None)


class TestTurningItOff(TwoStepSetupCase):
	def setUp(self):
		super().setUp()
		self.owner_has_two_step_on()

	def test_the_right_code_turns_it_off(self):
		self.assertTrue(api.disable_two_factor(otp=code_now(OWNERS_APP))["success"])
		self.assertIsNone(self.active_secret())

	def test_a_wrong_code_does_not(self):
		reply = api.disable_two_factor(otp="000000")
		self.assertFalse(reply["success"])
		self.assertEqual(self.active_secret(), OWNERS_APP)

	def test_wrong_codes_count_toward_the_lockout_here_too(self):
		for _attempt in range(login_guard.MAX_FAILURES):
			api.disable_two_factor(otp="000000")
		with self.assertRaises(frappe.AuthenticationError):
			api.disable_two_factor(otp=code_now(OWNERS_APP))
		self.assertEqual(self.active_secret(), OWNERS_APP)


if __name__ == "__main__":
	unittest.main()
