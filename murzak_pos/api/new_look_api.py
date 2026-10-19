"""
The "New look" switch: whether a business sees the redesigned screens.

Each business has its own switch, and it is ON unless a business owner turned it off. The owner's
decision to make the redesigned screens the default for every store is what this default expresses: a
business that never chose, including every business created from now on, sees the new screens. A
business that turned the switch off keeps the old screens, and turning it off again puts the old screens
back at once; nothing in the books changes either way, because this switch only decides which screens
are drawn.

Where it is kept: Frappe's own "DefaultValue" table, under one key per business. That table already
exists on every site, so installing this adds no document type, no custom field and no migration, and
it never alters a record that is there today. A business with no key is ON; a business whose key says
off stays off.

Who may do what:
- anyone signed in may ask whether their own business has it on (the screens need to know)
- only a business owner (a System Manager) or the Administrator may change it, and only for a
  business they belong to
"""

from __future__ import annotations

import hashlib

import frappe
from frappe import _

from murzak_pos.api.access_control import require_manager
from murzak_pos.api.payment_gateway_common import resolve_company

KEY_PREFIX = "murzak_new_look"
# DefaultValue.defkey holds at most 140 characters
MAX_KEY_LENGTH = 140

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off")


def _key(company: str) -> str:
	"""One key per business. A very long company name is shortened to a fingerprint so it always fits."""
	key = f"{KEY_PREFIX}:{company}"
	if len(key) <= MAX_KEY_LENGTH:
		return key
	return f"{KEY_PREFIX}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"


def _is_on(company: str) -> bool:
	"""On unless the business was explicitly switched off. No key at all means the default, which is on."""
	stored = frappe.db.get_default(_key(company))
	if stored is None:
		return True
	return str(stored).strip().lower() not in _OFF


def _as_flag(value) -> bool:
	"""Read an on or off value however the browser sent it (true, "true", 1, "1"). Anything else is refused."""
	if isinstance(value, bool):
		return value
	text = str(value if value is not None else "").strip().lower()
	if text in _ON:
		return True
	if text in _OFF:
		return False
	frappe.throw(_("Say whether the New look should be on or off."), frappe.ValidationError)


@frappe.whitelist()
def get_new_look(company: str | None = None) -> dict:
	"""Whether the redesigned screens are on for the caller's business. On unless an owner turned it off."""
	company = resolve_company(company)
	return {"success": True, "data": {"company": company, "enabled": _is_on(company)}}


@frappe.whitelist(methods=["POST"])
def set_new_look(enabled, company: str | None = None) -> dict:
	"""Turn the redesigned screens on or off for a business. Owners only."""
	require_manager()
	company = resolve_company(company)
	on = _as_flag(enabled)
	frappe.db.set_default(_key(company), "1" if on else "0")
	return {"success": True, "data": {"company": company, "enabled": on}}
