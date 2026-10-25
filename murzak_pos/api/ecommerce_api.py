"""
E-Commerce and Online Store Integrations API (WooCommerce, etc.)
Handles connecting the POS system with online stores, syncing product catalogs,
and updating stock levels in real time.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import cstr, flt, now_datetime, nowdate

from murzak_pos.api import online_catalogue
from murzak_pos.api.access_control import require_online_catalogue, require_online_integrations
from murzak_pos.api.payment_gateway_common import resolve_company


def _ecommerce_key(company: str) -> str:
	return f"murzak_ecommerce_{company}"


# The WooCommerce keys open the shop's online store. Site-wide defaults are sent to every desk session, so they are
# kept there encrypted with the site's own key (as Frappe keeps Password fields), never as plain text.
SEALED_FIELDS = ("consumer_key", "consumer_secret", "webhook_secret")
SEALED_PREFIX = "sealed:"


def seal(value):
	from frappe.utils.password import encrypt

	if not value or str(value).startswith(SEALED_PREFIX):
		return value
	return SEALED_PREFIX + encrypt(str(value))


def unseal(value):
	"""The plain key; a value saved before keys were sealed is read as it is."""
	from frappe.utils.password import decrypt

	if not value or not str(value).startswith(SEALED_PREFIX):
		return value or ""
	try:
		return decrypt(str(value)[len(SEALED_PREFIX) :])
	except Exception:
		frappe.log_error("E-Commerce keys could not be read", frappe.get_traceback())
		return ""


def read_settings(company: str) -> dict:
	raw = frappe.db.get_default(_ecommerce_key(company))
	try:
		stored = (json.loads(raw) if isinstance(raw, str) else raw) or {}
	except Exception:
		stored = {}
	for field in SEALED_FIELDS:
		if field in stored:
			stored[field] = unseal(stored[field])
	return stored


def write_settings(company: str, settings: dict) -> None:
	sealed = dict(settings)
	for field in SEALED_FIELDS:
		if field in sealed:
			sealed[field] = seal(sealed[field])
	frappe.db.set_default(_ecommerce_key(company), json.dumps(sealed))


def _mask_secret(value: str | None) -> str:
	if not value:
		return ""
	val = str(value).strip()
	if len(val) <= 8:
		return "********"
	return val[:4] + ("*" * (len(val) - 8)) + val[-4:]


def _is_masked(value: str | None) -> bool:
	return bool(value and "*" in str(value))


def store_address_problem(url: str) -> str | None:
	"""
	Why the server must not call this store address, or None when it may. The address is fetched from the server,
	so one inside the server's own network (127.0.0.1, 10.x, the cloud's metadata address...) is refused, with the
	same check the product image import uses.
	"""
	from murzak_pos.api.product_import_images import ImageProblem, check_link

	try:
		check_link(url)
	except ImageProblem:
		return _(
			"That store address cannot be used. Enter the shop's public web address, like https://myshop.com."
		)
	return None


@frappe.whitelist()
def get_ecommerce_settings(company: str | None = None) -> dict[str, Any]:
	"""
	Get e-commerce and WooCommerce settings for a company.
	"""
	require_online_integrations()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	# Read stored settings from Frappe defaults
	stored = read_settings(company)

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

	# Summary stats: this business's online products only
	catalog_count = online_catalogue.count_items(company)
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
		"webhook_ready": bool(stored.get("webhook_token") and stored.get("webhook_secret")),
		"last_order_at": stored.get("last_order_at"),
		"last_order_message": stored.get("last_order_message"),
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
	require_online_integrations()
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
	existing = read_settings(company)

	# Clean inputs
	store_url = cstr(settings.get("store_url", "")).strip().rstrip("/")
	if store_url:
		parsed = urlparse(store_url)
		if not parsed.scheme or not parsed.netloc:
			return {
				"success": False,
				"message": _("Please enter a valid website URL, e.g. https://example.com"),
			}
		problem = store_address_problem(store_url)
		if problem:
			return {"success": False, "message": problem}

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
		# A new address or new keys have not been tested yet
		"last_connection_success": existing.get("last_connection_success", False)
		and store_url == existing.get("store_url", "")
		and final_key == existing.get("consumer_key", "")
		and final_secret == existing.get("consumer_secret", ""),
		"updated_at": str(now_datetime()),
	}
	# Facts the settings form does not own are kept as they are
	for kept in ("webhook_token", "webhook_secret", "last_order_at", "last_order_message"):
		if existing.get(kept):
			payload[kept] = existing[kept]

	write_settings(company, payload)
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
	require_online_integrations()
	if company:
		company = resolve_company(company)
	else:
		company = resolve_company()

	# Read stored settings if credentials are empty or masked
	stored = read_settings(company)

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
	problem = store_address_problem(url)
	if problem:
		return {"success": False, "message": problem}

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
				# A redirect could point anywhere, past the address check
				allow_redirects=False,
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
			write_settings(company, stored)
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


# ---------------------------------------------------------------------------
# Talking to WooCommerce
# ---------------------------------------------------------------------------

BATCH_SIZE = 50  # products per request; WooCommerce accepts up to 100 changes in one batch
USER_AGENT = "MurzakPOS-WooCommerce-Connector/1.0"
REQUEST_TIMEOUT = 25


class WooError(Exception):
	"""WooCommerce refused or could not be reached; the message is fit to show the owner."""


def woo_request(settings: dict, method: str, path: str, params: dict | None = None, body: dict | None = None):
	"""One call to the shop's WooCommerce API. Returns the decoded answer or raises WooError."""
	import requests

	url = (settings.get("store_url") or "").rstrip("/")
	problem = store_address_problem(url)
	if problem:
		raise WooError(problem)
	try:
		resp = requests.request(
			method,
			f"{url}/wp-json/wc/v3/{path}",
			auth=(settings.get("consumer_key", ""), settings.get("consumer_secret", "")),
			params=params,
			json=body,
			timeout=REQUEST_TIMEOUT,
			headers={"User-Agent": USER_AGENT},
			# A redirect could point anywhere, past the address check
			allow_redirects=False,
		)
	except requests.exceptions.Timeout:
		raise WooError(_("The online store did not answer in time."))
	except requests.exceptions.ConnectionError:
		raise WooError(_("Could not reach the online store. Check its web address."))
	if resp.status_code in (401, 403):
		raise WooError(
			_(
				"The online store refused the keys. Check WooCommerce > Settings > Advanced > REST API (needs Read/Write)."
			)
		)
	if resp.status_code == 404:
		raise WooError(
			_("The store's WooCommerce API was not found. Turn on pretty permalinks in WordPress.")
		)
	if resp.status_code >= 400:
		raise WooError(
			_("The online store answered with an error ({0}): {1}").format(resp.status_code, resp.text[:150])
		)
	try:
		return resp.json()
	except ValueError:
		raise WooError(_("The online store gave an answer this system cannot read."))


