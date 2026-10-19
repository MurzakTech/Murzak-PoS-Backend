"""
Units by industry: the units a shop counts in, and the shop's own units (ticket P7-2).

The contract is docs/redesign/P7-2-contract.md. These calls are new; no existing call, record or setting changes.

What is kept, and where
- A unit is an ERPNext "UOM" record. Adding the usual units of an industry creates only the ones the site does not
  have (names are compared ignoring case, and the usual other names count: "Piece" is "Nos"). An existing unit is
  never renamed, changed or removed; "must be a whole number" is set only on a unit created here.
- Which kind of shop the business is for units, and the shop's own units (name and plain plural word), are kept the
  way the New look switch is (new_look_api.py): in Frappe's own DefaultValue table, one key each per business. That
  table exists on every site, so this adds no document type, custom field or migration.

Who may do what
- anyone signed in may read the units of their own business
- only an owner or a manager may add units, add their own unit, or change the kind of shop
"""

from __future__ import annotations

import hashlib

import frappe
from frappe import _

from murzak_pos.api import role_tiers
from murzak_pos.api import unit_sets as sets
from murzak_pos.api.payment_gateway_common import resolve_company

INDUSTRY_PREFIX = "murzak_units_industry"
CUSTOM_PREFIX = "murzak_units_custom"
# DefaultValue.defkey holds at most 140 characters
MAX_KEY_LENGTH = 140


def _key(prefix: str, company: str) -> str:
	"""One key per business and kind of answer. A very long company name is shortened to a fingerprint."""
	key = f"{prefix}:{company}"
	if len(key) <= MAX_KEY_LENGTH:
		return key
	return f"{prefix}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"


def _require_unit_manager() -> None:
	"""Owners and managers only. A cashier, and a guest, are refused before anything is read or written."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if tier not in (role_tiers.OWNER, role_tiers.MANAGER):
		frappe.throw(_("Only an owner or a manager can change units."), frappe.PermissionError)


def _can_manage() -> bool:
	return role_tiers.get_tier() in (role_tiers.OWNER, role_tiers.MANAGER)


def _existing_uoms() -> list[str]:
	return [r for r in frappe.get_all("UOM", pluck="name") if r]


def _chosen_industry(company: str) -> str | None:
	return sets.clean_industry(frappe.db.get_default(_key(INDUSTRY_PREFIX, company)))


def _custom_units(company: str) -> list[dict]:
	return sets.parse_custom(frappe.db.get_default(_key(CUSTOM_PREFIX, company)))


def _signup_industry() -> str | None:
	"""The industry picked at sign-up, if any. Never fails: not every site has the field."""
	try:
		industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
		if not industry:
			return None
		return frappe.db.get_value("POS Industry", industry, "industry_code") or industry
	except Exception:
		return None


def _business_kind(company: str) -> str | None:
	"""The answer to "What do you sell?" if there is one. Never fails."""
	try:
		from murzak_pos.api import setup_api

		return setup_api._read_kind(company)
	except Exception:
		return None


def _which_industry(company: str, asked=None) -> str:
	"""The industry whose units to show: the one asked for, else the owner's choice, else sign-up, else the kind."""
	if asked not in (None, ""):
		industry = sets.clean_industry(asked)
		if not industry:
			frappe.throw(_("That kind of shop is not one we have units for."), frappe.ValidationError)
		return industry
	return sets.industry_for(_chosen_industry(company), _signup_industry(), _business_kind(company))


def _payload(company: str, industry: str, created=()) -> dict:
	existing = _existing_uoms()
	plan = sets.plan_units(industry, existing)
	units = [sets.unit_row(item, not item["create"]) for item in plan]
	# A custom unit that was removed from the site by hand is simply not offered
	existing_lower = {n.lower() for n in existing}
	custom = [c for c in _custom_units(company) if c["name"].lower() in existing_lower]
	return {
		"company": company,
		"industry": industry,
		"industries": list(sets.INDUSTRIES),
		"units": units,
		"missing": sum(1 for u in units if not u["exists"]),
		"custom": custom,
		"created": list(created),
		"can_manage": _can_manage(),
	}


def _make_uom(name: str, whole: bool) -> str:
	uom = frappe.new_doc("UOM")
	uom.uom_name = name
	uom.must_be_whole_number = 1 if whole else 0
	uom.insert(ignore_permissions=True)
	return uom.name


