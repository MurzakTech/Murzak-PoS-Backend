"""
Helping an owner get a team member signed in: the welcome email, a check of why a person cannot sign in, and a new password.

Adding a person sent no email and gave the owner nothing to go on when the person could not sign in. These calls are for an owner
(a business owner or System Manager) and only reach people who work for the owner's own business, never another business and never
an owner. Nothing here ever returns, logs or emails a password.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api import staff_signin_calc as calc
from murzak_pos.api.access_control import require_manager
from murzak_pos.api.payment_gateway_common import get_user_companies
from murzak_pos.api.role_tiers import OWNER_ROLES

PROTECTED_USERS = ("administrator", "guest")  # compared in lower case, as the email is lowered first


def email_ready() -> bool:
	"""Whether the site has an outgoing email account switched on. Without one no email of any kind can be sent."""
	try:
		return bool(frappe.db.exists("Email Account", {"enable_outgoing": 1}))
	except Exception:
		return False


def _same_business(user: str) -> bool:
	if frappe.session.user == "Administrator":
		return True
	return bool(get_user_companies() & get_user_companies(user))


def _target(user_email: str) -> str:
	"""The team member to act on: a real account of the caller's own business that is not an owner or a protected account."""
	name = (user_email or "").strip().lower()
	if not name or name in PROTECTED_USERS or not frappe.db.exists("User", name):
		frappe.throw(_("There is no team member with that email address."), frappe.DoesNotExistError)
	if not _same_business(name):
		frappe.throw(_("That person does not work for your business."), frappe.PermissionError)
	if OWNER_ROLES & set(frappe.get_roles(name)):
		frappe.throw(_("An owner's password can only be changed by that owner."), frappe.PermissionError)
	return name


def _locked_minutes(user: str) -> int:
	try:
		from murzak_pos.api.two_factor_api import check_not_locked

		check_not_locked(user)
		return 0
	except frappe.AuthenticationError as error:
		import re

		found = re.search(r"(\d+)\s*(second|minute)", str(error))
		if not found:
			return 1
		amount = int(found.group(1))
		return max(1, round(amount / 60)) if found.group(2) == "second" else amount
	except Exception:
		return 0


def send_welcome(user: str, shop: str | None = None) -> dict:
	"""
	Email the welcome message to a team member. Never raises: the answer says whether it was handed to the mail queue and, when
	not, why, in words the owner can act on. The password is never in it.
	"""
	if not email_ready():
		return {
			"sent": False,
			"reason": _("The site cannot send email yet. Give the person their sign-in details yourself."),
		}
	try:
		row = frappe.db.get_value("User", user, ["email", "first_name"], as_dict=True)
		roles = [r for r in frappe.get_roles(user) if r not in ("All", "Guest", "Desk User")]
		url = calc.sign_in_url(frappe.get_request_header("Origin"), frappe.conf.get("pos_base_url") or "")
		subject, body = calc.welcome_message(shop or "", row.first_name, row.email, url, roles)
		frappe.sendmail(recipients=[row.email], subject=subject, message=body)
		return {"sent": True, "reason": ""}
	except Exception:
		frappe.log_error("Welcome email could not be sent", frappe.get_traceback())
		return {
			"sent": False,
			"reason": _(
				"The welcome email could not be sent. Give the person their sign-in details yourself."
			),
		}


@frappe.whitelist(methods=["POST"])
def resend_welcome_email(user_email: str) -> dict:
	"""Send the welcome email again to a team member."""
	require_manager()
	user = _target(user_email)
	company = frappe.db.get_value("User", user, "custom_company")
	return {"success": True, "welcome_email": send_welcome(user, company)}


@frappe.whitelist()
def check_staff_sign_in(user_email: str) -> dict:
	"""Why a team member may not be able to sign in: plain checks of the account, never a password."""
	require_manager()
	name = (user_email or "").strip().lower()
	exists = bool(name) and name not in PROTECTED_USERS and bool(frappe.db.exists("User", name))
	if not exists:
		return {"success": True, "data": calc.sign_in_report({"exists": False})}
	if not _same_business(name):
		return {"success": True, "data": calc.sign_in_report({"exists": True, "same_business": False})}
	from murzak_pos.api.two_factor_api import is_enabled

	row = frappe.db.get_value("User", name, ["enabled", "user_type"], as_dict=True)
	has_password = bool(
		frappe.db.sql(
			"SELECT 1 FROM `__Auth` WHERE doctype='User' AND name=%s AND fieldname='password' AND `encrypted`=0 LIMIT 1",
			name,
		)
	)
	roles = [r for r in frappe.get_roles(name) if r not in ("All", "Guest", "Desk User")]
	facts = {
		"exists": True,
		"same_business": True,
		"enabled": bool(row.enabled),
		"user_type": row.user_type,
		"roles": roles,
		"has_password": has_password,
		"locked_minutes": _locked_minutes(name),
		"two_factor": bool(is_enabled(name)),
		"email_ready": email_ready(),
	}
	return {"success": True, "data": calc.sign_in_report(facts)}


@frappe.whitelist(methods=["POST"])
def set_staff_password(user_email: str, password: str) -> dict:
	"""
	Set a new password for a team member of the owner's business. It must meet the same rules as at sign-up; every sign-in the person
	has is ended and a lock after wrong passwords is lifted. The password is not returned or kept anywhere else.
	"""
	require_manager()
	user = _target(user_email)
	from frappe.utils.password import update_password

	from murzak_pos.api.auth_api import validate_password_strength
	from murzak_pos.api.two_factor_api import clear_failures

	unmet = validate_password_strength(password or "")
	if unmet:
		frappe.throw(_("The password needs: {0}.").format(", ".join(unmet)), frappe.ValidationError)
	update_password(user, password, logout_all_sessions=True)
	clear_failures(user)
	return {"success": True, "message": _("A new password was set for {0}.").format(user)}
