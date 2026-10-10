"""
Two-step sign-in with an authenticator app, and the account lockout used at sign-in.

Each user turns two-step sign-in on for themselves (Settings > Security). The
secret is stored encrypted in the user's defaults; while setting up, a pending
secret is kept until the user proves their app works by entering a code.
The rules (lockout counting, code checking) live in login_guard.py.
"""

from __future__ import annotations

import time

import frappe
from frappe import _
from frappe.utils.password import decrypt, encrypt

from techsavanna_pos.api.login_guard import (
	LOCK_SECONDS,
	failure_key,
	lock_message,
	lock_seconds_left,
	new_secret,
	provisioning_uri,
	record_failure,
	verify_code,
)

SECRET_KEY = "_pos_totp_secret"
PENDING_KEY = "_pos_totp_pending"

# ---------------------------------------------------------------------------
# Lockout helpers for the sign-in endpoints
# ---------------------------------------------------------------------------


def check_not_locked(user: str) -> None:
	"""Refuse sign-in while the account is locked after repeated failures."""
	left = lock_seconds_left(frappe.cache().get_value(failure_key(user)), time.time())
	if left:
		frappe.throw(_(lock_message(left)), frappe.AuthenticationError)


def note_failure(user: str) -> None:
	cache = frappe.cache()
	key = failure_key(user)
	cache.set_value(key, record_failure(cache.get_value(key), time.time()), expires_in_sec=LOCK_SECONDS * 2)


def clear_failures(user: str) -> None:
	frappe.cache().delete_value(failure_key(user))


# ---------------------------------------------------------------------------
# Stored secrets
# ---------------------------------------------------------------------------


def _get_secret(user: str, key: str = SECRET_KEY) -> str | None:
	stored = frappe.db.get_value("DefaultValue", {"parent": user, "defkey": key}, "defvalue")
	if not stored:
		return None
	try:
		return decrypt(stored)
	except Exception:
		return None


def _set_secret(user: str, secret: str | None, key: str = SECRET_KEY) -> None:
	frappe.db.delete("DefaultValue", {"parent": user, "defkey": key})
	if secret:
		frappe.defaults.add_default(key, encrypt(secret), parent=user)
	frappe.clear_cache(user=user)


def is_enabled(user: str) -> bool:
	return bool(_get_secret(user))


def check_sign_in_code(user: str, otp) -> dict | None:
	"""
	For users with two-step sign-in: None when the code is right, otherwise the
	reply to send back (asking for a code, or saying it was wrong). The caller
	counts a wrong code as a failed attempt.
	"""
	secret = _get_secret(user)
	if not secret:
		return None
	if not otp:
		return {
			"success": False,
			"two_factor_required": True,
			"message": _("Enter the 6-digit code from your authenticator app."),
		}
	if not verify_code(secret, otp):
		return {
			"success": False,
			"two_factor_required": True,
			"message": _("That code is not correct. Check the time on your phone and try the newest code."),
		}
	return None


# ---------------------------------------------------------------------------
# Settings > Security
# ---------------------------------------------------------------------------


def _signed_in_user() -> str:
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return user


@frappe.whitelist()
def get_two_factor_status() -> dict:
	user = _signed_in_user()
	return {"success": True, "data": {"enabled": is_enabled(user)}}


@frappe.whitelist(methods=["POST"])
def start_two_factor_setup() -> dict:
	"""Create a new secret for the user's app. It only takes effect after confirm_two_factor_setup."""
	user = _signed_in_user()
	secret = new_secret()
	_set_secret(user, secret, PENDING_KEY)
	return {
		"success": True,
		"data": {
			"secret": secret,
			"otpauth_uri": provisioning_uri(secret, user),
		},
	}


@frappe.whitelist(methods=["POST"])
def confirm_two_factor_setup(otp: str | None = None) -> dict:
	user = _signed_in_user()
	pending = _get_secret(user, PENDING_KEY)
	if not pending:
		return {"success": False, "message": _("Start the setup again; the setup code has expired.")}
	if not verify_code(pending, otp):
		return {
			"success": False,
			"message": _("That code is not correct. Try the newest code from your app."),
		}
	_set_secret(user, pending)
	_set_secret(user, None, PENDING_KEY)
	return {"success": True, "message": _("Two-step sign-in is on.")}


@frappe.whitelist(methods=["POST"])
def disable_two_factor(otp: str | None = None) -> dict:
	"""Turning it off needs a current code, so a borrowed signed-in screen cannot remove it."""
	user = _signed_in_user()
	secret = _get_secret(user)
	if not secret:
		return {"success": True, "message": _("Two-step sign-in was already off.")}
	if not verify_code(secret, otp):
		return {
			"success": False,
			"message": _("That code is not correct. Try the newest code from your app."),
		}
	_set_secret(user, None)
	return {"success": True, "message": _("Two-step sign-in is off.")}


@frappe.whitelist(methods=["POST"])
def reset_two_factor_for_user(user: str) -> dict:
	"""For a staff member who lost their phone: an owner or System Manager turns it off for them."""
	_signed_in_user()
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only the owner can reset two-step sign-in for someone else."), frappe.PermissionError)
	from techsavanna_pos.api.payment_gateway_common import get_user_companies

	if frappe.session.user != "Administrator" and not (get_user_companies() & get_user_companies(user)):
		frappe.throw(_("That user does not work for your business."), frappe.PermissionError)
	_set_secret(user, None)
	_set_secret(user, None, PENDING_KEY)
	clear_failures(user)
	return {"success": True, "message": _("Two-step sign-in was turned off for {0}.").format(user)}
