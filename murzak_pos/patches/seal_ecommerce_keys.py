"""Encrypt the WooCommerce keys already saved in plain text (see ecommerce_api.seal)."""

import frappe


def execute():
	from murzak_pos.api.ecommerce_api import _ecommerce_key, read_settings, write_settings

	for company in frappe.get_all("Company", pluck="name"):
		if frappe.db.get_default(_ecommerce_key(company)):
			write_settings(company, read_settings(company))
