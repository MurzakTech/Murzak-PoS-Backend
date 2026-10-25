"""
The hosted online store: an owner's own web shop, with no website of their own.

A business turns it on here, picks a short name for its address ("duka-ya-amina"), says how customers get their
goods (delivery, pickup) and pay (cash on delivery, M-Pesa), and gives a WhatsApp number. The shop is then open at
`<storefront address>/shop/<short name>`, made from the products the business already has in the POS, with
live stock. A customer's order becomes a Sales Order (see online_orders.py) and appears on the till.

This file is the owner's side: the settings, the share links, and which products are shown. What customers see and
do is in storefront_api.py.

Settings are kept, as the WooCommerce settings are, as a small record per business. A second record maps each short
name to its business, so a customer's link can be opened without knowing who owns it.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime

from murzak_pos.api import delivery_fees, online_catalogue
from murzak_pos.api.access_control import require_catalogue_role, require_manager
from murzak_pos.api.payment_gateway_common import resolve_company

STORE_KEY = "murzak_store_"
SLUG_KEY = "murzak_store_slug_"

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
# Names that would look like the system's own pages
RESERVED_SLUGS = frozenset(
	{
		"admin",
		"api",
		"app",
		"assets",
		"help",
		"login",
		"logout",
		"murzak",
		"pos",
		"shop",
		"signup",
		"static",
		"store",
		"support",
		"www",
		"checkout",
		"cart",
		"order",
		"orders",
		"track",
	}
)
MAX_BULK_ITEMS = 500

DEFAULTS = {
	"enabled": False,
	"slug": "",
	"store_name": "",
	"tagline": "",
	"whatsapp_number": "",
	"delivery_enabled": True,
	"pickup_enabled": True,
	"pickup_address": "",
	"delivery_note": "",
	"accept_cash": True,
	"accept_mpesa": False,
	"warehouse": "",
	"minimum_order": 0,
	# What delivery costs (api/delivery_fees.py)
	"delivery_mode": "free",
	"delivery_flat_fee": 0,
	"delivery_areas": [],
	"free_delivery_above": 0,
	"shop_latitude": None,
	"shop_longitude": None,
	"distance_base_km": 0,
	"distance_base_fee": 0,
	"distance_per_km": 0,
	"distance_max_km": 0,
	"road_factor": delivery_fees.DEFAULT_ROAD_FACTOR,
}


def read_store(company: str) -> dict:
	raw = frappe.db.get_default(STORE_KEY + company)
	try:
		stored = (json.loads(raw) if isinstance(raw, str) else raw) or {}
	except ValueError:
		stored = {}
	return {
		**copy.deepcopy(DEFAULTS),
		**{k: v for k, v in stored.items() if k in DEFAULTS or k == "updated_at"},
	}


def write_store(company: str, data: dict) -> None:
	frappe.db.set_default(STORE_KEY + company, json.dumps(data))


def company_for_slug(slug: str | None) -> str | None:
	slug = cstr(slug).strip().lower()
	if not slug or not SLUG_PATTERN.match(slug):
		return None
	return frappe.db.get_default(SLUG_KEY + slug) or None


def slug_problem(slug: str, company: str) -> str | None:
	"""Why this short name cannot be used by this business, or None when it can."""
	if not SLUG_PATTERN.match(slug):
		return _("Use 3 to 40 lowercase letters, numbers or hyphens, like duka-ya-amina.")
	if slug in RESERVED_SLUGS:
		return _("That name is reserved. Please choose another.")
	owner = company_for_slug(slug)
	if owner and owner != company:
		return _("That name is already taken. Please choose another.")
	return None


def normalize_whatsapp(number: str | None) -> str:
	"""A WhatsApp number as digits with the country code (254712345678). Kenyan numbers may be written 0712 345 678."""
	digits = re.sub(r"\D", "", number or "")
	if digits.startswith("0") and len(digits) == 10:
		digits = "254" + digits[1:]
	if not digits:
		return ""
	if not re.match(r"^[1-9]\d{8,14}$", digits):
		frappe.throw(
			_("Enter the WhatsApp number with its country code, like 254712345678."), frappe.ValidationError
		)
	return digits


def storefront_base() -> str:
	"""The address customers open. Set `storefront_base_url` in the site config when the shop pages live elsewhere."""
	return (frappe.conf.get("storefront_base_url") or frappe.utils.get_url() or "").rstrip("/")


def store_links(settings: dict) -> dict:
	"""The addresses the owner shares with customers."""
	if not settings.get("slug"):
		return {"store": "", "whatsapp_share": ""}
	store = f"{storefront_base()}/shop/{settings['slug']}"
	text = _("Shop with us online: {0}").format(store)
	return {"store": store, "whatsapp_share": f"https://wa.me/?text={quote(text)}"}


def _mpesa_ready(company: str) -> bool:
	return bool(frappe.db.exists("MPESA Settings", {"company": company, "is_active": 1}))


@frappe.whitelist()
def get_online_store(company: str | None = None) -> dict[str, Any]:
	"""The business's online store settings, the links to share, and what is still missing before it can open."""
	require_catalogue_role()
	company = resolve_company(company) if company else resolve_company()
	settings = read_store(company)
	if not settings["store_name"]:
		settings["store_name"] = frappe.db.get_value("Company", company, "company_name") or company
	settings["warehouse"] = online_catalogue.default_warehouse(company, settings["warehouse"])
	return {
		"success": True,
		"data": {
			**settings,
			"company": company,
			"links": store_links(settings),
			"mpesa_ready": _mpesa_ready(company),
			"product_count": online_catalogue.count_items(company),
		},
	}


