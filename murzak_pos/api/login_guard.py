"""
Sign-in protection: account lockout after repeated failures, and authenticator-app codes.

Kept free of Frappe imports so it can be tested on its own. auth_api.py and
two_factor_api.py wire these rules to Frappe's cache and user settings.

Lockout: 5 wrong passwords or codes for one account within 15 minutes lock that
account for 15 minutes. It is counted per account, not per network address,
because a whole shop usually shares one internet connection.

Codes: standard time-based one-time passwords (RFC 6238, the 6-digit codes shown
by Google Authenticator, Microsoft Authenticator and similar apps).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60

TOTP_DIGITS = 6
TOTP_PERIOD = 30
# Accept the code before and after the current one, for phones whose clock is slightly off.
TOTP_DRIFT_STEPS = 1


# ---------------------------------------------------------------------------
# Lockout
# ---------------------------------------------------------------------------


def failure_key(user: str) -> str:
	return f"pos_login_failures:{(user or '').strip().lower()}"


def lock_seconds_left(record: dict | None, now: float) -> int:
	"""Seconds until the account may try again, or 0 when it is not locked."""
	if not record or not record.get("locked_until"):
		return 0
	return max(0, int(record["locked_until"] - now))


def record_failure(record: dict | None, now: float) -> dict:
	"""Add one failure; lock the account when the limit is reached inside the window."""
	recent = [t for t in (record or {}).get("failures", []) if now - t < WINDOW_SECONDS]
	recent.append(now)
	updated = {"failures": recent, "locked_until": None}
	if len(recent) >= MAX_FAILURES:
		updated = {"failures": [], "locked_until": now + LOCK_SECONDS}
	return updated


def attempts_left(record: dict | None, now: float) -> int:
	recent = [t for t in (record or {}).get("failures", []) if now - t < WINDOW_SECONDS]
	return max(0, MAX_FAILURES - len(recent))


def lock_message(seconds: int) -> str:
	minutes = max(1, -(-seconds // 60))  # round up
	unit = "minute" if minutes == 1 else "minutes"
	return f"Too many failed sign-in attempts. Try again in {minutes} {unit}, or reset your password."


# ---------------------------------------------------------------------------
# Authenticator-app codes (TOTP)
# ---------------------------------------------------------------------------


def new_secret() -> str:
	"""A random 160-bit secret in the base32 form authenticator apps expect."""
	return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
	clean = (secret or "").replace(" ", "").upper()
	return base64.b32decode(clean + "=" * (-len(clean) % 8))


def code_at(secret: str, at: float, digits: int = TOTP_DIGITS) -> str:
	counter = int(at // TOTP_PERIOD)
	digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
	offset = digest[-1] & 0x0F
	value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
	return str(value % (10**digits)).zfill(digits)


def verify_code(secret: str, code, at: float | None = None) -> bool:
	"""True when `code` matches the current 30-second step or one step either side."""
	entered = "".join(ch for ch in str(code or "") if ch.isdigit())
	if len(entered) != TOTP_DIGITS or not secret:
		return False
	at = time.time() if at is None else at
	return any(
		hmac.compare_digest(code_at(secret, at + step * TOTP_PERIOD), entered)
		for step in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1)
	)


def provisioning_uri(secret: str, account: str, issuer: str = "Murzak POS") -> str:
	"""The otpauth:// link an authenticator app reads from a QR code."""
	label = quote(f"{issuer}:{account}")
	return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={TOTP_DIGITS}&period={TOTP_PERIOD}"
