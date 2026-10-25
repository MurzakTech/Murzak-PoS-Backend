# Copyright (c) 2026, Murzak POS and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ClockEntry(Document):
	"""One stretch of work: the time a person clocked in and, later, out. Written through
	murzak_pos.api.rota_api only. Never deleted, because it is the record hours are paid from."""

	def on_trash(self):
		if frappe.session.user != "Administrator":
			frappe.throw(_("A clock entry cannot be deleted."))


def on_doctype_update():
	# While an entry is open its open_key is the person's name, and nobody can have two open entries: the database
	# enforces it, so two quick taps cannot both clock a person in. A closed entry has an empty key (NULLs do not clash).
	frappe.db.add_unique("Clock Entry", ["company", "open_key"], constraint_name="unique_company_open_key")
	frappe.db.add_index("Clock Entry", ["company", "user", "clock_in"], index_name="person_time")
