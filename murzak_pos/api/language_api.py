"""
The language a person uses the till in.

The server's own messages (errors, confirmations and the like) are translated with Frappe's translation files:
`murzak_pos/translations/sw.csv` holds the Swahili. A person's choice is kept on their user record, so every message the
server sends them afterwards is in that language. The screens' own wording is the frontend's.
"""

from __future__ import annotations

import frappe
from frappe import _

# code -> name written in that language
SUPPORTED = {"en": "English", "sw": "Kiswahili"}
DEFAULT = "en"


def enable_languages() -> None:
	"""Switch the supported languages on in Frappe. Runs after every migrate; safe to run again."""
	for code in SUPPORTED:
		if frappe.db.exists("Language", code) and not frappe.db.get_value("Language", code, "enabled"):
			frappe.db.set_value("Language", code, "enabled", 1)


def _current(user: str) -> str:
	code = frappe.db.get_value("User", user, "language") or DEFAULT
	return code if code in SUPPORTED else DEFAULT


@frappe.whitelist()
def get_languages() -> dict:
	"""The languages that can be chosen, and the one the signed-in person uses now."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return {
		"success": True,
		"data": {
			"languages": [{"code": code, "name": name} for code, name in SUPPORTED.items()],
			"current": _current(frappe.session.user),
		},
	}


@frappe.whitelist(methods=["POST"])
def set_language(language: str) -> dict:
	"""Choose the language the server uses for the signed-in person."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if language not in SUPPORTED:
		frappe.throw(
			_("Choose one of: {0}.").format(", ".join(SUPPORTED)),
			frappe.ValidationError,
		)
	frappe.db.set_value("User", frappe.session.user, "language", language)
	frappe.clear_cache(user=frappe.session.user)
	return {"success": True, "data": {"current": language}}
