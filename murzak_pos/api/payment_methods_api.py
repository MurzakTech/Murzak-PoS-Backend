"""
Payment methods: which ways of taking money the till offers, switched on or off in Settings.

The till offers Cash, M-Pesa, Card, On credit and Other, with Cash and M-Pesa first. A business owner decides
which of them are on. Switching one off only hides it at the till; the mode of payment itself, its accounts and
every sale already made stay exactly as they were.

Where it is kept: Frappe's own "DefaultValue" table, one key per business (the same place the New look switch
uses), holding a few characters of text that list what is switched OFF. That table already exists on every site,
so this adds no document type, no custom field and no migration, and it changes no record that is there today.
A business with no key has every payment method it owns switched on, so every till behaves as it does today
until an owner changes something.

Who may do what:
- anyone signed in may ask which methods are on for their own business (the till needs to know)
- only a business owner (a System Manager) or the Administrator may change a switch, and only for a business
  they belong to; a cashier or a manager is refused before anything is read or written

The rules themselves (the grouping of names, the order, the default and the "keep at least one on" rule) are plain
functions in `payment_methods_calc.py`.
"""

from __future__ import annotations

import hashlib

import frappe
from frappe import _

from murzak_pos.api import payment_methods_calc as calc
from murzak_pos.api.business_lists import modes_of_payment_for
from murzak_pos.api.payment_gateway_common import resolve_company
from murzak_pos.api.role_tiers import require_access

KEY_PREFIX = "murzak_payment_methods"
# DefaultValue.defkey holds at most 140 characters
MAX_KEY_LENGTH = 140
# role_tiers has no capability for Settings yet. "team" is held by owners alone, which is the rule wanted here.
# If a "settings" capability is added to role_tiers.CAPABILITIES, change this one name.
CAPABILITY = "team"


def _key(company: str) -> str:
	"""One key per business. A very long company name is shortened to a fingerprint so it always fits."""
	key = f"{KEY_PREFIX}:{company}"
	if len(key) <= MAX_KEY_LENGTH:
		return key
	return f"{KEY_PREFIX}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"


def _load(company: str) -> tuple[list, str | None]:
	"""The mode of payment names the business may use, and the raw text kept for its switches."""
	return modes_of_payment_for(company) or [], frappe.db.get_default(_key(company))


def _reply(company: str, payload: dict) -> dict:
	return {"success": True, "data": {**payload, "company": company}}


def _refuse(reason: str, mode=None) -> None:
	"""Turn a refusal reason from the rules into a plain sentence."""
	if reason == calc.BAD_FLAG:
		frappe.throw(_("Say whether this payment method should be on or off."), frappe.ValidationError)
	if reason == calc.UNKNOWN_KEY:
		frappe.throw(_("Choose Cash, M-Pesa, Card, On credit or Other."), frappe.ValidationError)
	if reason == calc.MODE_NEEDED:
		frappe.throw(_("Say which of the other payment methods you mean."), frappe.ValidationError)
	if reason == calc.HAS_OWN_SWITCH:
		frappe.throw(
			_("{0} has its own switch, so it cannot be changed under Other.").format(mode),
			frappe.ValidationError,
		)
	if reason == calc.NOT_AVAILABLE:
		frappe.throw(
			_("This shop has not set up {0} yet, so it cannot be switched on.").format(mode),
			frappe.ValidationError,
		)
	if reason == calc.LAST_METHOD:
		frappe.throw(
			_("At least one way of taking money must stay on. Switch another one on first."),
			frappe.ValidationError,
		)


@frappe.whitelist()
def get_payment_methods(company: str | None = None) -> dict:
	"""The four fixed payment methods and the others the shop has, each on or off. Any signed-in user of the
	business may ask."""
	company = resolve_company(company)
	names, raw = _load(company)
	return _reply(company, calc.build_payment_methods(names, raw))


@frappe.whitelist()
def get_till_payment_modes(company: str | None = None) -> dict:
	"""The mode of payment names the till should offer, in order: Cash, M-Pesa, Card, On credit, then the other
	methods that are on. An empty list means the shop has no mode of payment at all."""
	company = resolve_company(company)
	names, raw = _load(company)
	modes = calc.till_modes(calc.build_payment_methods(names, raw))
	return {"success": True, "data": {"modes": modes, "company": company}}


@frappe.whitelist(methods=["POST"])
def set_payment_method(key, enabled, mode: str | None = None, company: str | None = None) -> dict:
	"""Switch one payment method on or off. Owners only.

	`key` is cash, mpesa, card, credit or other; for other, `mode` names the mode of payment. A method the shop
	does not have cannot be switched on, and the last method that is on cannot be switched off. Asking for what is
	already so changes nothing.
	"""
	require_access(CAPABILITY)
	company = resolve_company(company)
	names, raw = _load(company)
	payload = calc.build_payment_methods(names, raw)
	plan = calc.plan_change(payload, key, enabled, mode)
	if plan["error"]:
		label = plan["mode"] or calc.LABELS.get(plan["key"], "")
		_refuse(plan["error"], label)
	if plan["changed"]:
		state = calc.apply_change(raw, plan["key"], plan["enabled"], plan["mode"])
		frappe.db.set_default(_key(company), calc.dump_stored(state))
		names, raw = _load(company)
	return _reply(company, calc.build_payment_methods(names, raw))
