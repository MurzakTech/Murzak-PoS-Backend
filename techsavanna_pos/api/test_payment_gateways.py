# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Tests for the payment gateways that need no database: every Frappe and HTTP call is mocked,
so they run under `bench run-tests` and in a plain Python environment alike.
"""

import base64
import json
import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from techsavanna_pos.api import (
	mpesa_client,
	payment_callbacks,
	payment_gateway_api,
	payment_gateway_common,
	paypal_client,
	pesapal_client,
)


class FakeDoc:
	"""Just enough of a Frappe Document for the code under test."""

	def __init__(self, **fields):
		self.__dict__.update(fields)
		self.saved = 0
		self.inserted = 0

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def set(self, key, value):
		self.__dict__[key] = value

	def get_password(self, fieldname, raise_exception=True):
		return self.__dict__.get(f"_secret_{fieldname}")

	def save(self, **kwargs):
		self.saved += 1

	def insert(self, **kwargs):
		self.inserted += 1
		self.__dict__.setdefault("name", "LOG-0001")

	def reload(self):
		pass

	def __getattr__(self, item):
		# Unset fields read as None, like on a real document
		return None


def response(status=200, data=None):
	r = MagicMock()
	r.status_code = status
	r.ok = 200 <= status < 300
	r.json.return_value = data if data is not None else {}
	r.text = json.dumps(data or {})
	return r


def mpesa_settings(**overrides):
	fields = dict(
		name="MPS-1",
		company="Shop A",
		environment="Sandbox",
		shortcode="174379",
		shortcode_type="Paybill",
		till_number=None,
		consumer_key="key",
		account_reference_prefix="",
		transaction_description=None,
		stk_callback_url=None,
		callback_token="tok123",
		_secret_consumer_secret="secret",
		_secret_passkey="pass",
	)
	fields.update(overrides)
	return FakeDoc(**fields)


class TestPhoneAndAmounts(unittest.TestCase):
	def test_phone_formats_are_normalised(self):
		for raw in ("0712345678", "0712 345 678", "+254712345678", "254712345678", "712345678"):
			self.assertEqual(payment_gateway_common.normalize_kenyan_phone(raw), "254712345678")
		self.assertEqual(payment_gateway_common.normalize_kenyan_phone("0110123456"), "254110123456")

	def test_bad_phone_numbers_are_refused(self):
		for raw in ("", "12345", "0212345678", "+12025550101"):
			with self.assertRaises(frappe.ValidationError):
				payment_gateway_common.normalize_kenyan_phone(raw)

	def test_mpesa_amount_rounds_up_to_whole_shillings(self):
		self.assertEqual(mpesa_client.stk_amount(100), 100)
		self.assertEqual(mpesa_client.stk_amount(100.2), 101)
		self.assertEqual(mpesa_client.stk_amount(0.1 + 0.2 + 99.7), 100)
		self.assertEqual(mpesa_client.stk_amount(0), 1)

	def test_password_and_timestamp(self):
		timestamp = mpesa_client.generate_timestamp()
		self.assertEqual(len(timestamp), 14)
		self.assertTrue(timestamp.isdigit())
		password = mpesa_client.generate_stk_password("174379", "pass", timestamp)
		self.assertEqual(base64.b64decode(password).decode(), f"174379pass{timestamp}")

	def test_callback_urls_avoid_words_daraja_rejects(self):
		with patch.object(
			payment_gateway_common, "get_callback_base_url", return_value="https://pos.example.com"
		):
			for method in ("daraja_stk_result", "daraja_c2b_confirmation", "daraja_c2b_validation"):
				url = payment_gateway_common.build_callback_url(method, "tok").lower()
				for word in payment_gateway_common.DARAJA_BLOCKED_URL_WORDS:
					self.assertNotIn(word, url, f"{word} in {url}")

	def test_callback_url_problems(self):
		self.assertIsNotNone(payment_gateway_common.callback_url_problem("http://pos.example.com/x"))
		self.assertIsNotNone(payment_gateway_common.callback_url_problem("https://localhost/x"))
		self.assertIsNone(payment_gateway_common.callback_url_problem("https://pos.example.com/x"))


class TestTenantIsolation(unittest.TestCase):
	def test_other_company_is_refused(self):
		with (
			patch.object(frappe, "session", frappe._dict(user="cashier@shop-a.test")),
			patch.object(payment_gateway_common, "get_user_companies", return_value={"Shop A"}),
			patch.object(frappe.db, "exists", return_value=True),
		):
			self.assertEqual(payment_gateway_common.resolve_company("Shop A"), "Shop A")
			with self.assertRaises(frappe.PermissionError):
				payment_gateway_common.resolve_company("Shop B")

	def test_only_managers_change_settings(self):
		with (
			patch.object(frappe, "session", frappe._dict(user="cashier@shop-a.test")),
			patch.object(frappe, "get_roles", return_value=["Sales User"]),
		):
			with self.assertRaises(frappe.PermissionError):
				payment_gateway_common.require_gateway_admin()
		with (
			patch.object(frappe, "session", frappe._dict(user="owner@shop-a.test")),
			patch.object(frappe, "get_roles", return_value=["Accounts Manager"]),
		):
			payment_gateway_common.require_gateway_admin()


class TestStkPush(unittest.TestCase):
	def _push(self, settings, post_response):
		log = FakeDoc()
		with (
			patch.object(mpesa_client, "get_settings", return_value=settings),
			patch.object(mpesa_client, "get_access_token", return_value="token"),
			patch.object(frappe, "new_doc", return_value=log),
			patch.object(frappe.db, "commit"),
			patch.object(mpesa_client.requests, "post", return_value=post_response) as post,
		):
			try:
				result = mpesa_client.initiate_stk_push("Shop A", "254712345678", 99.5, "SALE1")
			except mpesa_client.MpesaClientError as e:
				result = e
		return result, log, post

	def test_buy_goods_pays_the_till_with_the_store_number(self):
		settings = mpesa_settings(shortcode="123456", shortcode_type="BuyGoods", till_number="654321")
		with patch.object(
			payment_gateway_common, "get_callback_base_url", return_value="https://pos.example.com"
		):
			result, log, post = self._push(
				settings,
				response(
					200, {"ResponseCode": "0", "MerchantRequestID": "m1", "CheckoutRequestID": "ws_CO_1"}
				),
			)
		payload = post.call_args.kwargs["json"]
		self.assertEqual(payload["BusinessShortCode"], "123456")
		self.assertEqual(payload["PartyB"], "654321")
		self.assertEqual(payload["TransactionType"], "CustomerBuyGoodsOnline")
		self.assertEqual(payload["Amount"], 100)
		self.assertIn("t=tok123", payload["CallBackURL"])
		# The log was saved before the request and never stores the password
		self.assertEqual(log.inserted, 1)
		self.assertEqual(json.loads(log.request_payload)["Password"], "***")
		self.assertEqual(log.checkout_request_id, "ws_CO_1")
		self.assertEqual(result["amount"], 100)

	def test_paybill_uses_the_shortcode_and_account_prefix(self):
		settings = mpesa_settings(account_reference_prefix="SHOP")
		_result, _log, post = self._push(
			settings, response(200, {"ResponseCode": "0", "CheckoutRequestID": "x"})
		)
		payload = post.call_args.kwargs["json"]
		self.assertEqual(payload["PartyB"], "174379")
		self.assertEqual(payload["TransactionType"], "CustomerPayBillOnline")
		self.assertEqual(payload["AccountReference"], "SHOPSALE1")
		self.assertEqual(base64.b64decode(payload["Password"]).decode(), f"174379pass{payload['Timestamp']}")

	def test_refused_prompt_marks_the_log_failed(self):
		result, log, _post = self._push(
			mpesa_settings(), response(400, {"errorMessage": "Invalid Access Token"})
		)
		self.assertIsInstance(result, mpesa_client.MpesaAPIError)
		self.assertEqual(log.status, "Failed")
		self.assertIn("Invalid Access Token", log.error_message)


class TestStkQuery(unittest.TestCase):
	def _query(self, log, data, status=200):
		with (
			patch.object(mpesa_client, "get_settings", return_value=mpesa_settings()),
			patch.object(mpesa_client, "get_access_token", return_value="token"),
			patch.object(frappe.db, "get_value", return_value="LOG-1"),
			patch.object(frappe, "get_doc", return_value=log),
			patch.object(frappe.db, "commit"),
			patch.object(mpesa_client.requests, "post", return_value=response(status, data)),
		):
			return mpesa_client.query_stk_status("Shop A", "ws_CO_1")

	def test_customer_still_deciding_keeps_waiting(self):
		log = FakeDoc(name="LOG-1", status="Pending")
		result = self._query(log, {"errorCode": "500.001.1001", "errorMessage": "being processed"}, 500)
		self.assertTrue(result["still_processing"])
		self.assertEqual(log.status, "Pending")

	def test_cancelled_prompt(self):
		log = FakeDoc(name="LOG-1", status="Pending")
		result = self._query(log, {"ResultCode": "1032", "ResultDesc": "Request cancelled by user"})
		self.assertEqual(result["status"], "Cancelled")

	def test_callback_result_is_never_overwritten(self):
		log = FakeDoc(name="LOG-1", status="Success", mpesa_receipt_number="RCT1")
		self._query(log, {"ResultCode": "1037", "ResultDesc": "timeout"})
		self.assertEqual(log.status, "Success")


class TestStkCallback(unittest.TestCase):
	def _apply(self, log, callback):
		with (
			patch.object(frappe, "get_doc", return_value=log),
			patch.object(frappe.db, "commit"),
			patch.object(payment_callbacks, "settle_invoice_if_needed") as settle,
		):
			payment_callbacks.process_stk_callback("LOG-1", callback, {"Body": {"stkCallback": callback}})
		return settle

	def test_successful_payment_records_the_receipt(self):
		log = FakeDoc(name="LOG-1", status="Pending", amount=100)
		settle = self._apply(
			log,
			{
				"ResultCode": 0,
				"ResultDesc": "Processed",
				"CallbackMetadata": {
					"Item": [
						{"Name": "Amount", "Value": 100},
						{"Name": "MpesaReceiptNumber", "Value": "RCT9"},
					]
				},
			},
		)
		self.assertEqual(log.status, "Success")
		self.assertEqual(log.mpesa_receipt_number, "RCT9")
		settle.assert_called_once()

	def test_duplicate_delivery_changes_nothing(self):
		log = FakeDoc(name="LOG-1", status="Success", mpesa_receipt_number="RCT9", amount=100)
		self._apply(
			log,
			{
				"ResultCode": 0,
				"CallbackMetadata": {"Item": [{"Name": "MpesaReceiptNumber", "Value": "RCT9"}]},
			},
		)
		self.assertEqual(log.saved, 0)

	def test_wrong_pin_is_a_failure(self):
		log = FakeDoc(name="LOG-1", status="Pending", amount=100)
		self._apply(log, {"ResultCode": 2001, "ResultDesc": "The initiator information is invalid."})
		self.assertEqual(log.status, "Failed")


class TestPesapalStatus(unittest.TestCase):
	def _status(self, data):
		txn = FakeDoc(gateway_order_id="ord1", charged_amount=500, charged_currency="KES")
		with (
			patch.object(pesapal_client, "_auth_headers", return_value={}),
			patch.object(pesapal_client.requests, "get", return_value=response(200, data)),
		):
			return pesapal_client.fetch_status(mpesa_settings(), txn)

	def test_completed(self):
		result = self._status(
			{"status_code": 1, "amount": 500, "currency": "KES", "confirmation_code": "PSP1"}
		)
		self.assertEqual(result["status"], "Success")
		self.assertEqual(result["confirmation_code"], "PSP1")

	def test_completed_for_less_money_is_refused(self):
		self.assertEqual(
			self._status({"status_code": 1, "amount": 50, "currency": "KES"})["status"], "Failed"
		)

	def test_not_paid_yet(self):
		self.assertEqual(
			self._status({"status_code": 0, "payment_status_description": "INVALID"})["status"], "Pending"
		)

	def test_failed_and_reversed(self):
		self.assertEqual(self._status({"status_code": 2})["status"], "Failed")
		self.assertEqual(self._status({"status_code": 3})["status"], "Failed")


class TestPayPalStatus(unittest.TestCase):
	def _completed_order(self, value="12.50", currency="USD"):
		return {
			"id": "PP1",
			"status": "COMPLETED",
			"purchase_units": [
				{
					"payments": {
						"captures": [
							{
								"id": "CAP1",
								"status": "COMPLETED",
								"amount": {"value": value, "currency_code": currency},
							}
						]
					}
				}
			],
			"payer": {"email_address": "a@b.test", "name": {"given_name": "Ann", "surname": "W"}},
		}

	def _status(self, get_data, capture_response=None):
		txn = FakeDoc(
			gateway_order_id="PP1", merchant_reference="POS1", charged_amount=12.5, charged_currency="USD"
		)
		with (
			patch.object(paypal_client, "_headers", return_value={}),
			patch.object(paypal_client.requests, "get", return_value=response(200, get_data)),
			patch.object(paypal_client.requests, "post", return_value=capture_response) as post,
		):
			return paypal_client.fetch_status(mpesa_settings(), txn), post

	def test_approved_order_is_captured(self):
		result, post = self._status(
			{"id": "PP1", "status": "APPROVED"}, response(201, self._completed_order())
		)
		post.assert_called_once()
		self.assertEqual(result["status"], "Success")
		self.assertEqual(result["confirmation_code"], "CAP1")
		self.assertEqual(result["payer_name"], "Ann W")

	def test_waiting_for_customer(self):
		result, post = self._status({"id": "PP1", "status": "PAYER_ACTION_REQUIRED"})
		post.assert_not_called()
		self.assertEqual(result["status"], "Pending")

	def test_captured_amount_must_match(self):
		result, _post = self._status(self._completed_order(value="1.00"))
		self.assertEqual(result["status"], "Failed")

	def test_amount_formatting(self):
		self.assertEqual(paypal_client.format_amount(12.5, "USD"), "12.50")
		self.assertEqual(paypal_client.format_amount(1234.6, "JPY"), "1235")


class TestSaleValidation(unittest.TestCase):
	"""A sale can only record gateway payments that went through, belong to it and were not used before."""

	MODES: ClassVar[dict] = {
		"MPESA": {"gateway": "mpesa", "allow_manual_code": 0},
		"Pesapal": {"gateway": "pesapal"},
	}

	def _validate(self, rows, records=None, modes=None, receipt_lookup=None):
		records = records or {}

		def exists(doctype, name=None, *args, **kwargs):
			return name if name in records else None

		def get_doc(doctype, name):
			return records[name]

		with (
			patch.object(payment_gateway_api, "_gateway_modes", return_value=modes or self.MODES),
			patch.object(frappe.db, "exists", side_effect=exists),
			patch.object(frappe.db, "get_value", return_value=receipt_lookup),
			patch.object(frappe, "get_doc", side_effect=get_doc),
		):
			return payment_gateway_api.validate_gateway_payments(rows, "Shop A")

	def _mpesa(self, **fields):
		base = dict(
			name="LOG-1",
			company="Shop A",
			status="Success",
			invoice=None,
			amount=100,
			mpesa_receipt_number="RCT1",
		)
		base.update(fields)
		return FakeDoc(**base)

	def test_cash_and_card_rows_are_not_checked(self):
		self.assertEqual(self._validate([{"mode_of_payment": "Cash", "amount": 50}]), [])

	def test_successful_mpesa_payment_is_claimed(self):
		rows = [{"mode_of_payment": "MPESA", "amount": 99.5, "gateway_transaction": "LOG-1"}]
		claims = self._validate(rows, {"LOG-1": self._mpesa()})
		self.assertEqual(claims, [("MPESA Transaction Log", "LOG-1")])
		self.assertEqual(rows[0]["reference_no"], "RCT1")

	def test_mpesa_row_without_payment_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate([{"mode_of_payment": "MPESA", "amount": 100}])

	def test_used_pending_or_foreign_payments_are_refused(self):
		for record in (
			self._mpesa(invoice="POS-0001"),
			self._mpesa(status="Pending"),
			self._mpesa(company="Shop B"),
			self._mpesa(amount=50),
		):
			with self.assertRaises(frappe.ValidationError):
				self._validate(
					[{"mode_of_payment": "MPESA", "amount": 100, "gateway_transaction": "LOG-1"}],
					{"LOG-1": record},
				)

	def test_code_entered_by_cashier(self):
		row = {"mode_of_payment": "MPESA", "amount": 100, "reference_no": "sjk4h7q2xp"}
		with self.assertRaises(frappe.ValidationError):
			self._validate([dict(row)])
		modes = {"MPESA": {"gateway": "mpesa", "allow_manual_code": 1}}
		rows = [dict(row)]
		self.assertEqual(self._validate(rows, modes=modes), [])
		self.assertEqual(rows[0]["reference_no"], "SJK4H7Q2XP")

	def test_code_of_a_known_payment_is_claimed(self):
		rows = [{"mode_of_payment": "MPESA", "amount": 100, "reference_no": "RCT1"}]
		claims = self._validate(rows, {"LOG-1": self._mpesa()}, receipt_lookup="LOG-1")
		self.assertEqual(claims, [("MPESA Transaction Log", "LOG-1")])

	def test_same_payment_cannot_pay_twice(self):
		rows = [
			{"mode_of_payment": "MPESA", "amount": 50, "gateway_transaction": "LOG-1"},
			{"mode_of_payment": "MPESA", "amount": 50, "gateway_transaction": "LOG-1"},
		]
		with self.assertRaises(frappe.ValidationError):
			self._validate(rows, {"LOG-1": self._mpesa()})

	def test_pesapal_needs_a_successful_checkout(self):
		with self.assertRaises(frappe.ValidationError):
			self._validate([{"mode_of_payment": "Pesapal", "amount": 100}])
		txn = FakeDoc(
			name="PGT-1",
			company="Shop A",
			status="Success",
			invoice=None,
			amount=100,
			confirmation_code="PSP1",
			merchant_reference="POS1",
		)
		rows = [{"mode_of_payment": "Pesapal", "amount": 100, "gateway_transaction": "PGT-1"}]
		self.assertEqual(self._validate(rows, {"PGT-1": txn}), [("POS Gateway Transaction", "PGT-1")])
		self.assertEqual(rows[0]["reference_no"], "PSP1")


if __name__ == "__main__":
	unittest.main()
