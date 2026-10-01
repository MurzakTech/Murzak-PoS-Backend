# Workflow State Reconciliation Guide

## Overview

This guide explains how to reconcile workflow states for Stock Reconciliation documents after deploying the `techsavanna_pos` app.

## When to Run Reconciliation

You should run workflow state reconciliation in the following scenarios:

1. **After deploying the app** - To ensure all existing documents have correct workflow states
2. **After creating/updating a workflow** - To sync existing documents with the new workflow configuration
3. **After data migration** - To fix any workflow states that may have been lost or corrupted during migration
4. **Periodic maintenance** - As part of regular database maintenance tasks

## Automatic Reconciliation (Recommended)

The reconciliation script is automatically run during `bench migrate` as it's included in `patches.txt`. Simply run:

```bash
bench --site [site_name] migrate
```

## Manual Reconciliation

If you need to run reconciliation manually (e.g., on a production server where you only deploy the app without running full migration), use one of these methods:

### Method 1: Using Bench Console

```bash
bench --site [site_name] console
```

Then in the console:

```python
from techsavanna_pos.patches.reconcile_stock_reconciliation_workflow_states import execute
execute()
```

### Method 2: Using Python Script Directly

```bash
bench --site [site_name] console
```

Then:

```python
exec(open('apps/techsavanna_pos/techsavanna_pos/patches/reconcile_stock_reconciliation_workflow_states.py').read())
```

### Method 3: As a Standalone Script

You can also run the script file directly:

```bash
cd /path/to/frappe-bench
bench --site [site_name] console < apps/techsavanna_pos/techsavanna_pos/patches/reconcile_stock_reconciliation_workflow_states.py
```

## What the Script Does

The reconciliation script:

1. **Verifies the workflow exists** - Checks that "Multi-Level Stock Reconciliation" workflow is configured
2. **Activates the workflow** - Ensures the workflow is active (activates it if not)
3. **Validates the workflow state field** - Verifies the `workflow_state` field exists in Stock Reconciliation doctype
4. **Reconciles all documents** - Updates workflow states for all Stock Reconciliation documents:
   - Sets empty/missing workflow states to the default state based on `docstatus`
   - Fixes invalid workflow states (for draft documents with docstatus = 0)
   - Leaves submitted (docstatus = 1) and cancelled (docstatus = 2) documents unchanged
5. **Provides a summary** - Reports how many documents were updated and any errors encountered

## Workflow States

The Stock Reconciliation workflow has the following states (all with docstatus = 0):

- **Pending Sales Person** - Initial state, Sales User can edit
- **Pending Quality Manager** - Quality Manager can edit
- **Pending Stock Manager** - Stock Manager can edit
- **Completed** - Ready for final submission, Stock Manager can edit

## Prerequisites

Before running reconciliation, ensure:

1. **Workflow is set up** - Run the workflow setup script if not already done:
   ```bash
   bench --site [site_name] console
   ```
   ```python
   exec(open('apps/techsavanna_pos/techsavanna_pos/api/scripts/setup_stock_reconciliation_workflow.py').read())
   ```

2. **Services are running** - If using `bench migrate`, ensure bench services are running:
   ```bash
   bench start
   ```

## Troubleshooting

### Error: Workflow does not exist

**Solution**: Run the workflow setup script first (see Prerequisites above)

### Error: Workflow state field does not exist

**Solution**: The workflow should automatically create the field when saved. Save the workflow document once:
```python
workflow = frappe.get_doc("Workflow", "Multi-Level Stock Reconciliation")
workflow.save()
```

### Documents not updating

**Check**:
- Workflow is active: `frappe.db.get_value("Workflow", "Multi-Level Stock Reconciliation", "is_active")`
- Workflow state field exists: Check in Customize Form → Stock Reconciliation
- Documents exist: `frappe.db.count("Stock Reconciliation")`

## Example Output

```
Found 15 Stock Reconciliation document(s) to reconcile...

============================================================
Workflow State Reconciliation Summary
============================================================
Document Type: Stock Reconciliation
Workflow: Multi-Level Stock Reconciliation
Workflow State Field: workflow_state
Total Documents Processed: 15
Documents Updated: 8
Errors: 0
============================================================

✓ Successfully reconciled workflow states for 8 document(s)
```

## Notes

- The script is **idempotent** - safe to run multiple times
- It only updates documents that need fixing (empty or invalid states)
- Submitted and cancelled documents are left unchanged
- All changes are committed in a single transaction
- Errors are logged to Error Log for review