@frappe.whitelist()
def check_store_slug(slug: str, company: str | None = None) -> dict[str, Any]:
	"""Is this short name free for the business? Used as the owner types."""
	require_catalogue_role()
	company = resolve_company(company) if company else resolve_company()
	slug = cstr(slug).strip().lower()
	problem = slug_problem(slug, company)
	return {"success": True, "data": {"slug": slug, "available": problem is None, "message": problem}}


@frappe.whitelist()
def save_online_store(settings: str | dict[str, Any], company: str | None = None) -> dict[str, Any]:
	"""Save the store settings. A store can be switched on only when a customer could really order from it."""
	require_manager()
	company = resolve_company(company) if company else resolve_company()
	if isinstance(settings, str):
		try:
			settings = json.loads(settings)
		except ValueError:
			return {"success": False, "message": _("Invalid settings payload format")}
	if not isinstance(settings, dict):
		return {"success": False, "message": _("Settings must be an object")}

	existing = read_store(company)
	slug = cstr(settings.get("slug", existing["slug"])).strip().lower()
	if slug:
		problem = slug_problem(slug, company)
		if problem:
			return {"success": False, "message": problem}

	warehouse = cstr(settings.get("warehouse", existing["warehouse"])).strip()
	if warehouse and not frappe.db.exists("Warehouse", {"name": warehouse, "company": company}):
		return {"success": False, "message": _("Selected warehouse '{0}' does not exist").format(warehouse)}

	try:
		minimum = max(0.0, float(settings.get("minimum_order", existing["minimum_order"]) or 0))
	except (TypeError, ValueError):
		return {"success": False, "message": _("The minimum order must be a number.")}

	def flag(name):
		return bool(cint(settings.get(name, existing[name])))

	try:
		rules = delivery_fees.validate_rules(settings, existing)
	except delivery_fees.DeliveryRuleError as e:
		return {"success": False, "message": str(e)}

	data = {
		"enabled": flag("enabled"),
		"slug": slug,
		"store_name": cstr(settings.get("store_name", existing["store_name"])).strip()[:80],
		"tagline": cstr(settings.get("tagline", existing["tagline"])).strip()[:160],
		"whatsapp_number": normalize_whatsapp(
			cstr(settings.get("whatsapp_number", existing["whatsapp_number"]))
		),
		"delivery_enabled": flag("delivery_enabled"),
		"pickup_enabled": flag("pickup_enabled"),
		"pickup_address": cstr(settings.get("pickup_address", existing["pickup_address"])).strip()[:300],
		"delivery_note": cstr(settings.get("delivery_note", existing["delivery_note"])).strip()[:300],
		"accept_cash": flag("accept_cash"),
		"accept_mpesa": flag("accept_mpesa"),
		"warehouse": warehouse,
		"minimum_order": minimum,
		**rules,
		"updated_at": str(now_datetime()),
	}

	if data["enabled"]:
		missing = []
		if not data["slug"]:
			missing.append(_("a web address name"))
		if not (data["delivery_enabled"] or data["pickup_enabled"]):
			missing.append(_("delivery or pickup"))
		if not (data["accept_cash"] or data["accept_mpesa"]):
			missing.append(_("a way to pay"))
		if data["delivery_enabled"]:
			missing.extend(delivery_fees.problems_before_opening(rules))
		if data["accept_mpesa"] and not _mpesa_ready(company):
			return {
				"success": False,
				"message": _(
					"Set up M-Pesa under Settings > Payment Gateways before accepting M-Pesa orders."
				),
			}
		if missing:
			return {
				"success": False,
				"message": _("To open the store, choose {0}.").format(", ".join(missing)),
			}

	# The short name now points at this business, and the old one is released
	if existing["slug"] and existing["slug"] != data["slug"]:
		frappe.db.set_default(SLUG_KEY + existing["slug"], "")
	if data["slug"]:
		frappe.db.set_default(SLUG_KEY + data["slug"], company)
	write_store(company, data)
	frappe.db.commit()

	return {
		"success": True,
		"message": _("Online store settings saved"),
		"data": {**data, "links": store_links(data)},
	}


@frappe.whitelist()
def set_items_online(
	item_codes: str | list[str], show: int | bool = 1, company: str | None = None
) -> dict[str, Any]:
	"""Show products on the online store and website, or hide them. Only the business's own products change."""
	require_catalogue_role()
	company = resolve_company(company) if company else resolve_company()
	if isinstance(item_codes, str):
		try:
			item_codes = json.loads(item_codes)
		except ValueError:
			item_codes = [item_codes]
	if not isinstance(item_codes, list) or not item_codes:
		frappe.throw(_("Choose at least one product."), frappe.ValidationError)
	if len(item_codes) > MAX_BULK_ITEMS:
		frappe.throw(
			_("Choose at most {0} products at a time.").format(MAX_BULK_ITEMS), frappe.ValidationError
		)

	owned = frappe.get_all(
		"Item",
		filters={"name": ["in", [cstr(c) for c in item_codes]], "custom_company": company},
		pluck="name",
	)
	value = 1 if cint(show) else 0
	for name in owned:
		frappe.db.set_value("Item", name, "custom_show_online", value)
	frappe.db.commit()
	return {
		"success": True,
		"message": _("{0} products updated.").format(len(owned)),
		"data": {"updated": len(owned), "skipped": len(item_codes) - len(owned), "show": bool(value)},
	}
