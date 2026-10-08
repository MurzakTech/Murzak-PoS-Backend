"""
Callbacks payment providers call to report results.

These are reached without a login, so each URL carries the company's own callback token
(`?t=...`). A callback is only accepted when its token belongs to the company that owns the
payment, and every handler can safely receive the same callback more than once.

Function names avoid words Safaricom rejects in callback URLs (mpesa, safaricom, query, ...).
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, now, nowdate

from techsavanna_pos.api.mpesa_client import status_for_result_code
from techsavanna_pos.api.mpesa_constants import STATUS_PENDING, STATUS_SUCCESS, get_result_message
from techsavanna_pos.api.payment_gateway_common import read_request_json


def _company_for_mpesa_token(token: str | None) -> tuple[str | None, str | None]:
	"""(company, settings name) owning an M-Pesa callback token."""
	if not token:
		return None, None
	row = frappe.db.get_value("MPESA Settings", {"callback_token": token}, ["company", "name"], as_dict=True)
	return (row.company, row.name) if row else (None, None)


def _accept_daraja(message: str = "Accepted") -> None:
	"""Reply Daraja expects: {"ResultCode": 0, "ResultDesc": "..."} at the top level."""
	frappe.response["ResultCode"] = 0
	frappe.response["ResultDesc"] = message


def _metadata(items: list) -> dict:
	return {item.get("Name"): item.get("Value") for item in items or [] if isinstance(item, dict)}


# ============================================================================
# M-Pesa (Daraja)
# ============================================================================


@frappe.whitelist(allow_guest=True, methods=["POST"])
def daraja_stk_result(t: str | None = None, **kwargs):
	"""Result of an STK Push prompt: paid, cancelled, wrong PIN, timeout..."""
	payload = read_request_json()
	company, _settings = _company_for_mpesa_token(t)
	if not company:
		frappe.log_error(json.dumps(payload)[:5000], "MPESA callback with unknown token")
		return _accept_daraja()

	callback = (payload.get("Body") or {}).get("stkCallback") or {}
	checkout_request_id = callback.get("CheckoutRequestID")
	log_name = frappe.db.get_value(
		"MPESA Transaction Log",
		{"checkout_request_id": checkout_request_id, "company": company},
		"name",
	)
	if not log_name:
		frappe.log_error(json.dumps(payload)[:5000], "MPESA callback for unknown transaction")
		return _accept_daraja()

	process_stk_callback(log_name, callback, payload)
	return _accept_daraja()


def process_stk_callback(log_name: str, callback: dict, payload: dict | None = None) -> None:
	"""Apply an STK callback to its log. Safe to call again with the same callback."""
	log = frappe.get_doc("MPESA Transaction Log", log_name, for_update=True)
	result_code = int(callback.get("ResultCode", -1))
	meta = _metadata((callback.get("CallbackMetadata") or {}).get("Item"))
	receipt = meta.get("MpesaReceiptNumber")

	already_final = log.status != STATUS_PENDING
	if already_final and (log.mpesa_receipt_number or log.status != STATUS_SUCCESS or not receipt):
		# Duplicate delivery, or a status query already settled it: nothing new to record.
		return

	log.callback_payload = json.dumps(payload or callback)
	log.result_code = result_code
	log.result_description = callback.get("ResultDesc") or get_result_message(result_code)
	log.status = status_for_result_code(result_code)
	if receipt:
		log.mpesa_receipt_number = receipt
	if meta.get("Amount") is not None and log.status == STATUS_SUCCESS:
		paid = flt(meta.get("Amount"))
		if paid and abs(paid - flt(log.amount)) > 0.009:
			log.error_message = _("Customer paid {0} instead of {1}").format(paid, log.amount)
			log.amount = paid
	log.completed_at = log.completed_at or now()
	log.save(ignore_permissions=True)
	frappe.db.commit()

	if log.status == STATUS_SUCCESS:
		settle_invoice_if_needed(log)


def settle_invoice_if_needed(log) -> None:
	"""
	When a prompt was sent to collect an existing Sales Invoice (pay later), record the money
	against it with a Payment Entry. Till sales do not need this: the sale itself records the
	M-Pesa payment line.
	"""
	if not (log.get("settle_invoice") and log.invoice_type == "Sales Invoice" and log.invoice):
		return
	if log.payment_entry:
		return

	original_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		settings = frappe.db.get_value(
			"MPESA Settings",
			{"company": log.company},
			["payment_account", "mode_of_payment"],
			as_dict=True,
		)
		invoice = frappe.get_doc("Sales Invoice", log.invoice)
		amount = min(flt(log.amount), flt(invoice.outstanding_amount))
		if invoice.docstatus != 1 or amount <= 0:
			return

		pe = get_payment_entry("Sales Invoice", invoice.name, party_amount=amount)
		pe.mode_of_payment = settings.mode_of_payment
		if settings.payment_account:
			pe.paid_to = settings.payment_account
			pe.paid_to_account_currency = frappe.db.get_value(
				"Account", settings.payment_account, "account_currency"
			)
		pe.paid_amount = amount
		pe.received_amount = amount
		for ref in pe.references:
			ref.allocated_amount = amount
		pe.reference_no = log.mpesa_receipt_number or log.checkout_request_id
		pe.reference_date = getdate(log.completed_at) if log.completed_at else nowdate()
		pe.remarks = _("M-Pesa payment {0} from {1}").format(pe.reference_no, log.phone_number or "")
		pe.insert(ignore_permissions=True)
		pe.submit()

		frappe.db.set_value("MPESA Transaction Log", log.name, "payment_entry", pe.name)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "MPESA Payment Entry Error")
	finally:
		frappe.set_user(original_user)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def daraja_c2b_validation(t: str | None = None, **kwargs):
	"""Safaricom asks whether to accept a direct payment. The POS accepts all of them."""
	return _accept_daraja()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def daraja_c2b_confirmation(t: str | None = None, **kwargs):
	"""A customer paid straight to the till or paybill. Record it so a cashier can match it."""
	payload = read_request_json()
	company, _settings = _company_for_mpesa_token(t)
	if not company:
		frappe.log_error(json.dumps(payload)[:5000], "MPESA C2B callback with unknown token")
		return _accept_daraja()

	receipt = (payload.get("TransID") or "").strip().upper()
	if not receipt:
		return _accept_daraja()
	if frappe.db.exists("MPESA Transaction Log", {"company": company, "mpesa_receipt_number": receipt}):
		return _accept_daraja()

	msisdn = str(payload.get("MSISDN") or "")
	names = " ".join(
		part
		for part in (payload.get("FirstName"), payload.get("MiddleName"), payload.get("LastName"))
		if part
	)

	log = frappe.new_doc("MPESA Transaction Log")
	log.company = company
	log.transaction_type = "C2B"
	log.status = STATUS_SUCCESS
	log.amount = flt(payload.get("TransAmount"))
	log.mpesa_receipt_number = receipt
	# Safaricom now masks the payer's number in production; keep it only when it is a real number.
	log.phone_number = msisdn if msisdn.isdigit() and len(msisdn) == 12 and msisdn.startswith("254") else None
	log.payer_name = names or None
	log.reference_number = payload.get("BillRefNumber") or payload.get("InvoiceNumber")
	log.result_code = 0
	log.result_description = payload.get("TransactionType") or "Customer payment"
	log.callback_payload = json.dumps(payload)
	log.created_at = now()
	log.completed_at = now()
	log.insert(ignore_permissions=True)
	frappe.db.commit()
	return _accept_daraja()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def daraja_business_result(t: str | None = None, **kwargs):
	"""Result of a B2C or B2B payout."""
	payload = read_request_json()
	company, _settings = _company_for_mpesa_token(t)
	result = payload.get("Result") or {}
	conversation_id = result.get("ConversationID")
	log_name = (
		frappe.db.get_value(
			"MPESA Transaction Log", {"conversation_id": conversation_id, "company": company}, "name"
		)
		if company and conversation_id
		else None
	)
	if log_name:
		log = frappe.get_doc("MPESA Transaction Log", log_name)
		if log.status == STATUS_PENDING:
			result_code = int(result.get("ResultCode", -1))
			log.result_code = result_code
			log.result_description = result.get("ResultDesc")
			log.status = status_for_result_code(result_code)
			log.mpesa_receipt_number = result.get("TransactionID")
			log.callback_payload = json.dumps(payload)
			log.save(ignore_permissions=True)
			frappe.db.commit()
	return _accept_daraja()


@frappe.whitelist(allow_guest=True, methods=["POST"])
def daraja_business_timeout(t: str | None = None, **kwargs):
	"""Safaricom could not finish a payout in time."""
	payload = read_request_json()
	company, _settings = _company_for_mpesa_token(t)
	conversation_id = (payload.get("Result") or {}).get("ConversationID")
	if company and conversation_id:
		log_name = frappe.db.get_value(
			"MPESA Transaction Log", {"conversation_id": conversation_id, "company": company}, "name"
		)
		if log_name:
			log = frappe.get_doc("MPESA Transaction Log", log_name)
			if log.status == STATUS_PENDING:
				log.status = "Failed"
				log.result_description = _("Safaricom timed out")
				log.callback_payload = json.dumps(payload)
				log.save(ignore_permissions=True)
				frappe.db.commit()
	return _accept_daraja()


# ============================================================================
# Pesapal
# ============================================================================


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def pesapal_ipn(t: str | None = None, **kwargs):
	"""Pesapal says an order changed. Re-read its status from Pesapal rather than trusting the call."""
	payload = read_request_json()
	params = {**(frappe.form_dict or {}), **payload}
	tracking_id = params.get("OrderTrackingId")
	merchant_reference = params.get("OrderMerchantReference")
	notification_type = params.get("OrderNotificationType") or "IPNCHANGE"

	status = 500
	settings_name = (
		frappe.db.get_value("POS Gateway Settings", {"gateway": "Pesapal", "callback_token": t}, "name")
		if t
		else None
	)
	if settings_name and tracking_id:
		txn_name = frappe.db.get_value(
			"POS Gateway Transaction",
			{"gateway_order_id": tracking_id, "gateway_settings": settings_name},
			"name",
		)
		if txn_name:
			from techsavanna_pos.api.payment_gateway_api import refresh_transaction

			try:
				refresh_transaction(frappe.get_doc("POS Gateway Transaction", txn_name))
				status = 200
			except Exception:
				frappe.log_error(frappe.get_traceback(), "Pesapal IPN Error")

	frappe.response["orderNotificationType"] = notification_type
	frappe.response["orderTrackingId"] = tracking_id
	frappe.response["orderMerchantReference"] = merchant_reference
	frappe.response["status"] = status


def _customer_page(title: str, message: str) -> None:
	"""Small page shown on the customer's phone after they pay online."""
	from html import escape

	frappe.respond_as_web_page(escape(title), escape(message), indicator_color="green")


