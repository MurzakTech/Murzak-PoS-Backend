# Multi-Level Stock Reconciliation API Documentation

Complete API documentation for the multi-level stock reconciliation workflow where Sales User, Quality Manager, and Stock Manager each perform stock counts with comments, and only the Stock Manager can submit the final reconciliation.

## Base URL
```
/api/method/techsavanna_pos.api.inventory_api.{endpoint_name}
```

## Authentication
All endpoints require authentication. Include authentication headers in requests.

---

## Overview

This API provides a multi-level stock reconciliation workflow:

1. **Create Reconciliation** - Initialize a stock reconciliation document
2. **Sales User Stock Take** - Sales user counts stock and adds comments
3. **Quality Manager Stock Take** - Quality manager counts stock and adds comments
4. **Stock Manager Stock Take & Submit** - Stock manager counts stock, adds comments, and submits the reconciliation

### Workflow Flow

```
┌─────────────────────────┐
│ Create Reconciliation   │
└───────────┬─────────────┘
            │
            ▼
┌─────────────────────────┐
│ Pending Sales User       │
└───────────┬─────────────┘
            │ add_sales_person_stock_take
            ▼
┌─────────────────────────┐
│ Pending Quality Manager │
└───────────┬─────────────┘
            │ add_stock_controller_stock_take
            ▼
┌─────────────────────────┐
│ Pending Stock Manager    │
└───────────┬─────────────┘
            │ add_stock_manager_stock_take_and_submit
            ▼
┌─────────────────────────┐
│ Completed & Submitted    │
└─────────────────────────┘
```

---

## Setup

Before using these APIs, you need to set up custom fields and roles. Run the setup script:

```bash
cd /path/to/frappe-bench
bench console
```

Then in the console:
```python
from techsavanna_pos.api.scripts.setup_stock_reconciliation_custom_fields import setup_custom_fields
setup_custom_fields()
```

This will:
1. **Create custom fields** on:
   - **Stock Reconciliation Item**: Fields for each role's quantity, comment, name, and date
   - **Stock Reconciliation**: Workflow status field

**Note:** 
- This workflow uses existing ERPNext roles (no new roles need to be created):
  - **Sales User** - Standard ERPNext role (for sales person stock take)
  - **Quality Manager** - Standard ERPNext role (for stock controller stock take)
  - **Stock Manager** - Standard ERPNext role (for final stock take and submission)
- All roles exist by default in ERPNext

---

## Table of Contents

