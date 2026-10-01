"""
Item overrides for TechSavanna POS
Handles Item validation without eTIMS/Slade360 integration
"""
import frappe
from frappe import _
from frappe.model.document import Document


def validate(doc: Document, method: str = None) -> None:
    """
    Validate Item document.
    This is a lightweight validation for POS items without eTIMS integration.
    """
    # Ensure item has a stock UOM
    if not doc.stock_uom:
        doc.stock_uom = "Nos"
    
    # Ensure item group exists
    if doc.item_group and not frappe.db.exists("Item Group", doc.item_group):
        frappe.throw(_("Item Group '{0}' does not exist").format(doc.item_group))
    
    # Set default item group if not specified
    if not doc.item_group:
        doc.item_group = "All Item Groups"
    
    # For POS items, ensure is_stock_item is set correctly
    # This helps with inventory tracking
    if hasattr(doc, 'custom_pos_industry') and doc.custom_pos_industry:
        # POS items should typically be stock items for inventory tracking
        if not doc.flags.get('skip_stock_item_check'):
            doc.is_stock_item = 1
    
    # Validate custom_company if it exists
    if hasattr(doc, 'custom_company') and doc.custom_company:
        if not frappe.db.exists("Company", doc.custom_company):
            frappe.throw(_("Company '{0}' does not exist").format(doc.custom_company))


def on_update(doc: Document, method: str = None) -> None:
    """
    After Item update hook.
    Placeholder for any post-update operations.
    """
    pass


def on_trash(doc: Document, method: str = None) -> None:
    """
    Before Item deletion hook.
    Placeholder for any pre-deletion checks.
    """
    pass

