# Copyright (c) 2024, Techsavanna POS and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


class MpesaTransactionLog(Document):
	def validate(self):
		"""Validate MPESA Transaction Log"""
		self.validate_phone_number()
		self.validate_mpesa_identifiers()
		self.validate_invoice_link()

	def validate_phone_number(self):
		"""Validate phone number format if provided"""
		if self.phone_number:
			# MSISDN format: 254712345678 (country code + number)
			phone_pattern = r"^254\d{9}$"
			if not re.match(phone_pattern, self.phone_number):
				frappe.throw(
					_("Phone number must be in MSISDN format (e.g., 254712345678)"), frappe.ValidationError
				)

	def validate_mpesa_identifiers(self):
		"""A successful payment must carry Safaricom's identifiers.

		Pending and failed records may not have them yet: the log is written before the
		request is sent, so a request that never reached Safaricom is still recorded.
		"""
		if self.status != "Success":
			return
		if self.transaction_type == "STK Push" and not self.checkout_request_id:
			frappe.throw(
				_("Checkout Request ID is required for successful STK Push transactions"),
				frappe.ValidationError,
			)
		elif self.transaction_type in ["B2C", "B2B"] and not self.conversation_id:
			frappe.throw(
				_("Conversation ID is required for {0} transactions").format(self.transaction_type),
				frappe.ValidationError,
			)
		elif self.transaction_type == "C2B" and not self.mpesa_receipt_number:
			frappe.throw(_("M-Pesa receipt number is required for C2B transactions"), frappe.ValidationError)

	def validate_invoice_link(self):
		"""Validate invoice link if provided"""
		if self.invoice and not self.invoice_type:
			frappe.throw(_("Invoice Type is required when Invoice is specified"), frappe.ValidationError)

		if self.invoice_type and not self.invoice:
			frappe.throw(_("Invoice is required when Invoice Type is specified"), frappe.ValidationError)

	def before_insert(self):
		"""Set default values on insert"""
		if not self.status:
			self.status = "Pending"
		if not self.created_at:
			from frappe.utils import now

			self.created_at = now()

	def before_save(self):
		"""Stamp completed_at when the payment reaches a final state"""
		if self.status in ["Success", "Failed", "Cancelled"] and not self.completed_at:
			from frappe.utils import now

			self.completed_at = now()
