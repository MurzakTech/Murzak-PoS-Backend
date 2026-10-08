"""
Shared helpers for every payment gateway (M-Pesa, Pesapal, PayPal).

These keep the gateways safe to run for many businesses on one server:
- every call is limited to the companies the signed-in user belongs to
- only managers may change gateway credentials
- callback URLs are built from the site's own address, so a new client only
  enters their keys and never has to type or register URLs by hand
"""

from __future__ import annotations

import re
import secrets
from typing import Optional

import frappe
from frappe import _

# Roles allowed to view or change gateway credentials. Business owners get both at registration.
GATEWAY_ADMIN_ROLES = {"System Manager", "Accounts Manager"}

# Daraja refuses callback URLs that contain these words (case-insensitive), so callbacks
# live in payment_callbacks.py under neutral names.
DARAJA_BLOCKED_URL_WORDS = ("mpesa", "m-pesa", "safaricom", "exe", "exec", "cmd", "sql", "query")


def get_user_companies(user: str | None = None) -> set[str]:
	"""Companies the user may act for: default company, custom_company and Company user permissions."""
	user = user or frappe.session.user
	companies: set[str] = set()

	default_company = frappe.defaults.get_user_default("Company", user=user)
	if default_company:
		companies.add(default_company)

	custom_company = (
		frappe.db.get_value("User", user, "custom_company")
		if frappe.db.has_column("User", "custom_company")
		else None
	)
	if custom_company:
		companies.add(custom_company)

	for row in frappe.get_all(
		"User Permission", filters={"user": user, "allow": "Company"}, fields=["for_value"]
	):
		companies.add(row.for_value)

	return companies


def resolve_company(company: str | None = None) -> str:
	"""Return the company to act for, refusing companies the user does not belong to."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)

	allowed = get_user_companies()

	if not company:
		if len(allowed) == 1:
			return next(iter(allowed))
		company = frappe.defaults.get_user_default("Company")
		if not company:
			frappe.throw(_("Company is required."), frappe.ValidationError)

	if not frappe.db.exists("Company", company):
		frappe.throw(_("Company {0} does not exist.").format(company), frappe.ValidationError)

	if frappe.session.user != "Administrator" and company not in allowed:
		frappe.throw(_("You do not have access to company {0}.").format(company), frappe.PermissionError)

	return company


def require_gateway_admin() -> None:
	"""Only managers may view or change gateway credentials."""
	if frappe.session.user == "Administrator":
		return
	if not GATEWAY_ADMIN_ROLES.intersection(frappe.get_roles()):
		frappe.throw(
			_("Only an Accounts Manager or System Manager can change payment settings."),
			frappe.PermissionError,
		)


def ensure_record_company(record_company: str) -> None:
	"""Refuse to show or change a payment record that belongs to another company."""
	if frappe.session.user == "Administrator":
		return
	if record_company not in get_user_companies():
		frappe.throw(_("This payment belongs to another business."), frappe.PermissionError)


def normalize_kenyan_phone(phone: str | None) -> str:
	"""
	Turn the ways people write Kenyan mobile numbers into 2547XXXXXXXX / 2541XXXXXXXX.

	Accepts 0712345678, 712345678, +254712345678, 254712345678 and spaces or dashes.
	"""
	digits = re.sub(r"\D", "", phone or "")
	if digits.startswith("254") and len(digits) == 12:
		normalized = digits
	elif digits.startswith("0") and len(digits) == 10:
		normalized = "254" + digits[1:]
	elif len(digits) == 9 and digits[0] in "17":
		normalized = "254" + digits
	else:
		normalized = ""

	if not re.match(r"^254[17]\d{8}$", normalized):
		frappe.throw(
			_("{0} is not a valid Safaricom number. Use a number like 0712 345 678.").format(phone or ""),
			frappe.ValidationError,
		)
	return normalized


def get_callback_base_url() -> str:
	"""
	Public HTTPS address gateways call back to.

	Uses `payment_callback_base_url` from site_config.json when set (for sites behind a proxy),
	otherwise the site's own URL.
	"""
	base = (frappe.conf.get("payment_callback_base_url") or frappe.utils.get_url() or "").rstrip("/")
	return base


def build_callback_url(method: str, token: str) -> str:
	"""Full callback URL for a whitelisted method in payment_callbacks.py."""
	return f"{get_callback_base_url()}/api/method/techsavanna_pos.api.payment_callbacks.{method}?t={token}"


def callback_url_problem(url: str) -> str | None:
	"""Plain-language reason a callback URL cannot work, or None when it is fine."""
	if not url.startswith("https://"):
		return _(
			"Payment providers can only reach a public HTTPS address. Set host_name (or "
			"payment_callback_base_url) in this site's site_config.json to the public https:// address."
		)
	if "localhost" in url or "127.0.0.1" in url:
		return _("The site address is localhost, which payment providers cannot reach.")
	return None


def new_callback_token() -> str:
	return secrets.token_urlsafe(24)


def ensure_mode_of_payment(name: str, mop_type: str, company: str, account: str | None) -> str:
	"""Create the Mode of Payment if needed and point it at the company's account."""
	if not frappe.db.exists("Mode of Payment", name):
		mop = frappe.new_doc("Mode of Payment")
		mop.mode_of_payment = name
		mop.type = mop_type
		mop.enabled = 1
		mop.insert(ignore_permissions=True)

	mop = frappe.get_doc("Mode of Payment", name)
	changed = False
	if not mop.enabled:
		mop.enabled = 1
		changed = True
	if account:
		row = next((r for r in mop.accounts if r.company == company), None)
		if row is None:
			mop.append("accounts", {"company": company, "default_account": account})
			changed = True
		elif row.default_account != account:
			row.default_account = account
			changed = True
	if changed:
		mop.save(ignore_permissions=True)

	add_mode_to_pos_profiles(name, company)
	return name


def add_mode_to_pos_profiles(mode_of_payment: str, company: str) -> None:
	"""Make the method selectable in every POS Profile of the company."""
	for profile_name in frappe.get_all("POS Profile", filters={"company": company}, pluck="name"):
		profile = frappe.get_doc("POS Profile", profile_name)
		if any(p.mode_of_payment == mode_of_payment for p in profile.payments):
			continue
		profile.append("payments", {"mode_of_payment": mode_of_payment, "default": 0})
		profile.flags.ignore_gateway_hook = True
		profile.save(ignore_permissions=True)


def read_request_json() -> dict:
	"""Body of an incoming callback as a dict (empty when it is not JSON)."""
	import json

	try:
		data = frappe.request.get_data(as_text=True) if frappe.request else ""
		return json.loads(data) if data else {}
	except Exception:
		return {}
