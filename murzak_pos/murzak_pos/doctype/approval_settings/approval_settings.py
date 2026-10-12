# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ApprovalSettings(Document):
	def validate(self):
		if flt(self.refund_limit) < 0 or flt(self.purchase_order_limit) < 0:
			frappe.throw(_("An approval limit cannot be below zero."))
