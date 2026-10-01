# Copyright (c) 2024, Techsavanna POS and Contributors
# See license.txt

import frappe
import unittest
from unittest.mock import patch, MagicMock
from frappe.tests.utils import FrappeTestCase
from techsavanna_pos.api.mpesa_client import (
    generate_stk_password,
    generate_timestamp,
    get_access_token,
    initiate_stk_push,
    query_stk_status,
    MpesaClientError,
    MpesaAuthenticationError
)
from techsavanna_pos.api.mpesa_constants import (
    DARAJASANDBOX_BASE_URL,
    OAUTH_TOKEN_ENDPOINT
)


class TestMpesaClient(FrappeTestCase):
	def setUp(self):
		"""Set up test data"""
		# Create test company
		if not frappe.db.exists("Company", "_Test Company"):
			company = frappe.new_doc("Company")
			company.company_name = "_Test Company"
			company.abbr = "TC"
			company.default_currency = "KES"
			company.country = "Kenya"
			company.insert(ignore_permissions=True)
		
		# Create test account
		if not frappe.db.exists("Account", "Cash - TC"):
			account = frappe.new_doc("Account")
			account.account_name = "Cash"
			account.company = "_Test Company"
			account.parent_account = "Current Assets - TC"
			account.account_type = "Cash"
			account.insert(ignore_permissions=True)
		
		# Create test MPESA Settings
		if frappe.db.exists("MPESA Settings", {"company": "_Test Company"}):
			frappe.delete_doc("MPESA Settings", {"company": "_Test Company"}, force=True, ignore_permissions=True)
		
		settings = frappe.new_doc("MPESA Settings")
		settings.company = "_Test Company"
		settings.environment = "Sandbox"
		settings.shortcode = "174379"
		settings.shortcode_type = "Paybill"
		settings.consumer_key = "test_consumer_key"
		settings.consumer_secret = "test_consumer_secret"
		settings.passkey = "test_passkey"
		settings.payment_account = "Cash - TC"
		settings.stk_callback_url = "https://test.com/stk_callback"
		settings.is_active = 1
		settings.insert(ignore_permissions=True)
		frappe.db.commit()
	
	def test_generate_timestamp(self):
		"""Test timestamp generation"""
		timestamp = generate_timestamp()
		
		self.assertEqual(len(timestamp), 14)  # YYYYMMDDHHmmss
		self.assertTrue(timestamp.isdigit())
	
	def test_generate_stk_password(self):
		"""Test STK password generation"""
		shortcode = "174379"
		passkey = "test_passkey"
		timestamp = "20231219102036"
		
		password = generate_stk_password(shortcode, passkey, timestamp)
		
		# Should be Base64 encoded
		self.assertIsInstance(password, str)
		self.assertGreater(len(password), 0)
		
		# Verify it's Base64 decodable
		import base64
		try:
			decoded = base64.b64decode(password).decode()
			self.assertEqual(decoded, f"{shortcode}{passkey}{timestamp}")
		except Exception:
			self.fail("Generated password is not valid Base64")
	
	@patch('techsavanna_pos.api.mpesa_client.requests.get')
	def test_get_access_token_success(self, mock_get):
		"""Test successful OAuth token retrieval"""
		# Mock successful response
		mock_response = MagicMock()
		mock_response.json.return_value = {
			"access_token": "test_access_token_123",
			"expires_in": "3600"
		}
		mock_response.raise_for_status = MagicMock()
		mock_get.return_value = mock_response
		
		token = get_access_token("_Test Company")
		
		self.assertEqual(token, "test_access_token_123")
		
		# Verify settings were updated
		settings = frappe.get_doc("MPESA Settings", {"company": "_Test Company"})
		self.assertEqual(settings.access_token, "test_access_token_123")
		self.assertIsNotNone(settings.token_expiry)
	
	@patch('techsavanna_pos.api.mpesa_client.requests.get')
	def test_get_access_token_authentication_error(self, mock_get):
		"""Test OAuth token retrieval with invalid credentials"""
		# Mock 401 response
		mock_response = MagicMock()
		mock_response.status_code = 401
		mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
		mock_get.return_value = mock_response
		
		with self.assertRaises(MpesaAuthenticationError):
			get_access_token("_Test Company")
	
	@patch('techsavanna_pos.api.mpesa_client.requests.get')
	@patch('techsavanna_pos.api.mpesa_client.requests.post')
	def test_initiate_stk_push_success(self, mock_post, mock_get):
		"""Test successful STK Push initiation"""
		# Mock OAuth token response
		mock_token_response = MagicMock()
		mock_token_response.json.return_value = {
			"access_token": "test_token",
			"expires_in": "3600"
		}
		mock_token_response.raise_for_status = MagicMock()
		mock_get.return_value = mock_token_response
		
		# Mock STK Push response
		mock_stk_response = MagicMock()
		mock_stk_response.json.return_value = {
			"MerchantRequestID": "test_merchant_123",
			"CheckoutRequestID": "test_checkout_123",
			"ResponseCode": "0",
			"ResponseDescription": "Success",
			"CustomerMessage": "Success"
		}
		mock_stk_response.raise_for_status = MagicMock()
		mock_post.return_value = mock_stk_response
		
		result = initiate_stk_push(
			company="_Test Company",
			phone_number="254712345678",
			amount=100.00,
			reference="TEST-001",
			description="Test payment"
		)
		
		self.assertEqual(result["response_code"], 0)
		self.assertEqual(result["merchant_request_id"], "test_merchant_123")
		self.assertEqual(result["checkout_request_id"], "test_checkout_123")
		self.assertIn("transaction_id", result)
		
		# Verify transaction log was created
		transaction = frappe.get_doc("MPESA Transaction Log", result["transaction_id"])
		self.assertEqual(transaction.status, "Pending")
		self.assertEqual(transaction.amount, 100.00)
	
	def test_initiate_stk_push_invalid_phone(self):
		"""Test STK Push with invalid phone number"""
		with self.assertRaises(MpesaClientError):
			initiate_stk_push(
				company="_Test Company",
				phone_number="0712345678",  # Invalid format
				amount=100.00,
				reference="TEST-001"
            )
	
	@patch('techsavanna_pos.api.mpesa_client.requests.get')
	@patch('techsavanna_pos.api.mpesa_client.requests.post')
	def test_query_stk_status_success(self, mock_post, mock_get):
		"""Test successful STK status query"""
		# Create test transaction
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Pending"
		transaction.checkout_request_id = "test_checkout_123"
		transaction.insert(ignore_permissions=True)
		frappe.db.commit()
		
		# Mock OAuth token response
		mock_token_response = MagicMock()
		mock_token_response.json.return_value = {
			"access_token": "test_token",
			"expires_in": "3600"
		}
		mock_token_response.raise_for_status = MagicMock()
		mock_get.return_value = mock_token_response
		
		# Mock STK Query response
		mock_query_response = MagicMock()
		mock_query_response.json.return_value = {
			"ResultCode": "0",
			"ResultDesc": "The service request is processed successfully.",
			"MerchantRequestID": "test_merchant_123",
			"CheckoutRequestID": "test_checkout_123",
			"MpesaReceiptNumber": "RCT123456"
		}
		mock_query_response.raise_for_status = MagicMock()
		mock_post.return_value = mock_query_response
		
		result = query_stk_status("_Test Company", "test_checkout_123")
		
		self.assertEqual(result["result_code"], 0)
		self.assertEqual(result["mpesa_receipt_number"], "RCT123456")
		
		# Verify transaction was updated
		transaction.reload()
		self.assertEqual(transaction.status, "Success")
		self.assertEqual(transaction.mpesa_receipt_number, "RCT123456")
	
	def tearDown(self):
		"""Clean up test data"""
		frappe.db.delete("MPESA Transaction Log", {"company": "_Test Company"})
		frappe.db.delete("MPESA Settings", {"company": "_Test Company"})
		frappe.db.commit()