# --- reading ---------------------------------------------------------------------------------------------


@frappe.whitelist()
def get_industry_units(industry: str | None = None, company: str | None = None) -> dict:
	"""
	The units of the shop's industry, in order, each with its plain words and whether the site has it yet, plus the
	shop's own units. Anyone signed in may ask. Changes nothing.
	"""
	company = resolve_company(company)
	return {"success": True, "data": _payload(company, _which_industry(company, industry))}


# --- writing (owners and managers) ------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def ensure_industry_units(industry: str | None = None, company: str | None = None) -> dict:
	"""
	Add the usual units of an industry that the site does not have yet. Safe to call again and again: a second call
	creates nothing. Existing units are never renamed, changed or removed, and a unit that already exists under
	another usual name (Piece for Nos, Litre for Ltr) is used, not added twice.
	"""
	_require_unit_manager()
	company = resolve_company(company)
	chosen = _which_industry(company, industry)

	created = []
	for item in sets.plan_units(chosen, _existing_uoms()):
		if not item["create"]:
			continue
		try:
			created.append(_make_uom(item["stored"], item["unit"]["whole"]))
		except frappe.DuplicateEntryError:
			# Someone else added it a moment ago: that is the result we wanted
			continue
	return {"success": True, "data": _payload(company, chosen, created)}


@frappe.whitelist(methods=["POST"])
def set_units_industry(industry: str, company: str | None = None) -> dict:
	"""Say which kind of shop this is for units. It only decides which units come first; no unit is added or removed."""
	_require_unit_manager()
	company = resolve_company(company)
	chosen = sets.clean_industry(industry)
	if not chosen:
		frappe.throw(_("That kind of shop is not one we have units for."), frappe.ValidationError)
	frappe.db.set_default(_key(INDUSTRY_PREFIX, company), chosen)
	return {"success": True, "data": _payload(company, chosen)}


def _fail_on(code: str, label: str) -> None:
	if code == "empty":
		frappe.throw(_("Please type the {0}.").format(label), frappe.ValidationError)
	if code == "short":
		frappe.throw(_("The {0} needs at least two letters.").format(label), frappe.ValidationError)
	if code == "long":
		frappe.throw(_("The {0} can be at most 30 characters.").format(label), frappe.ValidationError)
	frappe.throw(
		_("The {0} can use letters, numbers, spaces, hyphens and full stops only.").format(label),
		frappe.ValidationError,
	)


@frappe.whitelist(methods=["POST"])
def add_custom_unit(name: str, plural: str, allow_parts=0, company: str | None = None) -> dict:
	"""
	Add the shop's own unit: its name and the plain word for many ("jerrycan", "jerrycans"). The name must be new
	(case and spaces at the ends are ignored). It becomes a UOM; `allow_parts` says whether it may be sold in parts
	(2.5 of it); by default it is sold whole.
	"""
	_require_unit_manager()
	company = resolve_company(company)

	name = sets.tidy(name)
	plural = sets.tidy(plural)
	problem = sets.check_word(name)
	if problem:
		_fail_on(problem, _("unit name"))
	problem = sets.check_word(plural)
	if problem:
		_fail_on(problem, _("plural word"))

	custom = _custom_units(company)
	if len(custom) >= sets.MAX_CUSTOM_UNITS:
		frappe.throw(
			_("You already have {0} units of your own, which is the most.").format(sets.MAX_CUSTOM_UNITS),
			frappe.ValidationError,
		)
	if sets.is_duplicate(name, _existing_uoms(), custom, extra_words=()):
		frappe.throw(_("There is already a unit called {0}.").format(name), frappe.ValidationError)

	if plural.lower() != name.lower() and sets.is_duplicate(plural, _existing_uoms(), custom):
		frappe.throw(_("There is already a unit called {0}.").format(plural), frappe.ValidationError)

	parts = str(allow_parts).strip().lower() in ("1", "true", "yes", "on")
	try:
		stored = _make_uom(sets.stored_name(name), not parts)
	except frappe.DuplicateEntryError:
		frappe.throw(_("There is already a unit called {0}.").format(name), frappe.ValidationError)

	custom.append({"name": stored, "plural": plural})
	frappe.db.set_default(_key(CUSTOM_PREFIX, company), sets.dump_custom(custom))
	return {"success": True, "data": {**_payload(company, _which_industry(company)), "added": stored}}
