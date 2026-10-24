"""
Fields for selling online.

- Item "Show Online": the owner chooses which products appear on the website and the hosted store.
- Sales Order: where an online order came from, the website's own number for it (so the same order is never
  recorded twice), a private tracking code for the customer, how it reaches them, and where it is in fulfilment.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	order_fields = [
		{
			"fieldname": "custom_online_channel",
			"label": "Online Channel",
			"fieldtype": "Select",
			"options": "\nWebsite\nWooCommerce\nWhatsApp",
			"insert_after": "order_type",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "Where this order came from. Empty for sales made at the till.",
		},
		{
			"fieldname": "custom_online_ref",
			"label": "Online Order Reference",
			"fieldtype": "Data",
			"insert_after": "custom_online_channel",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "The website's own order number; stops the same order being recorded twice.",
		},
		{
			"fieldname": "custom_online_token",
			"label": "Tracking Code",
			"fieldtype": "Data",
			"insert_after": "custom_online_ref",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"description": "Private code the customer uses to see where their order is.",
		},
		{
			"fieldname": "custom_online_fulfilment",
			"label": "Online Order Status",
			"fieldtype": "Select",
			"options": "\nNew\nConfirmed\nReady\nDelivered\nCancelled",
			"insert_after": "custom_online_token",
			"no_copy": 1,
			"search_index": 1,
		},
		{
			"fieldname": "custom_delivery_method",
			"label": "Delivery Method",
			"fieldtype": "Select",
			"options": "\nDelivery\nPickup",
			"insert_after": "custom_online_fulfilment",
			"no_copy": 1,
		},
		{
			"fieldname": "custom_delivery_address",
			"label": "Delivery Address",
			"fieldtype": "Small Text",
			"insert_after": "custom_delivery_method",
			"no_copy": 1,
		},
		{
			"fieldname": "custom_online_payment",
			"label": "Online Payment",
			"fieldtype": "Data",
			"insert_after": "custom_delivery_address",
			"no_copy": 1,
			"description": "How the customer pays: M-Pesa, pay on delivery, or already paid on the website.",
		},
		{
			"fieldname": "custom_online_paid",
			"label": "Online Order Paid",
			"fieldtype": "Check",
			"insert_after": "custom_online_payment",
			"no_copy": 1,
		},
		{
			"fieldname": "custom_online_contact",
			"label": "Customer Phone",
			"fieldtype": "Data",
			"insert_after": "custom_online_paid",
			"no_copy": 1,
		},
	]
	item_fields = [
		{
			"fieldname": "custom_show_online",
			"label": "Show Online",
			"fieldtype": "Check",
			"default": "1",
			"insert_after": "is_sales_item",
			"description": "Untick to keep this product off the website and the online store.",
		}
	]
	create_custom_fields({"Sales Order": order_fields, "Item": item_fields}, update=True)

	# Products that existed before this field are shown, as they are today
	frappe.db.sql("update `tabItem` set custom_show_online = 1 where custom_show_online is null")
