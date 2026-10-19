"""
The shop's brand: the logo, accent colour, tagline, receipt footer and receipt style a business chose.

It is used by the receipt, the sign-in page header and the top bar of the redesigned screens. Like the
New look switch, it is kept per business in Frappe's own "DefaultValue" table, as one JSON value under one
key per business. That table exists on every site, so installing this adds no document type, no custom
field and no migration, and it never alters a record that is there today. A business with no key has the
plain defaults (no logo, the standard accent, the 80mm till roll style).

Nothing secret is stored here. The logo is only the address of a picture that was uploaded through the
standard public file upload ("/files/..."); the picture itself is not touched by these calls.

Who may do what:
- anyone signed in may read the brand of their own business (every cashier prints receipts)
- only a business owner (a System Manager) or the Administrator may change it, and only for a business
  they belong to
"""

from __future__ import annotations

import hashlib
import json
import re

import frappe
from frappe import _

from murzak_pos.api.access_control import require_manager
from murzak_pos.api.payment_gateway_common import resolve_company

KEY_PREFIX = "murzak_brand"
# DefaultValue.defkey holds at most 140 characters
MAX_KEY_LENGTH = 140

# The accent colours an owner may pick. The colours themselves live in the front end's design tokens; the
# server only knows the names, so a colour that is not on the list can never be stored.
ACCENTS = ("indigo", "blue", "teal", "green", "amber", "red", "pink", "purple", "slate")
DEFAULT_ACCENT = "indigo"

STYLES = ("thermal80", "thermal58", "a4")
DEFAULT_STYLE = "thermal80"

MAX_TAGLINE = 80
MAX_FOOTER = 160
MAX_HANDLE = 30
MAX_WEBSITE = 100
MAX_LOGO = 200

LOGO_ALLOWED = ("jpg", "jpeg", "png", "webp")
# A public upload only: "/files/name.png". Letters, digits, dot, dash, underscore, space, brackets and percent.
_LOGO = re.compile(r"^/files/[A-Za-z0-9._() %-]+\.(jpg|jpeg|png|webp)$", re.IGNORECASE)
_HANDLE = re.compile(rf"^@?[A-Za-z0-9._]{{1,{MAX_HANDLE}}}$")
_WEBSITE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(/[A-Za-z0-9._~%/?=&#+:-]*)?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCHEME = re.compile(r"^https?://", re.IGNORECASE)

FIELDS = ("logo", "accent", "tagline", "footer", "style", "social", "website")


def defaults() -> dict:
	"""The brand of a business that never chose anything."""
	return {
		"logo": "",
		"accent": DEFAULT_ACCENT,
		"tagline": "",
		"footer": "",
		"style": DEFAULT_STYLE,
		"social": "",
		"website": "",
	}


def _key(company: str) -> str:
	"""One key per business. A very long company name is shortened to a fingerprint so it always fits."""
	key = f"{KEY_PREFIX}:{company}"
	if len(key) <= MAX_KEY_LENGTH:
		return key
	return f"{KEY_PREFIX}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"


def _plain(value) -> str:
	"""Text as one tidy line: control characters removed, runs of spaces made one, ends trimmed."""
	text = _CONTROL.sub(" ", str(value if value is not None else ""))
	return re.sub(r"\s+", " ", text).strip()


def _refuse(message: str):
	frappe.throw(message, frappe.ValidationError)


def clean_field(name: str, value):
	"""One field checked and tidied. Returns the value to keep, or throws a plain sentence."""
	if name == "accent":
		text = _plain(value).lower()
		if text not in ACCENTS:
			_refuse(_("Choose one of the colours on the list."))
		return text
	if name == "style":
		text = _plain(value).lower()
		if text not in STYLES:
			_refuse(_("Choose one of the receipt styles on the list."))
		return text
	if name == "logo":
		text = _plain(value)
		if not text:
			return ""
		if len(text) > MAX_LOGO or ".." in text or not _LOGO.match(text):
			_refuse(_("The logo must be a JPG, PNG or WebP picture uploaded here."))
		return text
	if name in ("tagline", "footer"):
		limit = MAX_TAGLINE if name == "tagline" else MAX_FOOTER
		text = _plain(value)
		if len(text) > limit:
			_refuse(_("Keep this to {0} characters or fewer.").format(limit))
		if "<" in text or ">" in text:
			_refuse(_("Please leave out the < and > signs."))
		return text
	if name == "social":
		text = _plain(value)
		if not text:
			return ""
		if not _HANDLE.match(text):
			_refuse(_("A social handle uses letters, numbers, dots and underscores only, such as @myshop."))
		return text if text.startswith("@") else "@" + text
	if name == "website":
		text = _SCHEME.sub("", _plain(value)).rstrip("/")
		if not text:
			return ""
		if len(text) > MAX_WEBSITE or not _WEBSITE.match(text):
			_refuse(_("Enter a website address such as www.myshop.co.ke."))
		return text
	_refuse(_("That setting is not known."))


def clean_brand(sent, current: dict | None = None) -> dict:
	"""
	The brand to keep: `current` with every field that was sent checked and written over it.
	Fields that were not sent are left as they were; a field that is not in FIELDS is refused.
	"""
	if isinstance(sent, str):
		try:
			sent = json.loads(sent)
		except ValueError:
			_refuse(_("The brand details could not be read."))
	if not isinstance(sent, dict):
		_refuse(_("The brand details could not be read."))
	unknown = [name for name in sent if name not in FIELDS]
	if unknown:
		_refuse(_("That setting is not known."))
	brand = {**defaults(), **(current or {})}
	for name in FIELDS:
		if name in sent:
			brand[name] = clean_field(name, sent[name])
	return brand


def read_brand(stored) -> dict:
	"""The brand from what is kept. Anything unreadable or no longer valid falls back to the default for that field."""
	brand = defaults()
	if not stored:
		return brand
	try:
		data = json.loads(stored) if isinstance(stored, str) else stored
	except ValueError:
		return brand
	if not isinstance(data, dict):
		return brand
	for name in FIELDS:
		if name not in data:
			continue
		try:
			brand[name] = clean_field(name, data[name])
		except frappe.ValidationError:
			continue
	return brand


@frappe.whitelist()
def get_brand(company: str | None = None) -> dict:
	"""The brand of the caller's business. Anyone signed in may read it."""
	company = resolve_company(company)
	return {"success": True, "data": {"company": company, **read_brand(frappe.db.get_default(_key(company)))}}


@frappe.whitelist(methods=["POST"])
def set_brand(brand, company: str | None = None) -> dict:
	"""Save the brand of a business. Owners only. Fields that are not sent keep their saved value."""
	require_manager()
	company = resolve_company(company)
	kept = clean_brand(brand, read_brand(frappe.db.get_default(_key(company))))
	if kept["logo"] and not frappe.db.exists("File", {"file_url": kept["logo"]}):
		_refuse(_("That logo was not found. Upload it again."))
	frappe.db.set_default(_key(company), json.dumps(kept, sort_keys=True))
	return {"success": True, "data": {"company": company, **kept}}
