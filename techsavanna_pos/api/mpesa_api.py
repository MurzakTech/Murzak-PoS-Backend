"""
MPESA API Endpoints
REST API endpoints for MPESA payment operations.

Every endpoint is limited to the signed-in user's own company, so one server can safely
run M-Pesa for many businesses, each with its own shortcode and Daraja keys.
Safaricom's callbacks are in payment_callbacks.py.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now, now_datetime

from techsavanna_pos.api.mpesa_client import (
	MpesaAPIError,
	MpesaAuthenticationError,
	MpesaClientError,
	c2b_shortcode,
	get_stk_callback_url,
	initiate_stk_push,
	process_b2b_payment,
	process_b2c_payment,
	query_stk_status,
	register_c2b_urls,
)
from techsavanna_pos.api.mpesa_client import (
	test_connection as daraja_test_connection,
)
from techsavanna_pos.api.mpesa_constants import (
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_SUCCESS,
	get_result_message,
)
from techsavanna_pos.api.payment_gateway_common import (
	build_callback_url,
	callback_url_problem,
	ensure_mode_of_payment,
	ensure_record_company,
	normalize_kenyan_phone,
	require_gateway_admin,
	resolve_company,
)

DEFAULT_MODE_OF_PAYMENT = "MPESA"
# Wait this long after sending a prompt before asking Safaricom directly; the callback
# normally arrives first, and Daraja limits how often the status query may be called.
QUERY_AFTER_SECONDS = 15
MASK = "***"


def _bool(value) -> int:
	if isinstance(value, str):
		return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
	return 1 if value else 0


def _error_response(e: Exception) -> dict:
	if isinstance(e, MpesaAuthenticationError):
		code, kind = "MPESA_AUTHENTICATION_ERROR", "AuthenticationError"
	elif isinstance(e, MpesaAPIError):
		code, kind = "MPESA_API_ERROR", "APIError"
	elif isinstance(e, MpesaClientError):
		code, kind = "MPESA_CLIENT_ERROR", "ClientError"
	else:
		code, kind = "UNEXPECTED_ERROR", "Exception"
	return {"success": False, "error_code": code, "error_type": kind, "message": str(e)}


def _get_settings_doc(company: str):
	name = frappe.db.get_value("MPESA Settings", {"company": company}, "name")
	return frappe.get_doc("MPESA Settings", name) if name else None


def _transaction_dict(log) -> dict:
	return {
		"transaction_id": log.name,
		"transaction_type": log.transaction_type,
		"checkout_request_id": log.checkout_request_id,
		"status": log.status,
		"result_code": log.result_code,
		"result_description": log.result_description,
		"mpesa_receipt_number": log.mpesa_receipt_number,
		"amount": flt(log.amount),
		"phone_number": log.phone_number,
		"payer_name": log.get("payer_name"),
		"reference": log.reference_number,
		"invoice_type": log.invoice_type,
		"invoice": log.invoice,
		"created_at": str(log.created_at) if log.created_at else None,
		"completed_at": str(log.completed_at) if log.completed_at else None,
	}


# ============================================================================
# Payments at the till
# ============================================================================


@frappe.whitelist()
def initiate_stk_push_payment(
	company: str | None = None,
	phone_number: str | None = None,
	amount: float | None = None,
	reference: str | None = None,
	description: str | None = None,
	invoice_type: str | None = None,
	invoice_name: str | None = None,
	settle_invoice: int = 0,
) -> dict:
	"""
	Send an M-Pesa payment prompt to the customer's phone.

	Example Request:
		POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment
		{"company": "Company Name", "phone_number": "0712345678", "amount": 100, "reference": "SALE-1"}
	"""
	company = resolve_company(company)

	if not phone_number:
		frappe.throw(_("Phone number is required"), frappe.ValidationError)
	if not amount or flt(amount) <= 0:
		frappe.throw(_("Valid amount is required (must be greater than 0)"), frappe.ValidationError)
	if not reference:
		frappe.throw(_("Reference number is required"), frappe.ValidationError)

	phone_number = normalize_kenyan_phone(phone_number)

	if invoice_type and invoice_name:
		if invoice_type not in ("Sales Invoice", "POS Invoice"):
			frappe.throw(_("Invalid invoice type"), frappe.ValidationError)
		invoice_company = frappe.db.get_value(invoice_type, invoice_name, "company")
		if not invoice_company:
			frappe.throw(
				_("Invoice {0} of type {1} does not exist").format(invoice_name, invoice_type),
				frappe.ValidationError,
			)
		if invoice_company != company:
			frappe.throw(_("This invoice belongs to another business."), frappe.PermissionError)

	try:
		result = initiate_stk_push(
			company=company,
			phone_number=phone_number,
			amount=flt(amount),
			reference=reference,
			description=description,
		)
	except Exception as e:
		if not isinstance(e, MpesaClientError):
			frappe.log_error(frappe.get_traceback(), "MPESA STK Push Error")
		return _error_response(e)

	if invoice_type and invoice_name:
		frappe.db.set_value(
			"MPESA Transaction Log",
			result["transaction_id"],
			{
				"invoice_type": invoice_type,
				"invoice": invoice_name,
				"settle_invoice": 1 if _bool(settle_invoice) and invoice_type == "Sales Invoice" else 0,
			},
		)
		frappe.db.commit()

	return {
		"success": True,
		"message": result.get("customer_message") or _("STK push initiated successfully"),
		"transaction": {
			"transaction_id": result.get("transaction_id"),
			"merchant_request_id": result.get("merchant_request_id"),
			"checkout_request_id": result.get("checkout_request_id"),
			"status": STATUS_PENDING,
			"phone_number": phone_number,
			"amount": result.get("amount"),
			"reference": reference,
		},
	}


@frappe.whitelist()
def check_payment_status(transaction_id: str | None = None, checkout_request_id: str | None = None) -> dict:
	"""
	Current state of an STK Push. The till polls this every few seconds.

	The callback from Safaricom normally sets the final state; if it is late, Safaricom is
	asked directly. A prompt nobody answers is closed after the configured timeout.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)

	if transaction_id:
		log_name = frappe.db.exists("MPESA Transaction Log", transaction_id)
	elif checkout_request_id:
		log_name = frappe.db.get_value(
			"MPESA Transaction Log", {"checkout_request_id": checkout_request_id}, "name"
		)
	else:
		frappe.throw(_("Either transaction_id or checkout_request_id is required"), frappe.ValidationError)

	if not log_name:
		frappe.throw(_("Transaction not found"), frappe.ValidationError)

	log = frappe.get_doc("MPESA Transaction Log", log_name)
	ensure_record_company(log.company)

	if log.transaction_type != "STK Push":
		frappe.throw(_("This endpoint is only for STK Push transactions."), frappe.ValidationError)

	if log.status == STATUS_PENDING and log.checkout_request_id:
		age = (now_datetime() - get_datetime(log.created_at or log.creation)).total_seconds()
		if age >= QUERY_AFTER_SECONDS:
			try:
				result = query_stk_status(company=log.company, checkout_request_id=log.checkout_request_id)
			except MpesaClientError as e:
				# A failed status check is not a failed payment; keep waiting for the callback.
				result = {"still_processing": True, "error": str(e)}
			log.reload()

			if log.status == STATUS_SUCCESS:
				from techsavanna_pos.api.payment_callbacks import settle_invoice_if_needed

				settle_invoice_if_needed(log)

			timeout = (
				cint(frappe.db.get_value("MPESA Settings", {"company": log.company}, "payment_timeout"))
				or 120
			)
			if log.status == STATUS_PENDING and result.get("still_processing") and age > timeout + 30:
				log.status = STATUS_FAILED
				log.result_description = _("The customer did not complete the payment in time.")
				log.save(ignore_permissions=True)
				frappe.db.commit()

	transaction = _transaction_dict(log)
	if log.status != STATUS_SUCCESS and log.result_code is not None and log.status != STATUS_PENDING:
		transaction["message"] = get_result_message(log.result_code)
	return {"success": True, "transaction": transaction}


