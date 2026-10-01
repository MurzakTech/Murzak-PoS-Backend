# Copyright (c) 2024, Techsavanna POS and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe import _


class TestMpesaSettings(FrappeTestCase):
	def setUp(self):
		"""Set up test data"""
		# Create test company if not exists
		if not frappe.db.exists("Company", "_Test Company"):
			company = frappe.new_doc("Company")
			company.company_name = "_Test Company"
			company.abbr = "TC"
			company.default_currency = "KES"
			company.country = "Kenya"
			company.insert(ignore_permissions=True)
		
		# Create test account if not exists
		if not frappe.db.exists("Account", "Cash - TC"):
			account = frappe.new_doc("Account")
			account.account_name = "Cash"
			account.company = "_Test Company"
			account.parent_account = "Current Assets - TC"
			account.account_type = "Cash"
			account.insert(ignore_permissions=True)
	
	def test_create_mpesa_settings(self):
		"""Test creating MPESA Settings"""
		settings = frappe.new_doc("MPESA Settings")
		settings.company = "_Test Company"
		settings.environment = "Sandbox"
		settings.shortcode = "174379"
		settings.shortcode_type = "Paybill"
		settings.consumer_key = "test_key"
		settings.consumer_secret = "test_secret"
		settings.passkey = "test_passkey"
		settings.payment_account = "Cash - TC"
		settings.stk_callback_url = "https://test.com/callback"
		
		settings.insert(ignore_permissions=True)
		
		self.assertEqual(settings.company, "_Test Company")
		self.assertEqual(settings.environment, "Sandbox")
		self.assertTrue(settings.is_active)
	
	def test_validate_shortcode_format(self):
		"""Test shortcode format validation"""
		settings = frappe.new_doc("MPESA Settings")
		settings.company = "_Test Company"
		settings.environment = "Sandbox"
		settings.shortcode = "abc123"  # Invalid - not numeric
		settings.shortcode_type = "Paybill"
		settings.consumer_key = "test_key"
		settings.consumer_secret = "test_secret"
		settings.passkey = "test_passkey"
		settings.payment_account = "Cash - TC"
		
		with self.assertRaises(frappe.ValidationError):
			settings.validate()
	
	def test_validate_callback_urls(self):
		"""Test callback URL validation (must be HTTPS)"""
		settings = frappe.new_doc("MPESA Settings")
		settings.company = "_Test Company"
		settings.environment = "Sandbox"
		settings.shortcode = "174379"
		settings.shortcode_type = "Paybill"
		settings.consumer_key = "test_key"
		settings.consumer_secret = "test_secret"
		settings.passkey = "test_passkey"
		settings.payment_account = "Cash - TC"
		settings.stk_callback_url = "http://test.com/callback"  # Invalid - not HTTPS
		
		with self.assertRaises(frappe.ValidationError):
			settings.validate()
	
	def test_validate_phone_number_format(self):
		"""Test phone number format validation"""
		settings = frappe.new_doc("MPESA Settings")
		settings.company = "_Test Company"
		settings.environment = "Sandbox"
		settings.shortcode = "174379"
		settings.shortcode_type = "Paybill"
		settings.consumer_key = "test_key"
		settings.consumer_secret = "test_secret"
		settings.passkey = "test_passkey"
		settings.payment_account = "Cash - TC"
		settings.test_phone_number = "0712345678"  # Invalid format
		
		with self.assertRaises(frappe.ValidationError):
			settings.validate()
	
	def test_company_uniqueness(self):
		"""Test that only one active MPESA setting per company"""
		# Create first settings
		settings1 = frappe.new_doc("MPESA Settings")
		settings1.company = "_Test Company"
		settings1.environment = "Sandbox"
		settings1.shortcode = "174379"
		settings1.shortcode_type = "Paybill"
		settings1.consumer_key = "test_key"
		settings1.consumer_secret = "test_secret"
		settings1.passkey = "test_passkey"
		settings1.payment_account = "Cash - TC"
		settings1.is_active = 1
		settings1.insert(ignore_permissions=True)
		
		# Try to create second active settings for same company
		settings2 = frappe.new_doc("MPESA Settings")
		settings2.company = "_Test Company"
		settings2.environment = "Sandbox"
		settings2.shortcode = "174380"
		settings2.shortcode_type = "Paybill"
		settings2.consumer_key = "test_key2"
		settings2.consumer_secret = "test_secret2"
		settings2.passkey = "test_passkey2"
		settings2.payment_account = "Cash - TC"
		settings2.is_active = 1
		
		with self.assertRaises(frappe.ValidationError):
			settings2.validate()
		
		# Cleanup
		settings1.delete(ignore_permissions=True)
	
	def tearDown(self):
		"""Clean up test data"""
		# Delete test MPESA Settings
		frappe.db.delete("MPESA Settings", {"company": "_Test Company"})
		frappe.db.commit()

