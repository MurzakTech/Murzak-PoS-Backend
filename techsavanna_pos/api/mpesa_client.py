"""
MPESA (Daraja) API Client
Handles all interactions with Safaricom Daraja API for one company's MPESA Settings.

Credentials are read with get_password(): Frappe only returns asterisks for Password fields
when they are read as plain attributes, which made every token request fail.
"""

import base64
import json
import math
from datetime import datetime, timedelta, timezone

import frappe
import requests
from frappe import _
from frappe.utils import now

from techsavanna_pos.api.mpesa_constants import (
	B2B_COMMAND_BUSINESS_PAY_BILL,
	B2B_ENDPOINT,
	B2C_COMMAND_BUSINESS_PAYMENT,
	B2C_ENDPOINT,
	C2B_REGISTER_URL_ENDPOINT,
	MPESA_STILL_PROCESSING_CODES,
	MPESA_SUCCESS,
	MPESA_USER_CANCELLED,
	OAUTH_TOKEN_ENDPOINT,
	STATUS_CANCELLED,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SUCCESS,
	STK_PUSH_ENDPOINT,
	STK_PUSH_QUERY_ENDPOINT,
	TRANSACTION_TYPE_BUYGOODS,
	TRANSACTION_TYPE_PAYBILL,
	get_base_url,
	get_result_message,
)

# Daraja timestamps and passwords are expected in Kenyan time (EAT, UTC+3).
EAT = timezone(timedelta(hours=3))
REQUEST_TIMEOUT = 30


class MpesaClientError(Exception):
	"""Base exception for MPESA client errors"""


class MpesaAuthenticationError(MpesaClientError):
	"""Authentication error with Daraja API"""


class MpesaAPIError(MpesaClientError):
	"""Error from Daraja API"""


def get_settings(company: str):
	"""Active MPESA Settings document for the company."""
	settings_name = frappe.db.get_value("MPESA Settings", {"company": company, "is_active": 1}, "name")
	if not settings_name:
		raise MpesaClientError(
			_("M-Pesa is not set up for {0}. Add it under Settings > Payment Gateways.").format(company)
		)
	return frappe.get_doc("MPESA Settings", settings_name)


def get_secret(settings, fieldname: str) -> str:
	"""Decrypted value of a Password field, or an empty string."""
	try:
		return settings.get_password(fieldname, raise_exception=False) or ""
	except Exception:
		return ""


def get_stk_callback_url(settings) -> str:
	"""Callback URL for STK results: the manual override if set, otherwise one built from the site URL."""
	if settings.stk_callback_url:
		return settings.stk_callback_url
	from techsavanna_pos.api.payment_gateway_common import build_callback_url

	return build_callback_url("daraja_stk_result", settings.callback_token)


def _token_cache_key(settings) -> str:
	return f"techsavanna_pos:daraja_token:{settings.name}:{settings.environment}"


def get_access_token(company: str, settings=None, force_refresh: bool = False) -> str:
	"""
	Get an OAuth token for a company, cached until shortly before it expires.

	Raises:
		MpesaAuthenticationError: If credentials are invalid
		MpesaClientError: For other errors
	"""
	settings = settings or get_settings(company)
	cache_key = _token_cache_key(settings)

	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached

	consumer_key = settings.consumer_key or ""
	consumer_secret = get_secret(settings, "consumer_secret")
	if not consumer_key or not consumer_secret:
		raise MpesaAuthenticationError(
			_("Enter the Consumer Key and Consumer Secret from the Daraja portal.")
		)

	url = f"{get_base_url(settings.environment)}{OAUTH_TOKEN_ENDPOINT}"
	encoded = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()

	try:
		response = requests.get(url, headers={"Authorization": f"Basic {encoded}"}, timeout=REQUEST_TIMEOUT)
	except requests.exceptions.RequestException as e:
		raise MpesaClientError(_("Could not reach Safaricom: {0}").format(str(e)))

	if response.status_code in (400, 401, 403):
		raise MpesaAuthenticationError(
			_(
				"Safaricom refused the Consumer Key or Consumer Secret. "
				"Check them and the Sandbox/Production choice."
			)
		)
	if not response.ok:
		raise MpesaAPIError(
			_("Safaricom returned an error ({0}) when signing in.").format(response.status_code)
		)

	try:
		data = response.json()
	except ValueError:
		raise MpesaAPIError(_("Safaricom sent an unreadable reply when signing in."))

	access_token = data.get("access_token")
	if not access_token:
		raise MpesaAuthenticationError(_("Safaricom did not return an access token."))

	expires_in = int(data.get("expires_in") or 3599)
	# Refresh a minute early so a token never expires mid-request.
	frappe.cache().set_value(cache_key, access_token, expires_in_sec=max(expires_in - 60, 60))
	return access_token


