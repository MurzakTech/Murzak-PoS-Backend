# How to Create a Stock Transfer Request (Material Request)

## Overview

In `stock.py`, there is **no API endpoint to create Material Requests**. The existing APIs work with **already created** Material Requests. To create a stock transfer request, you need to create a Material Request document first.

## Two Options

### Option 1: Use Frappe's Standard Resource API (Recommended)

Create Material Requests using Frappe's standard resource API:

**Endpoint:** `POST /api/resource/Material Request`

**URL:** `/api/resource/Material Request`

**Request Body:**
```json
{
  "company": "Savanna Ltd",
  "transaction_date": "2025-01-20",
  "material_request_type": "Material Transfer",
  "set_from_warehouse": "Stores - HO",
  "set_warehouse": "Stores - Branch",
  "items": [
    {
      "item_code": "ITEM-001",
      "qty": 10,
      "uom": "Nos",
      "s_warehouse": "Stores - HO",
      "warehouse": "Stores - Branch"
    },
    {
      "item_code": "ITEM-002",
      "qty": 5,
      "uom": "Nos",
      "s_warehouse": "Stores - HO",
      "warehouse": "Stores - Branch"
    }
  ]
}
```

**React.js Example:**
```javascript
const createMaterialRequest = async (requestData) => {
  try {
    const response = await fetch('/api/resource/Material Request', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        company: requestData.company,
        transaction_date: requestData.transactionDate,
        material_request_type: 'Material Transfer',
        set_from_warehouse: requestData.fromWarehouse,
        set_warehouse: requestData.toWarehouse,
        items: requestData.items.map(item => ({
          item_code: item.itemCode,
          qty: item.qty,
          uom: item.uom || 'Nos',
          s_warehouse: requestData.fromWarehouse,
          warehouse: requestData.toWarehouse
        }))
      })
    });

    const result = await response.json();
    return result.data;
  } catch (error) {
    console.error('Error creating Material Request:', error);
    throw error;
  }
};

// Usage
const materialRequest = await createMaterialRequest({
  company: 'Savanna Ltd',
  transactionDate: '2025-01-20',
  fromWarehouse: 'Stores - HO',
  toWarehouse: 'Stores - Branch',
  items: [
    { itemCode: 'ITEM-001', qty: 10, uom: 'Nos' },
    { itemCode: 'ITEM-002', qty: 5, uom: 'Nos' }
  ]
});

console.log('Material Request created:', materialRequest.name);
```

**Note:** After creating, you'll need to submit it:
```javascript
// Submit the Material Request
const submitMaterialRequest = async (requestName) => {
  const response = await fetch(`/api/resource/Material Request/${requestName}`, {
    method: 'PUT',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      docstatus: 1  // 1 = Submitted
    })
  });
  return response.json();
};
```

---

### Option 2: Add a Custom API Endpoint to stock.py

If you want a custom endpoint in `stock.py`, you can add this function:

```python
@frappe.whitelist(methods=["POST"])
def create_stock_transfer_request():
    """
    Create a Material Request for stock transfer
    
    Payload:
    {
        "company": "Savanna Ltd",
        "transaction_date": "2025-01-20",
        "from_warehouse": "Stores - HO",
        "to_warehouse": "Stores - Branch",
        "items": [
            {"item_code": "ITEM-001", "qty": 10},
            {"item_code": "ITEM-002", "qty": 5}
        ],
        "schedule_date": "2025-01-25",  // Optional
        "submit": true  // Optional: submit immediately
    }
    """
    try:
        data = frappe.request.get_json() or frappe.local.form_dict
        
        # Validation
        required_fields = ["company", "from_warehouse", "to_warehouse", "items"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return error_response(f"Missing required fields: {', '.join(missing)}", 400)
        
        if not isinstance(data.get("items"), list) or not data["items"]:
            return error_response("Items must be a non-empty list", 400)
        
        # Create Material Request
        mr = frappe.new_doc("Material Request")
        mr.company = data["company"]
        mr.transaction_date = data.get("transaction_date") or frappe.utils.today()
        mr.material_request_type = "Material Transfer"
        mr.set_from_warehouse = data["from_warehouse"]
        mr.set_warehouse = data["to_warehouse"]
        mr.schedule_date = data.get("schedule_date") or mr.transaction_date
        
        # Add items
        for item in data["items"]:
            if not item.get("item_code") or not item.get("qty"):
                return error_response("Each item must have item_code and qty", 400)
            
            mr.append("items", {
                "item_code": item["item_code"],
                "qty": item["qty"],
                "uom": item.get("uom"),
                "s_warehouse": data["from_warehouse"],
                "warehouse": data["to_warehouse"]
            })
        
        # Insert
        mr.insert(ignore_permissions=True)
        
        # Submit if requested
        should_submit = data.get("submit", False)
        if should_submit:
            mr.submit()
        
        return success_response(
            "Stock transfer request created successfully",
            {
                "material_request": mr.name,
                "status": mr.status,
                "docstatus": mr.docstatus
            }
        )
    
    except ValidationError as e:
        return error_response(str(e), 422)
    except Exception as e:
        frappe.log_error("Create Stock Transfer Request Failed", frappe.get_traceback())
        return error_response(f"Failed to create request: {str(e)}", 500)
```

