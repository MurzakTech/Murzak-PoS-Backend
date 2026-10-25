# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TillCashMovement(Document):
	"""Cash put into or taken out of a till during a shift. Written once and never changed or removed, because the
	shift-end count depends on it; a mistake is put right with a movement the other way."""

	def validate(self):
		if not self.is_new():
			frappe.throw(
				_("A cash movement cannot be changed. Record a movement the other way to correct it.")
			)

	def on_trash(self):
		if frappe.session.user != "Administrator":
			frappe.throw(_("A cash movement cannot be deleted."))


def on_doctype_update():
	# The till gives every movement an id of its own, so that sending it again is recognised; the database
	# enforces it so two requests cannot both record the same movement
	frappe.db.add_unique(
		"Till Cash Movement", ["company", "client_id"], constraint_name="unique_company_client_id"
	)
	frappe.db.add_index("Till Cash Movement", ["pos_opening_entry", "kind"], index_name="shift_kind")