@frappe.whitelist()
def find_c2b_payment(receipt_number: str, company: str | None = None) -> dict:
	"""
	Look up a payment the customer made straight to the till, by the code in their M-Pesa SMS.
	Used when no phone prompt was sent, or the prompt failed.
	"""
	company = resolve_company(company)
	code = (receipt_number or "").strip().upper()
	if not code:
		frappe.throw(_("Enter the M-Pesa code"), frappe.ValidationError)

	log_name = frappe.db.get_value(
		"MPESA Transaction Log", {"company": company, "mpesa_receipt_number": code}, "name"
	)
	if not log_name:
		return {
			"success": False,
			"message": _("No M-Pesa payment with code {0} has reached this business yet.").format(code),
		}
	log = frappe.get_doc("MPESA Transaction Log", log_name)
	if log.status != STATUS_SUCCESS:
		return {"success": False, "message": _("Payment {0} did not go through.").format(code)}
	return {"success": True, "transaction": _transaction_dict(log), "already_used": bool(log.invoice)}


@frappe.whitelist()
def list_unclaimed_c2b_payments(company: str | None = None, hours: int = 6) -> dict:
	"""Recent direct till payments that are not yet attached to a sale, newest first."""
	company = resolve_company(company)
	since = add_to_date(now_datetime(), hours=-max(1, min(cint(hours) or 6, 72)))
	rows = frappe.get_all(
		"MPESA Transaction Log",
		filters={
			"company": company,
			"transaction_type": "C2B",
			"status": STATUS_SUCCESS,
			"invoice": ["is", "not set"],
			"creation": [">=", since],
		},
		fields=["name", "mpesa_receipt_number", "amount", "payer_name", "phone_number", "created_at"],
		order_by="creation desc",
		limit=50,
	)
	return {
		"success": True,
		"transactions": [
			{
				"transaction_id": r.name,
				"mpesa_receipt_number": r.mpesa_receipt_number,
				"amount": flt(r.amount),
				"payer_name": r.payer_name,
				"phone_number": r.phone_number,
				"created_at": str(r.created_at) if r.created_at else None,
			}
			for r in rows
		],
	}