@frappe.whitelist(allow_guest=True, methods=["GET"])
def pesapal_return(t: str | None = None, **kwargs):
	"""Where Pesapal sends the customer's browser after paying."""
	_customer_page(_("Thank you"), _("Your payment has been received. You can close this page."))


# ============================================================================
# PayPal
# ============================================================================


@frappe.whitelist(allow_guest=True, methods=["GET"])
def paypal_return(t: str | None = None, token: str | None = None, **kwargs):
	"""
	PayPal sends the customer here after they approve. The payment is captured now, so the
	till sees it at once; `token` is PayPal's order id.
	"""
	settings_name = (
		frappe.db.get_value("POS Gateway Settings", {"gateway": "PayPal", "callback_token": t}, "name")
		if t
		else None
	)
	txn_name = (
		frappe.db.get_value(
			"POS Gateway Transaction",
			{"gateway_order_id": token, "gateway_settings": settings_name},
			"name",
		)
		if settings_name and token
		else None
	)
	if not txn_name:
		return _customer_page(_("Payment not found"), _("Please show this screen to the cashier."))

	from techsavanna_pos.api.payment_gateway_api import refresh_transaction

	try:
		txn = refresh_transaction(frappe.get_doc("POS Gateway Transaction", txn_name))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "PayPal Return Error")
		return _customer_page(_("Almost done"), _("Please show this screen to the cashier."))

	if txn.status == STATUS_SUCCESS:
		return _customer_page(_("Thank you"), _("Your payment has been received. You can close this page."))
	return _customer_page(_("Payment not completed"), txn.result_description or _("Please ask the cashier."))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def paypal_cancel(t: str | None = None, token: str | None = None, **kwargs):
	"""The customer backed out on PayPal's page."""
	settings_name = (
		frappe.db.get_value("POS Gateway Settings", {"gateway": "PayPal", "callback_token": t}, "name")
		if t
		else None
	)
	if settings_name and token:
		txn_name = frappe.db.get_value(
			"POS Gateway Transaction",
			{"gateway_order_id": token, "gateway_settings": settings_name, "status": STATUS_PENDING},
			"name",
		)
		if txn_name:
			frappe.db.set_value(
				"POS Gateway Transaction",
				txn_name,
				{
					"status": "Cancelled",
					"result_description": _("The customer cancelled on PayPal"),
					"completed_at": now(),
				},
			)
			frappe.db.commit()
	_customer_page(_("Payment cancelled"), _("No money was taken. Please speak to the cashier."))
