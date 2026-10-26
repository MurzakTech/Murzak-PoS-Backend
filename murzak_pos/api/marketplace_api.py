"""
The Murzak marketplace inside the POS: the things an owner can buy for their shop (an online storefront, a
WooCommerce website, backups), with the real prices.

The products and prices belong to the Murzak website, which also takes the payment and starts setting things up once
an order is paid. This file only asks the website for its list, checks what comes back, and gives the POS each
product's checkout address. It never takes money and never creates an order, so nothing here can charge anyone.

The owner is sent to the website's own checkout (`/checkout/new?serviceId=<id>`), where they sign in with their
Murzak account, pay by M-Pesa or PayPal, and the website starts the provisioning.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import frappe
import requests
from frappe import _

from murzak_pos.api.access_control import require_manager

DEFAULT_WEBSITE = "https://murzaktech.tech"
CATALOGUE_PATH = "/api/marketplace/catalog"
CACHE_KEY = "murzak_marketplace_catalogue"
CACHE_SECONDS = 300
TIMEOUT_SECONDS = 8

PRODUCT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
GROUP_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
MAX_GROUPS = 12
MAX_ITEMS_PER_GROUP = 30


def website_base() -> str:
	"""The Murzak website's address. Set `murzak_website_url` in the site config to use another (a test site)."""
	base = str(frappe.conf.get("murzak_website_url") or DEFAULT_WEBSITE).strip().rstrip("/")
	return base if base.lower().startswith("https://") else DEFAULT_WEBSITE


def _money(value: Any) -> int:
	try:
		number = float(value)
	except (TypeError, ValueError):
		return 0
	return int(number) if number > 0 else 0


def clean_groups(payload: Any, base: str) -> list[dict[str, Any]]:
	"""
	Keep only what the POS understands from the website's reply, whatever it contained: plain ids, short names,
	whole-shilling prices. Anything odd is dropped, never passed on to the page.
	"""
	groups = payload.get("groups") if isinstance(payload, dict) else None
	if not isinstance(groups, list):
		return []

	cleaned = []
	for group in groups[:MAX_GROUPS]:
		if not isinstance(group, dict) or not GROUP_KEY.match(str(group.get("key") or "")):
			continue
		items = []
		for item in (group.get("items") or [])[:MAX_ITEMS_PER_GROUP]:
			if not isinstance(item, dict):
				continue
			product_id = str(item.get("id") or "")
			monthly = _money(item.get("monthlyKes"))
			if not PRODUCT_ID.match(product_id) or not monthly:
				continue
			items.append(
				{
					"id": product_id,
					"name": str(item.get("name") or product_id).strip()[:80],
					"monthly": monthly,
					"setup": _money(item.get("setupKes")),
					"checkout_url": f"{base}/checkout/new?serviceId={quote(product_id)}",
				}
			)
		if items:
			cleaned.append({"key": str(group["key"]), "items": items})
	return cleaned


def fetch_catalogue(base: str) -> dict[str, Any] | None:
	"""The website's list, kept for a few minutes. None when the website could not be reached."""
	cache_key = f"{CACHE_KEY}:{base}"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return cached
	try:
		response = requests.get(
			f"{base}{CATALOGUE_PATH}",
			timeout=TIMEOUT_SECONDS,
			headers={"User-Agent": "MurzakPOS-Marketplace/1.0", "Accept": "application/json"},
			# A redirect could point anywhere, past the address we chose
			allow_redirects=False,
		)
		if response.status_code != 200:
			return None
		payload = response.json()
	except (requests.RequestException, ValueError):
		return None
	frappe.cache().set_value(cache_key, payload, expires_in_sec=CACHE_SECONDS)
	return payload


@frappe.whitelist()
def get_marketplace() -> dict[str, Any]:
	"""What this business can buy, in groups, with prices and the checkout address of each product."""
	require_manager()
	base = website_base()
	payload = fetch_catalogue(base)
	if payload is None:
		return {
			"success": False,
			"message": _("The marketplace could not be reached just now. Please try again in a few minutes."),
		}
	return {
		"success": True,
		"data": {"groups": clean_groups(payload, base), "currency": "KES", "website": base},
	}
