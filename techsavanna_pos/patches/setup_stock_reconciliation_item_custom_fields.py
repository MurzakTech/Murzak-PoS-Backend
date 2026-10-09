"""
Patch to ensure custom fields exist for Stock Reconciliation Item

This patch ensures that all custom fields required for multi-level stock reconciliation
are present in the Stock Reconciliation Item doctype:
- sales_person_qty, sales_person_comment, sales_person_name, sales_person_date
- stock_controller_qty, stock_controller_comment, stock_controller_name, stock_controller_date
- stock_manager_qty, stock_manager_comment, stock_manager_name, stock_manager_date

This should run after setup_stock_reconciliation_workflow to ensure fields are available.
"""
import frappe


def execute():
    """
    Execute the patch to ensure custom fields exist for Stock Reconciliation Item.
    """
    print("=" * 70)
    print("Setting up Stock Reconciliation Item Custom Fields")
    print("=" * 70)
    
    doctype = "Stock Reconciliation Item"
    
    # Custom fields for Stock Reconciliation Item
    custom_fields = [
        {
            "fieldname": "sales_person_qty",
            "fieldtype": "Float",
            "label": "Sales User Qty",
            "insert_after": "qty",
            "description": "Quantity counted by Sales User"
        },
        {
            "fieldname": "sales_person_comment",
            "fieldtype": "Small Text",
            "label": "Sales User Comment",
            "insert_after": "sales_person_qty"
        },
        {
            "fieldname": "sales_person_name",
            "fieldtype": "Link",
            "label": "Sales User",
            "options": "User",
            "insert_after": "sales_person_comment",
            "read_only": 1
        },
        {
            "fieldname": "sales_person_date",
            "fieldtype": "Datetime",
            "label": "Sales User Date",
            "insert_after": "sales_person_name",
            "read_only": 1
        },
        {
            "fieldname": "stock_controller_qty",
            "fieldtype": "Float",
            "label": "Quality Manager Qty",
            "insert_after": "sales_person_date",
            "description": "Quantity counted by Quality Manager"
        },
        {
            "fieldname": "stock_controller_comment",
            "fieldtype": "Small Text",
            "label": "Quality Manager Comment",
            "insert_after": "stock_controller_qty"
        },
        {
            "fieldname": "stock_controller_name",
            "fieldtype": "Link",
            "label": "Quality Manager",
            "options": "User",
            "insert_after": "stock_controller_comment",
            "read_only": 1
        },
        {
            "fieldname": "stock_controller_date",
            "fieldtype": "Datetime",
            "label": "Quality Manager Date",
            "insert_after": "stock_controller_name",
            "read_only": 1
        },
        {
            "fieldname": "stock_manager_qty",
            "fieldtype": "Float",
            "label": "Stock Manager Qty",
            "insert_after": "stock_controller_date"
        },
        {
            "fieldname": "stock_manager_comment",
            "fieldtype": "Small Text",
            "label": "Stock Manager Comment",
            "insert_after": "stock_manager_qty"
        },
        {
            "fieldname": "stock_manager_name",
            "fieldtype": "Link",
            "label": "Stock Manager",
            "options": "User",
            "insert_after": "stock_manager_comment",
            "read_only": 1
        },
        {
            "fieldname": "stock_manager_date",
            "fieldtype": "Datetime",
            "label": "Stock Manager Date",
            "insert_after": "stock_manager_name",
            "read_only": 1
        },
    ]
    
    created_count = 0
    existing_count = 0
    error_count = 0
    
    # Check if doctype exists
    if not frappe.db.exists("DocType", doctype):
        print(f"ERROR: DocType '{doctype}' does not exist!")
        return
    
    # Add custom fields to Stock Reconciliation Item
    for field_config in custom_fields:
        fieldname = field_config["fieldname"]
        
        # Check if field already exists
        if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
            existing_count += 1
            continue
        
        try:
            custom_field = frappe.new_doc("Custom Field")
            custom_field.update(field_config)
            custom_field.dt = doctype
            custom_field.insert(ignore_permissions=True)
            frappe.db.commit()
            created_count += 1
            print(f"  ✓ Created custom field: {fieldname}")
        except Exception as e:
            error_count += 1
            frappe.log_error(
                "Custom Field Setup Error",
                f"Error creating custom field '{fieldname}' on {doctype}: {str(e)}"
            )
            print(f"  ❌ Error creating field {fieldname}: {str(e)}")
    
    # Clear cache to ensure fields are available
    frappe.clear_cache(doctype=doctype)
    
    print("\n" + "=" * 70)
    print("Custom Fields Setup Summary")
    print("=" * 70)
    print(f"  Created: {created_count}")
    print(f"  Already Existed: {existing_count}")
    print(f"  Errors: {error_count}")
    print(f"  Total Fields: {len(custom_fields)}")
    print("=" * 70)
    
    if created_count > 0:
        print(f"\n✓ Successfully created {created_count} custom field(s)")
    elif error_count == 0:
        print("\n✓ All custom fields already exist")

