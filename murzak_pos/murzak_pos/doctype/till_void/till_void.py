# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TillVoid(Document):
	"""An item taken off a bill after the kitchen or bar already had it, with the reason and the manager who approved
	it. Written once and never changed or removed, because it is the owner's check on staff."""

	def validate(self):
		if not self.is_new():
			frappe.throw(_("A void cannot be changed."))

	def on_trash(self):
		if frappe.session.user != "Administrator":
			frappe.throw(_("A void cannot be deleted."))


def on_doctype_update():
	frappe.db.add_index("Till Void", ["company", "voided_at"], index_name="company_voided_at")
