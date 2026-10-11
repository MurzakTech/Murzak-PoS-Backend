"""Patch to set update_stock = 1 on all existing POS Profiles

This patch ensures all POS Profiles have update_stock enabled by default,
which is required for inventory deduction when creating invoices via API.
"""
import frappe
import frappe.utils


def execute():
    """Update all POS Profiles to have update_stock = 1"""
    # Get all POS Profiles (we'll check each one individually)
    pos_profiles = frappe.db.get_all(
        "POS Profile",
        fields=["name", "update_stock"]
    )
    
    if not pos_profiles:
        return
    
    # Update all POS Profiles that don't have update_stock = 1
    updated_count = 0
    for profile in pos_profiles:
        # Check if update_stock is not 1 (could be 0, None, or missing)
        current_value = frappe.utils.cint(profile.get("update_stock", 0))
        if current_value != 1:
            try:
                frappe.db.set_value(
                    "POS Profile",
                    profile.name,
                    "update_stock",
                    1,
                    update_modified=False
                )
                updated_count += 1
            except Exception as e:
                frappe.log_error(
                    "POS Profile Update Stock Patch Error",
                    f"Error updating POS Profile {profile.name}: {str(e)}"
                )
    
    # Commit the changes
    frappe.db.commit()
    
    if updated_count > 0:
        frappe.msgprint(
            f"Updated {updated_count} POS Profile(s) to have update_stock = 1"
        )

