# Approaches for Handling "Approved" Status in Material Request

## Problem
Material Request's `status` field is automatically calculated by ERPNext based on `per_ordered` and `per_received` fields. Overriding it directly can cause conflicts and be overridden by the system.

## Recommended Approaches (Best to Least Preferred)

### Option 1: Use Custom Field (Recommended) ⭐

**Best Practice:** Add a custom field `custom_approval_status` to Material Request doctype.

**Steps:**
1. Go to Material Request doctype customization
2. Add a custom field:
   - Field Name: `custom_approval_status`
   - Field Type: `Select`
   - Options: `\nPending Approval\nApproved\nRejected`
   - Label: `Approval Status`

**Code Implementation:**
```python
# Set custom approval status instead of main status
doc.approved_by = approved_by
doc.approval_notes = approval_notes or ""
doc.approved_on = frappe.utils.now()
doc.custom_approval_status = "Approved"
doc.save(ignore_permissions=True)
```

**Pros:**
- ✅ Doesn't interfere with ERPNext's automatic status calculation
- ✅ Clear separation of concerns
- ✅ Can be used for filtering and reporting
- ✅ Follows ERPNext best practices

**Cons:**
- Requires adding a custom field to the doctype

---

### Option 2: Check `approved_by` Field Existence

**Approach:** Use the existence of `approved_by` field to determine approval state, don't change status.

**Code Implementation:**
```python
doc.approved_by = approved_by
doc.approval_notes = approval_notes or ""
doc.approved_on = frappe.utils.now()
doc.save(ignore_permissions=True)

# In your API responses, check approved_by to determine if approved
is_approved = bool(doc.approved_by)
effective_status = "Approved" if is_approved else doc.status
```

**In Frontend/API:**
```javascript
// Check if approved
const isApproved = request.approved_by !== null && request.approved_by !== "";
const displayStatus = isApproved ? "Approved" : request.status;
```

**Pros:**
- ✅ No doctype changes needed
- ✅ Works immediately
- ✅ Doesn't conflict with ERPNext status

**Cons:**
- Status field won't show "Approved" directly
- Need to check `approved_by` in queries/filters

---

### Option 3: Extend Status Updater Rules

**Approach:** Modify ERPNext's status updater rules to include "Approved" as a valid status.

**Location:** `apps/erpnext/erpnext/controllers/status_updater.py`

**Modification:**
```python
"Material Request": [
    ["Draft", None],
    ["Stopped", "eval:self.status == 'Stopped'"],
    ["Cancelled", "eval:self.docstatus == 2"],
    ["Approved", "eval:self.approved_by and self.docstatus == 1 and self.per_ordered == 0"],  # Add this
    ["Pending", "eval:self.status != 'Stopped' and self.per_ordered == 0 and self.docstatus == 1 and not self.approved_by"],
    # ... rest of rules
]
```

**Code Implementation:**
```python
doc.approved_by = approved_by
doc.approval_notes = approval_notes or ""
doc.approved_on = frappe.utils.now()
doc.save(ignore_permissions=True)
# Status will be automatically set to "Approved" by status updater
```

**Pros:**
- ✅ Status field shows "Approved" directly
- ✅ Works with ERPNext's status system

**Cons:**
- ⚠️ Requires modifying core ERPNext files (not recommended for upgrades)
- ⚠️ May conflict with other status rules
- ⚠️ Need to add "Approved" to status field options in Material Request JSON

---

### Option 4: Use `transfer_status` Field (For Material Transfer Only)

**Approach:** Use the existing `transfer_status` field, but extend it to include "Approved".

**Steps:**
1. Modify Material Request doctype to add "Approved" to `transfer_status` options:
   - Current: `\nNot Started\nIn Transit\nCompleted`
   - New: `\nNot Started\nApproved\nIn Transit\nCompleted`

**Code Implementation:**
```python
doc.approved_by = approved_by
doc.approval_notes = approval_notes or ""
doc.approved_on = frappe.utils.now()
if doc.material_request_type == "Material Transfer":
    doc.transfer_status = "Approved"
doc.save(ignore_permissions=True)
```

**Pros:**
- ✅ Uses existing field designed for transfer lifecycle
- ✅ Makes semantic sense for Material Transfer

**Cons:**
- Only works for Material Transfer type
- Requires modifying the field options

---

## Recommended Implementation

**For your use case, I recommend Option 1 (Custom Field) or Option 2 (Check approved_by).**

### If you choose Option 1 (Custom Field):

1. Add the custom field via Customize Form in ERPNext UI
2. Update the code to set `custom_approval_status = "Approved"`
3. Use `custom_approval_status` for filtering and display in your frontend

### If you choose Option 2 (Check approved_by):

1. No doctype changes needed
2. Update your `list_stock_transfer_requests` to include approval status:
   ```python
   is_approved = bool(getattr(mr_doc, "approved_by", None))
   results.append({
       ...
       "status": mr.status,  # ERPNext's calculated status
       "approval_status": "Approved" if is_approved else "Pending Approval",
       "approved_by": getattr(mr_doc, "approved_by", "") or "",
       ...
   })
   ```

## Current Code Behavior

The current implementation uses `db_set()` to override the status. This works but:
- ⚠️ May be overridden by ERPNext's status updater on next save
- ⚠️ Not following ERPNext best practices
- ⚠️ Could cause conflicts with other processes

## Migration Path

If you want to switch from current approach:

1. **Immediate:** Use Option 2 (check `approved_by`) - no changes needed
2. **Short-term:** Add custom field (Option 1) and migrate existing data
3. **Long-term:** Consider workflow (if you need complex approval processes)

