"""
First-time set-up: the answers the wizard keeps, per business (ticket P5-4).

The contract is docs/redesign/P5-4-contract.md. These calls are new; no existing call, record or setting is changed.

What is kept, and where
- "What do you sell?" (pharmacy, retail, services or other), how far the wizard has got, and the usual opening cash
  are kept the way the New look switch is (new_look_api.py): in Frappe's own DefaultValue table, one key each per
  business. That table exists on every site, so this adds no document type, custom field or migration, and it never
  alters a record that is there today.
- The business kind only ADDS reasons to show things (the Expiry tab, barcodes first, services first). It never hides
  anything a business sees today, and nothing is switched off by it.

Who may do what
- any signed-in person may read the state and the features of their own business (the screens need them)
- only a business owner (a System Manager) or the Administrator may write, and only for a business they belong to

When the wizard is offered (setup_calc.needs_setup): no business kind recorded, no submitted sale, fewer than five
products, and not finished. Any shop with data of its own never sees it.
"""

from __future__ import annotations

import hashlib

import frappe
from frappe import _

from murzak_pos.api import role_tiers
from murzak_pos.api import setup_calc as calc
from murzak_pos.api.access_control import require_manager
from murzak_pos.api.payment_gateway_common import resolve_company

KIND_PREFIX = "murzak_setup_kind"
PROGRESS_PREFIX = "murzak_setup_progress"
CASH_PREFIX = "murzak_setup_cash"
# DefaultValue.defkey holds at most 140 characters
MAX_KEY_LENGTH = 140

# The "step" that ends the wizard ("Finish" and "Skip for now" both send it)
FINISH = "finish"


def _key(prefix: str, company: str) -> str:
	"""One key per business and kind of answer. A very long company name is shortened to a fingerprint."""
	key = f"{prefix}:{company}"
	if len(key) <= MAX_KEY_LENGTH:
		return key
	return f"{prefix}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"


# --- reading and writing the three answers (small on purpose, so tests can see them) -------------------------


def _read_kind(company: str) -> str | None:
	return calc.clean_kind(frappe.db.get_default(_key(KIND_PREFIX, company)))


def _read_progress(company: str) -> dict:
	return calc.parse_progress(frappe.db.get_default(_key(PROGRESS_PREFIX, company)))


def _write_progress(company: str, progress: dict) -> None:
	frappe.db.set_default(_key(PROGRESS_PREFIX, company), calc.dump_progress(progress))


def _read_cash(company: str) -> float:
	return calc.clean_amount(frappe.db.get_default(_key(CASH_PREFIX, company))) or 0.0


# --- what the business already has --------------------------------------------------------------------------


def _has_sales(company: str) -> bool:
	"""Any submitted sale of either kind (a Sales Invoice or a POS Invoice)."""
	for doctype in ("Sales Invoice", "POS Invoice"):
		if frappe.db.exists(doctype, {"company": company, "docstatus": 1}):
			return True
	return False


def _product_count(company: str) -> int:
	"""How many products the business has, counted only up to the limit (that is all the rule needs)."""
	rows = frappe.get_all(
		"Item",
		filters={"custom_company": company, "disabled": 0},
		pluck="name",
		limit=calc.PRODUCT_LIMIT,
	)
	return len(rows)


def _signup_industry() -> str | None:
	"""The industry the signed-in person picked when they signed up, if any. Never fails: not every site has the field."""
	try:
		industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
		if not industry:
			return None
		return frappe.db.get_value("POS Industry", industry, "industry_code") or industry
	except Exception:
		return None


def _features_payload(company: str) -> dict:
	kind = _read_kind(company)
	return {
		"company": company,
		"kind": kind,
		"features": calc.features_for(kind),
		"opening_cash": _read_cash(company),
	}


def _progress_payload(progress: dict) -> dict:
	return {
		"done_steps": progress["done"],
		"skipped_steps": progress["skipped"],
		"finished": progress["finished"],
		"next_step": calc.next_step(progress),
	}


# --- reading ---------------------------------------------------------------------------------------------