def _whole_stock(qty) -> int:
	"""WooCommerce counts stock in whole numbers."""
	return max(0, int(flt(qty)))


def _create_payload(row: dict) -> dict:
	payload = {
		"name": row["item_name"],
		"sku": row["item_code"],
		"regular_price": str(row["price"]) if row["price"] > 0 else "0",
		"description": row.get("description") or "",
	}
	if row.get("image"):
		payload["images"] = [{"src": frappe.utils.get_url(row["image"])}]
	payload.update(_stock_payload(row))
	return payload


def _stock_payload(row: dict) -> dict:
	if row.get("stock") is None:
		# A service or other product with no stock count
		return {"manage_stock": False, "stock_status": "instock"}
	qty = _whole_stock(row["stock"])
	return {
		"manage_stock": True,
		"stock_quantity": qty,
		"stock_status": "instock" if qty > 0 else "outofstock",
	}


def push_products(settings: dict, rows: list[dict], stock_only: bool = False) -> dict:
	"""
	Send products to the shop. A product the shop already has (same SKU) is updated, never created twice; its price and
	stock follow the POS, while its name, pictures and description stay as the owner edited them on the website.
	With `stock_only`, only products the shop already has are touched.

	Returns {"created", "updated", "failed", "skipped", "errors"}. A batch the shop rejects is counted as failed and
	reported, never counted as sent.
	"""
	result = {"created": 0, "updated": 0, "failed": 0, "skipped": 0, "errors": []}
	for start in range(0, len(rows), BATCH_SIZE):
		chunk = rows[start : start + BATCH_SIZE]
		try:
			known = woo_request(
				settings,
				"GET",
				"products",
				params={"sku": ",".join(r["item_code"] for r in chunk), "per_page": 100, "_fields": "id,sku"},
			)
			ids = {k.get("sku"): k.get("id") for k in known or [] if isinstance(k, dict)}

			create, create_rows, update, update_rows = [], [], [], []
			for r in chunk:
				if r["item_code"] in ids:
					change = {
						"id": ids[r["item_code"]],
						"regular_price": str(r["price"]) if r["price"] > 0 else "0",
					}
					if stock_only:
						change.pop("regular_price")
					change.update(_stock_payload(r))
					update.append(change)
					update_rows.append(r)
				elif stock_only:
					result["skipped"] += 1
				else:
					create.append(_create_payload(r))
					create_rows.append(r)
			if not create and not update:
				continue

			answer = woo_request(
				settings, "POST", "products/batch", body={"create": create, "update": update}
			)
			for key, sent in (("create", create_rows), ("update", update_rows)):
				for entry, r in zip((answer or {}).get(key, []), sent, strict=False):
					error = entry.get("error") if isinstance(entry, dict) else None
					if error:
						result["failed"] += 1
						result["errors"].append(f"{r['item_code']}: {error.get('message', '')}"[:200])
					else:
						result["created" if key == "create" else "updated"] += 1
		except WooError as e:
			result["failed"] += len(chunk)
			result["errors"].append(str(e))
			# The same fault will meet every later batch: stop and report, rather than hammer the shop
			break
	result["errors"] = result["errors"][:20]
	return result


