# Copyright (c) 2026, Techsavanna POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class POSGatewaySettings(Document):
	def validate(self):
		self.validate_one_per_company()
		if self.gateway == "PayPal" and not self.currency:
			self.currency = "USD"
		if self.gateway == "Pesapal" and not self.currency:
			self.currency = frappe.get_cached_value("Company", self.company, "default_currency") or "KES"

	def validate_one_per_company(self):
		existing = frappe.db.exists(
			"POS Gateway Settings",
			{"company": self.company, "gateway": self.gateway, "name": ["!=", self.name]},
		)
		if existing:
			frappe.throw(
				_("{0} is already set up for {1}").format(self.gateway, self.company), frappe.ValidationError
			)

	def before_save(self):
		if not self.callback_token:
			from techsavanna_pos.api.payment_gateway_common import new_callback_token

			self.callback_token = new_callback_token()
		# New keys or a new environment need a fresh sign-in and, for Pesapal, a new IPN registration
		before = self.get_doc_before_save()
		if before and (before.environment != self.environment or before.client_id != self.client_id):
			self.ipn_id = None
			self.ipn_url = None
		frappe.cache().delete_value(f"techsavanna_pos:gateway_token:{self.name}")
