# Plain unit tests for sign-in lockout and authenticator codes. They need no Frappe site:
#   python -m unittest murzak_pos.api.test_login_guard
import base64
import unittest

from murzak_pos.api.login_guard import (
	LOCK_SECONDS,
	MAX_FAILURES,
	WINDOW_SECONDS,
	attempts_left,
	code_at,
	failure_key,
	lock_message,
	lock_seconds_left,
	new_secret,
	provisioning_uri,
	record_failure,
	verify_code,
)

# RFC 6238 test secret: the ASCII bytes "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


class TestLockout(unittest.TestCase):
	def test_locks_after_max_failures_in_window(self):
		record, now = None, 1000.0
		for i in range(MAX_FAILURES - 1):
			record = record_failure(record, now + i)
			self.assertEqual(lock_seconds_left(record, now + i), 0)
		self.assertEqual(attempts_left(record, now + 10), 1)
		record = record_failure(record, now + 10)
		self.assertEqual(lock_seconds_left(record, now + 10), LOCK_SECONDS)
		self.assertEqual(lock_seconds_left(record, now + 10 + LOCK_SECONDS), 0)

	def test_old_failures_fall_out_of_the_window(self):
		record = None
		for i in range(MAX_FAILURES - 1):
			record = record_failure(record, 0.0 + i)
		record = record_failure(record, WINDOW_SECONDS + 100)
		self.assertEqual(lock_seconds_left(record, WINDOW_SECONDS + 100), 0)
		self.assertEqual(attempts_left(record, WINDOW_SECONDS + 100), MAX_FAILURES - 1)

	def test_key_ignores_case_and_spaces(self):
		self.assertEqual(failure_key(" Jane@Shop.KE "), failure_key("jane@shop.ke"))

	def test_message(self):
		self.assertIn("15 minutes", lock_message(LOCK_SECONDS))
		self.assertIn("1 minute,", lock_message(20))


class TestCodes(unittest.TestCase):
	def test_rfc6238_vectors(self):
		# RFC 6238 appendix B (SHA-1), last 6 of the 8 digits.
		self.assertEqual(code_at(RFC_SECRET, 59), "287082")
		self.assertEqual(code_at(RFC_SECRET, 1111111109), "081804")
		self.assertEqual(code_at(RFC_SECRET, 1234567890), "005924")

	def test_verify_allows_one_step_of_clock_drift(self):
		now = 1234567890
		self.assertTrue(verify_code(RFC_SECRET, "005924", now))
		self.assertTrue(verify_code(RFC_SECRET, code_at(RFC_SECRET, now - 30), now))
		self.assertTrue(verify_code(RFC_SECRET, "005 924", now))
		self.assertFalse(verify_code(RFC_SECRET, code_at(RFC_SECRET, now - 90), now))
		self.assertFalse(verify_code(RFC_SECRET, "12345", now))
		self.assertFalse(verify_code("", "005924", now))

	def test_new_secret_and_uri(self):
		secret = new_secret()
		self.assertEqual(len(secret), 32)
		self.assertEqual(len(code_at(secret, 0)), 6)
		uri = provisioning_uri(secret, "jane@shop.ke")
		self.assertTrue(uri.startswith("otpauth://totp/Murzak%20POS%3Ajane%40shop.ke?secret="))
		self.assertIn("issuer=Murzak%20POS", uri)


if __name__ == "__main__":
	unittest.main()


class TestLockoutIsPerAddress(unittest.TestCase):
	"""A stranger elsewhere cannot lock a cashier out; guessing from many addresses is still capped."""

	def setUp(self):
		from unittest.mock import MagicMock, patch

		import frappe

		store = {}
		cache = MagicMock()
		cache.get_value.side_effect = store.get
		cache.set_value.side_effect = lambda key, value, expires_in_sec=None: store.__setitem__(key, value)
		cache.delete_value.side_effect = lambda key: store.pop(key, None)
		self.local = MagicMock(request_ip="41.90.0.1")
		for p in (
			patch.object(frappe, "cache", return_value=cache, create=True),
			patch.object(frappe, "local", self.local, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)
		from murzak_pos.api import two_factor_api

		self.api = two_factor_api
		self.frappe = frappe

	def fail(self, times, address):
		self.local.request_ip = address
		for _ in range(times):
			self.api.note_failure("till@shop.ke")

	def locked_at(self, address):
		self.local.request_ip = address
		try:
			self.api.check_not_locked("till@shop.ke")
		except self.frappe.AuthenticationError:
			return True
		return False

	def test_failures_from_one_address_lock_only_that_address(self):
		self.fail(5, "102.0.0.9")  # a stranger somewhere else
		self.assertTrue(self.locked_at("102.0.0.9"))
		self.assertFalse(self.locked_at("41.90.0.1"))  # the shop still signs in

	def test_the_owner_of_the_account_is_still_locked_after_their_own_failures(self):
		self.fail(5, "41.90.0.1")
		self.assertTrue(self.locked_at("41.90.0.1"))

	def test_many_addresses_together_lock_the_account_everywhere(self):
		for n in range(8):
			self.fail(4, f"102.0.0.{n}")  # under the limit at each address, 32 in all
		self.assertTrue(self.locked_at("41.90.0.1"))


class TestALockIsKept(unittest.TestCase):
	def test_more_failures_while_locked_do_not_lift_the_lock(self):
		from murzak_pos.api.login_guard import lock_seconds_left, record_failure

		record = None
		for second in range(5):
			record = record_failure(record, 1000.0 + second)
		self.assertGreater(lock_seconds_left(record, 1010.0), 0)
		record = record_failure(record, 1010.0)
		self.assertGreater(lock_seconds_left(record, 1011.0), 0)