def _record_sync(company: str, stored: dict, status: str, message: str) -> None:
	stored["last_sync"] = str(now_datetime())
	stored["last_sync_status"] = status
	stored["last_sync_message"] = message
	write_settings(company, stored)


def run_catalog_sync(company: str, warehouse: str | None = None, stock_only: bool = False) -> dict:
	"""Send the business's whole online catalogue (or just its stock) to the shop. Used by the button and the hourly job."""
	stored = read_settings(company)
	target_wh = online_catalogue.default_warehouse(company, warehouse or stored.get("warehouse"))
	if not target_wh:
		return {"success": False, "message": _("A valid warehouse is required to synchronize stock levels")}

	totals = {"created": 0, "updated": 0, "failed": 0, "skipped": 0, "errors": []}
	prepared = 0
	sample: list[dict] = []
	for rows in online_catalogue.iter_all_items(company, target_wh):
		prepared += len(rows)
		if len(sample) < 10:
			sample.extend(rows[: 10 - len(sample)])
		part = push_products(stored, rows, stock_only=stock_only)
		for key in ("created", "updated", "failed", "skipped"):
			totals[key] += part[key]
		totals["errors"].extend(part["errors"])
		if part["failed"] and part["errors"] and not (part["created"] or part["updated"]):
			# Nothing went through in this batch: the shop is refusing us, so stop
			break
	totals["errors"] = totals["errors"][:20]

	sent = totals["created"] + totals["updated"]
	if totals["failed"] and not sent:
		status = "failed"
		message = totals["errors"][0] if totals["errors"] else _("Nothing could be sent to the online store")
	elif totals["failed"]:
		status = "partial"
		message = _("Sent {0} products; {1} could not be sent. {2}").format(
			sent, totals["failed"], totals["errors"][0] if totals["errors"] else ""
		)
	else:
		status = "success"
		message = _("Sent {0} products to the online store ({1} new, {2} updated)").format(
			sent, totals["created"], totals["updated"]
		)
	_record_sync(company, stored, status, message)
	frappe.db.commit()

	return {
		"success": status != "failed",
		"message": message,
		"data": {
			"status": status,
			"synced_count": prepared,
			"online_synced_count": sent,
			"created_count": totals["created"],
			"updated_count": totals["updated"],
			"error_count": totals["failed"],
			"errors": totals["errors"],
			"warehouse": target_wh,
			"timestamp": str(now_datetime()),
			"sample_items": sample,
		},
	}


