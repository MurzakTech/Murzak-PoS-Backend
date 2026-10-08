# Copyright (c) 2024, Techsavanna POS and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now


class TestMpesaTransactionLog(FrappeTestCase):
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
	
	def test_create_stk_transaction(self):
		"""Test creating STK Push transaction log"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Pending"
		transaction.checkout_request_id = "test_checkout_123"
		transaction.created_at = now()
		
		transaction.insert(ignore_permissions=True)
		
		self.assertEqual(transaction.transaction_type, "STK Push")
		self.assertEqual(transaction.status, "Pending")
		self.assertIsNotNone(transaction.checkout_request_id)
	
	def test_validate_phone_number_format(self):
		"""Test phone number format validation"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "0712345678"  # Invalid format
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Pending"
		
		with self.assertRaises(frappe.ValidationError):
			transaction.validate()
	
	def test_validate_stk_identifiers(self):
		"""A successful STK Push requires checkout_request_id"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Success"
		# Missing checkout_request_id

		with self.assertRaises(frappe.ValidationError):
			transaction.validate()

	def test_pending_stk_without_identifiers_is_allowed(self):
		"""The log is written before Safaricom answers, so a pending record has no ids yet"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Pending"

		transaction.validate()
	
	def test_validate_b2c_identifiers(self):
		"""Test B2C requires conversation_id"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "B2C"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Success"
		# Missing conversation_id
		
		with self.assertRaises(frappe.ValidationError):
			transaction.validate()
	
	def test_validate_invoice_link(self):
		"""Test invoice link validation"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Pending"
		transaction.checkout_request_id = "test_checkout_123"
		transaction.invoice = "TEST-INV-001"
		# Missing invoice_type
		
		with self.assertRaises(frappe.ValidationError):
			transaction.validate()
	
	def test_auto_set_created_at(self):
		"""Test that created_at is auto-set on insert"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.checkout_request_id = "test_checkout_123"
		
		# Don't set created_at
		transaction.insert(ignore_permissions=True)
		
		self.assertIsNotNone(transaction.created_at)
	
	def test_update_completed_at_on_status_change(self):
		"""Test that completed_at is set when status changes"""
		transaction = frappe.new_doc("MPESA Transaction Log")
		transaction.company = "_Test Company"
		transaction.transaction_type = "STK Push"
		transaction.phone_number = "254712345678"
		transaction.amount = 100.00
		transaction.reference_number = "TEST-001"
		transaction.status = "Pending"
		transaction.checkout_request_id = "test_checkout_123"
		transaction.insert(ignore_permissions=True)
		
		# Update status to Success
		transaction.status = "Success"
		transaction.save(ignore_permissions=True)
		
		self.assertIsNotNone(transaction.completed_at)
	
	def tearDown(self):
		"""Clean up test data"""
		frappe.db.delete("MPESA Transaction Log", {"company": "_Test Company"})
		frappe.db.commit()

