# Stock Transfer API Documentation

Complete API documentation for stock transfer endpoints, designed for React.js frontend consumption.

## Table of Contents

1. [Overview](#overview)
2. [Base URL & Authentication](#base-url--authentication)
3. [Transfer Workflow](#transfer-workflow)
4. [Process Flow](#process-flow)
   - [Direct Stock Transfer Flow](#flow-diagram-direct-stock-transfer)
   - [Request-Based Stock Transfer Flow](#flow-diagram-request-based-stock-transfer-full-workflow)
   - [Detailed Step-by-Step Process](#detailed-step-by-step-process)
   - [Status Transitions](#status-transitions)
   - [Decision Tree](#decision-tree-which-flow-to-use)
   - [State Management in React](#state-management-in-react)
5. [API Endpoints](#api-endpoints)
   - [Create Stock Transfer Request](#1-create-stock-transfer-request)
   - [Create Stock Transfer (Direct)](#2-create-stock-transfer-direct)
   - [List Stock Transfer Requests](#3-list-stock-transfer-requests)
   - [Get Stock Transfer Request](#4-get-stock-transfer-request)
   - [Approve Stock Transfer](#5-approve-stock-transfer)
   - [Approve Stock Transfer (Workflow)](#6-approve-stock-transfer-workflow)
   - [Dispatch Stock](#7-dispatch-stock)
   - [Receive Stock at Destination](#8-receive-stock-at-destination)
   - [Confirm Receive Transfer](#9-confirm-receive-transfer)
6. [Error Handling](#error-handling)
7. [React.js Examples](#reactjs-examples)
8. [Status Codes Reference](#status-codes-reference)

---

## Overview

This documentation covers all APIs related to stock transfers between warehouses. Stock transfers follow a request-based workflow using Material Requests with type "Material Transfer".

### Transfer Lifecycle

1. **Create Transfer** - Create a direct stock transfer (Material Entry) or initiate a Material Request
2. **Approve** - Approve the transfer request (if approval workflow is enabled)
3. **Dispatch** - Dispatch stock from origin warehouse
4. **Receive** - Receive stock at destination warehouse
5. **Complete** - Transfer is marked as completed

---

## Base URL & Authentication

All endpoints are relative to your Frappe backend API:

```
/api/method/techsavanna_pos.api.stock.<endpoint_name>
```

### Authentication

All endpoints (except where noted) require authentication. Include session cookie or API key in your requests.

```javascript
// Using fetch with session cookie (automatic)
fetch('/api/method/techsavanna_pos.api.stock.create_stock_transfer', {
  method: 'POST',
  credentials: 'include',
  headers: {
    'Content-Type': 'application/json'
  },
  body: JSON.stringify(data)
})

// Using API key
fetch('/api/method/techsavanna_pos.api.stock.create_stock_transfer', {
  method: 'POST',
  headers: {
    'Authorization': 'token <api_key>:<api_secret>',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify(data)
})
```

---

## Transfer Workflow

### Direct Transfer vs Request-Based Transfer

**Direct Transfer** (`create_stock_transfer`):
- Immediately creates and submits a Stock Entry
- No approval needed
- Instant stock movement
- Use for simple, trusted transfers

**Request-Based Transfer** (Material Request workflow):
1. Create Material Request (type: Material Transfer)
2. Approve request (optional, if workflow enabled)
3. Dispatch from origin warehouse
4. Receive at destination warehouse

---

## Process Flow

### Flow Diagram: Direct Stock Transfer

The simplest and fastest way to transfer stock. Ideal for trusted, immediate transfers within the same organization.

```
┌─────────────────────────────────────────────────────────────┐
│                    DIRECT STOCK TRANSFER                    │
└─────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   START      │
    └──────┬───────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ 1. create_stock_transfer()          │
    │    • Provide: company, warehouses,  │
    │      items, dates                   │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ 2. Stock Entry Created & Submitted  │
    │    • Stock deducted from source     │
    │    • Stock added to destination     │
    │    • Returns: stock_entry name      │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │         TRANSFER COMPLETE           │
    │    • Status: Submitted              │
    │    • Inventory updated immediately  │
    └─────────────────────────────────────┘
```

**Steps:**
1. **Call API:** `create_stock_transfer()`
   - Provide: company, posting_date, posting_time, from_warehouse, to_warehouse, items, notes
2. **System Processes:**
   - Creates Stock Entry document (type: Material Transfer)
   - Validates stock availability
   - Updates inventory in both warehouses
   - Submits the Stock Entry automatically
3. **Result:**
   - Stock Entry created and submitted
   - Stock moved immediately
   - Returns stock_entry name

**When to Use:**
- Simple transfers between trusted locations
- No approval workflow needed
- Immediate stock movement required
- Same-day transfers

---

### Flow Diagram: Request-Based Stock Transfer (Full Workflow)

The complete workflow with approval, dispatch, and receipt stages. Use this for controlled transfers requiring approvals or tracking.

```
┌─────────────────────────────────────────────────────────────┐
│              REQUEST-BASED STOCK TRANSFER                   │
│                  (Full Workflow)                            │
└─────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │   START      │
    └──────┬───────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 1: Create Material Request     │
    │    (Not in this API module - use    │
    │     standard Frappe Material Request│
    │     API or UI)                      │
    │                                     │
    │    Material Request Created         │
    │    • Status: Draft                  │
    │    • Type: Material Transfer        │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 2: Submit Material Request     │
    │    • Status: Draft → Submitted      │
    │    • Ready for approval             │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 3: Approval Required?          │
    │                                     │
    │    ┌────────────┐  ┌──────────────┐│
    │    │   YES      │  │     NO       ││
    │    │(Workflow)  │  │(No Workflow) ││
    │    └─────┬──────┘  └──────┬───────┘│
    │          │                │        │
    │          ▼                ▼        │
    │  ┌──────────────┐  ┌────────────┐│
    │  │ approve_     │  │ Skip to    ││
    │  │ stock_       │  │ STEP 5     ││
    │  │ transfer_    │  │ (Dispatch) ││
    │  │ workflow()   │  └────────────┘│
    │  └──────┬───────┘                │
    │         │                        │
    │         ▼                        │
    │  ┌──────────────────┐           │
    │  │ OR               │           │
    │  │ approve_         │           │
    │  │ stock_           │           │
    │  │ transfer()       │           │
    │  └──────┬───────────┘           │
    │         │                       │
    └─────────┴───────────────────────┘
              │
              ▼
    ┌─────────────────────────────────────┐
    │ STEP 4: Request Approved            │
    │    • Status: Approved               │
    │    • Workflow State: Approved       │
    │    • Ready for dispatch             │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 5: dispatch_stock()            │
    │    • Provide: request_id,           │
    │      origin_warehouse, items,       │
    │      dispatched_by                  │
    │                                     │
    │    Creates Material Issue Stock     │
    │    Entry                            │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 6: Stock Dispatched            │
    │    • Material Issue created         │
    │    • Stock deducted from origin     │
    │    • Status: In Transit /           │
    │             Partially In Transit    │
    │    • Returns: stock_entry name      │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 7: receive_stock_destination() │
    │    • Provide: request_id,           │
    │      destination_warehouse, items,  │
    │      received_by, GRN               │
    │                                     │
    │    Creates Material Receipt Stock   │
    │    Entry                            │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │ STEP 8: Stock Received              │
    │    • Material Receipt created       │
    │    • Stock added to destination     │
    │    • Status: Completed /            │
    │             Partially Received      │
    │    • Returns: stock_entry name,     │
    │               GRN                   │
    └──────┬──────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────┐
    │         TRANSFER COMPLETE           │
    │    • Status: Completed              │
    │    • All items received             │
    │    • Inventory fully updated        │
    └─────────────────────────────────────┘
```

---

### Detailed Step-by-Step Process

#### Path A: Direct Transfer (Simple)

```javascript
// Single API call completes the entire transfer
const result = await createStockTransfer({
  company: 'Savanna Ltd',
  postingDate: '2025-01-20',
  postingTime: '10:00:00',
  fromWarehouse: 'Stores - HO',
  toWarehouse: 'Stores - Branch',
  items: [
    { item_code: 'ITEM-001', qty: 10 },
    { item_code: 'ITEM-002', qty: 5 }
  ],
  notes: 'Direct transfer'
});

// Result: Stock Entry created and submitted immediately
// Inventory updated in both warehouses
```

**Timeline:** Immediate (one API call)

---

#### Path B: Request-Based Transfer (No Approval Workflow)

```javascript
// Step 1: Create Material Request (outside this API module)
// This would be done via standard Frappe API or UI

// Step 2: Dispatch Stock
const dispatchResult = await dispatchStock({
  requestId: 'MAT-MR-2025-00001',
  originWarehouse: 'Stores - HO',
  items: [
    { item_code: 'ITEM-001', dispatched_qty: 10 },
    { item_code: 'ITEM-002', dispatched_qty: 5 }
  ],
  dispatchedBy: 'warehouse@example.com',
  dispatchNotes: 'Dispatched via courier'
});

// Step 3: Receive Stock
const receiveResult = await receiveStockDestination({
  requestId: 'MAT-MR-2025-00001',
  destinationWarehouse: 'Stores - Branch',
  items: [
    { item_code: 'ITEM-001', received_qty: 10 },
    { item_code: 'ITEM-002', received_qty: 5 }
  ],
  receivedBy: 'branch@example.com',
  receiveNotes: 'All items received',
  goodsReceivedNote: 'GRN-2025-001'
});
```

**Timeline:** Multi-stage (2-3 API calls, can span days)

---

#### Path C: Request-Based Transfer (With Approval Workflow)

```javascript
// Step 1: Create Material Request (outside this API module)

// Step 2: Approve Request
const approveResult = await approveStockTransferWorkflow({
  requestId: 'MAT-MR-2025-00001',
  approvedBy: 'manager@example.com',
  approvalNotes: 'Approved for branch restocking'
});

// Step 3: Dispatch Stock
const dispatchResult = await dispatchStock({
  requestId: 'MAT-MR-2025-00001',
  originWarehouse: 'Stores - HO',
  items: [
    { item_code: 'ITEM-001', dispatched_qty: 10 },
    { item_code: 'ITEM-002', dispatched_qty: 5 }
  ],
  dispatchedBy: 'warehouse@example.com'
});

// Step 4: Receive Stock
const receiveResult = await receiveStockDestination({
  requestId: 'MAT-MR-2025-00001',
  destinationWarehouse: 'Stores - Branch',
  items: [
    { item_code: 'ITEM-001', received_qty: 10 },
    { item_code: 'ITEM-002', received_qty: 5 }
  ],
  receivedBy: 'branch@example.com',
  goodsReceivedNote: 'GRN-2025-001'
});
```

**Timeline:** Multi-stage with approval (3-4 API calls, can span days)

---

### Status Transitions

#### Material Request Status Flow

```
Draft
  │
  │ (Submit)
  ▼
Submitted
  │
  │ (Approve - if workflow enabled)
  ▼
Approved
  │
  │ (Dispatch)
  ▼
Partially In Transit / In Transit
  │
  │ (Receive)
  ▼
Partially Received / Completed
```

#### Stock Entry Status Flow

```
Draft (docstatus: 0)
  │
  │ (Submit)
  ▼
Submitted (docstatus: 1)
  │
  │ (Cancel - if needed)
  ▼
Cancelled (docstatus: 2)
```

---

### Decision Tree: Which Flow to Use?

```
                    START
                      │
                      │
        ┌─────────────┴─────────────┐
        │                           │
        ▼                           ▼
   Need Approval?            No Approval Needed?
        │                           │
        │                           │
    ┌───┴───┐                  ┌────┴────┐
    │  YES  │                  │   NO    │
    └───┬───┘                  └────┬────┘
        │                           │
        │                           │
        ▼                           ▼
   Need Tracking?           ┌───────────────┐
        │                  │ Use Direct    │
    ┌───┴───┐              │ Transfer:     │
    │  YES  │              │ create_       │
    └───┬───┘              │ stock_        │
        │                  │ transfer()    │
        │                  └───────────────┘
        │
        ▼
   ┌─────────────────────────────┐
   │ Use Request-Based Workflow: │
   │                             │
   │ 1. Create Material Request  │
   │ 2. Approve (if workflow)    │
   │ 3. Dispatch                 │
   │ 4. Receive                  │
   └─────────────────────────────┘
```

---

### State Management in React

Here's how you might track the process flow in a React component:

```javascript
const [transferState, setTransferState] = useState({
  step: 'idle', // idle | creating | approving | dispatching | receiving | completed
  materialRequestId: null,
  stockEntryIds: [],
  errors: []
});

// Direct Transfer Flow
const handleDirectTransfer = async (data) => {
  setTransferState({ ...transferState, step: 'creating' });
  
  try {
    const result = await createStockTransfer(data);
    setTransferState({
      step: 'completed',
      stockEntryIds: [result.stock_entry]
    });
  } catch (error) {
    setTransferState({
      step: 'idle',
      errors: [...transferState.errors, error.message]
    });
  }
};

// Request-Based Flow
const handleRequestBasedTransfer = async (requestId) => {
  // Step 1: Approve (if needed)
  setTransferState({ ...transferState, step: 'approving' });
  await approveStockTransfer(requestId, 'manager@example.com');
  
  // Step 2: Dispatch
  setTransferState({ ...transferState, step: 'dispatching' });
  const dispatchResult = await dispatchStock({...});
  setTransferState(prev => ({
    ...prev,
    stockEntryIds: [...prev.stockEntryIds, dispatchResult.data.stock_entry]
  }));
  
  // Step 3: Receive
  setTransferState({ ...transferState, step: 'receiving' });
  const receiveResult = await receiveStockDestination({...});
  setTransferState(prev => ({
    step: 'completed',
    stockEntryIds: [...prev.stockEntryIds, receiveResult.data.stock_entry]
  }));
};
```

---

## API Endpoints

### 1. Create Stock Transfer Request

Creates a Material Request for stock transfer. Use this to initiate a transfer request that will go through approval, dispatch, and receive workflows.

**Endpoint:** `create_stock_transfer_request`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.create_stock_transfer_request`  
**Authentication:** Required

#### Request Body

```typescript
{
  company: string;              // Required: Company name
  from_warehouse: string;       // Required: Source warehouse
  to_warehouse: string;         // Required: Destination warehouse
  items: Array<{                // Required: Array of items to transfer
    item_code: string;          // Required: Item code
    qty: number;                // Required: Quantity (must be > 0)
    uom?: string;               // Optional: Unit of measure
  }>;
  transaction_date?: string;    // Optional: Request date (YYYY-MM-DD, defaults to today)
  schedule_date?: string;       // Optional: Expected transfer date (YYYY-MM-DD)
  submit?: boolean;             // Optional: Submit immediately (default: false)
}
```

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "Stock transfer request created successfully",
  "data": {
    "material_request": "MAT-MR-2025-00001",
    "status": "Draft",
    "docstatus": 0,
    "submitted": false
  }
}
```

#### Example

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
        from_warehouse: requestData.fromWarehouse,
        to_warehouse: requestData.toWarehouse,
        items: requestData.items,
        transaction_date: requestData.transactionDate,
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

// Usage
const request = await createStockTransferRequest({
  company: 'Savanna Ltd',
  fromWarehouse: 'Stores - HO',
  toWarehouse: 'Stores - Branch',
  items: [
    { item_code: 'ITEM-001', qty: 10, uom: 'Nos' },
    { item_code: 'ITEM-002', qty: 5, uom: 'Nos' }
  ],
  transactionDate: '2025-01-20',
  scheduleDate: '2025-01-25',
  submit: false  // Will be in Draft status
});
```

**Note:** After creating the request, you can:
- Submit it using Frappe's standard API: `PUT /api/resource/Material Request/{name}` with `{"docstatus": 1}`
- Then proceed with approval using `approve_stock_transfer()` or `approve_stock_transfer_workflow()`
- Then dispatch using `dispatch_stock()`
- Finally receive using `receive_stock_destination()`

---

### 2. Create Stock Transfer (Direct)

Creates a Stock Entry of type "Material Transfer" to immediately transfer stock between warehouses without going through the request workflow.

**Endpoint:** `create_stock_transfer`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.create_stock_transfer`  
**Authentication:** Required (login required, not guest)

#### Request Body

```typescript
{
  company: string;              // Required: Company name
  posting_date: string;         // Required: Posting date (YYYY-MM-DD)
  posting_time: string;         // Required: Posting time (HH:MM:SS)
  from_warehouse: string;       // Required: Source warehouse
  to_warehouse: string;         // Required: Destination warehouse
  items: Array<{                // Required: Array of items to transfer
    item_code: string;          // Required: Item code
    qty: number;                // Required: Quantity (must be > 0)
  }>;
  notes?: string;               // Optional: Remarks/notes
}
```

#### Response

**Success (200):**
```json
{
  "status": "success",
  "message": "Stock transfer completed successfully",
  "stock_entry": "MAT-STE-00001"
}
```

**Error (400):**
```json
{
  "status": "error",
  "message": "Missing required fields: company, posting_date"
}
```

#### Example

```javascript
const createStockTransfer = async (transferData) => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.create_stock_transfer', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        company: transferData.company,
        posting_date: transferData.postingDate,
        posting_time: transferData.postingTime,
        from_warehouse: transferData.fromWarehouse,
        to_warehouse: transferData.toWarehouse,
        items: JSON.stringify(transferData.items),
        notes: transferData.notes || ''
      })
    });

    const result = await response.json();
    
    if (result.message && result.message.status === 'success') {
      return result.message;
    }
    return result;
  } catch (error) {
    console.error('Error creating stock transfer:', error);
    throw error;
  }
};

// Usage
const transferData = {
  company: 'Savanna Ltd',
  postingDate: '2025-01-20',
  postingTime: '10:30:00',
  fromWarehouse: 'Stores - HO',
  toWarehouse: 'Stores - Branch',
  items: [
    { item_code: 'ITEM-001', qty: 10 },
    { item_code: 'ITEM-002', qty: 5 }
  ],
  notes: 'Urgent transfer to branch'
};

const result = await createStockTransfer(transferData);
```

---

### 3. List Stock Transfer Requests

Lists all Material Requests of type "Material Transfer" with optional filtering.

**Endpoint:** `list_stock_transfer_requests`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.list_stock_transfer_requests`  
**Authentication:** Required

#### Request Body (Optional Filters)

```typescript
{
  status?: string;              // Optional: Filter by status (e.g., "In Transit", "Completed")
  origin_warehouse?: string;    // Optional: Filter by origin warehouse
  destination_warehouse?: string; // Optional: Filter by destination warehouse
  from_date?: string;           // Optional: Start date filter (YYYY-MM-DD)
  to_date?: string;             // Optional: End date filter (YYYY-MM-DD)
}
```

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "5 requests found",
  "data": {
    "requests": [
      {
        "name": "MAT-MR-2025-00001",
        "requested_by": "admin@example.com",
        "requested_on": "2025-01-20 10:00:00",
        "status": "In Transit",
        "origin_warehouse": "Stores - HO",
        "destination_warehouse": "Stores - Branch",
        "dispatched_by": "steve@steve.com",
        "received_by": "",
        "goods_received_note": ""
      }
    ]
  }
}
```

#### Example

```javascript
const listStockTransferRequests = async (filters = {}) => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.list_stock_transfer_requests', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(filters)
    });

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error listing stock transfer requests:', error);
    throw error;
  }
};

// Usage
const inTransitTransfers = await listStockTransferRequests({
  status: 'In Transit',
  from_date: '2025-01-01',
  to_date: '2025-01-31'
});
```

---

### 4. Get Stock Transfer Request

Retrieves detailed information about a specific stock transfer request.

**Endpoint:** `get_stock_transfer_request`  
**Method:** `GET` / `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.get_stock_transfer_request`  
**Authentication:** Required

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `request_id` | string | Yes | Material Request ID |

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "Stock transfer request fetched successfully",
  "data": {
    "name": "MAT-MR-2025-00001",
    "status": "In Transit",
    "requested_by": "admin@example.com",
    "requested_on": "2025-01-20 10:00:00",
    "origin_warehouse": "Stores - HO",
    "destination_warehouse": "Stores - Branch",
    "dispatched_by": "steve@steve.com",
    "received_by": "",
    "goods_received_note": "",
    "items": [
      {
        "item_code": "ITEM-001",
        "requested_qty": 10,
        "dispatched_qty": 10,
        "received_qty": 0
      },
      {
        "item_code": "ITEM-002",
        "requested_qty": 5,
        "dispatched_qty": 5,
        "received_qty": 0
      }
    ]
  }
}
```

#### Example

```javascript
const getStockTransferRequest = async (requestId) => {
  try {
    const response = await fetch(
      `/api/method/techsavanna_pos.api.stock.get_stock_transfer_request?request_id=${requestId}`,
      {
        method: 'GET',
        credentials: 'include'
      }
    );

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error getting stock transfer request:', error);
    throw error;
  }
};

// Usage
const transferRequest = await getStockTransferRequest('MAT-MR-2025-00001');
```

---

### 5. Approve Stock Transfer

Approves a stock transfer request (Material Request). Use this when workflow is NOT enabled.

**Endpoint:** `approve_stock_transfer`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.approve_stock_transfer`  
**Authentication:** Required

#### Request Body

```typescript
{
  request_id: string;           // Required: Material Request ID
  approved_by: string;          // Required: Email of approver
  approval_notes?: string;      // Optional: Approval notes/comments
}
```

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "Stock Transfer Request approved successfully",
  "data": {
    "request_id": "MAT-MR-2025-00001",
    "status": "Approved",
    "approved_by": "manager@example.com"
  }
}
```

**Error (409) - Already Approved:**
```json
{
  "success": false,
  "message": "Request is already approved"
}
```

#### Example

```javascript
const approveStockTransfer = async (requestId, approvedBy, notes = '') => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.approve_stock_transfer', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        request_id: requestId,
        approved_by: approvedBy,
        approval_notes: notes
      })
    });

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error approving stock transfer:', error);
    throw error;
  }
};

// Usage
const approval = await approveStockTransfer(
  'MAT-MR-2025-00001',
  'manager@example.com',
  'Approved for urgent branch restocking'
);
```

---

### 6. Approve Stock Transfer (Workflow)

Approves a stock transfer request when workflow IS enabled. This version uses workflow state transitions.

**Endpoint:** `approve_stock_transfer_workflow`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.approve_stock_transfer_workflow`  
**Authentication:** Required (not guest)

#### Request Body

```typescript
{
  request_id: string;           // Required: Material Request ID
  approved_by: string;          // Required: Email of approver (must match logged-in user)
  approval_notes?: string;      // Optional: Approval notes/comments
}
```

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "Stock Transfer Request approved",
  "data": {
    "request_id": "MAT-MR-2025-00001",
    "workflow_state": "Approved"
  }
}
```

**Error (403) - User Mismatch:**
```json
{
  "success": false,
  "message": "API user must match approved_by"
}
```

#### Example

```javascript
const approveStockTransferWorkflow = async (requestId, approvedBy, notes = '') => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.approve_stock_transfer_workflow', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        request_id: requestId,
        approved_by: approvedBy, // Must match logged-in user
        approval_notes: notes
      })
    });

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error approving stock transfer (workflow):', error);
    throw error;
  }
};
```

---

### 7. Dispatch Stock

Dispatches stock from the origin warehouse for an approved Material Request. Creates a Material Issue Stock Entry.

**Endpoint:** `dispatch_stock`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.dispatch_stock`  
**Authentication:** Required

#### Request Body

```typescript
{
  request_id: string;           // Required: Material Request ID
  origin_warehouse: string;     // Required: Source warehouse name
  items: Array<{                // Required: Items to dispatch
    item_code: string;          // Required: Item code
    dispatched_qty: number;     // Required: Quantity to dispatch
  }>;
  dispatched_by: string;        // Required: Email of person dispatching (must match logged-in user)
  dispatch_notes?: string;      // Optional: Dispatch notes
}
```

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "Stock dispatched successfully",
  "data": {
    "status": "In Transit",
    "stock_entry": "MAT-STE-00002"
  }
}
```

**Error (422) - Insufficient Stock:**
```json
{
  "success": false,
  "message": "Insufficient stock for ITEM-001"
}
```

**Error (409) - Not Approved:**
```json
{
  "success": false,
  "message": "Request not approved (current state: Pending)"
}
```

#### Example

```javascript
const dispatchStock = async (dispatchData) => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.dispatch_stock', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        request_id: dispatchData.requestId,
        origin_warehouse: dispatchData.originWarehouse,
        items: JSON.stringify(dispatchData.items),
        dispatched_by: dispatchData.dispatchedBy,
        dispatch_notes: dispatchData.dispatchNotes || ''
      })
    });

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error dispatching stock:', error);
    throw error;
  }
};

// Usage
const dispatch = await dispatchStock({
  requestId: 'MAT-MR-2025-00001',
  originWarehouse: 'Stores - HO',
  items: [
    { item_code: 'ITEM-001', dispatched_qty: 10 },
    { item_code: 'ITEM-002', dispatched_qty: 5 }
  ],
  dispatchedBy: 'warehouse@example.com',
  dispatchNotes: 'Dispatched via courier ABC123'
});
```

---

### 8. Receive Stock at Destination

Receives stock at the destination warehouse. Creates a Material Receipt Stock Entry.

**Endpoint:** `receive_stock_destination`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.receive_stock_destination`  
**Authentication:** Required

#### Request Body

```typescript
{
  request_id: string;           // Required: Material Request ID
  destination_warehouse: string; // Required: Destination warehouse name
  items: Array<{                // Required: Items being received
    item_code: string;          // Required: Item code
    received_qty: number;       // Required: Quantity received
  }>;
  received_by: string;          // Required: Email of person receiving
  receive_notes?: string;       // Optional: Receive notes
  goods_received_note?: string; // Optional: GRN reference number
}
```

#### Response

**Success (200):**
```json
{
  "success": true,
  "message": "Stock received successfully",
  "data": {
    "status": "Completed",
    "stock_entry": "MAT-STE-00003",
    "goods_received_note": "GRN-2025-001"
  }
}
```

**Error (409) - Not In Transit:**
```json
{
  "success": false,
  "message": "Only In Transit requests can be received"
}
```

#### Example

```javascript
const receiveStockDestination = async (receiveData) => {
  try {
    const response = await fetch('/api/method/techsavanna_pos.api.stock.receive_stock_destination', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        request_id: receiveData.requestId,
        destination_warehouse: receiveData.destinationWarehouse,
        items: receiveData.items,
        received_by: receiveData.receivedBy,
        receive_notes: receiveData.receiveNotes || '',
        goods_received_note: receiveData.goodsReceivedNote || ''
      })
    });

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error receiving stock:', error);
    throw error;
  }
};

// Usage
const receipt = await receiveStockDestination({
  requestId: 'MAT-MR-2025-00001',
  destinationWarehouse: 'Stores - Branch',
  items: [
    { item_code: 'ITEM-001', received_qty: 10 },
    { item_code: 'ITEM-002', received_qty: 5 }
  ],
  receivedBy: 'branch@example.com',
  receiveNotes: 'All items received in good condition',
  goodsReceivedNote: 'GRN-2025-001'
});
```

---

### 9. Confirm Receive Transfer

Confirms receipt of a stock transfer by processing a Stock Entry. This is an alternative endpoint for confirming transfers.

**Endpoint:** `confirm_receive_transfer`  
**Method:** `POST`  
**URL:** `/api/method/techsavanna_pos.api.stock.confirm_receive_transfer`  
**Authentication:** Guest allowed (but not recommended)

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `stock_entry_name` | string | Yes | Stock Entry name (Material Transfer) |

#### Response

**Success (200):**
```json
{
  "status": "success",
  "message": "Stock transfer processed successfully. Transfer Status: Received",
  "data": {
    "stock_entry_name": "MAT-STE-00001",
    "transfer_status": "Received"
  }
}
```

**Error (400):**
```json
{
  "status": "fail",
  "message": "This is not a material transfer type."
}
```

#### Example

```javascript
const confirmReceiveTransfer = async (stockEntryName) => {
  try {
    const response = await fetch(
      `/api/method/techsavanna_pos.api.stock.confirm_receive_transfer?stock_entry_name=${stockEntryName}`,
      {
        method: 'POST',
        credentials: 'include'
      }
    );

    const result = await response.json();
    return result.message || result;
  } catch (error) {
    console.error('Error confirming receive transfer:', error);
    throw error;
  }
};
```

---

## Error Handling

### Error Response Format

All endpoints return errors in a consistent format:

```typescript
{
  success: false;
  message: string;  // Human-readable error message
  // OR for some endpoints:
  status: "error";
  message: string;
}
```

### Common HTTP Status Codes

| Status Code | Meaning | Common Causes |
|-------------|---------|---------------|
| 400 | Bad Request | Missing required fields, invalid data format |
| 401 | Unauthorized | Not authenticated (guest access where not allowed) |
| 403 | Forbidden | Permission denied, user mismatch |
| 404 | Not Found | Resource doesn't exist (e.g., Material Request not found) |
| 409 | Conflict | Business rule violation (already approved, not in correct state) |
| 422 | Unprocessable Entity | Validation error (insufficient stock, quantity mismatch) |
| 500 | Internal Server Error | Unexpected server error |

### Error Handling Example

```javascript
const handleApiError = (response, result) => {
  if (!response.ok) {
    switch (response.status) {
      case 400:
        throw new Error(result.message || 'Invalid request');
      case 401:
        throw new Error('Authentication required');
      case 403:
        throw new Error(result.message || 'Permission denied');
      case 404:
        throw new Error(result.message || 'Resource not found');
      case 409:
        throw new Error(result.message || 'Conflict: ' + result.message);
      case 422:
        throw new Error(result.message || 'Validation error');
      default:
        throw new Error(result.message || 'Server error');
    }
  }
  
  if (result.status === 'error' || result.success === false) {
    throw new Error(result.message || 'Request failed');
  }
  
  return result;
};
```

---

## React.js Examples

### Complete Transfer Flow Example

```javascript
import React, { useState } from 'react';

const StockTransferManager = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Complete workflow: Create -> Approve -> Dispatch -> Receive
  const executeTransferWorkflow = async (transferData) => {
    setLoading(true);
    setError(null);

    try {
      // Step 1: Create transfer request (or use direct transfer)
      const createResult = await createStockTransfer(transferData);
      console.log('Transfer created:', createResult.stock_entry);

      // If using Material Request workflow instead:
      // Step 2: Approve the request
      // const approveResult = await approveStockTransfer(
      //   createResult.material_request_id,
      //   'manager@example.com'
      // );

      // Step 3: Dispatch stock
      // const dispatchResult = await dispatchStock({
      //   requestId: createResult.material_request_id,
      //   originWarehouse: transferData.fromWarehouse,
      //   items: transferData.items.map(item => ({
      //     item_code: item.item_code,
      //     dispatched_qty: item.qty
      //   })),
      //   dispatchedBy: 'warehouse@example.com'
      // });

      // Step 4: Receive stock
      // const receiveResult = await receiveStockDestination({
      //   requestId: createResult.material_request_id,
      //   destinationWarehouse: transferData.toWarehouse,
      //   items: transferData.items.map(item => ({
      //     item_code: item.item_code,
      //     received_qty: item.qty
      //   })),
      //   receivedBy: 'branch@example.com'
      // });

      return createResult;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      {/* Your UI components */}
    </div>
  );
};
```

### Custom Hook Example

```javascript
import { useState, useCallback } from 'react';

export const useStockTransfer = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const createTransfer = useCallback(async (transferData) => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        '/api/method/techsavanna_pos.api.stock.create_stock_transfer',
        {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: new URLSearchParams({
            company: transferData.company,
            posting_date: transferData.postingDate,
            posting_time: transferData.postingTime,
            from_warehouse: transferData.fromWarehouse,
            to_warehouse: transferData.toWarehouse,
            items: JSON.stringify(transferData.items),
            notes: transferData.notes || ''
          })
        }
      );

      const result = await response.json();
      const data = result.message || result;

      if (data.status === 'error' || data.success === false) {
        throw new Error(data.message || 'Transfer failed');
      }

      return data;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const listTransfers = useCallback(async (filters = {}) => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        '/api/method/techsavanna_pos.api.stock.list_stock_transfer_requests',
        {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(filters)
        }
      );

      const result = await response.json();
      const data = result.message || result;

      if (!data.success) {
        throw new Error(data.message || 'Failed to list transfers');
      }

      return data.data.requests;
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    createTransfer,
    listTransfers,
    loading,
    error
  };
};

// Usage in component
const MyComponent = () => {
  const { createTransfer, listTransfers, loading, error } = useStockTransfer();

  const handleTransfer = async () => {
    try {
      const result = await createTransfer({
        company: 'Savanna Ltd',
        postingDate: '2025-01-20',
        postingTime: '10:00:00',
        fromWarehouse: 'Stores - HO',
        toWarehouse: 'Stores - Branch',
        items: [
          { item_code: 'ITEM-001', qty: 10 }
        ],
        notes: 'Urgent transfer'
      });
      console.log('Transfer created:', result.stock_entry);
    } catch (err) {
      console.error('Transfer failed:', err);
    }
  };

  return (
    <button onClick={handleTransfer} disabled={loading}>
      {loading ? 'Creating...' : 'Create Transfer'}
    </button>
  );
};
```

### TypeScript Types Example

```typescript
// types/stockTransfer.ts

export interface StockTransferItem {
  item_code: string;
  qty: number;
}

export interface CreateStockTransferRequest {
  company: string;
  posting_date: string;
  posting_time: string;
  from_warehouse: string;
  to_warehouse: string;
  items: StockTransferItem[];
  notes?: string;
}

export interface StockTransferResponse {
  status: 'success' | 'error';
  message: string;
  stock_entry?: string;
}

export interface StockTransferRequest {
  name: string;
  requested_by: string;
  requested_on: string;
  status: string;
  origin_warehouse: string;
  destination_warehouse: string;
  dispatched_by: string;
  received_by: string;
  goods_received_note: string;
  items?: Array<{
    item_code: string;
    requested_qty: number;
    dispatched_qty: number;
    received_qty: number;
  }>;
}

export interface ListStockTransferFilters {
  status?: string;
  origin_warehouse?: string;
  destination_warehouse?: string;
  from_date?: string;
  to_date?: string;
}

export interface DispatchStockRequest {
  request_id: string;
  origin_warehouse: string;
  items: Array<{
    item_code: string;
    dispatched_qty: number;
  }>;
  dispatched_by: string;
  dispatch_notes?: string;
}

export interface ReceiveStockRequest {
  request_id: string;
  destination_warehouse: string;
  items: Array<{
    item_code: string;
    received_qty: number;
  }>;
  received_by: string;
  receive_notes?: string;
  goods_received_note?: string;
}
```

---

## Status Codes Reference

### Material Request Status Values

| Status | Description |
|--------|-------------|
| `Draft` | Request created but not submitted |
| `Submitted` | Request submitted and waiting for approval |
| `Approved` | Request approved and ready for dispatch |
| `In Transit` | Stock has been fully dispatched |
| `Partially In Transit` | Some items have been dispatched |
| `Completed` | Stock has been fully received |
| `Partially Received` | Some items have been received |
| `Cancelled` | Request has been cancelled |

### Stock Entry Status

Stock Entries use `docstatus` field:
- `0` = Draft
- `1` = Submitted (finalized, stock updated)
- `2` = Cancelled

---

## Additional Notes

### Best Practices

1. **Always check authentication** - Ensure user is logged in before making requests
2. **Handle errors gracefully** - Display user-friendly error messages
3. **Validate on client side** - Check required fields before sending requests
4. **Use proper date formats** - Always use `YYYY-MM-DD` for dates and `HH:MM:SS` for times
5. **Quantity validation** - Ensure quantities are positive numbers
6. **Warehouse validation** - Verify warehouse names exist before transfer
7. **Idempotency** - Some operations can be called multiple times safely, but check status first

### Rate Limiting

Be aware of potential rate limiting on the backend. Implement retry logic with exponential backoff for production applications.

### Testing

When testing, use a test/sandbox environment. Stock transfers update inventory immediately, so use caution in production.

---

## Support

For issues or questions regarding these APIs, please contact your backend development team or refer to the Frappe/ERPNext documentation.

---

**Last Updated:** January 2025  
**API Version:** 1.0  
**Module:** techsavanna_pos.api.stock

