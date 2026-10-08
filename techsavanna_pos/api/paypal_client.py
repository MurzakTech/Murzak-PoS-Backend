"""
PayPal Orders v2 client.

The customer approves the order on PayPal's page (opened from a QR code at the till), then
the payment is captured. Capture happens either when PayPal sends the customer back to us or
when the till next checks the order, whichever comes first.
"""

import frappe
import requests
from frappe import _
from frappe.utils import flt

from techsavanna_pos.api.pesapal_client import GatewayError

BASE_URLS = {
	"Sandbox": "https://api-m.sandbox.paypal.com",
	"Production": "https://api-m.paypal.com",
}
REQUEST_TIMEOUT = 30

# Currencies PayPal can charge in. KES is not one of them.
PAYPAL_CURRENCIES = {
	"AUD", "BRL", "CAD", "CNY", "CZK", "DKK", "EUR", "HKD", "HUF", "ILS", "JPY", "MYR", "MXN",
	"TWD", "NZD", "NOK", "PHP", "PLN", "GBP", "SGD", "SEK", "CHF", "THB", "USD",
}  # fmt: skip
ZERO_DECIMAL_CURRENCIES = {"HUF", "JPY", "TWD"}


def _base(settings) -> str:
	return BASE_URLS.get(settings.environment, BASE_URLS["Sandbox"])


def _error_text(response: requests.Response) -> str:
	try:
		data = response.json()
	except ValueError:
		return str(response.status_code)
	details = data.get("details") or []
	if details and isinstance(details[0], dict):
		return details[0].get("description") or details[0].get("issue") or data.get("message", "")
	return (
		data.get("message") or data.get("error_description") or data.get("name") or str(response.status_code)
	)


def get_token(settings, force_refresh: bool = False) -> str:
	cache_key = f"techsavanna_pos:gateway_token:{settings.name}"
	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached

	secret = settings.get_password("client_secret", raise_exception=False) or ""
	if not settings.client_id or not secret:
		raise GatewayError(_("Enter the PayPal Client ID and Secret."))

	try:
		response = requests.post(
			f"{_base(settings)}/v1/oauth2/token",
			auth=(settings.client_id, secret),
			data={"grant_type": "client_credentials"},
			headers={"Accept": "application/json"},
			timeout=REQUEST_TIMEOUT,
		)
	except requests.exceptions.RequestException as e:
		raise GatewayError(_("Could not reach PayPal: {0}").format(str(e)))

	if response.status_code in (400, 401, 403):
		raise GatewayError(
			_("PayPal refused the Client ID or Secret. Check them and the Sandbox/Production choice.")
		)
	if not response.ok:
		raise GatewayError(_("PayPal: {0}").format(_error_text(response)))

	data = response.json()
	token = data.get("access_token")
	if not token:
		raise GatewayError(_("PayPal did not return an access token."))
	frappe.cache().set_value(
		cache_key, token, expires_in_sec=max(int(data.get("expires_in") or 3600) - 120, 60)
	)
	return token


def _headers(settings, request_id: str | None = None) -> dict:
	headers = {"Authorization": f"Bearer {get_token(settings)}", "Content-Type": "application/json"}
	if request_id:
		# Makes retries safe: PayPal returns the first result instead of creating a second order.
		headers["PayPal-Request-Id"] = request_id
	return headers


def format_amount(amount: float, currency: str) -> str:
	if currency in ZERO_DECIMAL_CURRENCIES:
		return str(round(flt(amount)))
	return f"{flt(amount, 2):.2f}"


def test_connection(settings) -> dict:
	get_token(settings, force_refresh=True)
	if (settings.currency or "USD").upper() not in PAYPAL_CURRENCIES:
		raise GatewayError(
			_("PayPal cannot charge in {0}. Choose USD or another PayPal currency.").format(settings.currency)
		)
	return {"ok": True, "message": _("Connected to PayPal ({0}).").format(settings.environment)}


