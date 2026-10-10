import unittest

from techsavanna_pos.api.password_reset_codes import (
    CODE_TTL_SECONDS,
    MAX_WRONG_ATTEMPTS,
    RESEND_COOLDOWN_SECONDS,
    check_code,
    cooldown_left,
    new_code,
    new_record,
)


class TestPasswordResetCodes(unittest.TestCase):
    def test_new_code_is_six_digits(self):
        code = new_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_code_is_not_stored_in_plain_text(self):
        record = new_record("123456", now=1000)
        self.assertNotIn("123456", repr(record))

    def test_right_code_with_spaces_is_ok(self):
        record = new_record("123456", now=1000)
        self.assertEqual(check_code(record, "123 456", now=1001), "ok")

    def test_wrong_missing_expired_locked(self):
        record = new_record("123456", now=1000)
        self.assertEqual(check_code(record, "654321", now=1001), "wrong")
        self.assertEqual(check_code(None, "123456", now=1001), "missing")
        self.assertEqual(check_code(record, "123456", now=1000 + CODE_TTL_SECONDS + 1), "expired")
        record["wrong"] = MAX_WRONG_ATTEMPTS
        self.assertEqual(check_code(record, "123456", now=1001), "locked")

    def test_cooldown(self):
        record = new_record("123456", now=1000)
        self.assertEqual(cooldown_left(record, now=1000), RESEND_COOLDOWN_SECONDS)
        self.assertEqual(cooldown_left(record, now=1000 + RESEND_COOLDOWN_SECONDS), 0)
        self.assertEqual(cooldown_left(None, now=1000), 0)


if __name__ == "__main__":
    unittest.main()