# ============================================================================
# Settings Management APIs
# ============================================================================

SETTINGS_DATA_FIELDS = (
	"environment",
	"shortcode",
	"shortcode_type",
	"till_number",
	"consumer_key",
	"payment_account",
	"initiator_name",
	"stk_callback_url",
	"b2c_result_url",
	"b2c_timeout_url",
	"b2b_result_url",
	"b2b_timeout_url",
	"account_reference_prefix",
	"transaction_description",
	"test_phone_number",
)
SETTINGS_SECRET_FIELDS = ("consumer_secret", "passkey", "initiator_password", "security_credential")
SETTINGS_CHECK_FIELDS = ("auto_confirm_payments", "allow_manual_code", "enable_c2b")


@frappe.whitelist()
def register_mpesa_settings(company: str | None = None, **kwargs) -> dict:
	"""
	Create or update the company's M-Pesa settings.

	Secrets are only replaced when a new value is sent, so the settings screen can save
	other changes without asking for the keys again.
	"""
	company = resolve_company(company)
	require_gateway_admin()

	settings = _get_settings_doc(company)
	update_mode = bool(settings)
	if not settings:
		settings = frappe.new_doc("MPESA Settings")
		settings.company = company
		settings.environment = "Sandbox"
		settings.shortcode_type = "Paybill"

	for field in SETTINGS_DATA_FIELDS:
		if field in kwargs and kwargs[field] is not None and kwargs[field] != MASK:
			value = kwargs[field]
			settings.set(field, value.strip() if isinstance(value, str) else value)

	for field in SETTINGS_SECRET_FIELDS:
		value = kwargs.get(field)
		if value and value != MASK:
			settings.set(field, value.strip())

	for field in SETTINGS_CHECK_FIELDS:
		if field in kwargs and kwargs[field] is not None:
			settings.set(field, _bool(kwargs[field]))

	if kwargs.get("payment_timeout"):
		settings.payment_timeout = cint(kwargs["payment_timeout"])

	if kwargs.get("test_phone_number"):
		settings.test_phone_number = normalize_kenyan_phone(kwargs["test_phone_number"])

	settings.is_active = _bool(kwargs["is_active"]) if kwargs.get("is_active") is not None else 1

	settings.save(ignore_permissions=True)

	mode = setup_mpesa_mode_of_payment(company)

	c2b_message = None
	if settings.is_active and settings.enable_c2b:
		try:
			c2b_message = register_c2b_urls(settings)["message"]
			frappe.db.set_value("MPESA Settings", settings.name, "c2b_registered_on", now())
		except MpesaClientError as e:
			c2b_message = _("Settings saved, but direct till payments could not be registered: {0}").format(
				str(e)
			)

	frappe.db.commit()

	return {
		"success": True,
		"message": _("M-Pesa settings saved") if update_mode else _("M-Pesa settings registered"),
		"c2b_message": c2b_message,
		"settings": {
			"name": settings.name,
			"company": settings.company,
			"is_active": settings.is_active,
			"environment": settings.environment,
			"mode_of_payment": mode,
		},
	}