@frappe.whitelist()
def sync_woocommerce_catalog(
	company: str | None = None,
	warehouse: str | None = None,
	sync_type: str = "all",
) -> dict[str, Any]:
	"""
	Send the online catalogue to the WooCommerce shop: new products are created, existing ones (same SKU) are updated.
	`sync_type` "stock" sends only stock levels.
	"""
	require_online_integrations()
	company = resolve_company(company) if company else resolve_company()
	stored = read_settings(company)

	if not stored.get("enabled"):
		return {"success": False, "message": _("E-Commerce integration is currently disabled in Settings")}
	if not (stored.get("store_url") and stored.get("consumer_key") and stored.get("consumer_secret")):
		return {"success": False, "message": _("Enter the store address and the WooCommerce keys first.")}
	if warehouse and not frappe.db.exists("Warehouse", {"name": warehouse, "company": company}):
		return {"success": False, "message": _("A valid warehouse is required to synchronize stock levels")}

	return run_catalog_sync(company, warehouse, stock_only=(sync_type == "stock"))


# ---------------------------------------------------------------------------
# Keeping the shop's stock current
# ---------------------------------------------------------------------------


def _auto_stock_enabled(company: str) -> bool:
	"""Cheap check used on every sale: is this business pushing stock to a shop? (No decrypting.)"""
	raw = frappe.db.get_default(_ecommerce_key(company))
	try:
		stored = (json.loads(raw) if isinstance(raw, str) else raw) or {}
	except Exception:
		return False
	return bool(stored.get("enabled") and stored.get("auto_sync_stock", True) and stored.get("store_url"))


def push_stock_job(company: str, item_codes: list[str]) -> None:
	"""Background job: send the current stock of these products to the shop."""
	try:
		stored = read_settings(company)
		if not (stored.get("enabled") and stored.get("auto_sync_stock", True)):
			return
		wh = online_catalogue.default_warehouse(company, stored.get("warehouse"))
		items = frappe.get_all(
			"Item",
			filters={**online_catalogue.online_filters(company), "name": ["in", item_codes]},
			fields=[
				"name",
				"item_name",
				"item_group",
				"stock_uom",
				"standard_rate",
				"description",
				"image",
				"is_stock_item",
			],
		)
		if not items:
			return
		rows = online_catalogue.rows_for(items, wh)
		result = push_products(stored, rows, stock_only=True)
		if result["failed"]:
			_record_sync(
				company,
				stored,
				"partial",
				_("Stock update: {0}").format(result["errors"][0] if result["errors"] else ""),
			)
			frappe.db.commit()
	except Exception:
		frappe.log_error("WooCommerce stock push", frappe.get_traceback())


def queue_stock_push(doc, method=None) -> None:
	"""A sale, order or return changed stock: tell the shop, in the background. Never blocks or fails the sale."""
	try:
		company = doc.get("company")
		if not company or not _auto_stock_enabled(company):
			return
		codes = sorted({i.item_code for i in doc.get("items") or [] if i.get("item_code")})
		if codes:
			frappe.enqueue(
				"murzak_pos.api.ecommerce_api.push_stock_job",
				queue="short",
				enqueue_after_commit=True,
				company=company,
				item_codes=codes,
			)
	except Exception:
		frappe.log_error("WooCommerce stock queue", frappe.get_traceback())


def run_hourly_stock_sync() -> None:
	"""Catch-up for stock that moved without a sale (deliveries, counts, transfers): push what changed lately."""
	from frappe.utils import add_to_date

	since = add_to_date(now_datetime(), minutes=-70)
	for company in frappe.get_all("Company", pluck="name"):
		try:
			if not _auto_stock_enabled(company):
				continue
			stored = read_settings(company)
			wh = online_catalogue.default_warehouse(company, stored.get("warehouse"))
			if not wh:
				continue
			changed = frappe.get_all(
				"Bin", filters={"warehouse": wh, "modified": [">=", since]}, pluck="item_code"
			)
			for start in range(0, len(changed), 200):
				push_stock_job(company, changed[start : start + 200])
		except Exception:
			frappe.log_error("WooCommerce hourly stock sync", frappe.get_traceback())