def create_checkout(settings, txn, return_url: str, cancel_url: str, description: str) -> dict:
	currency = txn.charged_currency.upper()
	if currency not in PAYPAL_CURRENCIES:
		raise GatewayError(_("PayPal cannot charge in {0}.").format(currency))

	payload = {
		"intent": "CAPTURE",
		"purchase_units": [
			{
				"reference_id": txn.merchant_reference[:256],
				"custom_id": txn.merchant_reference[:127],
				"invoice_id": txn.merchant_reference[:127],
				"description": (description or "Payment")[:127],
				"amount": {"currency_code": currency, "value": format_amount(txn.charged_amount, currency)},
			}
		],
		"payment_source": {
			"paypal": {
				"experience_context": {
					"brand_name": (settings.display_name or settings.company)[:127],
					"shipping_preference": "NO_SHIPPING",
					"user_action": "PAY_NOW",
					"return_url": return_url,
					"cancel_url": cancel_url,
				}
			}
		},
	}
	try:
		response = requests.post(
			f"{_base(settings)}/v2/checkout/orders",
			json=payload,
			headers=_headers(settings, txn.merchant_reference),
			timeout=REQUEST_TIMEOUT,
		)
	except requests.exceptions.RequestException as e:
		raise GatewayError(_("Could not reach PayPal: {0}").format(str(e)))
	if not response.ok:
		raise GatewayError(_("PayPal: {0}").format(_error_text(response)))

	data = response.json()
	link = next(
		(lnk["href"] for lnk in data.get("links", []) if lnk.get("rel") in ("payer-action", "approve")), None
	)
	if not data.get("id") or not link:
		raise GatewayError(_("PayPal did not create the order."))
	return {"gateway_order_id": data["id"], "checkout_url": link, "request": payload, "response": data}


def _capture_result(order: dict) -> dict:
	captures = ((order.get("purchase_units") or [{}])[0].get("payments") or {}).get("captures") or []
	capture = captures[0] if captures else {}
	payer = order.get("payer") or {}
	name = payer.get("name") or {}
	return {
		"capture": capture,
		"payer_email": payer.get("email_address"),
		"payer_name": " ".join(p for p in (name.get("given_name"), name.get("surname")) if p) or None,
	}


def fetch_status(settings, txn) -> dict:
	"""
	Check the order, capturing it if the customer has approved.
	Returns {"status", "confirmation_code", "payment_method", "description", "raw", "payer_*"}.
	"""
	base = _base(settings)
	try:
		response = requests.get(
			f"{base}/v2/checkout/orders/{txn.gateway_order_id}",
			headers=_headers(settings),
			timeout=REQUEST_TIMEOUT,
		)
	except requests.exceptions.RequestException as e:
		raise GatewayError(_("Could not reach PayPal: {0}").format(str(e)))
	if not response.ok:
		raise GatewayError(_("PayPal: {0}").format(_error_text(response)))
	order = response.json()

	if order.get("status") == "APPROVED":
		try:
			capture_response = requests.post(
				f"{base}/v2/checkout/orders/{txn.gateway_order_id}/capture",
				json={},
				headers=_headers(settings, f"{txn.merchant_reference}-capture"),
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			raise GatewayError(_("Could not reach PayPal: {0}").format(str(e)))
		if capture_response.ok:
			order = capture_response.json()
		elif "ORDER_ALREADY_CAPTURED" not in capture_response.text:
			return {
				"status": "Failed",
				"description": _("PayPal declined the payment: {0}").format(_error_text(capture_response)),
				"raw": capture_response.text[:5000],
			}

	extra = _capture_result(order)
	capture = extra["capture"]
	result = {
		"status": "Pending",
		"confirmation_code": capture.get("id"),
		"payment_method": "PayPal",
		"description": order.get("status"),
		"raw": order,
		"payer_email": extra["payer_email"],
		"payer_name": extra["payer_name"],
	}

	order_status = order.get("status")
	capture_status = capture.get("status")
	if order_status == "COMPLETED" and capture_status == "COMPLETED":
		amount = capture.get("amount") or {}
		if (
			flt(amount.get("value")) + 0.009 < flt(txn.charged_amount, 2)
			or (amount.get("currency_code") or "").upper() != txn.charged_currency.upper()
		):
			result["status"] = "Failed"
			result["description"] = _("PayPal captured an amount that does not match this sale.")
		else:
			result["status"] = "Success"
			result["description"] = _("Paid")
	elif capture_status in ("DECLINED", "FAILED"):
		result["status"] = "Failed"
		result["description"] = _("PayPal declined the payment.")
	elif order_status == "VOIDED":
		result["status"] = "Cancelled"
		result["description"] = _("The PayPal order was cancelled.")
	return result
