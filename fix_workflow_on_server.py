#!/usr/bin/env python3
"""
Script to fix workflow_state field issue on server.

This script:
1. Checks if the workflow exists
2. Sets up the workflow if it doesn't exist
3. Ensures the workflow_state field exists in Stock Reconciliation doctype
4. Reconciles workflow states for existing documents

Run this on your server:
    bench --site [site_name] console
    >>> exec(open('apps/techsavanna_pos/fix_workflow_on_server.py').read())
"""

import frappe
from frappe.utils import cint

def check_and_fix_workflow():
    """Check and fix workflow setup"""
    workflow_name = "Multi-Level Stock Reconciliation"
    doctype = "Stock Reconciliation"
    
    print("=" * 70)
    print("Workflow Setup and Fix Script")
    print("=" * 70)
    
    # Step 1: Check if workflow exists
    print("\n[Step 1] Checking if workflow exists...")
    if not frappe.db.exists("Workflow", workflow_name):
        print(f"  ❌ Workflow '{workflow_name}' does not exist!")
        print("  → Setting up workflow...")
        setup_workflow()
    else:
        print(f"  ✓ Workflow '{workflow_name}' exists")
        workflow = frappe.get_doc("Workflow", workflow_name)
        
        # Check if workflow is active
        if not workflow.is_active:
            print("  ⚠ Workflow is not active. Activating...")
            workflow.is_active = 1
            workflow.save(ignore_permissions=True)
            frappe.db.commit()
            print("  ✓ Workflow activated")
    
    # Step 2: Check if workflow_state field exists
    print("\n[Step 2] Checking if workflow_state field exists...")
    meta = frappe.get_meta(doctype)
    workflow = frappe.get_doc("Workflow", workflow_name)
    workflow_state_field = workflow.workflow_state_field
    
    if not meta.has_field(workflow_state_field):
        print(f"  ❌ Field '{workflow_state_field}' does not exist in '{doctype}'")
        print("  → Creating field by saving workflow...")
        
        # The workflow's on_update method should create the field
        workflow.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Clear cache and reload meta
        frappe.clear_cache(doctype=doctype)
        meta = frappe.get_meta(doctype)
        
        if meta.has_field(workflow_state_field):
            print(f"  ✓ Field '{workflow_state_field}' created successfully")
        else:
            print(f"  ❌ Field '{workflow_state_field}' still does not exist")
            print("  → Manually creating custom field...")
            create_workflow_state_field(doctype, workflow_state_field)
    else:
        print(f"  ✓ Field '{workflow_state_field}' exists")
    
    # Step 3: Reconcile workflow states
    print("\n[Step 3] Reconciling workflow states for existing documents...")
    reconcile_workflow_states()
    
    print("\n" + "=" * 70)
    print("Workflow setup complete!")
    print("=" * 70)


def setup_workflow():
    """Setup the Stock Reconciliation workflow"""
    workflow_name = "Multi-Level Stock Reconciliation"
    doctype = "Stock Reconciliation"
    
    # Create workflow states
    states = [
        "Pending Sales Person",
        "Pending Quality Manager",
        "Pending Stock Manager",
        "Completed"
    ]
    
    for state_name in states:
        if not frappe.db.exists("Workflow State", state_name):
            workflow_state = frappe.new_doc("Workflow State")
            workflow_state.workflow_state_name = state_name
            workflow_state.insert(ignore_permissions=True)
            frappe.db.commit()
            print(f"    ✓ Created Workflow State: {state_name}")
    
    # Create workflow actions
    actions = [
        "Submit Sales Person Count",
        "Submit Quality Manager Count",
        "Complete Stock Count"
    ]
    
    for action_name in actions:
        if not frappe.db.exists("Workflow Action Master", action_name):
            workflow_action = frappe.new_doc("Workflow Action Master")
            workflow_action.workflow_action_name = action_name
            workflow_action.insert(ignore_permissions=True)
            frappe.db.commit()
            print(f"    ✓ Created Workflow Action: {action_name}")
    
    # Create the workflow
    workflow = frappe.new_doc("Workflow")
    workflow.workflow_name = workflow_name
    workflow.document_type = doctype
    workflow.workflow_state_field = "workflow_state"
    workflow.is_active = 1
    workflow.send_email_alert = 0
    workflow.override_status = 0
    
    # Add workflow states
    workflow.append("states", {
        "state": "Pending Sales Person",
        "doc_status": 0,
        "allow_edit": "Sales User"
    })
    
    workflow.append("states", {
        "state": "Pending Quality Manager",
        "doc_status": 0,
        "allow_edit": "Quality Manager"
    })
    
    workflow.append("states", {
        "state": "Pending Stock Manager",
        "doc_status": 0,
        "allow_edit": "Stock Manager"
    })
    
    workflow.append("states", {
        "state": "Completed",
        "doc_status": 0,
        "allow_edit": "Stock Manager"
    })
    
    # Add transitions
    workflow.append("transitions", {
        "state": "Pending Sales Person",
        "action": "Submit Sales Person Count",
        "next_state": "Pending Quality Manager",
        "allowed": "Sales User",
        "allow_self_approval": 1
    })
    
    workflow.append("transitions", {
        "state": "Pending Quality Manager",
        "action": "Submit Quality Manager Count",
        "next_state": "Pending Stock Manager",
        "allowed": "Quality Manager",
        "allow_self_approval": 1
    })
    
    workflow.append("transitions", {
        "state": "Pending Stock Manager",
        "action": "Complete Stock Count",
        "next_state": "Completed",
        "allowed": "Stock Manager",
        "allow_self_approval": 1
    })
    
    workflow.save(ignore_permissions=True)
    frappe.db.commit()
    print(f"    ✓ Created Workflow: {workflow_name}")


