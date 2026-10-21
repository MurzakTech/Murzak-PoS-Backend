import frappe


def execute():
	"""Ensure all active stock/sales items have is_purchase_item=1 so they can be bought on purchase orders/bills."""
	frappe.db.sql(
		"""
		UPDATE `tabItem`
		SET is_purchase_item = 1
		WHERE (is_purchase_item = 0 OR is_purchase_item IS NULL)
		  AND (is_stock_item = 1 OR is_sales_item = 1)
		"""
	)
