"""
Stock Entry overrides for TechSavanna POS
Handles Stock Entry submission without eTIMS/Slade360 integration
"""
import frappe
from frappe import _
from frappe.model.document import Document


def on_submit(doc: Document, method: str = None) -> None:
    """
    After Stock Entry submission hook.
    
    This hook runs after a Stock Entry is submitted.
    Used for any post-submission operations like logging or notifications.
    
    Note: Stock Ledger Entries are automatically created by ERPNext's
    standard Stock Entry submission process.
    """
    # Log successful stock entry for debugging
    if doc.stock_entry_type == "Material Receipt":
        frappe.logger().info(
            f"Stock Entry {doc.name}: Material Receipt submitted successfully. "
            f"Company: {doc.company}, Items: {len(doc.items)}"
        )
    elif doc.stock_entry_type == "Material Transfer":
        frappe.logger().info(
            f"Stock Entry {doc.name}: Material Transfer submitted successfully. "
            f"Company: {doc.company}, Items: {len(doc.items)}"
        )
    elif doc.stock_entry_type == "Material Issue":
        frappe.logger().info(
            f"Stock Entry {doc.name}: Material Issue submitted successfully. "
            f"Company: {doc.company}, Items: {len(doc.items)}"
        )


def validate(doc: Document, method: str = None) -> None:
    """
    Validate Stock Entry before save.
    
    Ensures all required fields are properly set.
    """
    # Ensure company is set
    if not doc.company:
        frappe.throw(_("Company is required for Stock Entry"))
    
    # Validate items have required fields
    for item in doc.items:
        # For Material Receipt, target warehouse is required
        if doc.stock_entry_type == "Material Receipt" and not item.t_warehouse:
            frappe.throw(
                _("Target Warehouse is required for item {0} in Material Receipt").format(
                    item.item_code
                )
            )
        
        # For Material Issue, source warehouse is required
        if doc.stock_entry_type == "Material Issue" and not item.s_warehouse:
            frappe.throw(
                _("Source Warehouse is required for item {0} in Material Issue").format(
                    item.item_code
                )
            )
        
        # For Material Transfer, both warehouses are required
        if doc.stock_entry_type == "Material Transfer":
            if not item.s_warehouse:
                frappe.throw(
                    _("Source Warehouse is required for item {0} in Material Transfer").format(
                        item.item_code
                    )
                )
            if not item.t_warehouse:
                frappe.throw(
                    _("Target Warehouse is required for item {0} in Material Transfer").format(
                        item.item_code
                    )
                )


def on_cancel(doc: Document, method: str = None) -> None:
    """
    After Stock Entry cancellation hook.
    
    Placeholder for any post-cancellation operations.
    """
    frappe.logger().info(
        f"Stock Entry {doc.name}: Cancelled. Type: {doc.stock_entry_type}"
    )

