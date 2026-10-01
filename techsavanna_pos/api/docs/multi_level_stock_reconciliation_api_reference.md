# Multi-Level Stock Reconciliation API Reference

Complete API reference with detailed payload structures and response formats for the multi-level stock reconciliation workflow.

## Base URL
```
/api/method/techsavanna_pos.api.inventory_api.{endpoint_name}
```

## Authentication
All endpoints require authentication. Include authentication headers in requests.

---

## Table of Contents

1. [Create Multi-Level Stock Reconciliation](#1-create-multi-level-stock-reconciliation)
2. [Add Sales User Stock Take](#2-add-sales-user-stock-take)
3. [Add Quality Manager Stock Take](#3-add-quality-manager-stock-take)
4. [Add Stock Manager Stock Take and Submit](#4-add-stock-manager-stock-take-and-submit)
5. [Get Multi-Level Stock Reconciliation](#5-get-multi-level-stock-reconciliation)
6. [List Multi-Level Stock Reconciliations](#6-list-multi-level-stock-reconciliations)

---

## 1. Create Multi-Level Stock Reconciliation

Creates a new stock reconciliation document for the multi-level workflow.

**Endpoint:** `create_multi_level_stock_reconciliation`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```json
{
  "warehouse": "string (required)",
  "posting_date": "string (optional, format: YYYY-MM-DD)",
  "posting_time": "string (optional, format: HH:MM:SS)",
  "company": "string (optional)",
  "expense_account": "string (optional)",
  "cost_center": "string (optional)",
  "purpose": "string (optional, 'Stock Reconciliation' or 'Opening Stock', default: 'Stock Reconciliation')",
  "items": [
    {
      "item_code": "string"
    }
  ]
}
```

### Payload Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `warehouse` | string | Yes | Warehouse name to reconcile |
| `posting_date` | string | No | Posting date (YYYY-MM-DD). Defaults to today |
| `posting_time` | string | No | Posting time (HH:MM:SS). Defaults to current time |
| `company` | string | No | Company name. Uses default company if not provided |
| `expense_account` | string | No | Difference account. Auto-selected if not provided |
| `cost_center` | string | No | Cost center. Auto-fetched from company defaults |
| `purpose` | string | No | Purpose: "Stock Reconciliation" or "Opening Stock" |
| `items` | array | No | Initial list of items (can be added later) |

### Example Request

```bash
curl -X POST "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.create_multi_level_stock_reconciliation" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "warehouse": "Stores - HO",
    "posting_date": "2025-01-20",
    "posting_time": "10:30:00",
    "company": "Savanna Ltd",
    "purpose": "Stock Reconciliation",
    "items": [
      { "item_code": "ITEM-001" },
      { "item_code": "ITEM-002" }
    ]
  }'
```

### Success Response

**HTTP Status:** `200 OK`

```json
{
  "success": true,
  "message": "Multi-level stock reconciliation created successfully",
  "data": {
    "name": "MAT-RECO-2025-00001",
    "company": "Savanna Ltd",
    "warehouse": "Stores - HO",
    "posting_date": "2025-01-20",
    "docstatus": 0,
    "workflow_status": "Pending Sales User"
  }
}
```

### Response Structure

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Always `true` on success |
| `message` | string | Success message |
| `data.name` | string | Stock Reconciliation document name |
| `data.company` | string | Company name |
| `data.warehouse` | string | Warehouse name |
| `data.posting_date` | string | Posting date (YYYY-MM-DD) |
| `data.docstatus` | number | Document status (0 = Draft) |
| `data.workflow_status` | string | Current workflow status |

### Error Response

**HTTP Status:** `400 Bad Request` or `500 Internal Server Error`

```json
{
  "success": false,
  "message": "Error description here"
}
```

---

## 2. Add Sales User Stock Take

Adds stock counts and comments from the Sales User.

**Endpoint:** `add_sales_person_stock_take`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```json
{
  "reconciliation_name": "string (required)",
  "items": [
    {
      "item_code": "string (required)",
      "qty": "number (required, >= 0)",
      "comment": "string (optional)"
    }
  ],
  "comment": "string (optional)"
}
```

### Payload Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reconciliation_name` | string | Yes | Stock Reconciliation document name |
| `items` | array | Yes | List of items with quantities and comments |
| `items[].item_code` | string | Yes | Item code |
| `items[].qty` | number | Yes | Quantity counted (must be >= 0) |
| `items[].comment` | string | No | Item-specific comment |
| `comment` | string | No | General comment for the stock take |

### Example Request

```bash
curl -X POST "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.add_sales_person_stock_take" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items": [
      {
        "item_code": "ITEM-001",
        "qty": 100,
        "comment": "Counted all items in main aisle"
      },
      {
        "item_code": "ITEM-002",
        "qty": 50,
        "comment": "Some items in back storage"
      }
    ],
    "comment": "Completed initial count for all items"
  }'
```

### Success Response

**HTTP Status:** `200 OK`

```json
{
  "success": true,
  "message": "Sales User stock take added successfully",
  "data": {
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items_counted": 2,
    "workflow_status": "Pending Quality Manager"
  }
}
```

### Response Structure

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Always `true` on success |
| `message` | string | Success message |
| `data.reconciliation_name` | string | Stock Reconciliation document name |
| `data.items_counted` | number | Number of items processed |
| `data.workflow_status` | string | Updated workflow status |

### Error Response

**HTTP Status:** `400 Bad Request` or `422 Unprocessable Entity`

```json
{
  "success": false,
  "message": "Stock Reconciliation is already submitted. Cannot add stock take."
}
```

---

## 3. Add Quality Manager Stock Take

Adds stock counts and comments from the Quality Manager.

**Endpoint:** `add_stock_controller_stock_take`  
**Method:** `POST`  
**Auth Required:** Yes

### Request Payload

```json
{
  "reconciliation_name": "string (required)",
  "items": [
    {
      "item_code": "string (required)",
      "qty": "number (required, >= 0)",
      "comment": "string (optional)"
    }
  ],
  "comment": "string (optional)"
}
```

### Payload Structure

Same as [Add Sales User Stock Take](#2-add-sales-user-stock-take)

### Example Request

```bash
curl -X POST "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.add_stock_controller_stock_take" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items": [
      {
        "item_code": "ITEM-001",
        "qty": 98,
        "comment": "Found 2 damaged items, excluded from count"
      },
      {
        "item_code": "ITEM-002",
        "qty": 52,
        "comment": "Found additional items in overflow area"
      }
    ],
    "comment": "Verified counts and found discrepancies"
  }'
```

### Success Response

**HTTP Status:** `200 OK`

```json
{
  "success": true,
  "message": "Quality Manager stock take added successfully",
  "data": {
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items_counted": 2,
    "workflow_status": "Pending Stock Manager"
  }
}
```

### Response Structure

Same as [Add Sales User Stock Take](#2-add-sales-user-stock-take), but with updated workflow status.

---

## 4. Add Stock Manager Stock Take and Submit

Adds stock counts and comments from the Stock Manager and optionally submits the reconciliation.

**Endpoint:** `add_stock_manager_stock_take_and_submit`  
**Method:** `POST`  
**Auth Required:** Yes  
**Role Required:** Stock Manager (for submission)

### Request Payload

```json
{
  "reconciliation_name": "string (required)",
  "items": [
    {
      "item_code": "string (required)",
      "qty": "number (required, >= 0)",
      "comment": "string (optional)"
    }
  ],
  "comment": "string (optional)",
  "submit": "boolean (optional, default: true)"
}
```

### Payload Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reconciliation_name` | string | Yes | Stock Reconciliation document name |
| `items` | array | Yes | List of items with quantities and comments |
| `items[].item_code` | string | Yes | Item code |
| `items[].qty` | number | Yes | Final quantity (becomes reconciliation quantity) |
| `items[].comment` | string | No | Item-specific comment |
| `comment` | string | No | General comment for the stock take |
| `submit` | boolean | No | Whether to submit the reconciliation (default: true) |

### Example Request

```bash
curl -X POST "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.add_stock_manager_stock_take_and_submit" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items": [
      {
        "item_code": "ITEM-001",
        "qty": 99,
        "comment": "Adjusted for damaged items, final count verified"
      },
      {
        "item_code": "ITEM-002",
        "qty": 51,
        "comment": "Reconciled with sales user and quality manager counts"
      }
    ],
    "comment": "Final reconciliation completed and approved",
    "submit": true
  }'
```

### Success Response

**HTTP Status:** `200 OK`

```json
{
  "success": true,
  "message": "Stock Manager stock take added successfully and reconciliation submitted",
  "data": {
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items_counted": 2,
    "workflow_status": "Completed",
    "submission": {
      "submitted": true,
      "docstatus": 1
    },
    "docstatus": 1
  }
}
```

### Response Structure

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Always `true` on success |
| `message` | string | Success message |
| `data.reconciliation_name` | string | Stock Reconciliation document name |
| `data.items_counted` | number | Number of items processed |
| `data.workflow_status` | string | Updated workflow status ("Completed") |
| `data.submission.submitted` | boolean | Whether submission was successful |
| `data.submission.docstatus` | number | Document status after submission (1 = Submitted) |
| `data.submission.error` | string | Error message if submission failed |
| `data.docstatus` | number | Final document status |

### Error Response (No Stock Manager Role)

**HTTP Status:** `403 Forbidden`

```json
{
  "success": false,
  "message": "Only users with Stock Manager role can submit stock reconciliation."
}
```

### Error Response (Submission Failed)

**HTTP Status:** `200 OK` (but submission failed)

```json
{
  "success": true,
  "message": "Stock Manager stock take added successfully",
  "data": {
    "reconciliation_name": "MAT-RECO-2025-00001",
    "items_counted": 2,
    "workflow_status": "Completed",
    "submission": {
      "submitted": false,
      "error": "Validation error: Insufficient stock"
    },
    "docstatus": 0
  }
}
```

---

## 5. Get Multi-Level Stock Reconciliation

Gets a stock reconciliation document with all levels of stock taking records.

**Endpoint:** `get_multi_level_stock_reconciliation`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

### Request Parameters

**Query Parameters (GET):**
```
?reconciliation_name=MAT-RECO-2025-00001
```

**Body Parameters (POST):**
```json
{
  "reconciliation_name": "string (required)"
}
```

### Example Request

```bash
# GET request
curl -X GET "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.get_multi_level_stock_reconciliation?reconciliation_name=MAT-RECO-2025-00001" \
  -H "Authorization: Bearer YOUR_TOKEN"

# POST request
curl -X POST "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.get_multi_level_stock_reconciliation" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "reconciliation_name": "MAT-RECO-2025-00001"
  }'
```

### Success Response

**HTTP Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "name": "MAT-RECO-2025-00001",
    "company": "Savanna Ltd",
    "warehouse": "Stores - HO",
    "posting_date": "2025-01-20",
    "posting_time": "10:30:00",
    "purpose": "Stock Reconciliation",
    "docstatus": 1,
    "workflow_status": "Completed",
    "stock_taking_records": [
      {
        "item_code": "ITEM-001",
        "warehouse": "Stores - HO",
        "final_qty": 99,
        "current_qty": 100,
        "sales_person_qty": 100,
        "sales_person_comment": "Counted all items in main aisle",
        "sales_person_name": "sales.user@example.com",
        "sales_person_date": "2025-01-20 11:00:00",
        "stock_controller_qty": 98,
        "stock_controller_comment": "Found 2 damaged items, excluded from count",
        "stock_controller_name": "quality.manager@example.com",
        "stock_controller_date": "2025-01-20 14:00:00",
        "stock_manager_qty": 99,
        "stock_manager_comment": "Adjusted for damaged items, final count verified",
        "stock_manager_name": "stock.manager@example.com",
        "stock_manager_date": "2025-01-20 16:00:00"
      },
      {
        "item_code": "ITEM-002",
        "warehouse": "Stores - HO",
        "final_qty": 51,
        "current_qty": 50,
        "sales_person_qty": 50,
        "sales_person_comment": "Some items in back storage",
        "sales_person_name": "sales.user@example.com",
        "sales_person_date": "2025-01-20 11:00:00",
        "stock_controller_qty": 52,
        "stock_controller_comment": "Found additional items in overflow area",
        "stock_controller_name": "quality.manager@example.com",
        "stock_controller_date": "2025-01-20 14:00:00",
        "stock_manager_qty": 51,
        "stock_manager_comment": "Reconciled with sales user and quality manager counts",
        "stock_manager_name": "stock.manager@example.com",
        "stock_manager_date": "2025-01-20 16:00:00"
      }
    ],
    "items": [
      {
        "item_code": "ITEM-001",
        "warehouse": "Stores - HO",
        "qty": 99,
        "current_qty": 100
      },
      {
        "item_code": "ITEM-002",
        "warehouse": "Stores - HO",
        "qty": 51,
        "current_qty": 50
      }
    ]
  }
}
```

### Response Structure

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Always `true` on success |
| `data.name` | string | Stock Reconciliation document name |
| `data.company` | string | Company name |
| `data.warehouse` | string | Warehouse name |
| `data.posting_date` | string | Posting date (YYYY-MM-DD) |
| `data.posting_time` | string | Posting time (HH:MM:SS) |
| `data.purpose` | string | Purpose of reconciliation |
| `data.docstatus` | number | Document status (0=Draft, 1=Submitted, 2=Cancelled) |
| `data.workflow_status` | string | Current workflow status |
| `data.stock_taking_records` | array | Array of stock taking records for each item |
| `data.stock_taking_records[].item_code` | string | Item code |
| `data.stock_taking_records[].warehouse` | string | Warehouse name |
| `data.stock_taking_records[].final_qty` | number | Final quantity used for reconciliation |
| `data.stock_taking_records[].current_qty` | number | Current system quantity before reconciliation |
| `data.stock_taking_records[].sales_person_qty` | number | Quantity counted by Sales User |
| `data.stock_taking_records[].sales_person_comment` | string | Comment from Sales User |
| `data.stock_taking_records[].sales_person_name` | string | Email/name of Sales User |
| `data.stock_taking_records[].sales_person_date` | string | Date/time when Sales User completed count |
| `data.stock_taking_records[].stock_controller_qty` | number | Quantity counted by Quality Manager |
| `data.stock_taking_records[].stock_controller_comment` | string | Comment from Quality Manager |
| `data.stock_taking_records[].stock_controller_name` | string | Email/name of Quality Manager |
| `data.stock_taking_records[].stock_controller_date` | string | Date/time when Quality Manager completed count |
| `data.stock_taking_records[].stock_manager_qty` | number | Quantity counted by Stock Manager |
| `data.stock_taking_records[].stock_manager_comment` | string | Comment from Stock Manager |
| `data.stock_taking_records[].stock_manager_name` | string | Email/name of Stock Manager |
| `data.stock_taking_records[].stock_manager_date` | string | Date/time when Stock Manager completed count |
| `data.items` | array | Array of items in the reconciliation |
| `data.items[].item_code` | string | Item code |
| `data.items[].warehouse` | string | Warehouse name |
| `data.items[].qty` | number | Final quantity |
| `data.items[].current_qty` | number | Current system quantity |

### Error Response

**HTTP Status:** `404 Not Found`

```json
{
  "success": false,
  "message": "Stock Reconciliation 'MAT-RECO-2025-00001' does not exist"
}
```

---

## 6. List Multi-Level Stock Reconciliations

Lists multi-level stock reconciliations with optional filters and pagination.

**Endpoint:** `list_multi_level_stock_reconciliations`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

### Request Parameters

**Query Parameters (GET):**
```
?workflow_status=Pending Sales User&warehouse=Stores - HO&limit=20&offset=0
```

**Body Parameters (POST):**
```json
{
  "workflow_status": "string (optional)",
  "warehouse": "string (optional)",
  "company": "string (optional)",
  "from_date": "string (optional, format: YYYY-MM-DD)",
  "to_date": "string (optional, format: YYYY-MM-DD)",
  "limit": "number (optional, default: 20)",
  "offset": "number (optional, default: 0)"
}
```

### Payload Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `workflow_status` | string | No | Filter by workflow status: "Pending Sales User", "Pending Quality Manager", "Pending Stock Manager", "Completed" |
| `warehouse` | string | No | Filter by warehouse name |
| `company` | string | No | Filter by company name |
| `from_date` | string | No | Filter from date (YYYY-MM-DD) |
| `to_date` | string | No | Filter to date (YYYY-MM-DD) |
| `limit` | number | No | Number of records to return (default: 20, max: 100) |
| `offset` | number | No | Number of records to skip for pagination (default: 0) |

### Example Request

```bash
# GET request
curl -X GET "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.list_multi_level_stock_reconciliations?workflow_status=Pending Sales User&limit=20&offset=0" \
  -H "Authorization: Bearer YOUR_TOKEN"

# POST request
curl -X POST "https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.list_multi_level_stock_reconciliations" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "workflow_status": "Pending Sales User",
    "warehouse": "Stores - HO",
    "from_date": "2025-01-01",
    "to_date": "2025-01-31",
    "limit": 20,
    "offset": 0
  }'
```

### Success Response

**HTTP Status:** `200 OK`

```json
{
  "success": true,
  "data": {
    "reconciliations": [
      {
        "name": "MAT-RECO-2025-00001",
        "company": "Savanna Ltd",
        "warehouse": "Stores - HO",
        "warehouses": ["Stores - HO"],
        "posting_date": "2025-01-20",
        "posting_time": "10:30:00",
        "purpose": "Stock Reconciliation",
        "docstatus": 0,
        "workflow_status": "Pending Sales User",
        "creation": "2025-01-20 10:30:00",
        "modified": "2025-01-20 10:30:00",
        "owner": "admin@example.com",
        "items_count": 2
      },
      {
        "name": "MAT-RECO-2025-00002",
        "company": "Savanna Ltd",
        "warehouse": "Stores - Branch",
        "warehouses": ["Stores - Branch"],
        "posting_date": "2025-01-19",
        "posting_time": "09:15:00",
        "purpose": "Stock Reconciliation",
        "docstatus": 0,
        "workflow_status": "Pending Quality Manager",
        "creation": "2025-01-19 09:15:00",
        "modified": "2025-01-19 14:30:00",
        "owner": "admin@example.com",
        "items_count": 5
      }
    ],
    "total_count": 15,
    "limit": 20,
    "offset": 0,
    "has_more": false
  }
}
```

### Response Structure

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Always `true` on success |
| `data.reconciliations` | array | Array of stock reconciliation summaries |
| `data.reconciliations[].name` | string | Stock Reconciliation document name |
| `data.reconciliations[].company` | string | Company name |
| `data.reconciliations[].warehouse` | string | Primary warehouse name (first warehouse if multiple) |
| `data.reconciliations[].warehouses` | array | List of all warehouses in the reconciliation |
| `data.reconciliations[].posting_date` | string | Posting date (YYYY-MM-DD) |
| `data.reconciliations[].posting_time` | string | Posting time (HH:MM:SS) |
| `data.reconciliations[].purpose` | string | Purpose of reconciliation |
| `data.reconciliations[].docstatus` | number | Document status (0=Draft, 1=Submitted, 2=Cancelled) |
| `data.reconciliations[].workflow_status` | string | Current workflow status |
| `data.reconciliations[].creation` | string | Creation date/time |
| `data.reconciliations[].modified` | string | Last modified date/time |
| `data.reconciliations[].owner` | string | Creator's email/name |
| `data.reconciliations[].items_count` | number | Number of items in the reconciliation |
| `data.total_count` | number | Total number of reconciliations matching filters |
| `data.limit` | number | Number of records returned |
| `data.offset` | number | Number of records skipped |
| `data.has_more` | boolean | Whether there are more records available |

### Error Response

**HTTP Status:** `400 Bad Request` or `500 Internal Server Error`

```json
{
  "success": false,
  "message": "Error listing multi-level stock reconciliations: [error details]"
}
```

---

## Common Response Patterns

### Success Response Pattern

All successful API calls follow this pattern:

```json
{
  "success": true,
  "message": "Operation completed successfully",
  "data": {
    // Endpoint-specific data
  }
}
```

### Error Response Pattern

All error responses follow this pattern:

```json
{
  "success": false,
  "message": "Error description",
  "error_type": "validation_error" // Optional
}
```

---

## HTTP Status Codes

| Status Code | Description |
|------------|-------------|
| `200 OK` | Request successful |
| `400 Bad Request` | Invalid request parameters |
| `401 Unauthorized` | Authentication required |
| `403 Forbidden` | Insufficient permissions (e.g., not Stock Manager) |
| `404 Not Found` | Resource not found |
| `422 Unprocessable Entity` | Validation error |
| `500 Internal Server Error` | Server error |

---

## Workflow Status Values

| Status | Description |
|--------|-------------|
| `Pending Sales User` | Waiting for Sales User to add stock take |
| `Pending Quality Manager` | Waiting for Quality Manager to add stock take |
| `Pending Stock Manager` | Waiting for Stock Manager to add stock take and submit |
| `Completed` | Stock Manager has completed stock take (may or may not be submitted) |

---

## Document Status Values

| Status | Description |
|--------|-------------|
| `0` | Draft - Can be modified |
| `1` | Submitted - Final, cannot be modified |
| `2` | Cancelled - Cancelled document |

---

## Pagination

The list endpoint supports pagination using `limit` and `offset` parameters:

- **limit**: Maximum number of records to return (default: 20, recommended max: 100)
- **offset**: Number of records to skip (default: 0)
- **has_more**: Boolean indicating if more records are available

### Pagination Example

```javascript
// First page
const page1 = await listReconciliations({ limit: 20, offset: 0 });

// Second page
const page2 = await listReconciliations({ limit: 20, offset: 20 });

// Check if more pages exist
if (page1.data.has_more) {
  // Load next page
}
```

---

## Filtering Examples

### Filter by Workflow Status

```bash
# Get all reconciliations pending Sales User
GET /api/method/.../list_multi_level_stock_reconciliations?workflow_status=Pending Sales User
```

### Filter by Date Range

```bash
# Get reconciliations from January 2025
POST /api/method/.../list_multi_level_stock_reconciliations
{
  "from_date": "2025-01-01",
  "to_date": "2025-01-31"
}
```

### Filter by Warehouse

```bash
# Get reconciliations for specific warehouse
POST /api/method/.../list_multi_level_stock_reconciliations
{
  "warehouse": "Stores - HO"
}
```

### Combined Filters

```bash
# Get pending Sales User reconciliations for a warehouse in date range
POST /api/method/.../list_multi_level_stock_reconciliations
{
  "workflow_status": "Pending Sales User",
  "warehouse": "Stores - HO",
  "from_date": "2025-01-01",
  "to_date": "2025-01-31",
  "limit": 50
}
```

---

## Complete Workflow Example

```javascript
// 1. Create reconciliation
const createResponse = await createReconciliation({
  warehouse: "Stores - HO",
  posting_date: "2025-01-20",
  items: [{ item_code: "ITEM-001" }, { item_code: "ITEM-002" }]
});
const reconciliationName = createResponse.data.name;

// 2. Sales User adds stock take
await addSalesUserStockTake({
  reconciliation_name: reconciliationName,
  items: [
    { item_code: "ITEM-001", qty: 100, comment: "Counted all items" },
    { item_code: "ITEM-002", qty: 50, comment: "Some in back storage" }
  ],
  comment: "Initial count completed"
});

// 3. Quality Manager adds stock take
await addQualityManagerStockTake({
  reconciliation_name: reconciliationName,
  items: [
    { item_code: "ITEM-001", qty: 98, comment: "Found 2 damaged items" },
    { item_code: "ITEM-002", qty: 52, comment: "Found additional items" }
  ],
  comment: "Verified and adjusted counts"
});

// 4. Stock Manager adds stock take and submits
await addStockManagerStockTake({
  reconciliation_name: reconciliationName,
  items: [
    { item_code: "ITEM-001", qty: 99, comment: "Final count verified" },
    { item_code: "ITEM-002", qty: 51, comment: "Reconciled all counts" }
  ],
  comment: "Final reconciliation approved",
  submit: true
});

// 5. Get final reconciliation
const finalReconciliation = await getReconciliation(reconciliationName);
console.log("Final reconciliation:", finalReconciliation.data);
```

---

## Notes

1. **Authentication**: All endpoints require valid authentication. Use Bearer tokens or session cookies based on your setup.

2. **Date Formats**: 
   - Dates: `YYYY-MM-DD` (e.g., "2025-01-20")
   - Times: `HH:MM:SS` (e.g., "10:30:00")
   - DateTimes: `YYYY-MM-DD HH:MM:SS` (e.g., "2025-01-20 10:30:00")

3. **Quantity Validation**: All quantities must be >= 0 (non-negative).

4. **Workflow Sequence**: Follow the workflow sequence (Sales User → Quality Manager → Stock Manager) for best results.

5. **Role Requirements**: Only users with "Stock Manager" role can submit reconciliations.

6. **Pagination**: Use `limit` and `offset` for pagination. Check `has_more` to determine if more records exist.

7. **Filtering**: Multiple filters can be combined. All filters are optional.

8. **Error Handling**: Always check the `success` field in responses and handle errors appropriately.

