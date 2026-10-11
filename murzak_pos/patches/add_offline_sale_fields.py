"""
Add two read-only fields to POS Invoice and Sales Invoice for sales made while the till was offline:
- pos_client_reference: the till's own id for the sale, used to never record the same sale twice;
- pos_offline_sold_at: when the customer actually paid, for sales uploaded later.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	fields = [
		{
			"fieldname": "pos_client_reference",
			"label": "Till Sale Reference",
			"fieldtype": "Data",
			"insert_after": "remarks",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "Id the till gave this sale; stops the same sale being recorded twice.",
		},
		{
			"fieldname": "pos_offline_sold_at",
			"label": "Sold Offline At",
			"fieldtype": "Datetime",
			"insert_after": "pos_client_reference",
			"read_only": 1,
			"no_copy": 1,
			"description": "When the sale was made at a till that had no connection.",
		},
	]
	create_custom_fields({"POS Invoice": fields, "Sales Invoice": fields}, update=True)
