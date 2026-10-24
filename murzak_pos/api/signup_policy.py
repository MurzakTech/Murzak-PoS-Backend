"""
Whether a new business can sign itself up on this site.

Each business gets its own site, built by murzaktech.tech at checkout (deploy/vps/bin/murzak-bench-provision in
the website repository). So the public sign-up page is closed by default on every site: the shared site takes no new
businesses, and a shop's own site never takes a stranger. The businesses already on the shared site stay there.

Only calls that come in over the web are refused. The provisioning scripts call register_user and create_company
from Python on the server itself, so they are never refused.

To open sign-up on a site (a staging or development server), add to its site_config.json:
    "murzak_pos_signup_open": 1
The page people are sent to instead can be changed with "murzak_pos_signup_url".
"""

from __future__ import annotations

import frappe
from frappe import _

DEFAULT_SIGNUP_URL = "https://murzaktech.tech"


def signup_open() -> bool:
	return bool(frappe.conf.get("murzak_pos_signup_open"))


def signup_url() -> str:
	return frappe.conf.get("murzak_pos_signup_url") or DEFAULT_SIGNUP_URL


def from_the_web() -> bool:
	"""The call came in over HTTP, not from a script on the server."""
	return bool(getattr(frappe.local, "request", None))


def signup_closed_reply() -> dict | None:
	"""The reply refusing a web sign-up while sign-up is closed, or None when it may go ahead."""
	if signup_open() or not from_the_web():
		return None
	frappe.local.response["http_status_code"] = 403
	url = signup_url()
	return {
		"success": False,
		"code": "SIGNUP_CLOSED",
		"message": _("New businesses sign up at {0}, where each business gets its own private till.").format(
			url
		),
		"signup_url": url,
	}


def refuse_new_business_if_closed() -> None:
	"""
	Creating a business: refused over the web while sign-up is closed, on a site that already has one and for
	someone who belongs to none. A shop's own site gets its first business from the provisioning script, and a
	business already on the site may still add another.
	"""
	if signup_open() or not from_the_web():
		return
	if frappe.session.user == "Administrator" or not frappe.db.count("Company"):
		return
	from murzak_pos.api.payment_gateway_common import get_user_companies

	if get_user_companies():
		return
	frappe.throw(
		_("New businesses sign up at {0}, where each business gets its own private till.").format(
			signup_url()
		),
		frappe.PermissionError,
	)
