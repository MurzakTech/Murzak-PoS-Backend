# Setup Custom Fields for Material Request Item

The dispatch and receive endpoints require custom fields to track dispatched and received quantities.

## Required Custom Fields

Add these custom fields to **Material Request Item** doctype:

1. **`custom_dispatched_qty`** - Tracks quantity dispatched from origin warehouse
2. **`custom_received_qty`** - Tracks quantity received at destination warehouse (optional, can use standard `received_qty`)

## Quick Setup via Bench Console

```bash
bench --site your-site-name console
```

Then paste this code:

```python
import frappe

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

frappe.db.commit()
frappe.clear_cache(doctype="Material Request Item")
print("✓ Setup complete!")
```

## Field Details

### custom_dispatched_qty
- **Type:** Float
- **Default:** 0
- **Read Only:** Yes (set by API)
- **Purpose:** Track how much has been dispatched from origin warehouse
- **Required:** Yes

### custom_received_qty (Optional)
- **Type:** Float
- **Default:** 0
- **Read Only:** Yes (set by API)
- **Purpose:** Track how much has been received at destination
- **Required:** No (API can use standard `received_qty` field as fallback)

## Fallback Behavior

If custom fields don't exist, the API will:
- Use `ordered_qty` field to track dispatched quantity (not ideal)
- Use standard `received_qty` field for received quantity (works fine)

**However, it's recommended to add `custom_dispatched_qty` to avoid conflicts with `ordered_qty`.**

## Verification

### Quick Check (One-liner)

```bash
bench --site your-site-name console -c "
import frappe
print('custom_dispatched_qty exists:', frappe.db.has_column('Material Request Item', 'custom_dispatched_qty'))
print('custom_received_qty exists:', frappe.db.has_column('Material Request Item', 'custom_received_qty'))
print('custom_approval_status exists:', frappe.db.has_column('Material Request', 'custom_approval_status'))
"
```

### Detailed Check (Recommended)

Use the verification script:

```bash
bench --site your-site-name console
```

Then paste:

```python
import sys
import os
sys.path.insert(0, os.path.join(os.getcwd(), 'apps', 'techsavanna_pos'))
from techsavanna_pos.api.scripts.check_custom_fields import check_custom_fields
check_custom_fields()
```

Or run directly:

```bash
bench --site your-site-name execute techsavanna_pos.api.scripts.check_custom_fields.check_custom_fields
```

### Manual Check via SQL

```bash
bench --site your-site-name mariadb
```

Then run:

```sql
-- Check if column exists in table
SHOW COLUMNS FROM `tabMaterial Request Item` LIKE 'custom_dispatched_qty';
SHOW COLUMNS FROM `tabMaterial Request` LIKE 'custom_approval_status';

-- Or check Custom Field documents
SELECT * FROM `tabCustom Field` 
WHERE dt = 'Material Request Item' 
AND fieldname = 'custom_dispatched_qty';

SELECT * FROM `tabCustom Field` 
WHERE dt = 'Material Request' 
AND fieldname = 'custom_approval_status';
```

## Troubleshooting

If you get "Unknown column" errors:
1. Verify fields were added: Check Customize Form → Material Request Item
2. Clear cache: `bench --site your-site-name clear-cache`
3. Restart bench: `bench restart`

