# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class PlatformPayout(Document):
	"""A delivery platform's payout (Glovo, Uber Eats, Bolt Food) matched against the orders it settles. Written once
	and never changed; the orders it settles point back to it (custom_channel_payout)."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("A platform payout cannot be changed."))

	def on_trash(self):
		if frappe.session.user != "Administrator":
			frappe.throw(_("A platform payout cannot be deleted."))


def on_doctype_update():
	frappe.db.add_index("Platform Payout", ["company", "payout_date"], index_name="company_payout_date")
