"""
Payment gateways for the POS: one API for M-Pesa, Pesapal, PayPal and bank payments.

Settings screen:
	get_payment_gateways, save_gateway_settings, test_gateway, deactivate_gateway
Till:
	get_pos_payment_options      which methods to show and how each is collected
	create_gateway_checkout      Pesapal / PayPal payment link for a QR code
	check_gateway_payment        polled by the till until the payment settles
	cancel_gateway_payment       cashier gives up on a checkout
Sales:
	validate_gateway_payments / mark_gateway_payments_used are called by sales_api so a sale
	can only record a gateway payment that really succeeded and was not used before.
"""

from __future__ import annotations

import json
import secrets

import frappe
from frappe import _
from frappe.utils import cint, flt, now, nowdate

from techsavanna_pos.api import paypal_client, pesapal_client
from techsavanna_pos.api.payment_gateway_common import (
	build_callback_url,
	callback_url_problem,
	ensure_mode_of_payment,
	ensure_record_company,
	get_callback_base_url,
	normalize_kenyan_phone,
	require_gateway_admin,
	resolve_company,
)
from techsavanna_pos.api.pesapal_client import GatewayError

ONLINE_GATEWAYS = ("Pesapal", "PayPal")
DEFAULT_MODES = {"Pesapal": "Pesapal", "PayPal": "PayPal"}
MASK = "***"
CLIENTS = {"Pesapal": pesapal_client, "PayPal": paypal_client}


def _bool(value) -> int:
	if isinstance(value, str):
		return 1 if value.strip().lower() in ("1", "true", "yes", "on") else 0
	return 1 if value else 0


def _gateway_name(gateway: str | None) -> str:
	match = next((g for g in ONLINE_GATEWAYS if g.lower() == (gateway or "").strip().lower()), None)
	if not match:
		frappe.throw(_("Unknown payment gateway {0}").format(gateway), frappe.ValidationError)
	return match


def _get_gateway_settings(company: str, gateway: str, active_only: bool = False):
	filters = {"company": company, "gateway": gateway}
	if active_only:
		filters["is_active"] = 1
	name = frappe.db.get_value("POS Gateway Settings", filters, "name")
	return frappe.get_doc("POS Gateway Settings", name) if name else None


def _pesapal_urls(settings) -> tuple[str, str]:
	return (
		build_callback_url("pesapal_ipn", settings.callback_token),
		build_callback_url("pesapal_return", settings.callback_token),
	)


def _paypal_urls(settings) -> tuple[str, str]:
	return (
		build_callback_url("paypal_return", settings.callback_token),
		build_callback_url("paypal_cancel", settings.callback_token),
	)


def _settings_dict(settings) -> dict:
	return {
		"name": settings.name,
		"gateway": settings.gateway,
		"company": settings.company,
		"is_active": settings.is_active,
		"environment": settings.environment,
		"display_name": settings.display_name,
		"client_id": settings.client_id,
		"client_secret": MASK if settings.client_secret else None,
		"currency": settings.currency,
		"mode_of_payment": settings.mode_of_payment,
		"payment_account": settings.payment_account,
		"ipn_id": settings.ipn_id,
		"last_tested_on": str(settings.last_tested_on) if settings.last_tested_on else None,
		"last_test_result": settings.last_test_result,
	}


# ============================================================================
# Settings screen
# ============================================================================


@frappe.whitelist()
def get_payment_gateways(company: str | None = None) -> dict:
	"""Everything the Payment Gateways settings screen shows, for the user's company."""
	company = resolve_company(company)
	require_gateway_admin()

	from techsavanna_pos.api.mpesa_api import get_mpesa_settings

	mpesa = get_mpesa_settings(company)
	gateways = {}
	for gateway in ONLINE_GATEWAYS:
		settings = _get_gateway_settings(company, gateway)
		gateways[gateway.lower()] = _settings_dict(settings) if settings else None

	base_url = get_callback_base_url()
	return {
		"success": True,
		"company": company,
		"company_currency": frappe.get_cached_value("Company", company, "default_currency"),
		"callback_base_url": base_url,
		"callback_problem": callback_url_problem(base_url + "/"),
		"mpesa": mpesa.get("settings"),
		"pesapal": gateways["pesapal"],
		"paypal": gateways["paypal"],
		"bank_methods": _bank_methods(company),
	}