def generate_stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
	"""Daraja formula: Base64(shortcode + passkey + timestamp)"""
	return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()


def generate_timestamp() -> str:
	"""Timestamp in Daraja format YYYYMMDDHHmmss, Kenyan time."""
	return datetime.now(EAT).strftime("%Y%m%d%H%M%S")


def stk_amount(amount: float) -> int:
	"""M-Pesa only takes whole shillings; round up so the business is never short."""
	return max(1, math.ceil(round(float(amount), 2)))


def _to_int(value, default=None):
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _post(settings, endpoint: str, payload: dict, access_token: str) -> requests.Response:
	url = f"{get_base_url(settings.environment)}{endpoint}"
	return requests.post(
		url,
		json=payload,
		headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
		timeout=REQUEST_TIMEOUT,
	)


def _error_from_response(response: requests.Response) -> str:
	try:
		data = response.json()
	except ValueError:
		return _("Safaricom returned an error ({0}).").format(response.status_code)
	return data.get("errorMessage") or data.get("ResponseDescription") or data.get("ResultDesc") or str(data)


def _fail_log(log, message: str) -> None:
	log.status = STATUS_FAILED
	log.error_message = message
	log.result_description = log.result_description or message
	log.completed_at = now()
	log.save(ignore_permissions=True)
	frappe.db.commit()


def status_for_result_code(result_code: int) -> str:
	if result_code == MPESA_SUCCESS:
		return STATUS_SUCCESS
	if result_code == MPESA_USER_CANCELLED:
		return STATUS_CANCELLED
	return STATUS_FAILED


def initiate_stk_push(
	company: str, phone_number: str, amount: float, reference: str, description: str | None = None
) -> dict:
	"""
	Send an STK Push (payment prompt) to the customer's phone.

	Returns:
		{"merchant_request_id", "checkout_request_id", "response_code", "response_description",
		 "customer_message", "transaction_id", "amount"}
	"""
	settings = get_settings(company)

	if not phone_number.startswith("254") or len(phone_number) != 12:
		raise MpesaClientError(_("Invalid phone number format. Use MSISDN format: 254712345678"))

	passkey = get_secret(settings, "passkey")
	if not passkey:
		raise MpesaClientError(_("Enter the Lipa na M-Pesa Online passkey in the M-Pesa settings."))

	charge = stk_amount(amount)
	access_token = get_access_token(company, settings)
	timestamp = generate_timestamp()

	if settings.shortcode_type == "Paybill":
		transaction_type = TRANSACTION_TYPE_PAYBILL
		party_b = settings.shortcode
		account_reference = f"{settings.account_reference_prefix or ''}{reference}"[:12]
	else:
		# Buy Goods: the shortcode is the store (head office) number and money goes to the till.
		transaction_type = TRANSACTION_TYPE_BUYGOODS
		party_b = settings.till_number or settings.shortcode
		account_reference = (reference or "POS")[:12]

	request_payload = {
		"BusinessShortCode": settings.shortcode,
		"Password": generate_stk_password(settings.shortcode, passkey, timestamp),
		"Timestamp": timestamp,
		"TransactionType": transaction_type,
		"Amount": charge,
		"PartyA": phone_number,
		"PartyB": party_b,
		"PhoneNumber": phone_number,
		"CallBackURL": get_stk_callback_url(settings),
		"AccountReference": account_reference or "POS",
		"TransactionDesc": (description or settings.transaction_description or "Payment")[:13],
	}

	# Log first, so even a request that never reaches Safaricom leaves a trace.
	log = frappe.new_doc("MPESA Transaction Log")
	log.company = company
	log.transaction_type = "STK Push"
	log.phone_number = phone_number
	log.amount = charge
	log.reference_number = reference
	log.status = STATUS_PENDING
	log.request_payload = json.dumps(dict(request_payload, Password="***"))
	log.created_at = now()
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		response = _post(settings, STK_PUSH_ENDPOINT, request_payload, access_token)
	except requests.exceptions.RequestException as e:
		_fail_log(log, _("Could not reach Safaricom: {0}").format(str(e)))
		raise MpesaClientError(log.error_message)

	if response.status_code == 401:
		# Token was revoked early; clear it so the next attempt signs in again.
		frappe.cache().delete_value(_token_cache_key(settings))

	try:
		response_data = response.json()
	except ValueError:
		response_data = {}
	log.response_payload = json.dumps(response_data)

	if not response.ok or str(response_data.get("ResponseCode", "")) != "0":
		message = _error_from_response(response)
		log.result_code = _to_int(response_data.get("ResponseCode"))
		_fail_log(log, message)
		raise MpesaAPIError(_("Safaricom did not send the prompt: {0}").format(message))

	log.merchant_request_id = response_data.get("MerchantRequestID")
	log.checkout_request_id = response_data.get("CheckoutRequestID")
	log.result_description = response_data.get("ResponseDescription", "")
	log.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"merchant_request_id": log.merchant_request_id,
		"checkout_request_id": log.checkout_request_id,
		"response_code": 0,
		"response_description": log.result_description,
		"customer_message": response_data.get("CustomerMessage", ""),
		"transaction_id": log.name,
		"amount": charge,
	}