Then add it to `stock.py` and use it:

**React.js Example with Custom API:**
```javascript
const createStockTransferRequest = async (requestData) => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.create_stock_transfer_request', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        company: requestData.company,
        transaction_date: requestData.transactionDate,
        from_warehouse: requestData.fromWarehouse,
        to_warehouse: requestData.toWarehouse,
        items: requestData.items,
        schedule_date: requestData.scheduleDate,
        submit: requestData.submit || false
      })
    });

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error creating stock transfer request:', error);
    throw error;
  }
};
```

---

## Complete Workflow Example

Here's how the complete workflow looks:

```javascript
// Step 1: Create Material Request (using Option 1)
const materialRequest = await createMaterialRequest({
  company: 'Savanna Ltd',
  transactionDate: '2025-01-20',
  fromWarehouse: 'Stores - HO',
  toWarehouse: 'Stores - Branch',
  items: [
    { itemCode: 'ITEM-001', qty: 10 }
  ]
});

// Submit it
await submitMaterialRequest(materialRequest.name);

// Step 2: Approve (if workflow enabled)
await approveStockTransferWorkflow({
  requestId: materialRequest.name,
  approvedBy: 'manager@example.com'
});

// Step 3: Dispatch
await dispatchStock({
  requestId: materialRequest.name,
  originWarehouse: 'Stores - HO',
  items: [{ item_code: 'ITEM-001', dispatched_qty: 10 }],
  dispatchedBy: 'warehouse@example.com'
});

// Step 4: Receive
await receiveStockDestination({
  requestId: materialRequest.name,
  destinationWarehouse: 'Stores - Branch',
  items: [{ item_code: 'ITEM-001', received_qty: 10 }],
  receivedBy: 'branch@example.com'
});
```

---

## Material Request Fields Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `transaction_date` | date | Yes | Request date (YYYY-MM-DD) |
| `material_request_type` | string | Yes | Must be "Material Transfer" |
| `set_from_warehouse` | string | Yes | Source warehouse |
| `set_warehouse` | string | Yes | Destination warehouse |
| `schedule_date` | date | No | Expected transfer date |
| `items` | array | Yes | Array of items |

**Item Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `item_code` | string | Yes | Item code |
| `qty` | number | Yes | Quantity to transfer |
| `uom` | string | No | Unit of measure (defaults to stock UOM) |
| `s_warehouse` | string | Yes | Source warehouse (usually same as set_from_warehouse) |
| `warehouse` | string | Yes | Destination warehouse (usually same as set_warehouse) |

---

## Summary

1. **stock.py does NOT have an API to create Material Requests**
2. Use Frappe's standard `/api/resource/Material Request` API (Option 1)
3. Or add a custom endpoint to stock.py (Option 2)
4. After creating, use the existing stock.py APIs:
   - `approve_stock_transfer()` or `approve_stock_transfer_workflow()`
   - `dispatch_stock()`
   - `receive_stock_destination()`

