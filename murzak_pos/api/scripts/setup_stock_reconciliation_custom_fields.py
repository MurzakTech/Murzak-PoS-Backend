"""
Script to set up custom fields for multi-level stock reconciliation workflow.
Run this script once to add the necessary custom fields to Stock Reconciliation and Stock Reconciliation Item.

Note: This workflow uses existing ERPNext roles:
- Sales User (for sales person stock take)
- Quality Manager (for stock controller stock take)
- Stock Manager (for final stock take and submission)
"""

import frappe


def setup_custom_fields():
    """Create custom fields for multi-level stock reconciliation workflow"""
    
    # Custom fields for Stock Reconciliation Item
    custom_fields_item = [
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
    
    # Custom field for Stock Reconciliation (workflow status)
    custom_fields_reconciliation = [
        {
            "fieldname": "workflow_status",
            "fieldtype": "Select",
            "label": "Workflow Status",
            "options": "\nPending Sales User\nPending Quality Manager\nPending Stock Manager\nCompleted",
            "default": "Pending Sales User",
            "insert_after": "purpose",
            "read_only": 1
        }
    ]
    
    # Add custom fields to Stock Reconciliation Item
    for field in custom_fields_item:
        if not frappe.db.exists("Custom Field", {"dt": "Stock Reconciliation Item", "fieldname": field["fieldname"]}):
            custom_field = frappe.new_doc("Custom Field")
            custom_field.update(field)
            custom_field.dt = "Stock Reconciliation Item"
            custom_field.insert(ignore_permissions=True)
            frappe.db.commit()
            print(f"Created custom field: {field['fieldname']} on Stock Reconciliation Item")
        else:
            print(f"Custom field {field['fieldname']} already exists on Stock Reconciliation Item")
    
    # Add custom fields to Stock Reconciliation
    for field in custom_fields_reconciliation:
        if not frappe.db.exists("Custom Field", {"dt": "Stock Reconciliation", "fieldname": field["fieldname"]}):
            custom_field = frappe.new_doc("Custom Field")
            custom_field.update(field)
            custom_field.dt = "Stock Reconciliation"
            custom_field.insert(ignore_permissions=True)
            frappe.db.commit()
            print(f"Created custom field: {field['fieldname']} on Stock Reconciliation")
        else:
            print(f"Custom field {field['fieldname']} already exists on Stock Reconciliation")
    
    print("Custom fields setup completed!")


if __name__ == "__main__":
    setup_custom_fields()