def query_stk_status(company: str, checkout_request_id: str) -> dict:
	"""
	Ask Safaricom for the result of an STK Push when its callback has not arrived yet.

	Leaves the log Pending while the customer is still deciding.
	"""
	settings = get_settings(company)
	passkey = get_secret(settings, "passkey")
	access_token = get_access_token(company, settings)
	timestamp = generate_timestamp()

	request_payload = {
		"BusinessShortCode": settings.shortcode,
		"Password": generate_stk_password(settings.shortcode, passkey, timestamp),
		"Timestamp": timestamp,
		"CheckoutRequestID": checkout_request_id,
	}

	log_name = frappe.db.get_value(
		"MPESA Transaction Log", {"checkout_request_id": checkout_request_id}, "name"
	)
	if not log_name:
		raise MpesaClientError(
			_("Transaction not found for CheckoutRequestID: {0}").format(checkout_request_id)
		)
	log = frappe.get_doc("MPESA Transaction Log", log_name)

	try:
		response = _post(settings, STK_PUSH_QUERY_ENDPOINT, request_payload, access_token)
	except requests.exceptions.RequestException as e:
		raise MpesaClientError(_("Could not reach Safaricom: {0}").format(str(e)))
	try:
		response_data = response.json()
	except ValueError:
		response_data = {}

	error_code = str(response_data.get("errorCode", ""))
	has_result = "ResultCode" in response_data
	if error_code in MPESA_STILL_PROCESSING_CODES or not has_result:
		# The customer has not answered the prompt yet (or Safaricom is busy): keep waiting.
		return {"status": log.status, "still_processing": True, "transaction_id": log.name}

	result_code = _to_int(response_data.get("ResultCode"), -1)
	result_desc = response_data.get("ResultDesc", "") or get_result_message(result_code)

	# The callback may have landed while we were asking; never overwrite a final result.
	log.reload()
	if log.status == STATUS_PENDING:
		log.result_code = result_code
		log.result_description = result_desc
		log.status = status_for_result_code(result_code)
		log.completed_at = now()
		log.save(ignore_permissions=True)
		frappe.db.commit()

	return {
		"status": log.status,
		"still_processing": False,
		"result_code": log.result_code,
		"result_description": log.result_description,
		"merchant_request_id": response_data.get("MerchantRequestID", ""),
		"checkout_request_id": checkout_request_id,
		"result_desc": log.result_description,
		"mpesa_receipt_number": log.mpesa_receipt_number or "",
		"transaction_id": log.name,
	}


def test_connection(settings) -> dict:
	"""Sign in to Daraja with the saved keys, without moving money."""
	get_access_token(settings.company, settings, force_refresh=True)
	return {"ok": True, "message": _("Connected to Safaricom ({0}).").format(settings.environment)}


def c2b_shortcode(settings) -> str:
	"""Number customers pay into directly: the till for Buy Goods, else the paybill."""
	if settings.shortcode_type == "BuyGoods" and settings.till_number:
		return settings.till_number
	return settings.shortcode


def register_c2b_urls(settings) -> dict:
	"""
	Ask Safaricom to tell us about payments customers make straight to the till or paybill
	(Lipa na M-Pesa without a prompt), so cashiers can confirm them by code.
	"""
	from techsavanna_pos.api.payment_gateway_common import build_callback_url

	access_token = get_access_token(settings.company, settings)
	payload = {
		"ShortCode": c2b_shortcode(settings),
		"ResponseType": "Completed",
		"ConfirmationURL": build_callback_url("daraja_c2b_confirmation", settings.callback_token),
		"ValidationURL": build_callback_url("daraja_c2b_validation", settings.callback_token),
	}
	try:
		response = _post(settings, C2B_REGISTER_URL_ENDPOINT, payload, access_token)
	except requests.exceptions.RequestException as e:
		raise MpesaClientError(_("Could not reach Safaricom: {0}").format(str(e)))
	if not response.ok:
		raise MpesaAPIError(
			_("Safaricom did not register the till for direct payments: {0}").format(
				_error_from_response(response)
			)
		)
	return {"ok": True, "message": _("Direct till payments will now appear in the POS.")}


