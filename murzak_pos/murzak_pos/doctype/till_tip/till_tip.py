# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TillTip(Document):
	"""A tip a guest left, kept apart from the sale (it is the staff's money, not the business's takings). Written once
	and never changed; a mistake is put right by recording a tip the other way."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("A tip cannot be changed."))

	def on_trash(self):
		if frappe.session.user != "Administrator":
			frappe.throw(_("A tip cannot be deleted."))


def on_doctype_update():
	frappe.db.add_index("Till Tip", ["company", "recorded_at"], index_name="company_recorded_at")