@frappe.whitelist()
def save_gateway_settings(company: str | None = None, gateway: str | None = None, **kwargs) -> dict:
	"""Create or update Pesapal or PayPal for the company. M-Pesa uses mpesa_api.register_mpesa_settings."""
	company = resolve_company(company)
	require_gateway_admin()
	gateway = _gateway_name(gateway)

	settings = _get_gateway_settings(company, gateway)
	is_new = not settings
	if is_new:
		settings = frappe.new_doc("POS Gateway Settings")
		settings.company = company
		settings.gateway = gateway

	for field in (
		"environment",
		"display_name",
		"client_id",
		"currency",
		"payment_account",
		"mode_of_payment",
	):
		if field in kwargs and kwargs[field] is not None:
			value = kwargs[field]
			settings.set(field, value.strip() if isinstance(value, str) else value)

	secret = kwargs.get("client_secret")
	if secret and secret != MASK:
		settings.client_secret = secret.strip()

	settings.is_active = _bool(kwargs["is_active"]) if kwargs.get("is_active") is not None else 1
	if not settings.mode_of_payment:
		settings.mode_of_payment = DEFAULT_MODES[gateway]

	if (
		gateway == "PayPal"
		and settings.currency
		and settings.currency.upper() not in paypal_client.PAYPAL_CURRENCIES
	):
		frappe.throw(
			_("PayPal cannot charge in {0}. Choose USD or another PayPal currency.").format(
				settings.currency
			),
			frappe.ValidationError,
		)

	settings.save(ignore_permissions=True)
	ensure_mode_of_payment(settings.mode_of_payment, "Bank", company, settings.payment_account)
	frappe.db.commit()

	return {
		"success": True,
		"message": _("{0} settings saved").format(gateway),
		"settings": _settings_dict(settings),
	}


@frappe.whitelist()
def test_gateway(company: str | None = None, gateway: str | None = None) -> dict:
	"""Sign in to the gateway with the saved keys. No money moves."""
	company = resolve_company(company)
	require_gateway_admin()

	if (gateway or "").lower() in ("mpesa", "m-pesa"):
		from techsavanna_pos.api.mpesa_api import test_mpesa_connection

		return test_mpesa_connection(company)

	gateway = _gateway_name(gateway)
	settings = _get_gateway_settings(company, gateway)
	if not settings:
		return {"success": False, "message": _("Save the {0} settings first.").format(gateway)}

	problem = callback_url_problem(get_callback_base_url() + "/")
	try:
		if gateway == "Pesapal":
			if problem:
				raise GatewayError(problem)
			result = pesapal_client.test_connection(settings, _pesapal_urls(settings)[0])
		else:
			result = paypal_client.test_connection(settings)
		ok, message = True, result["message"]
	except GatewayError as e:
		ok, message = False, str(e)

	frappe.db.set_value(
		"POS Gateway Settings", settings.name, {"last_tested_on": now(), "last_test_result": message}
	)
	frappe.db.commit()
	return {"success": ok, "message": message, "callback_problem": problem}


@frappe.whitelist()
def deactivate_gateway(company: str | None = None, gateway: str | None = None) -> dict:
	company = resolve_company(company)
	require_gateway_admin()

	if (gateway or "").lower() in ("mpesa", "m-pesa"):
		from techsavanna_pos.api.mpesa_api import deactivate_mpesa_settings

		return deactivate_mpesa_settings(company)

	gateway = _gateway_name(gateway)
	settings = _get_gateway_settings(company, gateway)
	if not settings:
		frappe.throw(_("{0} is not set up").format(gateway), frappe.ValidationError)
	settings.is_active = 0
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return {"success": True, "message": _("{0} turned off").format(gateway)}


# ============================================================================
# Till
# ============================================================================


