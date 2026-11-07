"""
Why a person added to the team may not be able to sign in, and the welcome email they should get.

Adding a person sent no email (the screen asked for none) and gave the owner no way to see why a person could not sign in.
This holds the plain-words checks (is the account there, switched on, in this business, with a password and a role, not
locked, and can the site send email at all), and the wording of the welcome email. The welcome email never contains the
password: it says where to sign in and how to get one.

Kept free of Frappe imports so it can be tested on its own. The facts are gathered in staff_signin_api.py.
"""

from __future__ import annotations

import html
from urllib.parse import urlparse

GREEN, AMBER, RED = "green", "amber", "red"


def _check(check_id, title, status, detail, fix=""):
	return {"id": check_id, "title": title, "status": status, "detail": detail, "fix": fix}


def sign_in_report(facts: dict) -> dict:
	"""
	The checks for one person. facts: exists, enabled, user_type, roles (list), same_business, has_password,
	locked_minutes (0 when not locked), two_factor (bool), email_ready (bool).
	Returns { checks, ok, summary } where ok is True when nothing is red.
	"""
	checks = []
	if not facts.get("exists"):
		checks.append(
			_check(
				"account",
				"The account",
				RED,
				"There is no account with this email address.",
				"Check the spelling, or add the person again.",
			)
		)
		return {"checks": checks, "ok": False, "summary": "There is no account with this email address."}
	checks.append(_check("account", "The account", GREEN, "The account exists."))

	if not facts.get("same_business"):
		checks.append(
			_check(
				"business",
				"Works for your business",
				RED,
				"This person is not part of your business, so you cannot check or change them.",
				"",
			)
		)
		return {"checks": checks, "ok": False, "summary": "This person is not part of your business."}
	checks.append(_check("business", "Works for your business", GREEN, "They are part of your business."))

	if facts.get("enabled"):
		checks.append(_check("enabled", "Switched on", GREEN, "The account is active."))
	else:
		checks.append(
			_check(
				"enabled",
				"Switched on",
				RED,
				"The account is deactivated, so they cannot sign in.",
				"Open the person's menu and choose Reactivate.",
			)
		)

	if facts.get("has_password"):
		checks.append(_check("password", "A password", GREEN, "A password is set."))
	else:
		checks.append(
			_check(
				"password",
				"A password",
				RED,
				"No password is set for this account.",
				"Choose Set a new password in the person's menu.",
			)
		)

	roles = [r for r in (facts.get("roles") or []) if r]
	if roles:
		checks.append(
			_check("roles", "A role", GREEN, f"They hold {len(roles)} role{'s' if len(roles) != 1 else ''}.")
		)
	else:
		checks.append(
			_check(
				"roles",
				"A role",
				RED,
				"They have no role, so there is nothing for them to do after signing in.",
				"Open the person's menu, choose Change details or role, and pick one.",
			)
		)

	minutes = int(facts.get("locked_minutes") or 0)
	if minutes > 0:
		checks.append(
			_check(
				"lock",
				"Not locked out",
				AMBER,
				f"Sign-in is locked for about {minutes} minute{'s' if minutes != 1 else ''} after wrong passwords.",
				"Wait, or set a new password for them.",
			)
		)
	else:
		checks.append(_check("lock", "Not locked out", GREEN, "Sign-in is not locked."))

	if facts.get("two_factor"):
		checks.append(
			_check(
				"two_factor",
				"Two-step sign-in",
				AMBER,
				"Two-step sign-in is on, so they need the 6-digit code from their phone as well as the password.",
				"If they lost the phone, an owner can turn it off for them under Team and security.",
			)
		)
	else:
		checks.append(
			_check(
				"two_factor", "Two-step sign-in", GREEN, "Two-step sign-in is off. The password is enough."
			)
		)

	if (facts.get("user_type") or "System User") != "System User":
		checks.append(
			_check(
				"user_type",
				"Kind of account",
				AMBER,
				"The account is not a normal staff account, which can stop it working in the till.",
				"Give it a role, or ask support.",
			)
		)

	if facts.get("email_ready"):
		checks.append(
			_check(
				"email",
				"Email from the site",
				GREEN,
				"The site can send email, so welcome emails and password-reset codes can arrive.",
			)
		)
	else:
		checks.append(
			_check(
				"email",
				"Email from the site",
				AMBER,
				"The site cannot send email yet, so no welcome email and no password-reset code will arrive.",
				"Give them their sign-in details yourself, and ask support to set up outgoing email.",
			)
		)

	reds = [c for c in checks if c["status"] == RED]
	ambers = [c for c in checks if c["status"] == AMBER]
	if reds:
		summary = reds[0]["detail"]
	elif ambers:
		summary = "Nothing stops the account working. Worth a look: " + ambers[0]["title"].lower() + "."
	else:
		summary = "Everything is in place. If they still cannot sign in, the password they are typing is probably not the one set. Set a new one."
	return {"checks": checks, "ok": not reds, "summary": summary}


# --- the sign-in address ------------------------------------------------------------------------------------


def sign_in_url(origin, configured="") -> str:
	"""
	The address of the till to put in the welcome email: the page the owner is using (its Origin), else the one set for the site.
	Only a plain http(s) address with a host is accepted; anything else gives an empty answer.
	"""
	for candidate in (origin, configured):
		text = str(candidate or "").strip().rstrip("/")
		parsed = urlparse(text)
		if (
			parsed.scheme in ("http", "https")
			and parsed.netloc
			and not parsed.path.strip("/")
			and " " not in text
		):
			return text
	return ""


# --- the welcome email --------------------------------------------------------------------------------------


def welcome_message(shop, first_name, email, url, roles=None) -> tuple[str, str]:
	"""(subject, html) of the welcome email. Everything typed by a person is escaped. It never contains a password."""
	shop_text = html.escape(str(shop or "your shop"))
	name = html.escape(str(first_name or "there"))
	address = html.escape(str(email or ""))
	subject = f"Welcome to {shop}: how to sign in" if shop else "Welcome: how to sign in"
	where = (
		f'<p>Sign in at <a href="{html.escape(url, quote=True)}">{html.escape(url)}</a></p>'
		if url
		else "<p>Ask your owner for the sign-in address.</p>"
	)
	role_line = ""
	named = [html.escape(str(r)) for r in (roles or []) if r]
	if named:
		role_line = f"<p>Your role: {', '.join(named)}.</p>"
	body = (
		f"<p>Hello {name},</p>"
		f"<p>You have been added to the team at <strong>{shop_text}</strong>.</p>"
		f"{where}"
		f"<p>Your sign-in email address is <strong>{address}</strong>.</p>"
		f"{role_line}"
		"<p>Your owner will give you your first password. If you do not have it, press "
		"<em>Forgot password</em> on the sign-in page and enter this email address: we send you a code to choose your own.</p>"
		"<p>If you were not expecting this, you can ignore this email.</p>"
	)
	return subject, body
