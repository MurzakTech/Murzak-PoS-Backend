"""Override POS Invoice to preserve update_stock from API"""
import frappe
from frappe.model.document import Document
from frappe.utils import cint


def before_submit(doc: Document, method: str = None) -> None:
    """Always ensure update_stock = 1 for POS invoices before submit"""
    # For POS invoices, always ensure update_stock is set to 1
    # This runs right before submit, after all validation, so it will override any changes
    # made by set_pos_fields() during validation
    
    # Check if this is a POS invoice and if update_stock needs to be set
    if doc.doctype == "POS Invoice":
        # Always set to 1 - this ensures stock is updated
        old_value = doc.update_stock
        doc.update_stock = 1
        # Also update in database to ensure it's persisted
        if doc.name:
            frappe.db.set_value(doc.doctype, doc.name, "update_stock", 1, update_modified=False)
        
        # Log for debugging
        frappe.logger().info(
            f"POS Invoice {doc.name}: before_submit hook - update_stock set to 1 (was {old_value}), "
            f"docstatus={doc.docstatus}, items_count={len(doc.items)}"
        )
        
        # Verify items have warehouses
        for item in doc.items:
            if not item.warehouse:
                frappe.logger().warning(
                    f"POS Invoice {doc.name}: Item {item.item_code} has no warehouse!"
                )

