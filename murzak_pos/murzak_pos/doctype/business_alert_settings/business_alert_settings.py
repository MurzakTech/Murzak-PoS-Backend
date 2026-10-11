# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from murzak_pos.api import alert_rules


class BusinessAlertSettings(Document):
	def validate(self):
		bad = alert_rules.invalid_recipients(self.email_recipients)
		if bad:
			frappe.throw(_("These are not valid email addresses: {0}").format(", ".join(bad)))
		problem = alert_rules.timing_problem(self.expiry_window_long, self.expiry_window_short, self.send_hour)
		if problem:
			frappe.throw(_(problem))
