"""
The one-time codes used to reset a forgotten password.

Kept free of Frappe imports so it can be tested on its own. The endpoints that
email the code and set the new password are in password_reset.py.

Only a salted hash of each code is stored, so someone who can read the server
cache still cannot see a working code. Codes are compared in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string

CODE_LENGTH = 6
CODE_TTL_SECONDS = 15 * 60  # a code works for 15 minutes
MAX_WRONG_ATTEMPTS = 5  # then the code is cancelled and a new one is needed
RESEND_COOLDOWN_SECONDS = 60  # at most one email a minute per account


def new_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(string.digits) for _ in range(length))


def hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def new_record(code: str, now: float) -> dict:
    """What is kept in the cache for one account while a reset is pending."""
    salt = secrets.token_hex(16)
    return {"salt": salt, "hash": hash_code(code, salt), "created": now, "wrong": 0}


def normalise_code(code) -> str:
    """People paste codes with spaces or dashes ("123 456"); keep only the digits."""
    return "".join(ch for ch in str(code or "") if ch.isdigit())


def check_code(record: dict | None, code, now: float) -> str:
    """
    "ok" when the code matches, otherwise why not:
    "missing" (no reset requested, or already used), "expired",
    "locked" (too many wrong tries) or "wrong".
    """
    if not record:
        return "missing"
    if now - float(record.get("created", 0)) > CODE_TTL_SECONDS:
        return "expired"
    if int(record.get("wrong", 0)) >= MAX_WRONG_ATTEMPTS:
        return "locked"
    given = hash_code(normalise_code(code), record.get("salt", ""))
    return "ok" if hmac.compare_digest(given, record.get("hash", "")) else "wrong"


def cooldown_left(record: dict | None, now: float) -> int:
    """Seconds before another code may be emailed for this account."""
    if not record:
        return 0
    return max(0, int(RESEND_COOLDOWN_SECONDS - (now - float(record.get("created", 0)))))