1. [Create Multi-Level Stock Reconciliation](#1-create-multi-level-stock-reconciliation)
2. [Add Sales User Stock Take](#2-add-sales-user-stock-take)
3. [Add Quality Manager Stock Take](#3-add-quality-manager-stock-take)
4. [Add Stock Manager Stock Take and Submit](#4-add-stock-manager-stock-take-and-submit)
5. [Get Multi-Level Stock Reconciliation](#5-get-multi-level-stock-reconciliation)

---

## 1. Create Multi-Level Stock Reconciliation

Creates a new stock reconciliation document for the multi-level workflow.

**Endpoint:** `create_multi_level_stock_reconciliation`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface CreateMultiLevelStockReconciliationPayload {
  warehouse: string;
  posting_date?: string; // Format: YYYY-MM-DD
  posting_time?: string; // Format: HH:MM:SS
  company?: string;
  expense_account?: string;
  cost_center?: string;
  purpose?: string; // "Stock Reconciliation" or "Opening Stock"
  items?: Array<{
    item_code: string;
  }>; // Optional initial items
}
```

### Example Request

```javascript
const createReconciliation = async () => {
  const response = await fetch('/api/method/techsavanna_pos.api.inventory_api.create_multi_level_stock_reconciliation', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      warehouse: 'Stores - HO',
      posting_date: '2025-01-20',
      company: 'Savanna Ltd',
      items: [
        { item_code: 'ITEM-001' },
        { item_code: 'ITEM-002' }
      ]
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface CreateMultiLevelStockReconciliationResponse {
  success: true;
  message: string;
  data: {
    name: string; // e.g., "MAT-RECO-2025-00001"
    company: string;
    warehouse: string;
    posting_date: string;
    docstatus: number; // 0 = Draft
    workflow_status: string; // "Pending Sales Person"
  };
}
```

---

## 2. Add Sales User Stock Take

Adds stock counts and comments from the Sales User.

**Endpoint:** `add_sales_person_stock_take`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface AddSalesPersonStockTakePayload {
  reconciliation_name: string;
  items: Array<{
    item_code: string;
    qty: number;
    comment?: string; // Optional comment for this specific item
  }>;
  comment?: string; // Optional general comment
}
```

### Example Request

```javascript
const addSalesPersonStockTake = async (reconciliationName) => {
  const response = await fetch('/api/method/techsavanna_pos.api.inventory_api.add_sales_person_stock_take', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      reconciliation_name: 'MAT-RECO-2025-00001',
      items: [
        { 
          item_code: 'ITEM-001', 
          qty: 100,
          comment: 'Counted all items in main aisle'
        },
        { 
          item_code: 'ITEM-002', 
          qty: 50,
          comment: 'Some items in back storage'
        }
      ],
      comment: 'Completed initial count for all items'
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface AddSalesPersonStockTakeResponse {
  success: true;
  message: string;
  data: {
    reconciliation_name: string;
    items_counted: number;
    workflow_status: string; // "Pending Quality Manager"
  };
}
```

---

## 3. Add Quality Manager Stock Take

Adds stock counts and comments from the Quality Manager.

**Endpoint:** `add_stock_controller_stock_take`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface AddStockControllerStockTakePayload {
  reconciliation_name: string;
  items: Array<{
    item_code: string;
    qty: number;
    comment?: string; // Optional comment for this specific item
  }>;
  comment?: string; // Optional general comment
}
```

### Example Request

```javascript
const addStockControllerStockTake = async (reconciliationName) => {
  const response = await fetch('/api/method/techsavanna_pos.api.inventory_api.add_stock_controller_stock_take', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      reconciliation_name: 'MAT-RECO-2025-00001',
      items: [
        { 
          item_code: 'ITEM-001', 
          qty: 98, // Different count from sales person
          comment: 'Found 2 damaged items, excluded from count'
        },
        { 
          item_code: 'ITEM-002', 
          qty: 52, // Different count
          comment: 'Found additional items in overflow area'
        }
      ],
      comment: 'Verified counts and found discrepancies'
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface AddStockControllerStockTakeResponse {
  success: true;
  message: string;
  data: {
    reconciliation_name: string;
    items_counted: number;
    workflow_status: string; // "Pending Stock Manager"
  };
}
```

---

## 4. Add Stock Manager Stock Take and Submit

Adds stock counts and comments from the Stock Manager and optionally submits the reconciliation. **Only users with Stock Manager role can submit.**

**Endpoint:** `add_stock_manager_stock_take_and_submit`  
**Method:** `POST`  
**Auth Required:** Yes  
**Role Required:** Stock Manager (for submission)

### Request Payload

```typescript
interface AddStockManagerStockTakePayload {
  reconciliation_name: string;
  items: Array<{
    item_code: string;
    qty: number; // This becomes the final quantity for reconciliation
    comment?: string; // Optional comment for this specific item
  }>;
  comment?: string; // Optional general comment
  submit?: boolean; // Whether to submit (default: true)
}
```

### Example Request

```javascript
const addStockManagerStockTake = async (reconciliationName) => {
  const response = await fetch('/api/method/techsavanna_pos.api.inventory_api.add_stock_manager_stock_take_and_submit', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      reconciliation_name: 'MAT-RECO-2025-00001',
      items: [
        { 
          item_code: 'ITEM-001', 
          qty: 99, // Final count (reconciliation will use this)
          comment: 'Adjusted for damaged items, final count verified'
        },
        { 
          item_code: 'ITEM-002', 
          qty: 51, // Final count
          comment: 'Reconciled with sales person and controller counts'
        }
      ],
      comment: 'Final reconciliation completed and approved',
      submit: true // Submit the reconciliation
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface AddStockManagerStockTakeResponse {
  success: true;
  message: string;
  data: {
    reconciliation_name: string;
    items_counted: number;
    workflow_status: string; // "Completed"
    submission: {
      submitted: boolean;
      docstatus?: number; // 1 if submitted
      error?: string; // If submission failed
    };
    docstatus: number;
  };
}
```

### Error Response (No Stock Manager Role)

```typescript
{
  success: false;
  message: "Only users with Stock Manager role can submit stock reconciliation.";
}
```

---

## 5. Get Multi-Level Stock Reconciliation

Gets a stock reconciliation document with all levels of stock taking records.

**Endpoint:** `get_multi_level_stock_reconciliation`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

### Request Parameters

```typescript
interface GetMultiLevelStockReconciliationParams {
  reconciliation_name: string;
}
```

### Example Request

```javascript
const getReconciliation = async (reconciliationName) => {
  const response = await fetch(
    `/api/method/techsavanna_pos.api.inventory_api.get_multi_level_stock_reconciliation?reconciliation_name=${reconciliationName}`,
    {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      }
    }
  );
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface GetMultiLevelStockReconciliationResponse {
  success: true;
  data: {
    name: string;
    company: string;
    warehouse: string;
    posting_date: string;
    posting_time: string;
    purpose: string;
    docstatus: number;
    workflow_status: string;
    stock_taking_records: Array<{
      item_code: string;
      warehouse: string;
      final_qty: number; // Final quantity used for reconciliation
      current_qty: number; // Current system quantity
      sales_person_qty?: number;
      sales_person_comment?: string;
      sales_person_name?: string;
      sales_person_date?: string;
      stock_controller_qty?: number;
      stock_controller_comment?: string;
      stock_controller_name?: string;
      stock_controller_date?: string;
      stock_manager_qty?: number;
      stock_manager_comment?: string;
      stock_manager_name?: string;
      stock_manager_date?: string;
    }>;
    items: Array<{
      item_code: string;
      warehouse: string;
      qty: number;
      current_qty: number;
    }>;
  };
}
```

---

## Complete Workflow Example

```javascript
// Step 1: Create reconciliation
const reconciliation = await createReconciliation();
const reconciliationName = reconciliation.data.name;

// Step 2: Sales User adds stock take
await addSalesPersonStockTake(reconciliationName);

// Step 3: Quality Manager adds stock take
await addStockControllerStockTake(reconciliationName);

// Step 4: Stock Manager adds stock take and submits
await addStockManagerStockTake(reconciliationName);

// Step 5: Get final reconciliation with all counts
const finalReconciliation = await getReconciliation(reconciliationName);
console.log('All stock taking records:', finalReconciliation.data.stock_taking_records);
```

---

## Workflow Status Values

| Status | Description | Next Action |
|--------|-------------|-------------|
| `Pending Sales User` | Initial state, waiting for sales user | Use `add_sales_person_stock_take` |
| `Pending Quality Manager` | Sales user completed, waiting for quality manager | Use `add_stock_controller_stock_take` |
| `Pending Stock Manager` | Quality manager completed, waiting for stock manager | Use `add_stock_manager_stock_take_and_submit` |
| `Completed` | Stock manager completed, ready for submission | Already completed |

---

## Important Notes

1. **Role Requirements**: Only users with "Stock Manager" role can submit the reconciliation.

2. **Item Addition**: Items can be added at any stage. If an item doesn't exist in the items table when a role adds their count, it will be automatically added.

3. **Final Quantity**: The Stock Manager's quantity becomes the final quantity used for reconciliation (stored in the `qty` field of Stock Reconciliation Item).

4. **Comments**: 
   - Each role can add a general comment for the entire stock take
   - Each item can have its own specific comment
   - All comments are preserved and visible in the final reconciliation

5. **Workflow Status**: The workflow status is automatically updated as each role completes their stock take.

6. **Document Status**: 
   - `docstatus = 0`: Draft (can be modified)
   - `docstatus = 1`: Submitted (final, cannot be modified)

7. **Custom Fields**: The custom fields must be set up before using these APIs. Run the setup script first.

---

## Error Handling

All endpoints return consistent error responses:

```typescript
interface ErrorResponse {
  success: false;
  message: string; // Error description
}
```

### Common HTTP Status Codes:
- `400` - Bad Request (missing/invalid parameters)
- `401` - Unauthorized (authentication required)
- `403` - Forbidden (insufficient permissions, e.g., not Stock Manager)
- `404` - Not Found (reconciliation doesn't exist)
- `422` - Validation Error

---

## React Hook Example

```typescript
import { useState, useCallback } from 'react';

export const useMultiLevelStockReconciliation = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiCall = useCallback(async (endpoint: string, payload: any) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/method/techsavanna_pos.api.inventory_api.${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!data.success) {
        throw new Error(data.message);
      }
      return data;
    } catch (err: any) {
      setError(err.message || 'An error occurred');
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const createReconciliation = useCallback((payload) => 
    apiCall('create_multi_level_stock_reconciliation', payload), [apiCall]);

  const addSalesPersonStockTake = useCallback((payload) => 
    apiCall('add_sales_person_stock_take', payload), [apiCall]);

  const addStockControllerStockTake = useCallback((payload) => 
    apiCall('add_stock_controller_stock_take', payload), [apiCall]);

  const addStockManagerStockTake = useCallback((payload) => 
    apiCall('add_stock_manager_stock_take_and_submit', payload), [apiCall]);

  const getReconciliation = useCallback(async (reconciliationName) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/method/techsavanna_pos.api.inventory_api.get_multi_level_stock_reconciliation?reconciliation_name=${reconciliationName}`
      );
      const data = await response.json();
      if (!data.success) {
        throw new Error(data.message);
      }
      return data;
    } catch (err: any) {
      setError(err.message || 'An error occurred');
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    createReconciliation,
    addSalesPersonStockTake,
    addStockControllerStockTake,
    addStockManagerStockTake,
    getReconciliation,
    loading,
    error
  };
};
```

---

## Best Practices

1. **Sequential Workflow**: Follow the workflow sequence (Sales Person → Controller → Manager) for best results.

2. **Item Validation**: Always validate that items exist before adding them to the reconciliation.

3. **Comment Clarity**: Add clear, descriptive comments at both item and general levels to maintain audit trail.

4. **Error Handling**: Implement proper error handling for all API calls, especially for role-based restrictions.

5. **Status Checking**: Check workflow status before allowing users to add their stock take to ensure proper sequence.

6. **Reconciliation Review**: Use `get_multi_level_stock_reconciliation` to review all counts before final submission.

---

## Troubleshooting

### Custom Fields Not Found
**Error**: Fields like `sales_person_qty` not found on items

**Solution**: Run the setup script to create custom fields:
```python
from techsavanna_pos.api.scripts.setup_stock_reconciliation_custom_fields import setup_custom_fields
setup_custom_fields()
```

### Cannot Submit
**Error**: "Only users with Stock Manager role can submit"

**Solution**: Ensure the user has the "Stock Manager" role assigned in ERPNext.

### Workflow Status Not Updating
**Solution**: Ensure the custom field `workflow_status` exists on Stock Reconciliation doctype. Run the setup script if needed.

---

## Changelog

### Version 1.0 (2025-01-20)
- Initial release
- Multi-level stock reconciliation workflow
- Support for Sales Person, Stock Controller, and Stock Manager roles
- Comment tracking at item and document level
- Workflow status tracking

