"""
Patch to ensure Stock Reconciliation workflow is set up

This patch ensures that:
1. The "Multi-Level Stock Reconciliation" workflow exists
2. The workflow_state field exists in Stock Reconciliation doctype
3. The workflow is active

This should run before reconcile_stock_reconciliation_workflow_states patch
to ensure the workflow infrastructure is in place.
"""
import frappe


def setup_workflow_if_missing():
    """Setup the Stock Reconciliation workflow if it doesn't exist"""
    workflow_name = "Multi-Level Stock Reconciliation"
    doctype = "Stock Reconciliation"
    
    if frappe.db.exists("Workflow", workflow_name):
        workflow = frappe.get_doc("Workflow", workflow_name)
        # Ensure workflow is active
        if not workflow.is_active:
            workflow.is_active = 1
            workflow.save(ignore_permissions=True)
            frappe.db.commit()
        return True
    
    print(f"Workflow '{workflow_name}' does not exist. Setting it up...")
    
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
    
    print(f"✓ Successfully created workflow '{workflow_name}'")
    return True


def ensure_workflow_state_field():
    """Ensure workflow_state field exists in Stock Reconciliation doctype"""
    doctype = "Stock Reconciliation"
    workflow_name = "Multi-Level Stock Reconciliation"
    fieldname = "workflow_state"
    
    # Check if workflow exists
    if not frappe.db.exists("Workflow", workflow_name):
        print(f"WARNING: Workflow '{workflow_name}' does not exist. Cannot ensure field exists.")
        return False
    
    workflow = frappe.get_doc("Workflow", workflow_name)
    workflow_state_field = workflow.workflow_state_field
    
    # Check if field exists
    meta = frappe.get_meta(doctype)
    if meta.has_field(workflow_state_field):
        return True
    
    # Field doesn't exist - save workflow to trigger field creation
    # The workflow's on_update method should create the field
    print(f"Field '{workflow_state_field}' does not exist. Creating it...")
    
    try:
        # Save workflow to trigger field creation
        workflow.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Clear cache and check again
        frappe.clear_cache(doctype=doctype)
        meta = frappe.get_meta(doctype)
        
        if meta.has_field(workflow_state_field):
            print(f"✓ Field '{workflow_state_field}' created successfully")
            return True
        else:
            # Manually create the field if automatic creation failed
            print(f"Field not created automatically. Creating manually...")
            create_workflow_state_field(doctype, workflow_state_field)
            return True
    except Exception as e:
        frappe.log_error(
            f"Error ensuring workflow_state field: {str(e)}",
            "Workflow Setup Error"
        )
        print(f"ERROR: Failed to create field: {str(e)}")
        return False


def create_workflow_state_field(doctype, fieldname):
    """Manually create the workflow_state custom field"""
    try:
        # Check if custom field already exists
        if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
            print(f"  ✓ Custom Field '{fieldname}' already exists")
            frappe.clear_cache(doctype=doctype)
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
        
        print(f"  ✓ Created Custom Field '{fieldname}'")
    except Exception as e:
        print(f"  ❌ Error creating custom field: {str(e)}")
        frappe.log_error(f"Error creating workflow_state field: {str(e)}", "Workflow Setup Error")
        raise


def execute():
    """
    Execute the patch to ensure workflow is set up.
    This should run before reconcile_stock_reconciliation_workflow_states.
    """
    print("=" * 70)
    print("Setting up Stock Reconciliation Workflow")
    print("=" * 70)
    
    # Step 1: Setup workflow if missing
    print("\n[Step 1] Ensuring workflow exists...")
    if setup_workflow_if_missing():
        print("  ✓ Workflow is set up")
    else:
        print("  ❌ Failed to set up workflow")
        return
    
    # Step 2: Ensure workflow_state field exists
    print("\n[Step 2] Ensuring workflow_state field exists...")
    if ensure_workflow_state_field():
        print("  ✓ workflow_state field exists")
    else:
        print("  ⚠ workflow_state field may not exist (check Error Log)")
    
    print("\n" + "=" * 70)
    print("Workflow setup complete!")
    print("=" * 70)

