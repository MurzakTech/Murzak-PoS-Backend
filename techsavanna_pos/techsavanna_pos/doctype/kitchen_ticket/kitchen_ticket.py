# Copyright (c) 2026, Techsavanna POS and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class KitchenTicket(Document):
	pass


def on_doctype_update():
	# The till gives every ticket an id of its own, so that sending it again after a dropped connection is
	# recognised. The database enforces that, so two requests saving the same new ticket at the same moment
	# cannot both create it.
	frappe.db.add_unique(
		"Kitchen Ticket", ["company", "client_id"], constraint_name="unique_company_client_id"
	)
	# A station screen asks for one station's open tickets every few seconds
	frappe.db.add_index(
		"Kitchen Ticket", ["company", "station", "status"], index_name="company_station_status"
	)