def _security_credential(settings) -> str:
	"""
	B2C and B2B need the initiator password encrypted with Safaricom's certificate.
	The Daraja portal generates it; it is stored in the Security Credential field.
	"""
	credential = get_secret(settings, "security_credential")
	if not credential:
		raise MpesaClientError(
			_("Generate the Security Credential on the Daraja portal and save it in the M-Pesa settings.")
		)
	return credential


def _send_business_payment(
	settings, transaction_type: str, endpoint: str, request_payload: dict, phone_number, amount, reference
) -> dict:
	access_token = get_access_token(settings.company, settings)

	log = frappe.new_doc("MPESA Transaction Log")
	log.company = settings.company
	log.transaction_type = transaction_type
	log.phone_number = phone_number
	log.amount = amount
	log.reference_number = reference
	log.status = STATUS_PENDING
	log.request_payload = json.dumps(dict(request_payload, SecurityCredential="***"))
	log.created_at = now()
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		response = _post(settings, endpoint, request_payload, access_token)
	except requests.exceptions.RequestException as e:
		_fail_log(log, _("Could not reach Safaricom: {0}").format(str(e)))
		raise MpesaClientError(log.error_message)

	try:
		response_data = response.json()
	except ValueError:
		response_data = {}
	log.response_payload = json.dumps(response_data)

	if not response.ok or str(response_data.get("ResponseCode", "")) != "0":
		_fail_log(log, _error_from_response(response))
		raise MpesaAPIError(_("Daraja API error: {0}").format(log.error_message))

	log.conversation_id = response_data.get("ConversationID")
	log.result_description = response_data.get("ResponseDescription", "")
	log.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"conversation_id": response_data.get("ConversationID"),
		"originator_conversation_id": response_data.get("OriginatorConversationID"),
		"response_code": 0,
		"response_description": log.result_description,
		"transaction_id": log.name,
	}


def process_b2c_payment(
	company: str, phone_number: str, amount: float, reference: str, remarks: str | None = None
) -> dict:
	"""Business to Customer payout (refunds, cash-back)."""
	from techsavanna_pos.api.payment_gateway_common import build_callback_url

	settings = get_settings(company)
	if not settings.initiator_name:
		raise MpesaClientError(_("Initiator name is required for B2C payments"))
	if not phone_number.startswith("254") or len(phone_number) != 12:
		raise MpesaClientError(_("Invalid phone number format. Use MSISDN format: 254712345678"))

	request_payload = {
		"InitiatorName": settings.initiator_name,
		"SecurityCredential": _security_credential(settings),
		"CommandID": B2C_COMMAND_BUSINESS_PAYMENT,
		"Amount": stk_amount(amount),
		"PartyA": settings.shortcode,
		"PartyB": phone_number,
		"Remarks": (remarks or f"Payment for {reference}")[:100],
		"QueueTimeOutURL": settings.b2c_timeout_url
		or build_callback_url("daraja_business_timeout", settings.callback_token),
		"ResultURL": settings.b2c_result_url
		or build_callback_url("daraja_business_result", settings.callback_token),
		"Occasion": (reference or "")[:100],
	}
	return _send_business_payment(
		settings, "B2C", B2C_ENDPOINT, request_payload, phone_number, amount, reference
	)


def process_b2b_payment(
	company: str, receiver_shortcode: str, amount: float, reference: str, remarks: str | None = None
) -> dict:
	"""Business to Business transfer (paying a supplier's paybill)."""
	from techsavanna_pos.api.payment_gateway_common import build_callback_url

	settings = get_settings(company)
	if not settings.initiator_name:
		raise MpesaClientError(_("Initiator name is required for B2B payments"))

	request_payload = {
		"Initiator": settings.initiator_name,
		"SecurityCredential": _security_credential(settings),
		"CommandID": B2B_COMMAND_BUSINESS_PAY_BILL,
		"SenderIdentifierType": "4",
		"RecieverIdentifierType": "4",
		"Amount": stk_amount(amount),
		"PartyA": settings.shortcode,
		"PartyB": receiver_shortcode,
		"AccountReference": reference,
		"Remarks": (remarks or f"Payment for {reference}")[:100],
		"QueueTimeOutURL": settings.b2b_timeout_url
		or build_callback_url("daraja_business_timeout", settings.callback_token),
		"ResultURL": settings.b2b_result_url
		or build_callback_url("daraja_business_result", settings.callback_token),
	}
	return _send_business_payment(settings, "B2B", B2B_ENDPOINT, request_payload, None, amount, reference)