@frappe.whitelist()
def get_mpesa_settings(company: str | None = None) -> dict:
	"""The company's M-Pesa settings. Secrets are never returned, only whether they are set."""
	company = resolve_company(company)
	require_gateway_admin()

	settings = _get_settings_doc(company)
	if not settings:
		return {
			"success": False,
			"message": _("No MPESA settings found for company {0}").format(company),
			"settings": None,
			"callback_problem": callback_url_problem(build_callback_url("daraja_stk_result", "x")),
		}

	callback_url = get_stk_callback_url(settings)
	data = {
		"name": settings.name,
		"company": settings.company,
		"is_active": settings.is_active,
		"environment": settings.environment,
		"shortcode": settings.shortcode,
		"shortcode_type": settings.shortcode_type,
		"till_number": settings.till_number,
		"c2b_shortcode": c2b_shortcode(settings),
		"payment_account": settings.payment_account,
		"mode_of_payment": settings.mode_of_payment,
		"account_reference_prefix": settings.account_reference_prefix,
		"transaction_description": settings.transaction_description,
		"auto_confirm_payments": settings.auto_confirm_payments,
		"allow_manual_code": settings.allow_manual_code,
		"enable_c2b": settings.enable_c2b,
		"c2b_registered_on": str(settings.c2b_registered_on) if settings.c2b_registered_on else None,
		"payment_timeout": settings.payment_timeout,
		"test_phone_number": settings.test_phone_number,
		"initiator_name": settings.initiator_name,
		"stk_callback_url": settings.stk_callback_url,
		"effective_callback_url": callback_url,
		"b2c_result_url": settings.b2c_result_url,
		"b2c_timeout_url": settings.b2c_timeout_url,
		"b2b_result_url": settings.b2b_result_url,
		"b2b_timeout_url": settings.b2b_timeout_url,
		"consumer_key": MASK if settings.consumer_key else None,
	}
	for field in SETTINGS_SECRET_FIELDS:
		data[field] = MASK if settings.get(field) else None

	return {"success": True, "settings": data, "callback_problem": callback_url_problem(callback_url)}


@frappe.whitelist()
def update_mpesa_settings(company: str | None = None, **kwargs) -> dict:
	"""Alias for register_mpesa_settings, which handles both create and update"""
	return register_mpesa_settings(company=company, **kwargs)


@frappe.whitelist()
def deactivate_mpesa_settings(company: str | None = None) -> dict:
	"""Turn M-Pesa off for the company without losing its settings"""
	company = resolve_company(company)
	require_gateway_admin()

	settings = _get_settings_doc(company)
	if not settings:
		frappe.throw(_("No MPESA settings found for company {0}").format(company), frappe.ValidationError)

	settings.is_active = 0
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"success": True,
		"message": _("MPESA settings deactivated successfully"),
		"settings": {"name": settings.name, "company": settings.company, "is_active": settings.is_active},
	}


@frappe.whitelist()
def test_mpesa_connection(company: str | None = None, phone_number: str | None = None) -> dict:
	"""
	Check the saved keys by signing in to Safaricom. With a phone number, also send a
	KES 1 prompt so the whole payment path, including the callback, can be tried.
	"""
	company = resolve_company(company)
	require_gateway_admin()

	settings = _get_settings_doc(company)
	if not settings:
		return {"success": False, "message": _("Save the M-Pesa settings first.")}

	problem = callback_url_problem(get_stk_callback_url(settings))
	try:
		result = daraja_test_connection(settings)
	except Exception as e:
		return _error_response(e)

	response = {"success": True, "message": result["message"], "callback_problem": problem}
	if phone_number:
		response["test_payment"] = initiate_stk_push_payment(
			company=company, phone_number=phone_number, amount=1, reference="TEST", description="Test"
		)
	return response


# ============================================================================
# B2C and B2B Payment APIs
# ============================================================================


@frappe.whitelist()
def initiate_b2c_payment(
	company: str | None = None,
	phone_number: str | None = None,
	amount: float | None = None,
	reference: str | None = None,
	remarks: str | None = None,
) -> dict:
	"""Business to Customer payout (refunds, cash-back). Managers only."""
	company = resolve_company(company)
	require_gateway_admin()

	if not phone_number:
		frappe.throw(_("Phone number is required"), frappe.ValidationError)
	if not amount or flt(amount) <= 0:
		frappe.throw(_("Valid amount is required"), frappe.ValidationError)
	if not reference:
		frappe.throw(_("Reference number is required"), frappe.ValidationError)

	phone_number = normalize_kenyan_phone(phone_number)
	try:
		result = process_b2c_payment(
			company=company,
			phone_number=phone_number,
			amount=flt(amount),
			reference=reference,
			remarks=remarks,
		)
	except Exception as e:
		return _error_response(e)

	return {
		"success": True,
		"message": _("B2C payment initiated successfully"),
		"transaction": {
			"transaction_id": result.get("transaction_id"),
			"conversation_id": result.get("conversation_id"),
			"status": STATUS_PENDING,
			"phone_number": phone_number,
			"amount": flt(amount),
			"reference": reference,
		},
	}


