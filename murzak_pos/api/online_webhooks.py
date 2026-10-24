"""
Orders arriving from a WooCommerce shop.

WooCommerce can call a web address whenever an order is made or changed (a "webhook"). The owner pastes the address
and secret from Settings > E-Commerce into WooCommerce once; after that every order lands here and becomes a Sales
Order in the POS (see online_orders.place_order), with its stock reserved.

Why it is safe to leave open to the internet:

- the address carries a long random code that identifies the business, and nothing else;
- every call is signed by WooCommerce with the shop's secret (HMAC-SHA256); a call whose signature does not match
  is refused, so only the owner's own shop can create orders;
- an order is recorded once, whatever number of times WooCommerce repeats it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cstr, flt, now_datetime

from murzak_pos.api import ecommerce_api, online_orders

# An order in one of these states is one the shop will fulfil
RECORD_STATUSES = ("processing", "on-hold", "completed")
# An order that reaches one of these should no longer be fulfilled
CANCEL_STATUSES = ("cancelled", "refunded", "failed")


def signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
	"""Did WooCommerce sign this exact body with our secret?"""
	if not secret or not signature:
		return False
	digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
	return hmac.compare_digest(digest, signature.strip())


def _clean_phone(value) -> str:
	return "".join(ch for ch in cstr(value) if ch.isdigit() or ch == "+")[:20]


def order_arguments(payload: dict) -> dict:
	"""Turn a WooCommerce order into the arguments of place_order."""
	billing = payload.get("billing") or {}
	shipping = payload.get("shipping") or {}

	name = " ".join(p for p in (billing.get("first_name"), billing.get("last_name")) if p).strip()
	name = name or cstr(billing.get("company")).strip()

	address_source = shipping if any(shipping.get(k) for k in ("address_1", "city")) else billing
	address = ", ".join(
		cstr(address_source.get(k)).strip()
		for k in ("address_1", "address_2", "city", "state")
		if cstr(address_source.get(k)).strip()
	)

	methods = [cstr(s.get("method_id")) for s in payload.get("shipping_lines") or [] if isinstance(s, dict)]
	delivery = "Pickup" if methods and all(m.startswith("local_pickup") for m in methods) else "Delivery"

	lines = []
	for li in payload.get("line_items") or []:
		if not isinstance(li, dict):
			continue
		qty = flt(li.get("quantity"))
		sku = cstr(li.get("sku")).strip()
		if not sku:
			frappe.throw(
				_(
					"Product '{0}' has no SKU on the website, so it cannot be matched to a POS product."
				).format(cstr(li.get("name"))[:60]),
				frappe.ValidationError,
			)
		# What the customer really paid per unit, after any discount
		rate = flt(li.get("total")) / qty if qty else 0
		lines.append({"item_code": sku, "qty": qty, "rate": rate})

	note_parts = []
	if payload.get("customer_note"):
		note_parts.append(cstr(payload["customer_note"]))
	note_parts.append(
		_("Website total {0} {1}, of which shipping {2}.").format(
			cstr(payload.get("currency")),
			cstr(payload.get("total")),
			cstr(payload.get("shipping_total") or "0"),
		)
	)

	return {
		"channel": "WooCommerce",
		"lines": lines,
		"customer_name": name,
		"phone": _clean_phone(billing.get("phone")),
		"email": cstr(billing.get("email")).strip()[:140] or None,
		"delivery_method": delivery,
		"address": address,
		"payment": cstr(payload.get("payment_method_title")).strip(),
		"paid": bool(payload.get("date_paid")) or payload.get("status") == "completed",
		"notes": " ".join(note_parts),
		"external_ref": cstr(payload.get("id")),
		"trust_prices": True,
	}


def _remember(company: str, stored: dict, message: str) -> None:
	stored["last_order_at"] = str(now_datetime())
	stored["last_order_message"] = message[:300]
	ecommerce_api.write_settings(company, stored)
	frappe.db.commit()


def handle_order(company: str, stored: dict, payload: dict) -> dict:
	"""Record, or cancel, the order a WooCommerce webhook describes."""
	status = cstr(payload.get("status"))
	reference = cstr(payload.get("id"))

	if status in CANCEL_STATUSES:
		existing = frappe.db.get_value(
			"Sales Order",
			{
				"company": company,
				"custom_online_channel": "WooCommerce",
				"custom_online_ref": reference,
				"docstatus": 1,
			},
			["name", "custom_online_fulfilment"],
			as_dict=True,
		)
		if existing and existing.custom_online_fulfilment not in ("Delivered", "Cancelled"):
			online_orders.cancel_order_document(existing.name)
			frappe.db.set_value("Sales Order", existing.name, "custom_online_fulfilment", "Cancelled")
			_remember(
				company, stored, _("Order {0} was cancelled on the website and here.").format(reference)
			)
			return {"recorded": False, "cancelled": existing.name}
		return {"recorded": False, "ignored": status}

	if status not in RECORD_STATUSES:
		# Waiting for payment, drafts and the like: recorded once the shop moves them on
		return {"recorded": False, "ignored": status}

	try:
		result = online_orders.place_order(company, **order_arguments(payload))
	except frappe.ValidationError as e:
		frappe.db.rollback()
		message = _("Website order {0} could not be recorded: {1}").format(reference, cstr(e))
		frappe.log_error("WooCommerce order not recorded", message + "\n" + json.dumps(payload)[:4000])
		_remember(company, stored, message)
		return {"recorded": False, "error": message}

	if result["created"]:
		_remember(company, stored, _("Website order {0} recorded as {1}.").format(reference, result["order"]))
	return {"recorded": result["created"], "order": result["order"]}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=300, seconds=60)
def woocommerce_order(t: str | None = None, **kwargs):
	"""The address WooCommerce calls for every order created or changed."""
	company = ecommerce_api.company_for_webhook_token(t)
	body = frappe.request.get_data() or b""
	stored = ecommerce_api.read_settings(company) if company else {}
	secret = stored.get("webhook_secret")

	# One answer for an unknown address and a bad signature, so neither tells a stranger anything
	if not (
		company and secret and signature_ok(secret, body, frappe.get_request_header("X-WC-Webhook-Signature"))
	):
		frappe.throw(_("Not allowed."), frappe.AuthenticationError)

	if not (stored.get("enabled") and stored.get("sync_orders", True)):
		return {"ignored": "order sync is off"}

	try:
		payload = json.loads(body)
	except ValueError:
		payload = None
	if not isinstance(payload, dict) or "line_items" not in payload:
		# WooCommerce's first test message is not an order
		return {"ignored": "not an order"}

	return handle_order(company, stored, payload)
