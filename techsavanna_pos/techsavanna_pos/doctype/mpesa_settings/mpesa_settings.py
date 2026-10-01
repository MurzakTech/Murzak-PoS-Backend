# Copyright (c) 2024, Techsavanna POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
import re


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
				"MPESA Settings",
				{
					"company": self.company,
					"is_active": 1,
					"name": ["!=", self.name]
				}
			)
			if existing:
				frappe.throw(
					_("An active MPESA Settings record already exists for company {0}").format(self.company),
					frappe.ValidationError
				)
	
	def validate_shortcode(self):
		"""Validate shortcode format"""
		if not self.shortcode:
			return
		
		# Shortcode should be numeric
		if not self.shortcode.isdigit():
			frappe.throw(
				_("Shortcode must be numeric"),
				frappe.ValidationError
			)
		
		# Validate shortcode type matches format
		if self.shortcode_type == "Paybill":
			# Paybill typically 6 digits
			if len(self.shortcode) != 6:
				frappe.throw(
					_("Paybill shortcode should be 6 digits"),
					frappe.ValidationError
				)
		elif self.shortcode_type == "BuyGoods":
			# BuyGoods (Till number) typically 5-6 digits
			if len(self.shortcode) < 5 or len(self.shortcode) > 6:
				frappe.throw(
					_("BuyGoods (Till) shortcode should be 5-6 digits"),
					frappe.ValidationError
				)
	
	def validate_callback_urls(self):
		"""Validate callback URLs are HTTPS"""
		url_fields = [
			"stk_callback_url",
			"b2c_result_url",
			"b2c_timeout_url",
			"b2b_result_url",
			"b2b_timeout_url"
		]
		
		for field in url_fields:
			url = self.get(field)
			if url and not url.startswith("https://"):
				frappe.throw(
					_("{0} must be an HTTPS URL").format(frappe.unscrub(field)),
					frappe.ValidationError
				)
	
	def validate_phone_number(self):
		"""Validate test phone number format if provided"""
		if self.test_phone_number:
			# MSISDN format: 254712345678 (country code + number)
			phone_pattern = r'^254\d{9}$'
			if not re.match(phone_pattern, self.test_phone_number):
				frappe.throw(
					_("Test phone number must be in MSISDN format (e.g., 254712345678)"),
					frappe.ValidationError
				)
	
	def before_save(self):
		"""Clear token if credentials changed"""
		# If this is an update and credentials changed, clear cached token
		if not self.is_new():
			old_doc = self.get_doc_before_save()
			if old_doc:
				credentials_changed = (
					old_doc.consumer_key != self.consumer_key or
					old_doc.consumer_secret != self.consumer_secret
				)
				if credentials_changed:
					self.access_token = None
					self.token_expiry = None
					self.last_token_refresh = None

