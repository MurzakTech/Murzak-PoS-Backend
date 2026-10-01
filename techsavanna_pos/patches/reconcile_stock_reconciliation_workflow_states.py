"""
Patch to reconcile workflow states for Stock Reconciliation documents

This script ensures that all Stock Reconciliation documents have their
workflow_state field properly set based on the active workflow configuration.

Run this script after deploying the techsavanna_pos app to ensure workflow
states are synchronized with the workflow configuration.

Usage:
    bench --site [site_name] console
    >>> exec(open('apps/techsavanna_pos/techsavanna_pos/patches/reconcile_stock_reconciliation_workflow_states.py').read())
    
Or add to patches.txt and run bench migrate
"""
import frappe
from frappe.utils import cint


def setup_workflow_if_missing():
    """Setup the Stock Reconciliation workflow if it doesn't exist"""
    workflow_name = "Multi-Level Stock Reconciliation"
    doctype = "Stock Reconciliation"
    
    if frappe.db.exists("Workflow", workflow_name):
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


def execute():
    """
    Reconcile workflow states for Stock Reconciliation documents.
    This ensures all documents have their workflow_state field set correctly
    based on the active workflow configuration.
    
    If the workflow doesn't exist, it will be automatically created.
    """
    doctype = "Stock Reconciliation"
    workflow_name = "Multi-Level Stock Reconciliation"
    
    # Setup workflow if it doesn't exist
    if not frappe.db.exists("Workflow", workflow_name):
        try:
            setup_workflow_if_missing()
        except Exception as e:
            frappe.log_error(
                f"Failed to setup workflow '{workflow_name}': {str(e)}",
                "Workflow Reconciliation Error"
            )
            print(f"ERROR: Failed to setup workflow '{workflow_name}': {str(e)}")
            print("Please run the workflow setup script manually:")
            print("  bench --site [site_name] console")
            print("  >>> from techsavanna_pos.api.scripts.setup_stock_reconciliation_workflow import setup_stock_reconciliation_workflow")
            print("  >>> setup_stock_reconciliation_workflow()")
            return
    
    # Get the workflow document
    workflow = frappe.get_doc("Workflow", workflow_name)
    
    # Verify workflow is for the correct doctype
    if workflow.document_type != doctype:
        frappe.log_error(
            f"Workflow '{workflow_name}' is for '{workflow.document_type}', not '{doctype}'",
            "Workflow Reconciliation Error"
        )
        print(f"ERROR: Workflow '{workflow_name}' is configured for '{workflow.document_type}', not '{doctype}'")
        return
    
    # Check if workflow is active
    if not workflow.is_active:
        frappe.log_error(
            f"Workflow '{workflow_name}' is not active. Activating it...",
            "Workflow Reconciliation Warning"
        )
        workflow.is_active = 1
        workflow.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"WARNING: Workflow '{workflow_name}' was not active. Activated it.")
    
    workflow_state_field = workflow.workflow_state_field
    
    # Verify the workflow_state_field exists in the doctype
    meta = frappe.get_meta(doctype)
    if not meta.has_field(workflow_state_field):
        frappe.log_error(
            f"Workflow state field '{workflow_state_field}' does not exist in '{doctype}'",
            "Workflow Reconciliation Error"
        )
        print(f"ERROR: Workflow state field '{workflow_state_field}' does not exist in '{doctype}'")
        print("The workflow will automatically create this field when saved. Please save the workflow once.")
        return
    
    # Get workflow states mapped by docstatus
    docstatus_state_map = {}
    for state in workflow.states:
        docstatus = cint(state.doc_status)
        # Use the first state for each docstatus if multiple exist
        if docstatus not in docstatus_state_map:
            docstatus_state_map[docstatus] = state.state
    
    # Get all Stock Reconciliation documents
    all_docs = frappe.db.get_all(
        doctype,
        fields=["name", "docstatus", workflow_state_field],
        limit=None
    )
    
    if not all_docs:
        print(f"No {doctype} documents found to reconcile.")
        return
    
    print(f"Found {len(all_docs)} {doctype} document(s) to reconcile...")
    
    updated_count = 0
    error_count = 0
    
    # Reconcile each document
    for doc_data in all_docs:
        docname = doc_data.name
        docstatus = cint(doc_data.docstatus)
        current_state = doc_data.get(workflow_state_field)
        
        # Get the expected state for this docstatus
        expected_state = docstatus_state_map.get(docstatus)
        
        # Skip if no expected state for this docstatus
        if not expected_state:
            continue
        
        # Update if state is missing or incorrect
        # Note: For docstatus = 0, we set to the first state (usually "Pending Sales Person")
        # If the document already has a valid state for docstatus = 0, we keep it
        if not current_state:
            # State is empty - set to default state for this docstatus
            try:
                frappe.db.set_value(
                    doctype,
                    docname,
                    workflow_state_field,
                    expected_state,
                    update_modified=False
                )
                updated_count += 1
            except Exception as e:
                error_count += 1
                frappe.log_error(
                    f"Error updating workflow state for {doctype} '{docname}': {str(e)}",
                    "Workflow Reconciliation Error"
                )
        elif docstatus == 0:
            # For draft documents, verify the state is valid
            valid_states = [s.state for s in workflow.states if cint(s.doc_status) == 0]
            if current_state not in valid_states:
                # Invalid state - set to default state for draft
                try:
                    frappe.db.set_value(
                        doctype,
                        docname,
                        workflow_state_field,
                        expected_state,
                        update_modified=False
                    )
                    updated_count += 1
                except Exception as e:
                    error_count += 1
                    frappe.log_error(
                        f"Error updating workflow state for {doctype} '{docname}': {str(e)}",
                        "Workflow Reconciliation Error"
                    )
        # For submitted (docstatus = 1) or cancelled (docstatus = 2) documents,
        # we don't modify them as they should not change workflow states
    
    # Commit all changes
    frappe.db.commit()
    
    # Print summary
    print(f"\n{'='*60}")
    print("Workflow State Reconciliation Summary")
    print(f"{'='*60}")
    print(f"Document Type: {doctype}")
    print(f"Workflow: {workflow_name}")
    print(f"Workflow State Field: {workflow_state_field}")
    print(f"Total Documents Processed: {len(all_docs)}")
    print(f"Documents Updated: {updated_count}")
    print(f"Errors: {error_count}")
    print(f"{'='*60}\n")
    
    if updated_count > 0:
        print(f"✓ Successfully reconciled workflow states for {updated_count} document(s)")
    elif error_count == 0:
        print("✓ All documents already have correct workflow states")
    
    if error_count > 0:
        print(f"⚠ {error_count} error(s) occurred. Check Error Log for details.")


if __name__ == "__main__":
    execute()