@frappe.whitelist()
def initiate_b2b_payment(
	company: str | None = None,
	receiver_shortcode: str | None = None,
	amount: float | None = None,
	reference: str | None = None,
	remarks: str | None = None,
) -> dict:
	"""Business to Business transfer. Managers only."""
	company = resolve_company(company)
	require_gateway_admin()

	if not receiver_shortcode:
		frappe.throw(_("Receiver shortcode is required"), frappe.ValidationError)
	if not amount or flt(amount) <= 0:
		frappe.throw(_("Valid amount is required"), frappe.ValidationError)
	if not reference:
		frappe.throw(_("Reference number is required"), frappe.ValidationError)

	try:
		result = process_b2b_payment(
			company=company,
			receiver_shortcode=receiver_shortcode,
			amount=flt(amount),
			reference=reference,
			remarks=remarks,
		)
	except Exception as e:
		return _error_response(e)

	return {
		"success": True,
		"message": _("B2B payment initiated successfully"),
		"transaction": {
			"transaction_id": result.get("transaction_id"),
			"conversation_id": result.get("conversation_id"),
			"status": STATUS_PENDING,
			"receiver_shortcode": receiver_shortcode,
			"amount": flt(amount),
			"reference": reference,
		},
	}


# ============================================================================
# Transaction Management APIs
# ============================================================================


@frappe.whitelist()
def get_transactions(
	company: str | None = None,
	status: str | None = None,
	transaction_type: str | None = None,
	limit: int = 50,
	offset: int = 0,
) -> dict:
	"""Transaction history for the user's company"""
	company = resolve_company(company)

	filters = {"company": company}
	if status:
		filters["status"] = status
	if transaction_type:
		filters["transaction_type"] = transaction_type

	limit = max(1, min(cint(limit) or 50, 500))
	offset = max(0, cint(offset))

	transactions = frappe.get_all(
		"MPESA Transaction Log",
		filters=filters,
		fields=[
			"name",
			"transaction_type",
			"status",
			"phone_number",
			"payer_name",
			"amount",
			"reference_number",
			"mpesa_receipt_number",
			"result_code",
			"result_description",
			"created_at",
			"completed_at",
			"payment_entry",
			"invoice_type",
			"invoice",
		],
		order_by="creation desc",
		limit=limit,
		start=offset,
	)

	return {
		"success": True,
		"transactions": transactions,
		"total": frappe.db.count("MPESA Transaction Log", filters),
		"limit": limit,
		"offset": offset,
	}


@frappe.whitelist()
def get_transaction(transaction_id: str) -> dict:
	"""Single transaction details, including what was sent to and received from Safaricom"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if not frappe.db.exists("MPESA Transaction Log", transaction_id):
		frappe.throw(_("Transaction not found: {0}").format(transaction_id), frappe.ValidationError)

	transaction_log = frappe.get_doc("MPESA Transaction Log", transaction_id)
	ensure_record_company(transaction_log.company)

	transaction_data = transaction_log.as_dict()
	for field in ("request_payload", "response_payload", "callback_payload"):
		if isinstance(transaction_data.get(field), str):
			try:
				transaction_data[field] = json.loads(transaction_data[field])
			except ValueError:
				pass

	return {"success": True, "transaction": transaction_data}


# ============================================================================
# Mode of Payment Setup
# ============================================================================


def setup_mpesa_mode_of_payment(company: str) -> str | None:
	"""Create the M-Pesa Mode of Payment, map it to the company's account and add it to the tills"""
	settings = _get_settings_doc(company)
	if not settings:
		return None

	mode = settings.mode_of_payment or DEFAULT_MODE_OF_PAYMENT
	try:
		ensure_mode_of_payment(mode, "Phone", company, settings.payment_account)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "MPESA Setup Error")
		return None

	if settings.mode_of_payment != mode:
		frappe.db.set_value("MPESA Settings", settings.name, "mode_of_payment", mode)
	return mode