def _gateway_modes(company: str) -> dict:
	"""{mode_of_payment: gateway info} for the company's active gateways."""
	modes = {}
	mpesa = frappe.db.get_value(
		"MPESA Settings",
		{"company": company, "is_active": 1},
		[
			"name",
			"mode_of_payment",
			"allow_manual_code",
			"enable_c2b",
			"shortcode_type",
			"shortcode",
			"till_number",
		],
		as_dict=True,
	)
	if mpesa and mpesa.mode_of_payment:
		modes[mpesa.mode_of_payment] = {
			"gateway": "mpesa",
			"settings": mpesa.name,
			"allow_manual_code": cint(mpesa.allow_manual_code),
			"c2b_enabled": cint(mpesa.enable_c2b),
			"pay_to": mpesa.till_number or mpesa.shortcode,
			"pay_to_type": "Till" if mpesa.shortcode_type == "BuyGoods" else "Paybill",
		}
	for row in frappe.get_all(
		"POS Gateway Settings",
		filters={"company": company, "is_active": 1},
		fields=["name", "gateway", "mode_of_payment", "currency", "environment"],
	):
		if row.mode_of_payment:
			modes[row.mode_of_payment] = {
				"gateway": row.gateway.lower(),
				"settings": row.name,
				"currency": row.currency,
				"environment": row.environment,
			}
	return modes


def _bank_methods(company: str) -> list[str]:
	"""Bank-type methods with an account for this company that are not online gateways."""
	gateway_modes = set(_gateway_modes(company))
	rows = frappe.get_all(
		"Mode of Payment Account",
		filters={"company": company, "parenttype": "Mode of Payment"},
		fields=["parent"],
	)
	names = {r.parent for r in rows} - gateway_modes
	if not names:
		return []
	return sorted(
		frappe.get_all(
			"Mode of Payment",
			filters={"name": ["in", list(names)], "type": "Bank", "enabled": 1},
			pluck="name",
		)
	)


@frappe.whitelist()
def get_pos_payment_options(company: str | None = None) -> dict:
	"""
	How the till collects each payment method:
	gateway "mpesa" (phone prompt / M-Pesa code), "pesapal" or "paypal" (QR link),
	"bank" (needs a transfer or slip reference) or none (cash, card terminal, credit...).
	"""
	company = resolve_company(company)
	options = {mode: info for mode, info in _gateway_modes(company).items()}
	for info in options.values():
		info.pop("settings", None)
	for mode in _bank_methods(company):
		options.setdefault(mode, {"gateway": "bank", "requires_reference": 1})
	return {
		"success": True,
		"company_currency": frappe.get_cached_value("Company", company, "default_currency"),
		"options": options,
	}


def _convert(amount: float, from_currency: str, to_currency: str) -> tuple[float, float]:
	"""(converted amount, rate) using ERPNext's Currency Exchange records."""
	if from_currency.upper() == to_currency.upper():
		return flt(amount, 2), 1.0
	from erpnext.setup.utils import get_exchange_rate

	rate = flt(get_exchange_rate(from_currency, to_currency, nowdate()))
	if not rate:
		frappe.throw(
			_(
				"No exchange rate from {0} to {1}. Add one under Accounting > Currency Exchange "
				"so the POS can charge in {1}."
			).format(from_currency, to_currency),
			frappe.ValidationError,
		)
	return flt(amount * rate, 2), rate


def _txn_dict(txn) -> dict:
	return {
		"transaction_id": txn.name,
		"gateway": txn.gateway,
		"status": txn.status,
		"amount": flt(txn.amount),
		"currency": txn.currency,
		"charged_amount": flt(txn.charged_amount),
		"charged_currency": txn.charged_currency,
		"exchange_rate": flt(txn.exchange_rate),
		"merchant_reference": txn.merchant_reference,
		"checkout_url": txn.checkout_url,
		"confirmation_code": txn.confirmation_code,
		"payment_method": txn.payment_method,
		"payer_name": txn.payer_name,
		"result_description": txn.result_description,
		"invoice": txn.invoice,
	}


