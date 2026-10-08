"""
Pesapal API 3.0 client.

Pesapal hosts a payment page (card, M-Pesa, Airtel Money, bank) for an order. At the till the
customer opens that page from a QR code on their phone; Pesapal then tells us the result
through the IPN (instant payment notification) and we confirm it with GetTransactionStatus.
"""

import frappe
import requests
from frappe import _
from frappe.utils import flt

BASE_URLS = {
	"Sandbox": "https://cybqa.pesapal.com/pesapalv3",
	"Production": "https://pay.pesapal.com/v3",
}
REQUEST_TIMEOUT = 30
JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

# GetTransactionStatus status_code values
PESAPAL_INVALID = 0
PESAPAL_COMPLETED = 1
PESAPAL_FAILED = 2
PESAPAL_REVERSED = 3


class GatewayError(Exception):
	"""A gateway refused a request or could not be reached"""


def _base(settings) -> str:
	return BASE_URLS.get(settings.environment, BASE_URLS["Sandbox"])


def _secret(settings) -> str:
	return settings.get_password("client_secret", raise_exception=False) or ""


def _error_text(data: dict) -> str | None:
	error = data.get("error") if isinstance(data, dict) else None
	if error and isinstance(error, dict) and (error.get("message") or error.get("code")):
		return error.get("message") or error.get("code")
	return None


def _request(method: str, url: str, **kwargs) -> dict:
	try:
		response = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
	except requests.exceptions.RequestException as e:
		raise GatewayError(_("Could not reach Pesapal: {0}").format(str(e)))
	try:
		data = response.json()
	except ValueError:
		raise GatewayError(_("Pesapal sent an unreadable reply ({0}).").format(response.status_code))
	message = _error_text(data)
	if message or not response.ok:
		raise GatewayError(_("Pesapal: {0}").format(message or response.status_code))
	return data


def get_token(settings, force_refresh: bool = False) -> str:
	"""Pesapal tokens last five minutes; keep one for four."""
	cache_key = f"techsavanna_pos:gateway_token:{settings.name}"
	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached

	if not settings.client_id or not _secret(settings):
		raise GatewayError(_("Enter the Pesapal Consumer Key and Consumer Secret."))

	data = _request(
		"POST",
		f"{_base(settings)}/api/Auth/RequestToken",
		json={"consumer_key": settings.client_id, "consumer_secret": _secret(settings)},
		headers=JSON_HEADERS,
	)
	token = data.get("token")
	if not token:
		raise GatewayError(_("Pesapal refused the Consumer Key or Consumer Secret."))
	frappe.cache().set_value(cache_key, token, expires_in_sec=240)
	return token


def _auth_headers(settings) -> dict:
	return {**JSON_HEADERS, "Authorization": f"Bearer {get_token(settings)}"}


def ensure_ipn(settings, ipn_url: str) -> str:
	"""Register this company's IPN address with Pesapal once, and again if the address changes."""
	if settings.ipn_id and settings.ipn_url == ipn_url:
		return settings.ipn_id

	data = _request(
		"POST",
		f"{_base(settings)}/api/URLSetup/RegisterIPN",
		json={"url": ipn_url, "ipn_notification_type": "GET"},
		headers=_auth_headers(settings),
	)
	ipn_id = data.get("ipn_id")
	if not ipn_id:
		raise GatewayError(_("Pesapal did not register the notification address."))
	frappe.db.set_value("POS Gateway Settings", settings.name, {"ipn_id": ipn_id, "ipn_url": ipn_url})
	settings.ipn_id, settings.ipn_url = ipn_id, ipn_url
	return ipn_id


def test_connection(settings, ipn_url: str) -> dict:
	get_token(settings, force_refresh=True)
	ensure_ipn(settings, ipn_url)
	return {"ok": True, "message": _("Connected to Pesapal ({0}).").format(settings.environment)}


def create_checkout(settings, txn, ipn_url: str, return_url: str, payer: dict, description: str) -> dict:
	"""Create a Pesapal order and return its payment page link."""
	notification_id = ensure_ipn(settings, ipn_url)

	billing = {"country_code": "KE"}
	if payer.get("phone"):
		billing["phone_number"] = payer["phone"]
	if payer.get("email"):
		billing["email_address"] = payer["email"]
	if payer.get("name"):
		billing["first_name"] = payer["name"][:50]
	if not billing.get("phone_number") and not billing.get("email_address"):
		raise GatewayError(_("Pesapal needs the customer's phone number or email."))

	payload = {
		"id": txn.merchant_reference,
		"currency": txn.charged_currency,
		"amount": flt(txn.charged_amount, 2),
		"description": (description or f"Payment {txn.merchant_reference}")[:100],
		"callback_url": return_url,
		"notification_id": notification_id,
		"billing_address": billing,
	}
	data = _request(
		"POST",
		f"{_base(settings)}/api/Transactions/SubmitOrderRequest",
		json=payload,
		headers=_auth_headers(settings),
	)
	if not data.get("order_tracking_id") or not data.get("redirect_url"):
		raise GatewayError(_("Pesapal did not create the order."))
	return {
		"gateway_order_id": data["order_tracking_id"],
		"checkout_url": data["redirect_url"],
		"request": payload,
		"response": data,
	}


def fetch_status(settings, txn) -> dict:
	"""
	Ask Pesapal how an order stands. Returns {"status", "confirmation_code", "payment_method",
	"description", "raw"} where status is Pending, Success or Failed.
	"""
	try:
		response = requests.get(
			f"{_base(settings)}/api/Transactions/GetTransactionStatus",
			params={"orderTrackingId": txn.gateway_order_id},
			headers=_auth_headers(settings),
			timeout=REQUEST_TIMEOUT,
		)
		data = response.json()
	except requests.exceptions.RequestException as e:
		raise GatewayError(_("Could not reach Pesapal: {0}").format(str(e)))
	except ValueError:
		raise GatewayError(_("Pesapal sent an unreadable reply."))

	code = data.get("status_code")
	description = data.get("payment_status_description") or data.get("description") or ""
	result = {
		"status": "Pending",
		"confirmation_code": data.get("confirmation_code"),
		"payment_method": data.get("payment_method"),
		"description": description,
		"raw": data,
	}

	if code == PESAPAL_COMPLETED:
		paid = flt(data.get("amount"), 2)
		currency = (data.get("currency") or "").upper()
		if paid + 0.009 < flt(txn.charged_amount, 2) or (
			currency and currency != txn.charged_currency.upper()
		):
			result["status"] = "Failed"
			result["description"] = _("Pesapal reports {0} {1}, which does not match this sale.").format(
				currency, paid
			)
		else:
			result["status"] = "Success"
	elif code in (PESAPAL_FAILED, PESAPAL_REVERSED):
		result["status"] = "Failed"
		result["description"] = description or (_("Reversed") if code == PESAPAL_REVERSED else _("Failed"))
	# INVALID (0) or no code yet: the customer has not paid; keep waiting.
	return result
