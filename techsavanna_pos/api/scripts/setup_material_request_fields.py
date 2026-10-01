"""
Script to set up custom fields for Material Request and Material Request Item.
Run this script once to add the necessary custom fields.
"""

#bench --site master-pos execute techsavanna_pos.api.scripts.setup_material_request_fields.setup_all

import frappe


def setup_material_request_item_fields():
    """Create custom fields for Material Request Item"""
    
    # Add custom_dispatched_qty field
    if not frappe.db.exists("Custom Field", {"dt": "Material Request Item", "fieldname": "custom_dispatched_qty"}):
        cf1 = frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Material Request Item",
            "fieldname": "custom_dispatched_qty",
            "label": "Dispatched Quantity",
            "fieldtype": "Float",
            "default": 0,
            "read_only": 1,
            "insert_after": "ordered_qty",
            "description": "Quantity dispatched from origin warehouse"
        })
        cf1.insert(ignore_permissions=True)
        print("✓ Added custom_dispatched_qty field")
    else:
        print("✓ custom_dispatched_qty field already exists")

    # Add custom_received_qty field (optional - can use standard received_qty)
    if not frappe.db.exists("Custom Field", {"dt": "Material Request Item", "fieldname": "custom_received_qty"}):
        cf2 = frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Material Request Item",
            "fieldname": "custom_received_qty",
            "label": "Received Quantity (Custom)",
            "fieldtype": "Float",
            "default": 0,
            "read_only": 1,
            "insert_after": "received_qty",
            "description": "Quantity received at destination warehouse"
        })
        cf2.insert(ignore_permissions=True)
        print("✓ Added custom_received_qty field")
    else:
        print("✓ custom_received_qty field already exists")

    frappe.db.commit()
    frappe.clear_cache(doctype="Material Request Item")
    print("✓ Material Request Item fields setup complete!")


def setup_material_request_fields():
    """Create custom fields for Material Request"""
    
    fields_to_add = [
        {
            "fieldname": "custom_dispatched_by",
            "label": "Dispatched By",
            "fieldtype": "Data",
            "insert_after": "status"
        },
        {
            "fieldname": "custom_dispatch_notes",
            "label": "Dispatch Notes",
            "fieldtype": "Small Text",
            "insert_after": "custom_dispatched_by"
        },
        {
            "fieldname": "custom_last_dispatch_on",
            "label": "Last Dispatch On",
            "fieldtype": "Datetime",
            "insert_after": "custom_dispatch_notes"
        },
        {
            "fieldname": "custom_received_by",
            "label": "Received By",
            "fieldtype": "Data",
            "insert_after": "custom_last_dispatch_on"
        },
        {
            "fieldname": "custom_receive_notes",
            "label": "Receive Notes",
            "fieldtype": "Small Text",
            "insert_after": "custom_received_by"
        },
        {
            "fieldname": "custom_received_on",
            "label": "Received On",
            "fieldtype": "Datetime",
            "insert_after": "custom_receive_notes"
        },
        {
            "fieldname": "custom_goods_received_note",
            "label": "Goods Received Note",
            "fieldtype": "Data",
            "insert_after": "custom_received_on"
        },
        {
            "fieldname": "custom_approval_status",
            "label": "Approval Status",
            "fieldtype": "Select",
            "options": "\nPending\nApproved\nRejected",
            "insert_after": "status",
            "default": "Pending"
        }
    ]

    for field_config in fields_to_add:
        fieldname = field_config["fieldname"]
        
        # Check if field already exists
        if frappe.db.exists("Custom Field", {"dt": "Material Request", "fieldname": fieldname}):
            print(f"✓ {fieldname} already exists")
            continue
        
        # Check if column exists (might exist without Custom Field doc)
        if frappe.db.has_column("Material Request", fieldname):
            print(f"✓ {fieldname} column exists")
            continue
        
        # Create custom field
        cf = frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Material Request",
            **field_config,
            "read_only": 1,
            "allow_on_submit": 1
        })
        cf.insert(ignore_permissions=True)
        print(f"✓ Added {fieldname}")

    frappe.db.commit()
    frappe.clear_cache(doctype="Material Request")
    print("✓ Material Request fields setup complete!")


def setup_all():
    """Setup all Material Request related custom fields"""
    print("Setting up Material Request Item custom fields...")
    setup_material_request_item_fields()
    print("\nSetting up Material Request custom fields...")
    setup_material_request_fields()
    print("\n✓ All Material Request fields setup completed!")


if __name__ == "__main__":
    setup_all()