@frappe.whitelist()
def get_business_features(company: str | None = None) -> dict:
	"""
	What this business turned on by answering "What do you sell?": its kind and each feature on or off, plus the
	usual opening cash (a suggestion the Open till dialog can prefill). Anyone signed in may ask; a business that
	never answered gets no kind and every feature off, which is today's screens exactly.
	"""
	company = resolve_company(company)
	return {"success": True, "data": _features_payload(company)}


@frappe.whitelist()
def get_setup_state(company: str | None = None) -> dict:
	"""
	Where the first-time set-up stands for this business: whether to offer it, the kind, the features, and the steps
	passed. A count that cannot be read counts as "has data", so a failure never offers the wizard to a real shop.
	"""
	company = resolve_company(company)
	tier = role_tiers.get_tier()
	kind = _read_kind(company)
	progress = _read_progress(company)
	signup = _signup_industry()

	sales, products = 1, calc.PRODUCT_LIMIT
	if not progress["finished"]:
		try:
			sales = 1 if _has_sales(company) else 0
			products = _product_count(company)
		except Exception:
			frappe.log_error("Set-up state counts error", frappe.get_traceback())
			sales, products = 1, calc.PRODUCT_LIMIT

	needs = calc.needs_setup(
		bool(kind or calc.kind_from_industry(signup)), sales, products, progress["finished"]
	)
	resuming = calc.should_resume(progress, sales, products)
	data = {
		**_features_payload(company),
		**_progress_payload(progress),
		"needs_setup": needs,
		"in_progress": resuming,
		"offer": needs or resuming,
		"suggested_kind": calc.kind_from_industry(signup),
		"industry_code": signup,
		"can_edit": tier == role_tiers.OWNER,
	}
	return {"success": True, "data": data}


# --- writing (owners only) --------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save_business_kind(kind, company: str | None = None) -> dict:
	"""Keep the answer to "What do you sell?" and turn on the matching features. Owners only."""
	require_manager()
	company = resolve_company(company)
	clean = calc.clean_kind(kind)
	if not clean:
		frappe.throw(_("Choose what you sell: pharmacy, retail, services or other."), frappe.ValidationError)
	frappe.db.set_default(_key(KIND_PREFIX, company), clean)
	progress = calc.record_step(_read_progress(company), "business")
	_write_progress(company, progress)
	return {"success": True, "data": {**_features_payload(company), **_progress_payload(progress)}}


@frappe.whitelist(methods=["POST"])
def save_setup_progress(step, skipped=0, company: str | None = None) -> dict:
	"""
	Remember that a step was passed (completed, or skipped when `skipped` is set), so a refresh carries on from the
	next one. The step "finish" marks the whole wizard done: "Finish" and "Skip for now" both send it. Owners only.
	"""
	require_manager()
	company = resolve_company(company)
	text = str(step if step is not None else "").strip().lower()
	progress = _read_progress(company)
	if text == FINISH:
		progress = calc.finish(progress)
	else:
		name = calc.clean_step(text)
		if not name:
			frappe.throw(_("Choose a set-up step."), frappe.ValidationError)
		progress = calc.record_step(progress, name, skipped=_is_yes(skipped))
	_write_progress(company, progress)
	return {"success": True, "data": {"company": company, **_progress_payload(progress)}}


@frappe.whitelist(methods=["POST"])
def save_opening_cash(amount, company: str | None = None) -> dict:
	"""
	Keep the usual opening cash as a suggestion the Open till dialog can prefill (the person opening the till can
	always change it). An empty amount or zero means no suggestion. Owners only.
	"""
	require_manager()
	company = resolve_company(company)
	value = calc.clean_amount(amount)
	if value is None:
		frappe.throw(_("Enter the opening cash as an amount of zero or more."), frappe.ValidationError)
	frappe.db.set_default(_key(CASH_PREFIX, company), str(value))
	progress = calc.record_step(_read_progress(company), "cash")
	_write_progress(company, progress)
	return {
		"success": True,
		"data": {"company": company, "opening_cash": value, **_progress_payload(progress)},
	}


def _is_yes(value) -> bool:
	if isinstance(value, bool):
		return value
	return str(value if value is not None else "").strip().lower() in ("1", "true", "yes", "on")