@frappe.whitelist()
def create_gateway_checkout(
	gateway: str,
	amount: float,
	company: str | None = None,
	reference: str | None = None,
	phone_number: str | None = None,
	email: str | None = None,
	customer_name: str | None = None,
	description: str | None = None,
) -> dict:
	"""Start a Pesapal or PayPal payment and return the link the customer opens (as a QR code)."""
	company = resolve_company(company)
	gateway = _gateway_name(gateway)
	amount = flt(amount, 2)
	if amount <= 0:
		frappe.throw(_("Valid amount is required (must be greater than 0)"), frappe.ValidationError)

	settings = _get_gateway_settings(company, gateway, active_only=True)
	if not settings:
		frappe.throw(_("{0} is not set up for this business.").format(gateway), frappe.ValidationError)

	problem = callback_url_problem(get_callback_base_url() + "/")
	if problem:
		frappe.throw(problem, frappe.ValidationError)

	company_currency = frappe.get_cached_value("Company", company, "default_currency") or "KES"
	charge_currency = (settings.currency or ("USD" if gateway == "PayPal" else company_currency)).upper()
	charged_amount, rate = _convert(amount, company_currency, charge_currency)

	phone = normalize_kenyan_phone(phone_number) if phone_number else None

	txn = frappe.new_doc("POS Gateway Transaction")
	txn.company = company
	txn.gateway = gateway
	txn.gateway_settings = settings.name
	txn.status = "Pending"
	txn.amount = amount
	txn.currency = company_currency
	txn.charged_amount = charged_amount
	txn.charged_currency = charge_currency
	txn.exchange_rate = rate
	# Unique per attempt so a retried sale never collides with an earlier order at the gateway
	txn.merchant_reference = f"{(reference or 'POS')[:30]}-{secrets.token_hex(4)}".upper()
	txn.payer_phone = phone
	txn.payer_email = email
	txn.payer_name = customer_name
	txn.insert(ignore_permissions=True)
	frappe.db.commit()

	text = description or _("Payment to {0}").format(settings.display_name or company)
	try:
		if gateway == "Pesapal":
			ipn_url, return_url = _pesapal_urls(settings)
			result = pesapal_client.create_checkout(
				settings,
				txn,
				ipn_url,
				return_url,
				{"phone": phone, "email": email, "name": customer_name},
				text,
			)
		else:
			return_url, cancel_url = _paypal_urls(settings)
			result = paypal_client.create_checkout(settings, txn, return_url, cancel_url, text)
	except GatewayError as e:
		txn.status = "Failed"
		txn.error_message = str(e)
		txn.result_description = str(e)
		txn.save(ignore_permissions=True)
		frappe.db.commit()
		return {"success": False, "message": str(e), "transaction": _txn_dict(txn)}

	txn.gateway_order_id = result["gateway_order_id"]
	txn.checkout_url = result["checkout_url"]
	txn.request_payload = json.dumps(result.get("request"), default=str)
	txn.response_payload = json.dumps(result.get("response"), default=str)
	txn.save(ignore_permissions=True)
	frappe.db.commit()
	return {"success": True, "transaction": _txn_dict(txn)}


def refresh_transaction(txn):
	"""Ask the gateway how a pending payment stands and record the answer."""
	if txn.status != "Pending" or not txn.gateway_order_id:
		return txn
	settings = frappe.get_doc("POS Gateway Settings", txn.gateway_settings)
	result = CLIENTS[txn.gateway].fetch_status(settings, txn)

	txn.reload()
	if txn.status != "Pending":
		return txn
	txn.status_payload = json.dumps(result.get("raw"), default=str)[:60000]
	if result["status"] != "Pending":
		txn.status = result["status"]
		txn.result_description = result.get("description")
		txn.confirmation_code = result.get("confirmation_code") or txn.confirmation_code
		txn.payment_method = result.get("payment_method") or txn.payment_method
		txn.payer_email = txn.payer_email or result.get("payer_email")
		txn.payer_name = txn.payer_name or result.get("payer_name")
		txn.completed_at = now()
	txn.save(ignore_permissions=True)
	frappe.db.commit()
	return txn


