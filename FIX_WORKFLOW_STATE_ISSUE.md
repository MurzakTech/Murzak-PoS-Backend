# Fix Workflow State Issue on Server

## Problem

The `workflow_state` field is missing in the API response for Stock Reconciliation documents on the server, even though it works locally.

## Root Cause

The workflow and `workflow_state` field may not be properly set up on the server. This can happen if:
1. The workflow was not created on the server
2. The `workflow_state` custom field was not created (should be automatic when workflow is saved)
3. Existing documents don't have workflow states set

## Solution

Run the fix script on your server to:
1. Check if the workflow exists
2. Create the workflow if it doesn't exist
3. Ensure the `workflow_state` field exists
4. Reconcile workflow states for existing documents

## Steps to Fix

### Option 1: Run the Fix Script (Recommended)

1. SSH into your server
2. Navigate to your frappe-bench directory
3. Run the fix script:

```bash
bench --site [your_site_name] console
```

Then in the console:

```python
exec(open('apps/techsavanna_pos/fix_workflow_on_server.py').read())
```

### Option 2: Run Setup Script + Reconciliation

If you prefer to run them separately:

**Step 1: Setup the workflow**

```bash
bench --site [your_site_name] console
```

```python
exec(open('apps/techsavanna_pos/techsavanna_pos/api/scripts/setup_stock_reconciliation_workflow.py').read())
```

**Step 2: Reconcile workflow states**

```python
from techsavanna_pos.patches.reconcile_stock_reconciliation_workflow_states import execute
execute()
```

### Option 3: Use Bench Migrate (if patch is registered)

If the patch is already in `patches.txt`, you can run:

```bash
bench --site [your_site_name] migrate
```

This will automatically run the reconciliation patch.

## Verification

After running the fix script, verify the workflow is set up correctly:

```python
# Check if workflow exists
frappe.db.exists("Workflow", "Multi-Level Stock Reconciliation")

# Check if workflow_state field exists
meta = frappe.get_meta("Stock Reconciliation")
meta.has_field("workflow_state")

# Check a specific document
doc = frappe.get_doc("Stock Reconciliation", "MAT-RECO-2026-00006")
print(doc.workflow_state)
```

## What the Script Does

1. **Checks workflow existence**: Verifies if "Multi-Level Stock Reconciliation" workflow exists
2. **Creates workflow if missing**: Sets up the workflow with all states and transitions
3. **Verifies workflow_state field**: Checks if the field exists in Stock Reconciliation doctype
4. **Creates field if missing**: Creates the custom field if it doesn't exist
5. **Reconciles existing documents**: Updates workflow states for documents that don't have one

## Expected Workflow States

- **Pending Sales Person** - Initial state
- **Pending Quality Manager** - After sales person submits
- **Pending Stock Manager** - After quality manager submits
- **Completed** - Ready for final submission

## Troubleshooting

### Error: "Workflow does not exist"
- Run the setup script first (Option 2, Step 1)

### Error: "Field does not exist"
- The script should create it automatically
- If it doesn't, manually save the workflow document:
  ```python
  workflow = frappe.get_doc("Workflow", "Multi-Level Stock Reconciliation")
  workflow.save()
  ```

### Documents still showing null workflow_state
- Make sure the workflow is active:
  ```python
  workflow = frappe.get_doc("Workflow", "Multi-Level Stock Reconciliation")
  workflow.is_active = 1
  workflow.save()
  ```
- Run the reconciliation script again

## After Fixing

Once the workflow is set up, the API endpoint should return the `workflow_state` field:

```json
{
    "message": {
        "success": true,
        "data": {
            "workflow_state": "Pending Sales Person",
            ...
        }
    }
}
```

## Notes

- The fix script is **idempotent** - safe to run multiple times
- It only updates documents that need fixing
- All changes are committed in a single transaction
- Errors are logged to Error Log for review