def create_workflow_state_field(doctype, fieldname):
    """Manually create the workflow_state custom field"""
    try:
        # Check if custom field already exists
        if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
            print(f"    ✓ Custom Field '{fieldname}' already exists")
            return
        
        custom_field = frappe.new_doc("Custom Field")
        custom_field.dt = doctype
        custom_field.fieldname = fieldname
        custom_field.label = fieldname.replace("_", " ").title()
        custom_field.hidden = 1
        custom_field.allow_on_submit = 1
        custom_field.no_copy = 1
        custom_field.fieldtype = "Link"
        custom_field.options = "Workflow State"
        custom_field.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Clear cache
        frappe.clear_cache(doctype=doctype)
        
        print(f"    ✓ Created Custom Field '{fieldname}'")
    except Exception as e:
        print(f"    ❌ Error creating custom field: {str(e)}")
        frappe.log_error(f"Error creating workflow_state field: {str(e)}", "Workflow Setup Error")


def reconcile_workflow_states():
    """Reconcile workflow states for existing documents"""
    doctype = "Stock Reconciliation"
    workflow_name = "Multi-Level Stock Reconciliation"
    
    if not frappe.db.exists("Workflow", workflow_name):
        print("  ❌ Workflow does not exist. Cannot reconcile.")
        return
    
    workflow = frappe.get_doc("Workflow", workflow_name)
    workflow_state_field = workflow.workflow_state_field
    
    # Verify field exists
    meta = frappe.get_meta(doctype)
    if not meta.has_field(workflow_state_field):
        print(f"  ❌ Field '{workflow_state_field}' does not exist. Cannot reconcile.")
        return
    
    # Get default state for docstatus 0
    default_state = None
    for state in workflow.states:
        if cint(state.doc_status) == 0:
            default_state = state.state
            break
    
    if not default_state:
        print("  ❌ No default state found for draft documents")
        return
    
    # Get all documents
    all_docs = frappe.db.get_all(
        doctype,
        fields=["name", "docstatus", workflow_state_field],
        limit=None
    )
    
    if not all_docs:
        print("  ℹ No documents found")
        return
    
    print(f"  → Found {len(all_docs)} document(s)")
    
    updated_count = 0
    for doc_data in all_docs:
        docname = doc_data.name
        docstatus = cint(doc_data.docstatus)
        current_state = doc_data.get(workflow_state_field)
        
        # Only update draft documents (docstatus = 0) that don't have a state
        if docstatus == 0 and not current_state:
            try:
                frappe.db.set_value(
                    doctype,
                    docname,
                    workflow_state_field,
                    default_state,
                    update_modified=False
                )
                updated_count += 1
            except Exception as e:
                frappe.log_error(
                    f"Error updating workflow state for {doctype} '{docname}': {str(e)}",
                    "Workflow Reconciliation Error"
                )
    
    frappe.db.commit()
    
    if updated_count > 0:
        print(f"  ✓ Updated workflow state for {updated_count} document(s)")
    else:
        print("  ✓ All documents already have workflow states")


if __name__ == "__main__":
    check_and_fix_workflow()

