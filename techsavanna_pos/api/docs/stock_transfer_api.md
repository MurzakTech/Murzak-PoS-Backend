# Stock Transfer API Documentation

Complete API documentation for managing stock transfers through Material Requests (with approval workflow) and direct Stock Entry transfers.

## Base URL
```
/api/method/techsavanna_pos.api.stock.{endpoint_name}
```

## Authentication
All endpoints require authentication (except where noted). Include authentication headers in requests.

---

## Overview

This API provides two approaches for stock transfers:

1. **Material Request Flow** (Recommended for approval workflows):
   - Create → Submit → Approve → Dispatch → Receive
   - Supports approval workflows and tracking
   - Best for multi-step processes requiring authorization

2. **Direct Stock Entry Transfer** (Quick transfers):
   - Create and submit Stock Entry directly
   - Immediate stock movement
   - Best for simple, immediate transfers

---

## Understanding Status vs Approval Status

**Important:** The Material Request API uses a two-field approach for tracking request state:

1. **`status`** - Automatically calculated by ERPNext based on `per_ordered` and `per_received` fields. This field may show:
   - `Draft`, `Submitted`, `Pending` - Request lifecycle states
   - `In Transit`, `Partially In Transit` - Stock dispatch states
   - `Completed`, `Partially Received` - Stock receipt states
   - `Cancelled` - Cancelled state

2. **`approval_status`** - Indicates approval state (not calculated by ERPNext):
   - `"Approved"` - Request has been approved (when `approved_by` field is set)
   - `"Pending Approval"` - Request is submitted but not yet approved

**Key Points:**
- The `status` field does NOT change to "Approved" after approval
- Always check `approval_status` or `is_approved` flag to determine if a request is approved
- Use `is_approved: true` or `approval_status === "Approved"` to filter approved requests
- The `approved_by` field contains the approver's email when approved

**Example:**
```javascript
// ❌ Don't check status field for approval
if (request.status === "Approved") { ... } // This won't work!

// ✅ Check approval_status or is_approved instead
if (request.is_approved) { ... }
if (request.approval_status === "Approved") { ... }
```

---

## Table of Contents

