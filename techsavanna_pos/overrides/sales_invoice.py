"""Override Sales Invoice to preserve update_stock from API"""
import frappe
from frappe.model.document import Document
from frappe.utils import cint


def before_submit(doc: Document, method: str = None) -> None:
    """Always ensure update_stock = 1 for POS Sales Invoices before submit"""
    # Only enforce for POS Sales Invoices
    if not doc.is_pos or doc.doctype != "Sales Invoice":
        return
    
    # Always set to 1 for POS Sales Invoices - this ensures stock is updated
    # This runs right before submit, after all validation, so it will override any changes
    # made by set_pos_fields() during validation
    old_value = doc.update_stock
    doc.update_stock = 1
    # Also update in database to ensure it's persisted
    if doc.name:
        frappe.db.set_value(doc.doctype, doc.name, "update_stock", 1, update_modified=False)
    
    # Log for debugging
    if old_value != 1:
        frappe.logger().info(
            f"Sales Invoice {doc.name}: update_stock changed from {old_value} to 1 in before_submit hook"
        )

