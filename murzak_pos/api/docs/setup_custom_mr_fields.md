# Setup Custom Fields for Material Request

The dispatch and receive endpoints require custom fields to track dispatch and receive information.

## Required Custom Fields

Add these custom fields to **Material Request** doctype:

1. **`custom_dispatched_by`** - Email of person who dispatched
2. **`custom_dispatch_notes`** - Notes about dispatch
3. **`custom_last_dispatch_on`** - Timestamp of last dispatch
4. **`custom_received_by`** - Email of person who received
5. **`custom_receive_notes`** - Notes about receipt
6. **`custom_received_on`** - Timestamp of receipt
7. **`custom_goods_received_note`** - GRN reference number

## Quick Setup via Bench Console

```bash
bench --site your-site-name console
```

Then paste this code:

```python
import frappe

fields_to_add = [
    {
        "fieldname": "custom_dispatched_by",
        "label": "Dispatched By",
        "fieldtype": "Data",
        "insert_after": "status"
    },
    {
        "fieldname": "custom_dispatch_notes",
        "label": "Dispatch Notes",
        "fieldtype": "Small Text",
        "insert_after": "custom_dispatched_by"
    },
    {
        "fieldname": "custom_last_dispatch_on",
        "label": "Last Dispatch On",
        "fieldtype": "Datetime",
        "insert_after": "custom_dispatch_notes"
    },
    {
        "fieldname": "custom_received_by",
        "label": "Received By",
        "fieldtype": "Data",
        "insert_after": "custom_last_dispatch_on"
    },
    {
        "fieldname": "custom_receive_notes",
        "label": "Receive Notes",
        "fieldtype": "Small Text",
        "insert_after": "custom_received_by"
    },
    {
        "fieldname": "custom_received_on",
        "label": "Received On",
        "fieldtype": "Datetime",
        "insert_after": "custom_receive_notes"
    },
    {
        "fieldname": "custom_goods_received_note",
        "label": "Goods Received Note",
        "fieldtype": "Data",
        "insert_after": "custom_received_on"
    }
]

for field_config in fields_to_add:
    fieldname = field_config["fieldname"]
    
    # Check if field already exists
    if frappe.db.exists("Custom Field", {"dt": "Material Request", "fieldname": fieldname}):
        print(f"✓ {fieldname} already exists")
        continue
    
    # Check if column exists (might exist without Custom Field doc)
    if frappe.db.has_column("Material Request", fieldname):
        print(f"✓ {fieldname} column exists")
        continue
    
    # Create custom field
    cf = frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Material Request",
        **field_config,
        "read_only": 1,
        "allow_on_submit": 1
    })
    cf.insert(ignore_permissions=True)
    print(f"✓ Added {fieldname}")

frappe.db.commit()
frappe.clear_cache(doctype="Material Request")
print("\n✓ Setup complete!")
```

## Field Details

| Field Name | Label | Type | Purpose |
|-----------|-------|------|---------|
| `custom_dispatched_by` | Dispatched By | Data | Email of person who dispatched stock |
| `custom_dispatch_notes` | Dispatch Notes | Small Text | Notes about the dispatch |
| `custom_last_dispatch_on` | Last Dispatch On | Datetime | Timestamp of dispatch |
| `custom_received_by` | Received By | Data | Email of person who received stock |
| `custom_receive_notes` | Receive Notes | Small Text | Notes about the receipt |
| `custom_received_on` | Received On | Datetime | Timestamp of receipt |
| `custom_goods_received_note` | Goods Received Note | Data | GRN reference number |

## Verification

After adding fields, verify:

```bash
bench --site your-site-name console -c "
import frappe
fields = ['custom_dispatched_by', 'custom_received_by', 'custom_goods_received_note']
for field in fields:
    exists = frappe.db.has_column('Material Request', field)
    print(f'{field}: {exists}')
"
```

## Fallback Behavior

If custom fields don't exist, the API will:
- Try to use standard field names (if they exist)
- Skip setting the field if neither custom nor standard field exists
- Continue processing without errors

However, the information won't be stored, so it's recommended to add these fields.

## Troubleshooting

If you get "Unknown column" errors:
1. Verify fields were added: Check Customize Form → Material Request
2. Clear cache: `bench --site your-site-name clear-cache`
3. Restart bench: `bench restart`
4. Check field names match exactly (case-sensitive)

