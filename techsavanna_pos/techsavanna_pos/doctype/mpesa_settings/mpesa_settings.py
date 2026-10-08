# Copyright (c) 2024, Techsavanna POS and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


class MpesaSettings(Document):
	def validate(self):
		"""Validate MPESA Settings"""
		self.validate_company_uniqueness()
		self.validate_shortcode()
		self.validate_callback_urls()
		self.validate_phone_number()

	def validate_company_uniqueness(self):
		"""Ensure only one active MPESA setting per company"""
		if self.is_active:
			existing = frappe.db.exists(
				"MPESA Settings", {"company": self.company, "is_active": 1, "name": ["!=", self.name]}
			)
			if existing:
				frappe.throw(
					_("An active MPESA Settings record already exists for company {0}").format(self.company),
					frappe.ValidationError,
				)

	def validate_shortcode(self):
		"""Shortcodes and till numbers are 5 to 7 digit numbers"""
		for fieldname, label in (("shortcode", _("Business Shortcode")), ("till_number", _("Till Number"))):
			value = (self.get(fieldname) or "").strip()
			self.set(fieldname, value)
			if not value:
				continue
			if not value.isdigit() or not 5 <= len(value) <= 7:
				frappe.throw(_("{0} must be a 5 to 7 digit number").format(label), frappe.ValidationError)

	def validate_callback_urls(self):
		"""Validate callback URLs are HTTPS"""
		url_fields = [
			"stk_callback_url",
			"b2c_result_url",
			"b2c_timeout_url",
			"b2b_result_url",
			"b2b_timeout_url",
		]

		for field in url_fields:
			url = self.get(field)
			if url and not url.startswith("https://"):
				frappe.throw(
					_("{0} must be an HTTPS URL").format(frappe.unscrub(field)), frappe.ValidationError
				)

	def validate_phone_number(self):
		"""Validate test phone number format if provided"""
		if self.test_phone_number:
			# MSISDN format: 254712345678 (country code + number)
			phone_pattern = r"^254\d{9}$"
			if not re.match(phone_pattern, self.test_phone_number):
				frappe.throw(
					_("Test phone number must be in MSISDN format (e.g., 254712345678)"),
					frappe.ValidationError,
				)

	def before_insert(self):
		self.ensure_callback_token()

	def before_save(self):
		"""Give every company its own callback token and drop any cached Daraja token"""
		self.ensure_callback_token()
		frappe.cache().delete_value(f"techsavanna_pos:daraja_token:{self.name}:Sandbox")
		frappe.cache().delete_value(f"techsavanna_pos:daraja_token:{self.name}:Production")

	def ensure_callback_token(self):
		if not self.callback_token:
			from techsavanna_pos.api.payment_gateway_common import new_callback_token

			self.callback_token = new_callback_token()
