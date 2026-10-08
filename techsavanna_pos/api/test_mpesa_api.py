# Copyright (c) 2024, Techsavanna POS and Contributors
# See license.txt

import frappe
import json
from unittest.mock import patch, MagicMock
from frappe.tests.utils import FrappeTestCase
from frappe.test_runner import make_test_records


class TestMpesaAPI(FrappeTestCase):
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
		
		# Set test user
		frappe.set_user("Administrator")
	
	@patch('techsavanna_pos.api.mpesa_api.initiate_stk_push')
	def test_initiate_stk_push_payment_api(self, mock_initiate):
		"""Test STK Push payment API endpoint"""
		from techsavanna_pos.api.mpesa_api import initiate_stk_push_payment
		
		# Mock successful STK push
		mock_initiate.return_value = {
			"merchant_request_id": "test_merchant_123",
			"checkout_request_id": "test_checkout_123",
			"response_code": 0,
			"response_description": "Success",
			"transaction_id": "MPESA-TXN-001"
		}
		
		result = initiate_stk_push_payment(
			company="_Test Company",
			phone_number="254712345678",
			amount=100.00,
			reference="TEST-001"
		)
		
		self.assertTrue(result["success"])
		self.assertEqual(result["transaction"]["status"], "Pending")
		mock_initiate.assert_called_once()
	
	def test_register_mpesa_settings_api(self):
		"""Test register MPESA settings API"""
		from techsavanna_pos.api.mpesa_api import register_mpesa_settings
		
		# Delete existing settings
		frappe.db.delete("MPESA Settings", {"company": "_Test Company"})
		frappe.db.commit()
		
		result = register_mpesa_settings(
			company="_Test Company",
			environment="Sandbox",
			shortcode="174379",
			shortcode_type="Paybill",
			consumer_key="new_key",
			consumer_secret="new_secret",
			passkey="new_passkey",
			payment_account="Cash - TC",
			stk_callback_url="https://test.com/callback"
		)
		
		self.assertTrue(result["success"])
		self.assertEqual(result["settings"]["company"], "_Test Company")
		
		# Verify settings were created
		settings = frappe.get_doc("MPESA Settings", {"company": "_Test Company"})
		self.assertEqual(settings.environment, "Sandbox")
	
	def test_get_mpesa_settings_api(self):
		"""Test get MPESA settings API"""
		from techsavanna_pos.api.mpesa_api import get_mpesa_settings
		
		result = get_mpesa_settings(company="_Test Company")
		
		self.assertTrue(result["success"])
		self.assertEqual(result["settings"]["company"], "_Test Company")
		# Verify sensitive fields are masked
		self.assertEqual(result["settings"]["consumer_key"], "***")
		self.assertEqual(result["settings"]["consumer_secret"], "***")
	
	def test_get_transactions_api(self):
		"""Test get transactions API"""
		from techsavanna_pos.api.mpesa_api import get_transactions
		
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
		
		result = get_transactions(company="_Test Company")
		
		self.assertTrue(result["success"])
		self.assertGreaterEqual(len(result["transactions"]), 1)
		self.assertEqual(result["transactions"][0]["reference_number"], "TEST-001")
	
	def test_get_transaction_api(self):
		"""Test get single transaction API"""
		from techsavanna_pos.api.mpesa_api import get_transaction
		
		# Create test transaction
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Success"
		transaction.checkout_request_id = "test_checkout_123"
		transaction.request_payload = json.dumps({"test": "data"})
		transaction.insert(ignore_permissions=True)
		frappe.db.commit()
		
		result = get_transaction(transaction.name)
		
		self.assertTrue(result["success"])
		self.assertEqual(result["transaction"]["reference_number"], "TEST-001")
		# Verify payloads are parsed
		self.assertIsInstance(result["transaction"]["request_payload"], dict)
	
	def test_stk_callback_idempotency(self):
		"""Test STK callback idempotency"""
		from techsavanna_pos.api.payment_callbacks import process_stk_callback
		
		# Create test transaction
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Success"  # Already processed
		transaction.checkout_request_id = "test_checkout_123"
		transaction.insert(ignore_permissions=True)
		frappe.db.commit()
		
		# Mock callback data
		callback_data = {
			"Body": {
				"stkCallback": {
					"CheckoutRequestID": "test_checkout_123",
					"ResultCode": 0,
					"ResultDesc": "Success",
					"CallbackMetadata": {
						"Item": [
							{"Name": "MpesaReceiptNumber", "Value": "RCT123456"},
							{"Name": "Amount", "Value": 100},
							{"Name": "PhoneNumber", "Value": "254712345678"}
						]
					}
				}
			}
		}
		
		# Delivering the callback twice records the receipt once and changes nothing else
		callback = callback_data["Body"]["stkCallback"]
		process_stk_callback(transaction.name, callback, callback_data)
		process_stk_callback(transaction.name, callback, callback_data)

		transaction.reload()
		self.assertEqual(transaction.status, "Success")
		self.assertEqual(transaction.mpesa_receipt_number, "RCT123456")
	
	def tearDown(self):
		"""Clean up test data"""
		frappe.set_user("Administrator")
		frappe.db.delete("MPESA Transaction Log", {"company": "_Test Company"})
		frappe.db.delete("MPESA Settings", {"company": "_Test Company"})
		frappe.db.commit()