### Direct Stock Transfer (Quick Transfer)
- [Create Direct Stock Transfer](#create-direct-stock-transfer)

### Material Request Flow (Approval Workflow)
1. [Create Stock Transfer Request](#1-create-stock-transfer-request)
2. [Submit Stock Transfer Request](#2-submit-stock-transfer-request)
3. [Approve Stock Transfer Request](#3-approve-stock-transfer-request)
4. [Dispatch Stock](#4-dispatch-stock)
5. [Receive Stock at Destination](#5-receive-stock-at-destination)
6. [Cancel Stock Transfer Request](#6-cancel-stock-transfer-request)
7. [List Stock Transfer Requests](#7-list-stock-transfer-requests)
8. [Get Single Stock Transfer Request](#8-get-single-stock-transfer-request)

---

## Create Direct Stock Transfer

Creates a Stock Entry of type "Material Transfer" directly, moving stock between warehouses immediately without requiring approval workflow.

**Endpoint:** `create_stock_transfer`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface CreateStockTransferPayload {
  company: string;
  posting_date: string; // Format: YYYY-MM-DD
  posting_time: string; // Format: HH:MM:SS
  from_warehouse: string; // Source warehouse
  to_warehouse: string; // Destination warehouse
  items: Array<{
    item_code: string;
    qty: number; // Must be greater than 0
  }>;
  notes?: string; // Optional remarks
}
```

### Example Request

```javascript
const createDirectStockTransfer = async () => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.create_stock_transfer', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      // Add authentication headers
    },
    body: JSON.stringify({
      company: 'Savanna Ltd',
      posting_date: '2025-01-20',
      posting_time: '10:30:00',
      from_warehouse: 'Stores - HO',
      to_warehouse: 'Stores - Branch',
      items: [
        { item_code: 'ITEM-001', qty: 10 },
        { item_code: 'ITEM-002', qty: 5 }
      ],
      notes: 'Direct transfer to branch store'
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface CreateStockTransferResponse {
  status: "success";
  message: string; // "Stock transfer completed successfully"
  stock_entry: string; // e.g., "MAT-STE-2025-00001"
}
```

### Error Response

```typescript
interface ErrorResponse {
  status: "error";
  message: string; // Error description
}
```

### Common Errors

- **400 Bad Request**: Missing required fields or invalid data
- **403 Forbidden**: Insufficient permissions
- **422 Validation Error**: Insufficient stock, invalid warehouse, etc.

### Notes

- Stock Entry is automatically submitted upon creation
- Stock is immediately moved from source to destination warehouse
- No approval workflow is required
- Use this for simple, immediate transfers
- For approval workflows, use the Material Request flow instead

---

## 1. Create Stock Transfer Request

Creates a new Material Request for stock transfer in `Draft` status.

**Endpoint:** `create_stock_transfer_request`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface CreateStockTransferRequestPayload {
  company: string;
  transaction_date?: string; // Optional, defaults to today
  from_warehouse: string; // Origin warehouse
  to_warehouse: string; // Destination warehouse
  items: Array<{
    item_code: string;
    qty: number;
    uom?: string; // Optional unit of measure
  }>;
  schedule_date?: string; // Optional, defaults to transaction_date
  submit?: boolean; // Optional, submit immediately (default: false)
}
```

### Example Request

```javascript
const createStockTransferRequest = async () => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.create_stock_transfer_request', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      // Add authentication headers
    },
    body: JSON.stringify({
      company: 'Savanna Ltd',
      transaction_date: '2025-01-20',
      from_warehouse: 'Stores - HO',
      to_warehouse: 'Stores - Branch',
      items: [
        { item_code: 'ITEM-001', qty: 10, uom: 'Nos' },
        { item_code: 'ITEM-002', qty: 5, uom: 'Nos' }
      ],
      schedule_date: '2025-01-25',
      submit: false // Set to true to submit immediately
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface CreateStockTransferRequestResponse {
  success: true;
  message: string;
  data: {
    material_request: string; // e.g., "MAT-MR-2025-00001"
    status: string; // e.g., "Draft" or "Submitted"
    docstatus: number; // 0 = Draft, 1 = Submitted
    submitted: boolean;
  };
}
```

### Error Response

```typescript
interface ErrorResponse {
  success: false;
  message: string;
}
```

---

## 2. Submit Stock Transfer Request

Submits a Material Request from `Draft` to `Submitted` status.

**Endpoint:** `submit_stock_transfer_request`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface SubmitStockTransferRequestPayload {
  request_id: string; // Material Request name
}
```

### Example Request

```javascript
const submitStockTransferRequest = async (requestId) => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.submit_stock_transfer_request', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      request_id: 'MAT-MR-2025-00001'
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface SubmitStockTransferRequestResponse {
  success: true;
  message: string;
  data: {
    request_id: string;
    status: string; // "Submitted"
    docstatus: number; // 1
  };
}
```

---

## 3. Approve Stock Transfer Request

Approves a submitted Material Request. Sets approval fields (`approved_by`, `approval_notes`, `approved_on`) which marks the request as approved.

**Note:** 
- In ERPNext, a submitted Material Request may show status as `Pending` (which means it's submitted but not yet processed). You can use this endpoint to approve requests with either `Submitted` or `Pending` status.
- The `status` field is automatically calculated by ERPNext and may remain as "Pending" or "Submitted" even after approval. Use the `approval_status` field or `is_approved` flag in API responses to determine approval state.

**Endpoints:**
- `approve_stock_transfer` - Use this for standard approval (no workflow required)
- `approve_stock_transfer_workflow` - Use this **only if** a workflow is configured for Material Request in your system

**Method:** `POST`  
**Auth Required:** Yes

### Important: Which Endpoint to Use?

- **Use `approve_stock_transfer`** if:
  - No workflow is configured for Material Request
  - You're unsure whether workflow is configured
  - You want a simpler approval process

- **Use `approve_stock_transfer_workflow`** only if:
  - A workflow is explicitly configured for Material Request in your ERPNext instance
  - You need workflow state transitions and workflow-specific features
  - The system administrator has set up a workflow

**If you use `approve_stock_transfer_workflow` without a configured workflow, you'll get an error.**

### Request Payload

```typescript
interface ApproveStockTransferRequestPayload {
  request_id: string;
  approved_by: string; // Email of approver
  approval_notes?: string; // Optional notes
}
```

### Example Request

```javascript
const approveStockTransferRequest = async (requestId, approvedBy, notes) => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.approve_stock_transfer', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      request_id: requestId,
      approved_by: approvedBy,
      approval_notes: notes || ''
    })
  });
  
  const data = await response.json();
  return data;
};

// Example: Approving a request with "Pending" status
// When status is "Pending", use the same approve endpoint
const approvePendingRequest = async () => {
  // If your request shows status as "Pending", use approve_stock_transfer
  const result = await approveStockTransferRequest(
    'MAT-MR-2025-00001',
    'manager@example.com',
    'Approved for dispatch'
  );
  console.log('Request approved:', result);
};
```

### Success Response

```typescript
interface ApproveStockTransferRequestResponse {
  success: true;
  message: string;
  data: {
    request_id: string;
    status: string; // ERPNext's calculated status (may be "Pending" or "Submitted")
    approval_status: string; // "Approved" - effective approval status for display
    approved_by: string;
    is_approved: boolean; // true - boolean flag for easy checking
    custom_approval_status?: string; // Only present if custom field exists
  };
}
```

**Important:** The `status` field remains as ERPNext calculates it (typically "Pending" or "Submitted" for submitted requests). Use the `approval_status` field or `is_approved` flag to determine if the request is approved.

---

## 4. Dispatch Stock

Dispatches stock from the origin warehouse, moving the request from approved state to `In Transit` or `Partially In Transit`.

**Note:** The request must be approved (have `approved_by` field set) before dispatch. Check `is_approved` or `approval_status` field to verify approval.

**Endpoint:** `dispatch_stock`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface DispatchStockPayload {
  request_id: string;
  origin_warehouse: string;
  items: Array<{
    item_code: string;
    dispatched_qty: number;
  }>;
  dispatched_by: string; // Email of person dispatching
  dispatch_notes?: string; // Optional notes
}
```

### Example Request

```javascript
const dispatchStock = async (requestId, originWarehouse, items, dispatchedBy, notes) => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.dispatch_stock', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      request_id: requestId,
      origin_warehouse: originWarehouse,
      items: items,
      dispatched_by: dispatchedBy,
      dispatch_notes: notes || ''
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface DispatchStockResponse {
  success: true;
  message: string;
  data: {
    status: string; // "In Transit" or "Partially In Transit"
    stock_entry: string; // Stock Entry name created
  };
}
```

---

## 5. Receive Stock at Destination

Receives stock at the destination warehouse, moving the request from `In Transit` to `Completed` or `Partially Received`.

**Endpoint:** `receive_stock_destination`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface ReceiveStockPayload {
  request_id: string;
  destination_warehouse: string;
  items: Array<{
    item_code: string;
    received_qty: number;
  }>;
  received_by: string; // Email of person receiving
  receive_notes?: string; // Optional notes
  goods_received_note?: string; // Optional GRN reference
}
```

### Example Request

```javascript
const receiveStock = async (requestId, destinationWarehouse, items, receivedBy, notes, grn) => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.receive_stock_destination', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      request_id: requestId,
      destination_warehouse: destinationWarehouse,
      items: items,
      received_by: receivedBy,
      receive_notes: notes || '',
      goods_received_note: grn || ''
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface ReceiveStockResponse {
  success: true;
  message: string;
  data: {
    status: string; // "Completed" or "Partially Received"
    stock_entry: string; // Stock Entry name created
    goods_received_note?: string;
  };
}
```

---

## 6. Cancel Stock Transfer Request

Cancels a Material Request, moving it to `Cancelled` status.

**Endpoint:** `cancel_stock_transfer_request`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface CancelStockTransferRequestPayload {
  request_id: string;
  reason?: string; // Optional cancellation reason
}
```

### Example Request

```javascript
const cancelStockTransferRequest = async (requestId, reason) => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.cancel_stock_transfer_request', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      request_id: requestId,
      reason: reason || ''
    })
  });
  
  const data = await response.json();
  return data;
};
```

### Success Response

```typescript
interface CancelStockTransferRequestResponse {
  success: true;
  message: string;
  data: {
    request_id: string;
    status: string; // "Cancelled"
    docstatus: number; // 2
  };
}
```

**Note:** Cannot cancel requests that are already `In Transit` or `Partially In Transit`.

---

## 7. List Stock Transfer Requests

Lists all Material Requests (Stock Transfer Requests) with optional filters.

**Endpoint:** `list_stock_transfer_requests`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```typescript
interface ListStockTransferRequestsPayload {
  status?: string; // Filter by status
  origin_warehouse?: string; // Filter by origin warehouse
  destination_warehouse?: string; // Filter by destination warehouse
  from_date?: string; // Filter from date (YYYY-MM-DD)
  to_date?: string; // Filter to date (YYYY-MM-DD)
}
```

### Example Request

```javascript
const listStockTransferRequests = async (filters = {}) => {
  const response = await fetch('/api/method/techsavanna_pos.api.stock.list_stock_transfer_requests', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(filters)
  });
  
  const data = await response.json();
  return data;
};

// Example usage with filters
const filteredRequests = await listStockTransferRequests({
  status: 'In Transit',
  from_date: '2025-01-01',
  to_date: '2025-01-31'
});
```

### Success Response

```typescript
interface ListStockTransferRequestsResponse {
  success: true;
  message: string; // e.g., "5 requests found"
  data: {
    requests: Array<{
      name: string; // Material Request name
      requested_by: string; // Email of requester
      requested_on: string; // ISO datetime string
      status: string; // ERPNext's calculated status (Pending/Submitted/etc.)
      approval_status: string; // "Approved" or "Pending Approval" - effective approval status
      approved_by: string; // Email of approver (empty if not approved)
      is_approved: boolean; // true if approved, false otherwise
      origin_warehouse: string;
      destination_warehouse: string;
      dispatched_by: string;
      received_by: string;
      goods_received_note: string;
      custom_approval_status?: string; // Only present if custom field exists
    }>;
  };
}
```

---

## 8. Get Single Stock Transfer Request

Gets detailed information about a single Material Request.

**Endpoint:** `get_stock_transfer_request`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

### Request Parameters

```typescript
interface GetStockTransferRequestParams {
  request_id: string; // Material Request name
}
```

### Example Request

```javascript
const getStockTransferRequest = async (requestId) => {
  const response = await fetch(
    `/api/method/techsavanna_pos.api.stock.get_stock_transfer_request?request_id=${requestId}`,
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
interface GetStockTransferRequestResponse {
  success: true;
  message: string;
  data: {
    name: string;
    status: string; // ERPNext's calculated status
    approval_status: string; // "Approved" or "Pending Approval"
    approved_by: string; // Email of approver (empty if not approved)
    is_approved: boolean; // true if approved, false otherwise
    requested_by: string;
    requested_on: string;
    origin_warehouse: string;
    destination_warehouse: string;
    dispatched_by: string;
    received_by: string;
    goods_received_note: string;
    items: Array<{
      item_code: string;
      requested_qty: number;
      dispatched_qty: number;
      received_qty: number;
    }>;
    custom_approval_status?: string; // Only present if custom field exists
  };
}
```

---

## State Transition Flow

```
┌─────────┐
│  Draft  │
└────┬────┘
     │ submit_stock_transfer_request
     ▼
┌─────────────┐     ┌─────────┐
│  Submitted  │ or  │ Pending │  (Both are submitted, use approve_stock_transfer)
└────┬────────┘     └────┬────┘
     │                   │
     └───────┬───────────┘
             │ approve_stock_transfer
             ▼
     ┌───────────┐
     │  Approved │
     └────┬──────┘
          │ dispatch_stock
          ▼
     ┌──────────────────────┐
     │  In Transit /        │
     │  Partially In Transit│
     └────┬─────────────────┘
          │ receive_stock_destination
          ▼
     ┌─────────────────────┐
     │  Completed /        │
     │  Partially Received │
     └─────────────────────┘

Any status → cancel_stock_transfer_request → Cancelled
(except In Transit/Partially In Transit)
```

### Quick Reference: Which Endpoint to Use Based on Status

| Current State | Use This Endpoint | Payload |
|---------------|-------------------|---------|
| `Draft` (status) | `submit_stock_transfer_request` | `{ "request_id": "MAT-MR-2025-00001" }` |
| `Submitted` or `Pending` (status) and not approved | `approve_stock_transfer` | `{ "request_id": "MAT-MR-2025-00001", "approved_by": "user@example.com", "approval_notes": "Optional notes" }` |
| Approved (`is_approved: true` or `approval_status: "Approved"`) | `dispatch_stock` | See [Dispatch Stock](#4-dispatch-stock) section |
| `In Transit` or `Partially In Transit` (status) | `receive_stock_destination` | See [Receive Stock](#5-receive-stock-at-destination) section |
| `Partially Received` (status) | `receive_stock_destination` | See [Receive Stock](#5-receive-stock-at-destination) section |
| `Completed` (status) | No action needed | - |
| `Cancelled` (status) | No action possible | - |

**Note:** Check `is_approved` or `approval_status` field to determine if a request is approved, not the `status` field.

## Status Values

| Status | Description | Next Action |
|--------|-------------|-------------|
| `Draft` | Request created but not submitted | Use `submit_stock_transfer_request` |
| `Submitted` | Request submitted and waiting for approval | Use `approve_stock_transfer` |
| `Pending` | Request submitted (ERPNext internal status, same as Submitted) | Use `approve_stock_transfer` |
| `Approved` (approval_status) | Request approved (check `approval_status` field or `is_approved` flag) | Use `dispatch_stock` |
| `In Transit` | Stock has been fully dispatched | Use `receive_stock_destination` |
| `Partially In Transit` | Some items have been dispatched | Use `receive_stock_destination` |
| `Completed` | Stock has been fully received | No further action needed |
| `Partially Received` | Some items have been received | Use `receive_stock_destination` for remaining items |
| `Cancelled` | Request has been cancelled | No further action possible |

**Important:** The `status` field is automatically calculated by ERPNext. To check if a request is approved, use:
- `approval_status` field: "Approved" or "Pending Approval"
- `is_approved` boolean flag: `true` if approved
- `approved_by` field: Contains approver email if approved, empty otherwise

### Important Notes about Status and Approval

#### Status Field
The `status` field is automatically calculated by ERPNext based on `per_ordered` and `per_received` fields. It may show:
- `Draft` - Not submitted
- `Pending` or `Submitted` - Submitted but not yet processed
- `In Transit`, `Partially In Transit` - Stock dispatched
- `Completed`, `Partially Received` - Stock received
- `Cancelled` - Request cancelled

**The `status` field does NOT change to "Approved" after approval.** It remains as "Pending" or "Submitted" because ERPNext calculates it automatically.

#### Approval Status
To determine if a request is approved, use these fields in API responses:
- **`approval_status`**: "Approved" or "Pending Approval"
- **`is_approved`**: `true` if approved, `false` otherwise
- **`approved_by`**: Email of approver (empty string if not approved)

#### Example Usage
```javascript
// Check if request is approved
if (request.is_approved) {
  // Request is approved, can proceed with dispatch
  console.log(`Approved by: ${request.approved_by}`);
} else {
  // Request not yet approved
  console.log('Status:', request.approval_status); // "Pending Approval"
}

// Filter approved requests
const approvedRequests = requests.filter(r => r.is_approved);
```

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
- `403` - Forbidden (insufficient permissions)
- `404` - Not Found (request doesn't exist)
- `409` - Conflict (invalid state transition)
- `422` - Validation Error
- `500` - Internal Server Error

## React Hook Example

```typescript
import { useState, useCallback } from 'react';

interface UseStockTransferRequestReturn {
  createRequest: (payload: CreateStockTransferRequestPayload) => Promise<any>;
  submitRequest: (requestId: string) => Promise<any>;
  approveRequest: (requestId: string, approvedBy: string, notes?: string) => Promise<any>;
  dispatchStock: (payload: DispatchStockPayload) => Promise<any>;
  receiveStock: (payload: ReceiveStockPayload) => Promise<any>;
  cancelRequest: (requestId: string, reason?: string) => Promise<any>;
  listRequests: (filters?: ListStockTransferRequestsPayload) => Promise<any>;
  getRequest: (requestId: string) => Promise<any>;
  loading: boolean;
  error: string | null;
}

export const useStockTransferRequest = (): UseStockTransferRequestReturn => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiCall = useCallback(async (endpoint: string, payload: any) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/method/techsavanna_pos.api.stock.${endpoint}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!data.success && data.message) {
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

  const createRequest = useCallback((payload: CreateStockTransferRequestPayload) => 
    apiCall('create_stock_transfer_request', payload), [apiCall]);

  const submitRequest = useCallback((requestId: string) => 
    apiCall('submit_stock_transfer_request', { request_id: requestId }), [apiCall]);

  const approveRequest = useCallback((requestId: string, approvedBy: string, notes?: string) => 
    apiCall('approve_stock_transfer', { request_id: requestId, approved_by: approvedBy, approval_notes: notes }), [apiCall]);

  const dispatchStock = useCallback((payload: DispatchStockPayload) => 
    apiCall('dispatch_stock', payload), [apiCall]);

  const receiveStock = useCallback((payload: ReceiveStockPayload) => 
    apiCall('receive_stock_destination', payload), [apiCall]);

  const cancelRequest = useCallback((requestId: string, reason?: string) => 
    apiCall('cancel_stock_transfer_request', { request_id: requestId, reason }), [apiCall]);

  const listRequests = useCallback((filters?: ListStockTransferRequestsPayload) => 
    apiCall('list_stock_transfer_requests', filters || {}), [apiCall]);

  const getRequest = useCallback(async (requestId: string) => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/method/techsavanna_pos.api.stock.get_stock_transfer_request?request_id=${requestId}`
      );
      const data = await response.json();
      if (!data.success && data.message) {
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
    createRequest,
    submitRequest,
    approveRequest,
    dispatchStock,
    receiveStock,
    cancelRequest,
    listRequests,
    getRequest,
    loading,
    error
  };
};
```

## Usage Example in React Component

```typescript
import React from 'react';
import { useStockTransferRequest } from './hooks/useStockTransferRequest';

const StockTransferComponent: React.FC = () => {
  const {
    createRequest,
    submitRequest,
    approveRequest,
    dispatchStock,
    receiveStock,
    listRequests,
    loading,
    error
  } = useStockTransferRequest();

  const handleCreateRequest = async () => {
    try {
      const result = await createRequest({
        company: 'Savanna Ltd',
        from_warehouse: 'Stores - HO',
        to_warehouse: 'Stores - Branch',
        items: [
          { item_code: 'ITEM-001', qty: 10 }
        ]
      });
      console.log('Request created:', result.data.material_request);
    } catch (err) {
      console.error('Error creating request:', err);
    }
  };

  const handleApproveRequest = async (requestId) => {
    try {
      const result = await approveRequest(
        requestId,
        'manager@example.com',
        'Approved for dispatch'
      );
      console.log('Request approved:', result.data.approval_status); // "Approved"
      console.log('Is approved:', result.data.is_approved); // true
    } catch (err) {
      console.error('Error approving request:', err);
    }
  };

  const handleListRequests = async () => {
    try {
      const result = await listRequests();
      const requests = result.data.requests;
      
      // Filter approved requests
      const approvedRequests = requests.filter(r => r.is_approved);
      console.log('Approved requests:', approvedRequests);
      
      // Filter pending approval
      const pendingApproval = requests.filter(r => !r.is_approved);
      console.log('Pending approval:', pendingApproval);
      
      // Display status for each request
      requests.forEach(request => {
        console.log(`${request.name}: ${request.status} (Approval: ${request.approval_status})`);
      });
    } catch (err) {
      console.error('Error listing requests:', err);
    }
  };

  return (
    <div>
      <button onClick={handleCreateRequest} disabled={loading}>
        Create Request
      </button>
      <button onClick={() => handleApproveRequest('MAT-MR-2025-00001')} disabled={loading}>
        Approve Request
      </button>
      <button onClick={handleListRequests} disabled={loading}>
        List Requests
      </button>
      {error && <div className="error">{error}</div>}
    </div>
  );
};
```

### Example: Displaying Approval Status in UI

```typescript
import React from 'react';

interface StockTransferRequest {
  name: string;
  status: string;
  approval_status: string;
  is_approved: boolean;
  approved_by: string;
  // ... other fields
}

const RequestCard: React.FC<{ request: StockTransferRequest }> = ({ request }) => {
  return (
    <div className="request-card">
      <h3>{request.name}</h3>
      <p>Status: {request.status}</p>
      
      {/* Display approval status */}
      {request.is_approved ? (
        <div className="approved">
          <span>✓ Approved</span>
          <p>Approved by: {request.approved_by}</p>
        </div>
      ) : (
        <div className="pending-approval">
          <span>⏳ Pending Approval</span>
        </div>
      )}
      
      {/* Show dispatch button only if approved */}
      {request.is_approved && (
        <button onClick={() => handleDispatch(request.name)}>
          Dispatch Stock
        </button>
      )}
    </div>
  );
};
```

---

## Choosing the Right Approach

### When to Use Direct Stock Transfer (`create_stock_transfer`)

Use direct stock transfer when:
- ✅ Immediate stock movement is required
- ✅ No approval workflow is needed
- ✅ Simple, straightforward transfers
- ✅ User has direct warehouse access permissions
- ✅ Real-time inventory updates are acceptable

**Example Use Cases:**
- Emergency stock replenishment
- Internal warehouse reorganization
- Automated system-to-system transfers
- Same-day urgent transfers

### When to Use Material Request Flow

Use Material Request flow when:
- ✅ Approval workflow is required
- ✅ Multi-step process with tracking needed
- ✅ Audit trail and accountability are important
- ✅ Partial dispatch/receipt tracking is needed
- ✅ Integration with procurement processes

**Example Use Cases:**
- Inter-branch transfers requiring manager approval
- High-value item transfers
- Transfers requiring documentation (GRN, etc.)
- Scheduled transfers with future dates
- Multi-warehouse coordination

---

## Comparison Table

| Feature | Direct Stock Transfer | Material Request Flow |
|---------|---------------------|---------------------|
| **Speed** | Immediate | Multi-step process |
| **Approval** | Not required | Required (configurable) |
| **Tracking** | Basic (Stock Entry only) | Full lifecycle tracking |
| **Partial Operations** | Not supported | Supported (dispatch/receive) |
| **Audit Trail** | Stock Entry only | Full request history |
| **Status Tracking** | Simple (Submitted/Cancelled) | Detailed (Draft → Submitted → Approved → In Transit → Completed) |
| **Use Case** | Quick transfers | Controlled transfers |

---

## Best Practices

### 1. Error Handling

Always implement proper error handling for all API calls:

```typescript
try {
  const response = await createStockTransfer(payload);
  if (response.status === "error") {
    // Handle API-level errors
    console.error("Transfer failed:", response.message);
    return;
  }
  // Success handling
  console.log("Transfer completed:", response.stock_entry);
} catch (error) {
  // Handle network/parsing errors
  console.error("Request failed:", error);
}
```

### 2. Stock Availability Checks

Before creating transfers, verify stock availability:

```typescript
// Check stock before transfer
const stockCheck = await checkStockAvailability({
  item_code: "ITEM-001",
  warehouse: "Stores - HO",
  required_qty: 10
});

if (stockCheck.available_qty < 10) {
  throw new Error("Insufficient stock");
}
```

### 3. Idempotency

For critical operations, implement idempotency checks:

```typescript
// Check if transfer already exists
const existingTransfer = await getStockTransferByReference(externalRef);
if (existingTransfer) {
  return existingTransfer; // Return existing instead of creating duplicate
}
```

### 4. Batch Operations

For multiple items, validate all items before processing:

```typescript
// Validate all items first
const validationErrors = [];
for (const item of items) {
  const validation = await validateItem(item);
  if (!validation.valid) {
    validationErrors.push(validation.error);
  }
}

if (validationErrors.length > 0) {
  throw new Error(`Validation failed: ${validationErrors.join(", ")}`);
}

// Proceed with transfer only if all validations pass
```

### 5. Status Polling

For Material Request flow, implement status polling:

```typescript
async function waitForApproval(requestId, timeout = 300000) {
  const startTime = Date.now();
  
  while (Date.now() - startTime < timeout) {
    const request = await getStockTransferRequest(requestId);
    
    if (request.data.is_approved) {
      return request.data;
    }
    
    if (request.data.status === "Cancelled") {
      throw new Error("Request was cancelled");
    }
    
    // Wait 5 seconds before next check
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
  
  throw new Error("Approval timeout");
}
```

### 6. Transaction Management

Handle partial failures gracefully:

```typescript
// For Material Request flow, track partial dispatches
const dispatchResult = await dispatchStock({
  request_id: requestId,
  items: itemsToDispatch,
  // ... other fields
});

if (dispatchResult.data.status === "Partially In Transit") {
  // Handle partial dispatch
  console.log("Partial dispatch completed");
  // Schedule retry for remaining items
}
```

---

## Notes

1. **Authentication**: All endpoints require valid authentication. Include appropriate headers (cookies, tokens, etc.) based on your authentication setup.

2. **Date Formats**: Use `YYYY-MM-DD` format for all date fields. Time format: `HH:MM:SS`.

3. **Quantity Validation**: All quantities must be greater than 0.

4. **State Transitions**: Follow the state transition flow. Attempting invalid transitions will return a 409 Conflict error.

5. **Partial Operations**: Both dispatch and receive operations support partial quantities, which will result in "Partially In Transit" or "Partially Received" statuses.

6. **Stock Entry Creation**: Dispatch and receive operations automatically create Stock Entry documents that update inventory.

7. **Workflow Support**: If your system uses workflows, use `approve_stock_transfer_workflow` instead of `approve_stock_transfer`.

8. **Approval Status**: The `status` field is automatically calculated by ERPNext and does NOT change to "Approved" after approval. Always check the `approval_status` field or `is_approved` flag to determine if a request is approved. The `approval_status` field will show "Approved" when `approved_by` is set, or "Pending Approval" otherwise.

9. **Filtering Approved Requests**: Use the `is_approved` boolean field or check `approval_status === "Approved"` to filter approved requests in your frontend.

10. **Custom Fields**: The API supports custom fields for tracking additional information (e.g., `custom_dispatched_by`, `custom_received_by`, `custom_goods_received_note`). If these fields don't exist, the API falls back to standard fields or logs warnings.

11. **Stock Availability**: Always verify stock availability before creating transfers. The API will return validation errors if insufficient stock exists.

12. **Warehouse Validation**: Ensure both source and destination warehouses exist and are valid before creating transfers.

13. **Concurrent Operations**: Be cautious with concurrent operations on the same Material Request. Consider implementing locking mechanisms for critical operations.

14. **Logging**: All errors are logged server-side. Check server logs for detailed error information when debugging issues.

