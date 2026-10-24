"""
Reading the POS profile and the eTIMS settings of a business, and changing the eTIMS settings.

These are the calls behind the Settings > POS Profile and Settings > eTIMS screens. They are reached as
`murzak_pos.api.onboarding_api.<name>` (onboarding_api.py re-exports them), which is where the screens look.

The eTIMS settings record belongs to the Kenya tax app and carries the details KRA certified for the
business's device. This app reads it and changes only the few details that are safe to change from a screen;
it never creates one (that is done once, in the Desk, when KRA certifies the system) and it never sends a
password or key back to the browser.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from murzak_pos.api.access_control import require_any_role
from murzak_pos.api.payment_gateway_common import resolve_company

SETTINGS_DOCTYPE = "Navari KRA eTims Settings"
ETIMS_ROLES = frozenset({"Business Owner", "System Manager", "Accounts Manager"})

# What the screen may read, and the part of it that may be changed. Anything else the screen sends is
# reported back as ignored, so nobody is told a change was saved when it was not.
ETIMS_READ_FIELDS = ("name", "company", "tin", "bhfid", "dvcsrlno", "vendor", "env", "sandbox", "is_active")
# The PIN is not here: the Kenya app copies it from the company's Tax ID every time the record is saved
ETIMS_CHANGEABLE_FIELDS = ("dvcsrlno", "sandbox", "is_active")
ETIMS_CHECKS = ("sandbox", "is_active")

POS_PROFILE_FIELDS = (
	"name",
	"company",
	"warehouse",
	"currency",
	"customer",
	"selling_price_list",
	"cost_center",
	"update_stock",
	"allow_discount_change",
	"allow_rate_change",
	"allow_partial_payment",
	"ignore_pricing_rule",
	"disabled",
)
POS_PROFILE_TICKS = (
	"update_stock",
	"allow_discount_change",
	"allow_rate_change",
	"allow_partial_payment",
	"ignore_pricing_rule",
	"disabled",
)


@frappe.whitelist()
def get_pos_profile(company: str | None = None) -> dict:
	"""
	The POS profile a user sells with in a business: one that names them, else the oldest one in use.
	`pos_profile` is empty when the business has none.
	"""
	company = resolve_company(company)
	names = frappe.get_all(
		"POS Profile",
		filters={"company": company, "disabled": 0},
		pluck="name",
		order_by="creation asc",
	)
	chosen = None
	if names:
		mine = frappe.get_all(
			"POS Profile User",
			filters={"user": frappe.session.user, "parent": ["in", names]},
			pluck="parent",
		)
		chosen = next((name for name in names if name in mine), names[0])
	if not chosen:
		return {"pos_profile": None}

	# Some of these settings are not on every version of ERPNext's POS Profile; only those that are get read
	meta = frappe.get_meta("POS Profile")
	fields = [field for field in POS_PROFILE_FIELDS if field == "name" or meta.has_field(field)]
	row = frappe.db.get_value("POS Profile", chosen, fields, as_dict=True)
	for field in POS_PROFILE_TICKS:
		if field in row:
			row[field] = bool(cint(row[field]))
	return {"pos_profile": row}


def _settings_row(company: str, name: str | None = None) -> dict | None:
	"""The business's eTIMS settings record: the one asked for, else the active one, else the newest."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return None
	filters = {"company": company}
	if name:
		filters["name"] = name
	fields = [
		field
		for field in ETIMS_READ_FIELDS
		if frappe.get_meta(SETTINGS_DOCTYPE).has_field(field) or field == "name"
	]
	return frappe.db.get_value(
		SETTINGS_DOCTYPE, filters, fields, as_dict=True, order_by="is_active desc, modified desc"
	)


def _flag(value) -> int:
	"""A tick-box value as the screen may send it: true, 1, "1" or "true"."""
	if isinstance(value, str):
		return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
	return cint(value)


def _shown(row: dict) -> dict:
	shown = dict(row)
	for field in ETIMS_CHECKS:
		shown[field] = bool(cint(shown.get(field)))
	return shown


@frappe.whitelist()
def get_etims_settings(company: str | None = None) -> dict:
	"""The eTIMS details of a business. Keys and passwords are never sent. `etims_settings` is empty when there is none."""
	require_any_role(ETIMS_ROLES, _("see the eTIMS settings"))
	company = resolve_company(company)
	row = _settings_row(company)
	return {"etims_settings": _shown(row) if row else None}


@frappe.whitelist(methods=["POST"])
def update_etims_settings(
	company: str | None = None,
	name: str | None = None,
	dvcsrlno: str | None = None,
	sandbox=None,
	is_active=None,
	**ignored,
) -> dict:
	"""
	Change the device serial number, the test/live switch or the active switch of a business's eTIMS settings.

	The record is saved the way the Desk saves it, so the Kenya app's own checks apply. Its PIN comes from the
	company's Tax ID, so it is changed there. Anything else the screen sends is listed in `ignored` and left alone.
	"""
	require_any_role(ETIMS_ROLES, _("change the eTIMS settings"))
	company = resolve_company(company)
	row = _settings_row(company, name)
	if not row:
		frappe.throw(
			_(
				"This business has no eTIMS settings yet. They are created once, in the Desk, when KRA has certified the system."
			),
			frappe.DoesNotExistError,
		)

	wanted = {"dvcsrlno": dvcsrlno, "sandbox": sandbox, "is_active": is_active}
	before = _shown(row)
	doc = frappe.get_doc(SETTINGS_DOCTYPE, row["name"])
	touched = False
	for field in ETIMS_CHANGEABLE_FIELDS:
		value = wanted[field]
		if value is None or value == "":
			continue
		value = _flag(value) if field in ETIMS_CHECKS else str(value).strip()
		if doc.get(field) != value:
			doc.set(field, value)
			touched = True
	if touched:
		if not frappe.db.get_value("Company", company, "tax_id"):
			frappe.throw(
				_(
					"Add the business's KRA PIN as its Tax ID in the company details first; the eTIMS settings take their PIN from there."
				),
				frappe.ValidationError,
			)
		doc.save()
	after = _shown(_settings_row(company, row["name"]))
	# What really changed: the Kenya app can put a value back, such as keeping one record active
	changed = [field for field in ETIMS_CHANGEABLE_FIELDS if before.get(field) != after.get(field)]

	skipped = sorted(key for key in ignored if key not in ("cmd", "data") and ignored[key] not in (None, ""))
	return {
		"etims_settings": after,
		"changed": changed,
		"ignored": skipped,
		"message": _("eTIMS settings updated successfully") if changed else _("Nothing was changed."),
	}
