"""
Script to check if custom fields exist in Material Request Item
Run this to verify field setup
"""

import frappe


def check_custom_fields():
    """Check if required custom fields exist"""
    
    print("=" * 60)
    print("Checking Custom Fields for Material Request Item")
    print("=" * 60)
    
    # Check custom_dispatched_qty
    has_dispatched_qty = frappe.db.has_column("Material Request Item", "custom_dispatched_qty")
    print(f"\n✓ custom_dispatched_qty field exists: {has_dispatched_qty}")
    
    if has_dispatched_qty:
        # Get field details
        cf = frappe.db.get_value(
            "Custom Field",
            {"dt": "Material Request Item", "fieldname": "custom_dispatched_qty"},
            ["label", "fieldtype"],
            as_dict=True
        )
        if cf:
            print(f"  - Label: {cf.label}")
            print(f"  - Type: {cf.fieldtype}")
    else:
        print("  ⚠️  Field does NOT exist - needs to be added")
    
    # Check custom_received_qty (optional)
    has_received_qty = frappe.db.has_column("Material Request Item", "custom_received_qty")
    print(f"\n✓ custom_received_qty field exists: {has_received_qty}")
    
    if has_received_qty:
        cf = frappe.db.get_value(
            "Custom Field",
            {"dt": "Material Request Item", "fieldname": "custom_received_qty"},
            ["label", "fieldtype"],
            as_dict=True
        )
        if cf:
            print(f"  - Label: {cf.label}")
            print(f"  - Type: {cf.fieldtype}")
    else:
        print("  ℹ️  Field does NOT exist (optional - API uses standard received_qty)")
    
    # Check custom_approval_status (for Material Request)
    has_approval_status = frappe.db.has_column("Material Request", "custom_approval_status")
    print(f"\n✓ custom_approval_status field exists (Material Request): {has_approval_status}")
    
    if has_approval_status:
        cf = frappe.db.get_value(
            "Custom Field",
            {"dt": "Material Request", "fieldname": "custom_approval_status"},
            ["label", "fieldtype"],
            as_dict=True
        )
        if cf:
            print(f"  - Label: {cf.label}")
            print(f"  - Type: {cf.fieldtype}")
    else:
        print("  ⚠️  Field does NOT exist - needs to be added for approval status")
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    
    if has_dispatched_qty and has_approval_status:
        print("✅ All required fields are present!")
        print("   Your API endpoints should work correctly.")
    else:
        print("⚠️  Some required fields are missing:")
        if not has_dispatched_qty:
            print("   - custom_dispatched_qty (Material Request Item) - REQUIRED")
        if not has_approval_status:
            print("   - custom_approval_status (Material Request) - REQUIRED")
        print("\n   See setup guides for instructions on adding these fields.")
    
    print("=" * 60)


if __name__ == "__main__":
    check_custom_fields()

