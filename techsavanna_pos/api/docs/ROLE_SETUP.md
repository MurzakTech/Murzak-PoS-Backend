# Role Setup for Multi-Level Stock Reconciliation

## Overview

The multi-level stock reconciliation workflow uses existing ERPNext roles. **No new roles need to be created.**

## Role Status

### ✅ All Required Roles Exist

1. **Sales User** - Standard ERPNext role (used for sales person stock take)
   - Exists by default in ERPNext
   - Used for the first level of stock counting

2. **Quality Manager** - Standard ERPNext role (used for stock controller stock take)
   - Exists by default in ERPNext
   - Used for the second level of stock counting/verification

3. **Stock Manager** - Standard ERPNext role (used for final stock take and submission)
   - Exists by default in ERPNext
   - Has full permissions for stock operations
   - Can submit stock reconciliations
   - Used for the final level of stock counting and submission

## Setup Instructions

### Run the Setup Script

The setup script will create the necessary custom fields (no roles need to be created):

```bash
cd /path/to/frappe-bench
bench console
```

Then in the console:
```python
from techsavanna_pos.api.scripts.setup_stock_reconciliation_custom_fields import setup_custom_fields
setup_custom_fields()
```

**Note:** The setup script only creates custom fields. All required roles (Sales User, Quality Manager, Stock Manager) already exist in ERPNext by default.

## Assigning Roles to Users

Assign the existing roles to users:

1. Go to **User List** (Search for "User")
2. Open the user you want to assign a role to
3. In the **Roles** section, click **Add Row**
4. Select the appropriate role:
   - **Sales User** role → for sales staff who will perform initial stock counts
   - **Quality Manager** role → for quality managers/controllers who will verify stock counts
   - **Stock Manager** role → for stock managers who will perform final counts and submit reconciliations

## Role Permissions

### Recommended Permissions

**Sales User Role:**
- Read access to Stock Reconciliation
- Write access to Stock Reconciliation (for adding their counts)
- Read access to Item, Warehouse

**Quality Manager Role:**
- Read access to Stock Reconciliation
- Write access to Stock Reconciliation (for adding their counts)
- Read access to Item, Warehouse

**Stock Manager Role:**
- Full access to Stock Reconciliation (already configured)
- Can submit Stock Reconciliation documents
- Full access to stock operations

### Setting Permissions

1. Go to **Stock Reconciliation** doctype
2. Click on **Permissions** tab
3. Verify/Add permissions for each role:
   - **Sales User**: Read, Write (for adding their stock counts)
   - **Quality Manager**: Read, Write (for adding their stock counts)
   - **Stock Manager**: Read, Write, Submit (for final counts and submission)

## Verification

To verify roles are set up correctly:

```python
# In bench console
import frappe

# Check if roles exist (all should exist by default)
roles = ["Sales User", "Quality Manager", "Stock Manager"]
for role in roles:
    exists = frappe.db.exists("Role", role)
    print(f"{role}: {'✓ Exists' if exists else '✗ Missing'}")

# Check user roles
user_email = "user@example.com"  # Replace with actual user email
user_roles = frappe.get_roles(user_email)
print(f"\nUser {user_email} has roles: {user_roles}")
```

## Troubleshooting

### Role Not Found Error

If you get an error that a role doesn't exist:

1. Verify the role exists: `frappe.db.exists("Role", "Role Name")`
2. Check role name spelling (case-sensitive)
3. All required roles (Sales User, Quality Manager, Stock Manager) should exist by default in ERPNext

### User Cannot Access

If a user cannot access the stock reconciliation:

1. Verify the user has the appropriate role assigned
2. Check role permissions on Stock Reconciliation doctype
3. Ensure the role has "Desk Access" enabled

### Permission Denied

If users get permission errors:

1. Go to Stock Reconciliation → Permissions
2. Add the role with appropriate permissions if not already present
3. Ensure "Read" and "Write" permissions are set for Sales User and Quality Manager roles
4. Ensure "Submit" permission is set for Stock Manager role

