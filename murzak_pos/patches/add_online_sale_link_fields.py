"""
Fields that tie a till sale to the online order it settles, in both directions.

- POS Invoice and Sales Invoice: the online order the sale settles, and the channel ("Till" or "Online") for reports.
- Sales Order: the sale that settled it, so the same order cannot be sold twice at the till.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	sale_fields = [
		{
			"fieldname": "custom_online_order",
			"label": "Online Order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"insert_after": "pos_offline_sold_at",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "The online order this sale settled at the till.",
		},
		{
			"fieldname": "custom_sales_channel",
			"label": "Sales Channel",
			"fieldtype": "Select",
			"options": "\nTill\nOnline",
			"insert_after": "custom_online_order",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "Online when the sale settled an online order. Empty means the till.",
		},
	]
	order_fields = [
		{
			"fieldname": "custom_till_sale_type",
			"label": "Settled By (Type)",
			"fieldtype": "Data",
			"insert_after": "custom_online_contact",
			"read_only": 1,
			"no_copy": 1,
		},
		{
			"fieldname": "custom_till_sale",
			"label": "Settled By (Till Sale)",
			"fieldtype": "Data",
			"insert_after": "custom_till_sale_type",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "The till sale that took payment and handed over this order.",
		},
	]
	create_custom_fields(
		{"POS Invoice": sale_fields, "Sales Invoice": sale_fields, "Sales Order": order_fields}, update=True
	)
