"""
Script to set up Frappe Workflow for multi-level stock reconciliation.
Run this script once to create the Workflow configuration for Stock Reconciliation.

This creates:
1. Workflow States: "Pending Sales Person", "Pending Quality Manager", "Pending Stock Manager", "Completed"
2. Workflow with workflow_state_field = "workflow_state"
3. Transitions between states based on roles

Note: This workflow uses existing ERPNext roles:
- Sales User (for sales person stock take)
- Quality Manager (for stock controller stock take)
- Stock Manager (for final stock take and submission)
"""

import frappe


def setup_workflow_states():
    """Create Workflow States if they don't exist"""
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
            print(f"Created Workflow State: {state_name}")
        else:
            print(f"Workflow State '{state_name}' already exists")


def setup_workflow_actions():
    """Create Workflow Actions if they don't exist"""
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
            print(f"Created Workflow Action: {action_name}")
        else:
            print(f"Workflow Action '{action_name}' already exists")


def setup_stock_reconciliation_workflow():
    """Create or update the Stock Reconciliation Workflow"""
    
    # First, create the workflow states and actions
    setup_workflow_states()
    setup_workflow_actions()
    
    workflow_name = "Multi-Level Stock Reconciliation"
    
    # Check if workflow already exists
    if frappe.db.exists("Workflow", workflow_name):
        workflow = frappe.get_doc("Workflow", workflow_name)
        print(f"Workflow '{workflow_name}' already exists. Updating...")
    else:
        workflow = frappe.new_doc("Workflow")
        workflow.workflow_name = workflow_name
        print(f"Creating new Workflow: {workflow_name}")
    
    # Set workflow properties
    workflow.document_type = "Stock Reconciliation"
    workflow.workflow_state_field = "workflow_state"
    workflow.is_active = 1
    workflow.send_email_alert = 0  # Set to 1 if you want email alerts
    workflow.override_status = 0
    
    # Clear existing states and transitions
    workflow.states = []
    workflow.transitions = []
    
    # Add workflow states
    # State 1: Pending Sales Person (Draft - docstatus 0)
    workflow.append("states", {
        "state": "Pending Sales Person",
        "doc_status": 0,
        "allow_edit": "Sales User"
    })
    
    # State 2: Pending Quality Manager (Draft - docstatus 0)
    workflow.append("states", {
        "state": "Pending Quality Manager",
        "doc_status": 0,
        "allow_edit": "Quality Manager"
    })
    
    # State 3: Pending Stock Manager (Draft - docstatus 0)
    workflow.append("states", {
        "state": "Pending Stock Manager",
        "doc_status": 0,
        "allow_edit": "Stock Manager"
    })
    
    # State 4: Completed (Draft - docstatus 0, but ready for submission)
    workflow.append("states", {
        "state": "Completed",
        "doc_status": 0,
        "allow_edit": "Stock Manager"
    })
    
    # Add transitions
    # Transition 1: Pending Sales Person -> Pending Quality Manager
    workflow.append("transitions", {
        "state": "Pending Sales Person",
        "action": "Submit Sales Person Count",
        "next_state": "Pending Quality Manager",
        "allowed": "Sales User",
        "allow_self_approval": 1
    })
    
    # Transition 2: Pending Quality Manager -> Pending Stock Manager
    workflow.append("transitions", {
        "state": "Pending Quality Manager",
        "action": "Submit Quality Manager Count",
        "next_state": "Pending Stock Manager",
        "allowed": "Quality Manager",
        "allow_self_approval": 1
    })
    
    # Transition 3: Pending Stock Manager -> Completed
    workflow.append("transitions", {
        "state": "Pending Stock Manager",
        "action": "Complete Stock Count",
        "next_state": "Completed",
        "allowed": "Stock Manager",
        "allow_self_approval": 1
    })
    
    # Save workflow
    workflow.save(ignore_permissions=True)
    frappe.db.commit()
    
    print(f"Workflow '{workflow_name}' setup completed!")
    print("\nWorkflow Configuration:")
    print(f"  - Document Type: Stock Reconciliation")
    print(f"  - Workflow State Field: workflow_state")
    print(f"  - States: Pending Sales Person, Pending Quality Manager, Pending Stock Manager, Completed")
    print(f"  - Is Active: {workflow.is_active}")


def verify_workflow():
    """Verify that the workflow is correctly configured"""
    workflow_name = "Multi-Level Stock Reconciliation"
    
    if not frappe.db.exists("Workflow", workflow_name):
        print(f"ERROR: Workflow '{workflow_name}' does not exist!")
        return False
    
    workflow = frappe.get_doc("Workflow", workflow_name)
    
    # Verify workflow_state_field
    if workflow.workflow_state_field != "workflow_state":
        print(f"ERROR: workflow_state_field is '{workflow.workflow_state_field}', expected 'workflow_state'")
        return False
    
    # Verify document type
    if workflow.document_type != "Stock Reconciliation":
        print(f"ERROR: document_type is '{workflow.document_type}', expected 'Stock Reconciliation'")
        return False
    
    # Verify states
    expected_states = ["Pending Sales Person", "Pending Quality Manager", "Pending Stock Manager", "Completed"]
    actual_states = [state.state for state in workflow.states]
    
    for expected_state in expected_states:
        if expected_state not in actual_states:
            print(f"ERROR: Workflow State '{expected_state}' not found in workflow!")
            return False
    
    print("✓ Workflow verification passed!")
    print(f"  - Workflow Name: {workflow_name}")
    print(f"  - Document Type: {workflow.document_type}")
    print(f"  - Workflow State Field: {workflow.workflow_state_field}")
    print(f"  - Is Active: {workflow.is_active}")
    print(f"  - States: {', '.join(actual_states)}")
    print(f"  - Transitions: {len(workflow.transitions)}")
    
    return True


if __name__ == "__main__":
    print("Setting up Stock Reconciliation Workflow...")
    print("=" * 50)
    setup_stock_reconciliation_workflow()
    print("\n" + "=" * 50)
    print("Verifying workflow configuration...")
    verify_workflow()

