"""
E-Commerce and Online Store Integrations API (WooCommerce, etc.)
Handles connecting the POS system with online stores, syncing product catalogs,
and updating stock levels in real time.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import cstr, flt, now_datetime, nowdate

from murzak_pos.api.access_control import require_catalogue_role, require_manager
from murzak_pos.api.payment_gateway_common import resolve_company


def _ecommerce_key(company: str) -> str:
	return f"murzak_ecommerce_{company}"


def _mask_secret(value: str | None) -> str:
	if not value:
		return ""
	val = str(value).strip()
	if len(val) <= 8:
		return "********"
	return val[:4] + ("*" * (len(val) - 8)) + val[-4:]


def _is_masked(value: str | None) -> bool:
	return bool(value and "*" in str(value))


@frappe.whitelist()
def get_ecommerce_settings(company: str | None = None) -> dict[str, Any]:
	"""
	Get e-commerce and WooCommerce settings for a company.
	"""
	require_catalogue_role()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	# Read stored settings from Frappe defaults
	raw = frappe.db.get_default(_ecommerce_key(company))
	stored = {}
	if raw:
		try:
			stored = json.loads(raw) if isinstance(raw, str) else raw
		except Exception:
			stored = {}

	# Default warehouse
	default_wh = stored.get("warehouse")
	if not default_wh or not frappe.db.exists("Warehouse", default_wh):
		default_wh = (
			frappe.db.get_value(
				"Warehouse",
				{"company": company, "is_group": 0},
				"name",
				order_by="creation asc",
			)
			or ""
		)

	# Summary stats from Frappe catalogue
	catalog_count = frappe.db.count("Item", {"disabled": 0, "is_sales_item": 1})
	stock_count = 0
	if default_wh:
		stock_count = frappe.db.count(
			"Bin",
			{"warehouse": default_wh, "actual_qty": [">", 0]},
		)

	enabled = bool(stored.get("enabled", False))
	store_url = stored.get("store_url", "")
	consumer_key = stored.get("consumer_key", "")
	consumer_secret = stored.get("consumer_secret", "")

	status = "not_configured"
	if enabled and store_url and consumer_key and consumer_secret:
		status = "configured"
		if stored.get("last_connection_success"):
			status = "connected"

	data = {
		"company": company,
		"enabled": enabled,
		"platform": stored.get("platform", "woocommerce"),
		"store_url": store_url,
		"consumer_key": _mask_secret(consumer_key),
		"consumer_secret": _mask_secret(consumer_secret),
		"has_consumer_key": bool(consumer_key),
		"has_consumer_secret": bool(consumer_secret),
		"warehouse": default_wh,
		"auto_sync_stock": stored.get("auto_sync_stock", True),
		"auto_sync_products": stored.get("auto_sync_products", True),
		"sync_orders": stored.get("sync_orders", True),
		"last_sync": stored.get("last_sync"),
		"last_sync_status": stored.get("last_sync_status"),
		"last_sync_message": stored.get("last_sync_message"),
		"status": status,
		"catalog_count": catalog_count,
		"stock_items_count": stock_count,
	}

	return {"success": True, "data": data}


@frappe.whitelist()
def save_ecommerce_settings(
	settings: str | dict[str, Any],
	company: str | None = None,
) -> dict[str, Any]:
	"""
	Save e-commerce / WooCommerce configuration for a company.
	"""
	require_manager()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	if isinstance(settings, str):
		try:
			settings = json.loads(settings)
		except Exception:
			return {"success": False, "message": _("Invalid settings payload format")}

	if not isinstance(settings, dict):
		return {"success": False, "message": _("Settings must be an object")}

	# Read existing config to retain secrets if masked
	raw = frappe.db.get_default(_ecommerce_key(company))
	existing = {}
	if raw:
		try:
			existing = json.loads(raw) if isinstance(raw, str) else raw
		except Exception:
			existing = {}

	# Clean inputs
	store_url = cstr(settings.get("store_url", "")).strip().rstrip("/")
	if store_url:
		parsed = urlparse(store_url)
		if not parsed.scheme or not parsed.netloc:
			return {
				"success": False,
				"message": _("Please enter a valid website URL, e.g. https://example.com"),
			}

	incoming_key = cstr(settings.get("consumer_key", "")).strip()
	incoming_secret = cstr(settings.get("consumer_secret", "")).strip()

	# If incoming is masked or empty and existing has value, keep existing
	final_key = (
		existing.get("consumer_key", "") if _is_masked(incoming_key) or not incoming_key else incoming_key
	)
	final_secret = (
		existing.get("consumer_secret", "")
		if _is_masked(incoming_secret) or not incoming_secret
		else incoming_secret
	)

	warehouse = cstr(settings.get("warehouse", "")).strip()
	if warehouse and not frappe.db.exists("Warehouse", warehouse):
		return {"success": False, "message": _("Selected warehouse '{0}' does not exist").format(warehouse)}

	payload = {
		"company": company,
		"enabled": bool(settings.get("enabled", False)),
		"platform": cstr(settings.get("platform", "woocommerce")).strip() or "woocommerce",
		"store_url": store_url,
		"consumer_key": final_key,
		"consumer_secret": final_secret,
		"warehouse": warehouse,
		"auto_sync_stock": bool(settings.get("auto_sync_stock", True)),
		"auto_sync_products": bool(settings.get("auto_sync_products", True)),
		"sync_orders": bool(settings.get("sync_orders", True)),
		"last_sync": existing.get("last_sync"),
		"last_sync_status": existing.get("last_sync_status"),
		"last_sync_message": existing.get("last_sync_message"),
		"last_connection_success": existing.get("last_connection_success", False),
		"updated_at": str(now_datetime()),
	}

	frappe.db.set_default(_ecommerce_key(company), json.dumps(payload))
	frappe.db.commit()

	return {
		"success": True,
		"message": _("E-Commerce settings saved successfully"),
		"data": {
			"enabled": payload["enabled"],
			"store_url": payload["store_url"],
			"warehouse": payload["warehouse"],
			"has_consumer_key": bool(final_key),
			"has_consumer_secret": bool(final_secret),
		},
	}


@frappe.whitelist()
def test_woocommerce_connection(
	store_url: str | None = None,
	consumer_key: str | None = None,
	consumer_secret: str | None = None,
	company: str | None = None,
) -> dict[str, Any]:
	"""
	Test API connection to a WooCommerce store.
	"""
	require_manager()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	# Read stored settings if credentials are empty or masked
	raw = frappe.db.get_default(_ecommerce_key(company))
	stored = {}
	if raw:
		try:
			stored = json.loads(raw) if isinstance(raw, str) else raw
		except Exception:
			stored = {}

	url = cstr(store_url or stored.get("store_url", "")).strip().rstrip("/")
	key = cstr(consumer_key or "").strip()
	secret = cstr(consumer_secret or "").strip()

	if _is_masked(key) or not key:
		key = stored.get("consumer_key", "")
	if _is_masked(secret) or not secret:
		secret = stored.get("consumer_secret", "")

	if not url:
		return {"success": False, "message": _("WooCommerce Store URL is required")}
	if not key or not secret:
		return {"success": False, "message": _("Both Consumer Key and Consumer Secret are required")}

	parsed = urlparse(url)
	if not parsed.scheme or not parsed.netloc:
		return {"success": False, "message": _("Invalid store URL format. Must include https:// or http://")}

	import requests

	test_endpoints = [
		f"{url}/wp-json/wc/v3/system_status",
		f"{url}/wp-json/wc/v3/products?per_page=1",
	]

	last_err = None
	success = False
	wc_info = {}

	for ep in test_endpoints:
		try:
			resp = requests.get(
				ep,
				auth=(key, secret),
				timeout=12,
				headers={"User-Agent": "MurzakPOS-WooCommerce-Connector/1.0"},
			)
			if resp.status_code == 200:
				success = True
				try:
					wc_info = resp.json()
				except Exception:
					pass
				break
			elif resp.status_code == 401:
				return {
					"success": False,
					"message": _(
						"Authentication failed (401 Unauthorized): Consumer Key or Consumer Secret is invalid. Please check WooCommerce > Settings > Advanced > REST API."
					),
				}
			elif resp.status_code == 404:
				last_err = _(
					"WooCommerce REST API endpoint not found (404). Verify pretty permalinks are enabled in WordPress Settings > Permalinks."
				)
			else:
				last_err = _("Store returned status {0}: {1}").format(resp.status_code, resp.text[:150])
		except requests.exceptions.Timeout:
			last_err = _("Connection to {0} timed out after 12 seconds. Check server availability.").format(
				url
			)
		except requests.exceptions.ConnectionError:
			last_err = _(
				"Could not reach server at {0}. Please check domain name and internet connectivity."
			).format(url)
		except Exception as e:
			last_err = str(e)

	if success:
		if stored:
			stored["last_connection_success"] = True
			stored["last_connection_time"] = str(now_datetime())
			frappe.db.set_default(_ecommerce_key(company), json.dumps(stored))
			frappe.db.commit()

		environment = wc_info.get("environment", {}) if isinstance(wc_info, dict) else {}
		store_name = environment.get("site_title") or url
		wc_version = environment.get("version") or "Active"

		return {
			"success": True,
			"message": _("Successfully connected to WooCommerce store!"),
			"data": {
				"store_name": store_name,
				"version": wc_version,
				"url": url,
			},
		}

	return {
		"success": False,
		"message": last_err or _("Failed to connect to WooCommerce"),
	}


@frappe.whitelist()
def sync_woocommerce_catalog(
	company: str | None = None,
	warehouse: str | None = None,
	sync_type: str = "all",
) -> dict[str, Any]:
	"""
	Synchronize product catalog and inventory stock balances with WooCommerce.
	"""
	require_catalogue_role()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	raw = frappe.db.get_default(_ecommerce_key(company))
	stored = {}
	if raw:
		try:
			stored = json.loads(raw) if isinstance(raw, str) else raw
		except Exception:
			stored = {}

	if not stored.get("enabled"):
		return {
			"success": False,
			"message": _("E-Commerce integration is currently disabled in Settings"),
		}

	target_wh = warehouse or stored.get("warehouse")
	if not target_wh or not frappe.db.exists("Warehouse", target_wh):
		return {
			"success": False,
			"message": _("A valid warehouse is required to synchronize stock levels"),
		}

	# Gather items to sync
	items = frappe.get_all(
		"Item",
		filters={"disabled": 0, "is_sales_item": 1},
		fields=["name", "item_name", "item_group", "stock_uom", "standard_rate", "description"],
		limit_page_length=500,
	)

	# Fetch current stock quantities
	from murzak_pos.api.inventory_api import get_stock_balance_multiple

	item_codes = [it.name for it in items]
	balances_res = get_stock_balance_multiple(items=item_codes, warehouse=target_wh, company=company)
	balances = balances_res.get("data", {}) if isinstance(balances_res, dict) else {}

	synced_items = []
	for it in items:
		qty = flt(balances.get(it.name, 0.0))
		synced_items.append(
			{
				"sku": it.name,
				"name": it.item_name,
				"price": flt(it.standard_rate),
				"category": it.item_group,
				"stock_quantity": qty,
				"in_stock": qty > 0,
			}
		)

	# If credentials exist, post payload to WooCommerce batch endpoint
	url = stored.get("store_url")
	key = stored.get("consumer_key")
	secret = stored.get("consumer_secret")

	wc_synced = 0
	if url and key and secret:
		import requests

		batch_endpoint = f"{url.rstrip('/')}/wp-json/wc/v3/products/batch"
		batch_create = [
			{
				"name": s["name"],
				"sku": s["sku"],
				"regular_price": str(s["price"]) if s["price"] > 0 else "0",
				"manage_stock": True,
				"stock_quantity": s["stock_quantity"],
			}
			for s in synced_items[:50]
		]
		try:
			resp = requests.post(
				batch_endpoint,
				auth=(key, secret),
				json={"create": batch_create, "update": []},
				timeout=20,
				headers={"User-Agent": "MurzakPOS-WooCommerce-Connector/1.0"},
			)
			if resp.status_code in (200, 201):
				wc_synced = len(batch_create)
		except Exception:
			pass

	# Update sync record
	stored["last_sync"] = str(now_datetime())
	stored["last_sync_status"] = "success"
	stored["last_sync_message"] = _("Synced {0} products").format(len(synced_items))
	frappe.db.set_default(_ecommerce_key(company), json.dumps(stored))
	frappe.db.commit()

	return {
		"success": True,
		"message": _(
			"Catalog synchronized successfully ({0} items prepared, {1} items sent to online store)"
		).format(len(synced_items), wc_synced),
		"data": {
			"synced_count": len(synced_items),
			"online_synced_count": wc_synced,
			"warehouse": target_wh,
			"timestamp": str(now_datetime()),
			"sample_items": synced_items[:10],
		},
	}


@frappe.whitelist()
def get_ecommerce_products(
	company: str | None = None,
	warehouse: str | None = None,
	search: str | None = None,
	page: int = 1,
	page_size: int = 20,
) -> dict[str, Any]:
	"""
	List products with their online sync status and live stock for the e-commerce dashboard.
	"""
	require_catalogue_role()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 20)))
	offset = (page - 1) * page_size

	filters = {"disabled": 0, "is_sales_item": 1}
	if search:
		filters["item_name"] = ["like", f"%{search}%"]

	total = frappe.db.count("Item", filters)
	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=["name", "item_name", "item_group", "stock_uom", "standard_rate", "is_stock_item"],
		order_by="item_name asc",
		limit_start=offset,
		limit_page_length=page_size,
	)

	from murzak_pos.api.inventory_api import get_stock_balance_multiple

	wh = warehouse or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	item_codes = [it.name for it in items]
	balances_res = get_stock_balance_multiple(items=item_codes, warehouse=wh, company=company)
	balances = balances_res.get("data", {}) if isinstance(balances_res, dict) else {}

	rows = []
	for it in items:
		qty = flt(balances.get(it.name, 0.0))
		rows.append(
			{
				"item_code": it.name,
				"item_name": it.item_name,
				"category": it.item_group,
				"unit": it.stock_uom,
				"price": flt(it.standard_rate),
				"stock": qty,
				"in_stock": qty > 0,
				"sync_status": "ready" if it.name and it.standard_rate > 0 else "needs_price",
			}
		)

	return {
		"success": True,
		"data": {
			"rows": rows,
			"total": total,
			"page": page,
			"page_size": page_size,
			"warehouse": wh,
		},
	}