@frappe.whitelist()
def check_gateway_payment(transaction_id: str) -> dict:
	"""Current state of a Pesapal or PayPal checkout. The till polls this."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if not frappe.db.exists("POS Gateway Transaction", transaction_id):
		frappe.throw(_("Payment not found"), frappe.ValidationError)
	txn = frappe.get_doc("POS Gateway Transaction", transaction_id)
	ensure_record_company(txn.company)
	try:
		txn = refresh_transaction(txn)
	except GatewayError as e:
		# Could not ask right now; the IPN/return page will still settle it.
		return {"success": True, "transaction": _txn_dict(txn), "warning": str(e)}
	return {"success": True, "transaction": _txn_dict(txn)}


@frappe.whitelist()
def cancel_gateway_payment(transaction_id: str) -> dict:
	"""The cashier stopped waiting. A payment that already went through is kept."""
	txn = frappe.get_doc("POS Gateway Transaction", transaction_id)
	ensure_record_company(txn.company)
	try:
		txn = refresh_transaction(txn)
	except GatewayError:
		pass
	if txn.status == "Pending":
		txn.status = "Cancelled"
		txn.result_description = _("Cancelled at the till")
		txn.save(ignore_permissions=True)
		frappe.db.commit()
	return {"success": True, "transaction": _txn_dict(txn)}


def add_gateway_modes_to_pos_profile(pos_profile: str, company: str) -> None:
	"""Called when a POS Profile is saved, so new tills list every active gateway."""
	modes = list(_gateway_modes(company))
	if not modes:
		return
	profile = frappe.get_doc("POS Profile", pos_profile)
	missing = [m for m in modes if not any(p.mode_of_payment == m for p in profile.payments)]
	if not missing:
		return
	for mode in missing:
		profile.append("payments", {"mode_of_payment": mode, "default": 0})
	profile.flags.ignore_gateway_hook = True
	profile.save(ignore_permissions=True)


# ============================================================================
# Sales: only real, unused gateway payments can be recorded on a sale
# ============================================================================


def validate_gateway_payments(payments: list[dict] | None, company: str) -> list[tuple[str, str]]:
	"""
	Check each payment row that uses a gateway method.

	Row keys read: mode_of_payment, amount, gateway_transaction (the id the till got back),
	reference_no (M-Pesa code or bank reference). Fills reference_no from the gateway.
	Returns [(doctype, name)] of payment records to link once the sale is saved.
	"""
	if not payments:
		return []

	modes = _gateway_modes(company)
	claims: list[tuple[str, str]] = []

	for row in payments:
		info = modes.get(row.get("mode_of_payment"))
		amount = flt(row.get("amount"))
		if not info or amount <= 0:
			continue

		txn_id = (row.get("gateway_transaction") or "").strip()
		code = (row.get("reference_no") or "").strip().upper()

		if info["gateway"] == "mpesa":
			log_name = None
			if txn_id:
				log_name = frappe.db.exists("MPESA Transaction Log", txn_id)
			elif code:
				log_name = frappe.db.get_value(
					"MPESA Transaction Log", {"company": company, "mpesa_receipt_number": code}, "name"
				)
			if log_name:
				log = frappe.get_doc("MPESA Transaction Log", log_name)
				_check_claim(
					log.company,
					company,
					log.status,
					log.invoice,
					flt(log.amount),
					amount,
					log.mpesa_receipt_number or log.name,
				)
				row["reference_no"] = log.mpesa_receipt_number or row.get("reference_no")
				claims.append(("MPESA Transaction Log", log.name))
			elif code and info.get("allow_manual_code"):
				row["reference_no"] = code
			else:
				frappe.throw(
					_("Collect the M-Pesa payment of {0} before completing the sale.").format(amount),
					frappe.ValidationError,
				)
		else:
			if not txn_id or not frappe.db.exists("POS Gateway Transaction", txn_id):
				frappe.throw(
					_("Collect the {0} payment before completing the sale.").format(
						row.get("mode_of_payment")
					),
					frappe.ValidationError,
				)
			txn = frappe.get_doc("POS Gateway Transaction", txn_id)
			_check_claim(
				txn.company, company, txn.status, txn.invoice, flt(txn.amount), amount, txn.merchant_reference
			)
			row["reference_no"] = txn.confirmation_code or txn.merchant_reference
			claims.append(("POS Gateway Transaction", txn.name))

	seen = set()
	for claim in claims:
		if claim in seen:
			frappe.throw(_("The same payment cannot be used twice on one sale."), frappe.ValidationError)
		seen.add(claim)

	return claims


def _check_claim(record_company, company, status, invoice, paid, amount, label) -> None:
	if record_company != company:
		frappe.throw(_("Payment {0} belongs to another business.").format(label), frappe.ValidationError)
	if status != "Success":
		frappe.throw(_("Payment {0} has not gone through.").format(label), frappe.ValidationError)
	if invoice:
		frappe.throw(
			_("Payment {0} is already used on sale {1}.").format(label, invoice), frappe.ValidationError
		)
	# M-Pesa rounds up to whole shillings, so allow up to one shilling of difference
	if paid + 1 < amount:
		frappe.throw(
			_("Payment {0} was for {1}, less than the {2} recorded.").format(label, paid, amount),
			frappe.ValidationError,
		)


SALE_MEMORY_SECONDS = 24 * 60 * 60


def _sale_key(company: str, client_reference: str) -> str:
	return f"techsavanna_pos:sale_ref:{company}:{client_reference}"


def find_recorded_sale(company: str | None, client_reference: str | None) -> tuple[str, str] | None:
	"""
	(doctype, name) of the invoice already created for this till sale, if any.

	The till sends the same client_reference when it retries a sale whose reply was lost, so
	the retry gets the original invoice back instead of creating a second one.
	"""
	if not company or not client_reference:
		return None
	remembered = frappe.cache().get_value(_sale_key(company, client_reference))
	if remembered and "::" in remembered:
		doctype, name = remembered.split("::", 1)
		docstatus = frappe.db.get_value(doctype, name, "docstatus")
		if docstatus is not None and int(docstatus) != 2:
			return doctype, name

	# The memory above lasts a day and can be cleared; offline sales may arrive later than
	# that, so also look for the id stored on the invoice itself.
	from techsavanna_pos.api.offline_sales import CLIENT_REFERENCE_FIELD

	for doctype in ("POS Invoice", "Sales Invoice"):
		if not frappe.get_meta(doctype).has_field(CLIENT_REFERENCE_FIELD):
			continue
		name = frappe.db.get_value(
			doctype,
			{CLIENT_REFERENCE_FIELD: str(client_reference)[:140], "company": company, "docstatus": ["!=", 2]},
			"name",
		)
		if name:
			return doctype, name
	return None


def remember_sale(company: str | None, client_reference: str | None, doctype: str, name: str) -> None:
	if company and client_reference:
		frappe.cache().set_value(
			_sale_key(company, client_reference), f"{doctype}::{name}", expires_in_sec=SALE_MEMORY_SECONDS
		)


def lock_gateway_payments(claims: list[tuple[str, str]]) -> None:
	"""
	Lock the claimed payment records and check again that they are still free.

	Call this right before the invoice is saved. The row locks last until the request's
	transaction ends, so a second sale trying to use the same payment at the same moment
	waits here, then finds it used and is refused. Nothing between this call and
	mark_gateway_payments_used may commit, or the locks are released early.
	"""
	# Always lock in the same order so two sales can never wait on each other
	for doctype, name in sorted(claims):
		row = frappe.db.get_value(doctype, name, ["status", "invoice"], as_dict=True, for_update=True)
		if not row or row.status != "Success":
			frappe.throw(_("Payment {0} has not gone through.").format(name), frappe.ValidationError)
		if row.invoice:
			frappe.throw(
				_("Payment {0} is already used on sale {1}.").format(name, row.invoice),
				frappe.ValidationError,
			)


def mark_gateway_payments_used(claims: list[tuple[str, str]], invoice_type: str, invoice: str) -> None:
	for doctype, name in claims:
		frappe.db.set_value(doctype, name, {"invoice_type": invoice_type, "invoice": invoice})