# ---------------------------------------------------------------------------
# Receiving the shop's orders
# ---------------------------------------------------------------------------

TOKEN_KEY = "murzak_ecommerce_token_"


def webhook_url(token: str) -> str:
	from murzak_pos.api.payment_gateway_common import get_callback_base_url

	return f"{get_callback_base_url()}/api/method/murzak_pos.api.online_webhooks.woocommerce_order?t={token}"


@frappe.whitelist()
def get_order_webhook(company: str | None = None) -> dict[str, Any]:
	"""
	The web address and secret to paste into WooCommerce (Settings > Advanced > Webhooks), so the shop's orders reach the
	POS. Made on first use. Only a manager may see the secret.
	"""
	require_online_integrations()
	company = resolve_company(company) if company else resolve_company()
	stored = read_settings(company)
	changed = False
	if not stored.get("webhook_token"):
		stored["webhook_token"] = secrets.token_urlsafe(24)
		changed = True
	if not stored.get("webhook_secret"):
		stored["webhook_secret"] = secrets.token_urlsafe(32)
		changed = True
	if changed:
		write_settings(company, stored)
		frappe.db.set_default(TOKEN_KEY + stored["webhook_token"], company)
		frappe.db.commit()
	return {
		"success": True,
		"data": {
			"delivery_url": webhook_url(stored["webhook_token"]),
			"secret": stored["webhook_secret"],
			"topic": "Order created (add a second webhook for Order updated)",
			"steps": [
				_("In WordPress open WooCommerce > Settings > Advanced > Webhooks > Add webhook."),
				_("Topic: Order created. Status: Active."),
				_("Delivery URL and Secret: copy the two values given here."),
				_("Save. Repeat with the topic Order updated, so cancelled orders are cancelled here too."),
			],
		},
	}


@frappe.whitelist()
def regenerate_order_webhook(company: str | None = None) -> dict[str, Any]:
	"""Make a new address and secret (the old ones stop working). Use if they may have leaked."""
	require_online_integrations()
	company = resolve_company(company) if company else resolve_company()
	stored = read_settings(company)
	old = stored.pop("webhook_token", None)
	stored.pop("webhook_secret", None)
	write_settings(company, stored)
	if old:
		frappe.db.set_default(TOKEN_KEY + old, "")
	frappe.db.commit()
	return get_order_webhook(company)


def company_for_webhook_token(token: str | None) -> str | None:
	if not token:
		return None
	return frappe.db.get_default(TOKEN_KEY + token) or None


@frappe.whitelist()
def get_ecommerce_products(
	company: str | None = None,
	warehouse: str | None = None,
	search: str | None = None,
	page: int = 1,
	page_size: int = 20,
	visibility: str = "shown",
) -> dict[str, Any]:
	"""
	List this business's online products with their live stock for the e-commerce dashboard.
	`visibility` is "shown" (the default), "hidden" or "all"; each row says whether it is shown online (`show_online`),
	so the owner can switch a hidden product back on.
	"""
	require_online_catalogue()
	company = resolve_company(company) if company else resolve_company()
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 20)))
	if visibility not in online_catalogue.VISIBILITY:
		visibility = "shown"

	wh = online_catalogue.default_warehouse(company, warehouse)
	total = online_catalogue.count_items(company, search=search, visibility=visibility)
	rows = [
		{
			"item_code": r["item_code"],
			"item_name": r["item_name"],
			"category": r["category"],
			"unit": r["unit"],
			"price": r["price"],
			"stock": r["stock"] or 0,
			"in_stock": r["in_stock"],
			"show_online": r["show_online"],
			"sync_status": "ready" if r["price"] > 0 else "needs_price",
		}
		for r in online_catalogue.list_items(
			company, wh, search=search, page=page, page_size=page_size, visibility=visibility
		)
	]
	return {
		"success": True,
		"data": {"rows": rows, "total": total, "page": page, "page_size": page_size, "warehouse": wh},
	}
