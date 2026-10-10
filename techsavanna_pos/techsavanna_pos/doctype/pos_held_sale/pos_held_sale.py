# Copyright (c) 2026, Techsavanna POS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class POSHeldSale(Document):
	pass


def on_doctype_update():
	# A till's bill id only has to be unique within its own business. The database enforces it, so two
	# requests saving the same new bill at the same moment cannot both create it.
	frappe.db.add_unique("POS Held Sale", ["company", "held_id"], constraint_name="unique_company_held_id")
