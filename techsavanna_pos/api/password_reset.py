"""
Self-service password reset.

1. request_password_reset(identifier): the person types the email address (or
   phone number) on their account. If it matches an active account, a 6-digit
   code is emailed to that account's address. The reply is the same whether or
   not an account matched, so this page cannot be used to find out who has an
   account.
2. reset_password_with_code(identifier, code, new_password): checks the code
   and sets the new password, which must meet the same rules as at sign-up.
   All existing sign-ins for the account are ended.

Email is sent through the site's default outgoing Email Account
(ERPNext: Settings > Email Account, tick "Default Outgoing").
"""

import time

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from techsavanna_pos.api.auth_api import validate_password_strength
from techsavanna_pos.api.password_reset_codes import (
    CODE_TTL_SECONDS,
    check_code,
    cooldown_left,
    new_code,
    new_record,
)

# Accounts that must never be reset from a public page
PROTECTED_USERS = ("Administrator", "Guest")

SENT_MESSAGE = _(
    "If that matches an account, we have emailed a 6-digit code to the address on it. "
    "It works for 15 minutes. Check your spam folder if it has not arrived."
)


def _cache_key(user: str) -> str:
    return f"techsavanna_pos:password_reset:{user}"


def _find_user(identifier: str):
    """The active account for an email address or phone number, or None."""
    value = (identifier or "").strip()
    if not value:
        return None
    if "@" in value:
        user = frappe.db.get_value("User", {"name": value.lower(), "enabled": 1}, "name") or frappe.db.get_value(
            "User", {"email": value, "enabled": 1}, "name"
        )
    else:
        digits = "".join(ch for ch in value if ch.isdigit())
        candidates = {value, digits, f"+{digits}"}
        if digits.startswith("0") and len(digits) == 10:  # 0712345678 -> 254712345678
            candidates |= {f"254{digits[1:]}", f"+254{digits[1:]}"}
        if digits.startswith("254"):
            candidates |= {f"0{digits[3:]}"}
        user = frappe.db.get_value("User", {"mobile_no": ["in", list(candidates)], "enabled": 1}, "name")
    if not user or user in PROTECTED_USERS:
        return None
    return user


def _send_code_email(user: str, code: str) -> None:
    email, first_name = frappe.db.get_value("User", user, ["email", "first_name"])
    frappe.sendmail(
        recipients=[email],
        subject=_("Your Murzak POS password reset code: {0}").format(code),
        message=_(
            """
            <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto;">
              <p>Hello {name},</p>
              <p>Use this code to set a new password for your Murzak POS account:</p>
              <div style="background:#f3f2ff; padding:18px; text-align:center; border-radius:8px; margin:18px 0;">
                <span style="font-size:30px; letter-spacing:6px; font-weight:700; color:#4f46e5;">{code}</span>
              </div>
              <p>The code works for {minutes} minutes. If you did not ask to reset your password,
              you can ignore this email; your password stays the same.</p>
              <p style="color:#666; font-size:12px;">Murzak POS</p>
            </div>
            """
        ).format(name=first_name or _("there"), code=code, minutes=CODE_TTL_SECONDS // 60),
        delayed=False,
        retry=2,
    )


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=60 * 60)
def request_password_reset(identifier: str = None):
    user = _find_user(identifier)
    if not user:
        return {"success": True, "message": SENT_MESSAGE}

    cache = frappe.cache()
    key = _cache_key(user)
    now = time.time()
    wait = cooldown_left(cache.get_value(key), now)
    if wait:
        return {"success": True, "message": SENT_MESSAGE, "retry_after": wait}

    code = new_code()
    cache.set_value(key, new_record(code, now), expires_in_sec=CODE_TTL_SECONDS)
    try:
        _send_code_email(user, code)
    except Exception:
        cache.delete_value(key)
        frappe.log_error(title="Password reset email failed", message=frappe.get_traceback())
        # Said only when sending failed, so it does not reveal whether an account exists
        return {
            "success": False,
            "code": "EMAIL_FAILED",
            "message": _("We could not send the email just now. Please try again in a few minutes, or contact Murzak support."),
        }
    return {"success": True, "message": SENT_MESSAGE}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=60 * 60)
def reset_password_with_code(identifier: str = None, code: str = None, new_password: str = None):
    user = _find_user(identifier)
    cache = frappe.cache()
    key = _cache_key(user) if user else None
    record = cache.get_value(key) if key else None
    result = check_code(record, code, time.time())

    if result != "ok":
        if result == "wrong" and record:
            record["wrong"] = int(record.get("wrong", 0)) + 1
            cache.set_value(key, record, expires_in_sec=CODE_TTL_SECONDS)
        messages = {
            "missing": _("That code is not valid. Ask for a new code and try again."),
            "expired": _("That code has expired. Ask for a new code and try again."),
            "locked": _("Too many wrong codes. Ask for a new code and try again."),
            "wrong": _("That code is not right. Check the email and try again."),
        }
        return {"success": False, "code": f"CODE_{result.upper()}", "message": messages[result]}

    unmet = validate_password_strength(new_password or "")
    if unmet:
        return {
            "success": False,
            "code": "WEAK_PASSWORD",
            "message": _("Password does not meet security requirements."),
            "requirements": unmet,
        }

    from frappe.utils.password import update_password

    update_password(user, new_password, logout_all_sessions=True)
    cache.delete_value(key)

    # End every existing sign-in, in case the old password was known to someone else
    for token in frappe.get_all("OAuth Bearer Token", filters={"user": user, "status": "Active"}, pluck="name"):
        frappe.db.set_value("OAuth Bearer Token", token, "status", "Revoked")
    frappe.db.commit()

    return {"success": True, "message": _("Your password has been changed. Sign in with your new password.")}
