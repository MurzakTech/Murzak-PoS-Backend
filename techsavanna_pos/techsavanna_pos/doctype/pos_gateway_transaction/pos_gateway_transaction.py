# Copyright (c) 2026, Techsavanna POS and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import now


class POSGatewayTransaction(Document):
	def before_insert(self):
		if not self.created_at:
			self.created_at = now()

	def before_save(self):
		if self.status != "Pending" and not self.completed_at:
			self.completed_at = now()
