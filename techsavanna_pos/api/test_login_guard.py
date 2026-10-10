# Plain unit tests for sign-in lockout and authenticator codes. They need no Frappe site:
#   python -m unittest techsavanna_pos.api.test_login_guard
import base64
import unittest

from techsavanna_pos.api.login_guard import (
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
