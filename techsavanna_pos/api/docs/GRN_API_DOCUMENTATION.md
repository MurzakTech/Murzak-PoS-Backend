# GRN (Goods Receipt Note) API Documentation

This document provides comprehensive API documentation for GRN (Goods Receipt Note) endpoints, designed for consumption in React.js applications.

## Table of Contents

- [Overview](#overview)
- [Base URL](#base-url)
- [Authentication](#authentication)
- [Endpoints](#endpoints)
  - [List GRNs](#1-list-grns)
  - [Get GRN Details](#2-get-grn-details)
- [Response Format](#response-format)
- [Error Handling](#error-handling)
- [React.js Integration Examples](#reactjs-integration-examples)
- [TypeScript Types](#typescript-types)

---

## Overview

The GRN API provides endpoints to list and retrieve detailed information about Goods Receipt Notes (Purchase Receipts) in the system. These endpoints support filtering, pagination, and detailed item-level information.

---

## Base URL

```
https://your-domain.com/api/method/techsavanna_pos.api.reports
```

---

## Authentication

All endpoints use `allow_guest=True`, meaning they can be accessed without authentication. However, if your system requires authentication, include the session cookie or API key in your requests.

**For authenticated requests:**
```javascript
// Include session cookie automatically with axios
axios.defaults.withCredentials = true;

// Or include in fetch
fetch(url, {
  credentials: 'include'
});
```

---

## Endpoints

### 1. List GRNs

Retrieve a paginated list of GRNs with optional filtering.

#### Endpoint

```
GET /api/method/techsavanna_pos.api.reports.grn_list_report
```

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `company` | string | No | User's default | Company name filter |
| `supplier` | string | No | - | Supplier name filter |
| `purchase_order` | string | No | - | Purchase Order number filter |
| `warehouse` | string | No | - | Warehouse name filter |
| `start_date` | string | No | - | Start date filter (YYYY-MM-DD) |
| `end_date` | string | No | - | End date filter (YYYY-MM-DD) |
| `status` | string | No | - | GRN status filter (e.g., "Received", "To Bill", "Completed") |
| `docstatus` | number | No | Excludes cancelled | Document status (0=Draft, 1=Submitted, 2=Cancelled) |
| `page` | number | No | 1 | Page number for pagination |
| `page_size` | number | No | 20 | Number of records per page (max: 100) |

#### Example Request

```javascript
// Using fetch
const fetchGRNs = async () => {
  const params = new URLSearchParams({
    company: 'Your Company',
    supplier: 'Supplier ABC',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    page: 1,
    page_size: 20
  });

  const response = await fetch(
    `https://your-domain.com/api/method/techsavanna_pos.api.reports.grn_list_report?${params}`,
    {
      method: 'GET',
      credentials: 'include'
    }
  );

  const data = await response.json();
  return data;
};
```

#### Success Response

```json
{
  "success": true,
  "data": [
    {
      "name": "MAT-PRE-2026-00001",
      "supplier": "Supplier ABC",
      "supplier_name": "Supplier ABC Ltd",
      "company": "Your Company",
      "posting_date": "2026-01-15",
      "posting_time": "14:30:00",
      "set_warehouse": "Main Warehouse - YC",
      "purchase_order": "PUR-ORD-2026-00001",
      "grand_total": 50000.00,
      "status": "Received",
      "docstatus": 1,
      "is_return": 0,
      "per_received": 100.0,
      "per_billed": 0.0,
      "items_count": 5,
      "total_qty": 100.0
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 45,
    "total_pages": 3
  }
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | Indicates if the request was successful |
| `data` | array | Array of GRN objects |
| `meta` | object | Pagination metadata |

**GRN Object Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | GRN number |
| `supplier` | string | Supplier ID |
| `supplier_name` | string | Supplier name |
| `company` | string | Company name |
| `posting_date` | string | Posting date (YYYY-MM-DD) |
| `posting_time` | string | Posting time (HH:MM:SS) |
| `set_warehouse` | string | Warehouse name |
| `purchase_order` | string | Linked Purchase Order number |
| `grand_total` | number | Total amount |
| `status` | string | GRN status |
| `docstatus` | number | Document status (0=Draft, 1=Submitted, 2=Cancelled) |
| `is_return` | number | Whether this is a return GRN (0 or 1) |
| `per_received` | number | Percentage received |
| `per_billed` | number | Percentage billed |
| `items_count` | number | Number of items in the GRN |
| `total_qty` | number | Total quantity of all items |

---

### 2. Get GRN Details

Retrieve detailed information for a specific GRN, including all items and stock application status.

#### Endpoint

```
GET /api/method/techsavanna_pos.api.reports.grn_detail_report
```

#### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `grn_no` | string | Yes | GRN number (Purchase Receipt name) |

#### Example Request

```javascript
const fetchGRNDetails = async (grnNo) => {
  const response = await fetch(
    `https://your-domain.com/api/method/techsavanna_pos.api.reports.grn_detail_report?grn_no=${grnNo}`,
    {
      method: 'GET',
      credentials: 'include'
    }
  );

  const data = await response.json();
  return data;
};
```

#### Success Response

```json
{
  "success": true,
  "data": {
    "grn_no": "MAT-PRE-2026-00001",
    "supplier": "Supplier ABC",
    "supplier_name": "Supplier ABC Ltd",
    "company": "Your Company",
    "posting_date": "2026-01-15",
    "posting_time": "14:30:00",
    "set_warehouse": "Main Warehouse - YC",
    "purchase_order": "PUR-ORD-2026-00001",
    "status": "Received",
    "docstatus": 1,
    "is_return": 0,
    "grand_total": 50000.00,
    "net_total": 45000.00,
    "total_qty": 100.0,
    "per_received": 100.0,
    "per_billed": 0.0,
    "items": [
      {
        "item_code": "ITEM-001",
        "item_name": "Product Name",
        "description": "Product Description",
        "qty": 50.0,
        "received_qty": 50.0,
        "rejected_qty": 0.0,
        "rate": 500.00,
        "amount": 25000.00,
        "warehouse": "Main Warehouse - YC",
        "uom": "Nos",
        "purchase_order": "PUR-ORD-2026-00001",
        "purchase_order_item": "abc123",
        "applied_to_stock": true,
        "stock_entry": "STE-2026-00001",
        "stock_entry_date": "2026-01-15"
      }
    ],
    "purchase_order_details": {
      "po_no": "PUR-ORD-2026-00001",
      "transaction_date": "2026-01-10",
      "status": "To Receive and Bill",
      "grand_total": 50000.00
    }
  }
}
```

#### Response Fields

**Item Object Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `item_code` | string | Item code |
| `item_name` | string | Item name |
| `description` | string | Item description |
| `qty` | number | Quantity |
| `received_qty` | number | Received quantity |
| `rejected_qty` | number | Rejected quantity |
| `rate` | number | Item rate |
| `amount` | number | Item amount |
| `warehouse` | string | Warehouse name |
| `uom` | string | Unit of measure |
| `purchase_order` | string | Linked Purchase Order |
| `purchase_order_item` | string | Purchase Order item reference |
| `applied_to_stock` | boolean | Whether item has been applied to stock |
| `stock_entry` | string | Stock Entry number (if applied) |
| `stock_entry_date` | string | Stock Entry date (if applied) |

---

## Response Format

All endpoints return a consistent response format:

### Success Response

```json
{
  "success": true,
  "data": { ... }
}
```

### Error Response

```json
{
  "success": false,
  "message": "Error message describing what went wrong"
}
```

---

## Error Handling

### Common Error Scenarios

1. **Invalid GRN Number**
   ```json
   {
     "success": false,
     "message": "GRN MAT-PRE-2026-99999 not found"
   }
   ```

2. **Missing Required Parameter**
   ```json
   {
     "success": false,
     "message": "GRN number is required"
   }
   ```

3. **Server Error**
   ```json
   {
     "success": false,
     "message": "Error fetching GRN list: [error details]"
   }
   ```

### Error Handling in React

```javascript
const handleGRNFetch = async () => {
  try {
    const response = await fetch(apiUrl);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.message || 'An error occurred');
    }
    
    return data.data;
  } catch (error) {
    console.error('Error fetching GRNs:', error);
    // Handle error (show toast, set error state, etc.)
    throw error;
  }
};
```

---

## React.js Integration Examples

### 1. Basic GRN List Component

```jsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';

const GRNList = () => {
  const [grns, setGrns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [pagination, setPagination] = useState({
    page: 1,
    pageSize: 20,
    total: 0,
    totalPages: 0
  });

  const fetchGRNs = async (page = 1, filters = {}) => {
    setLoading(true);
    setError(null);

    try {
      const params = {
        page,
        page_size: pagination.pageSize,
        ...filters
      };

      const response = await axios.get(
        '/api/method/techsavanna_pos.api.reports.grn_list_report',
        { params }
      );

      if (response.data.success) {
        setGrns(response.data.data);
        setPagination(prev => ({
          ...prev,
          page,
          ...response.data.meta
        }));
      } else {
        throw new Error(response.data.message);
      }
    } catch (err) {
      setError(err.message);
      console.error('Error fetching GRNs:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGRNs();
  }, []);

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div>
      <h2>GRN List</h2>
      <table>
        <thead>
          <tr>
            <th>GRN No</th>
            <th>Supplier</th>
            <th>Date</th>
            <th>Warehouse</th>
            <th>Status</th>
            <th>Total Amount</th>
            <th>Items</th>
          </tr>
        </thead>
        <tbody>
          {grns.map(grn => (
            <tr key={grn.name}>
              <td>{grn.name}</td>
              <td>{grn.supplier_name}</td>
              <td>{grn.posting_date}</td>
              <td>{grn.set_warehouse}</td>
              <td>{grn.status}</td>
              <td>{grn.grand_total.toLocaleString()}</td>
              <td>{grn.items_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      
      <div className="pagination">
        <button
          disabled={pagination.page === 1}
          onClick={() => fetchGRNs(pagination.page - 1)}
        >
          Previous
        </button>
        <span>
          Page {pagination.page} of {pagination.totalPages}
        </span>
        <button
          disabled={pagination.page >= pagination.totalPages}
          onClick={() => fetchGRNs(pagination.page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
};

export default GRNList;
```

### 2. GRN List with Filters

```jsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';

const GRNListWithFilters = () => {
  const [grns, setGrns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({
    supplier: '',
    warehouse: '',
    start_date: '',
    end_date: '',
    status: ''
  });

  const fetchGRNs = async () => {
    setLoading(true);
    try {
      // Remove empty filters
      const activeFilters = Object.fromEntries(
        Object.entries(filters).filter(([_, value]) => value !== '')
      );

      const response = await axios.get(
        '/api/method/techsavanna_pos.api.reports.grn_list_report',
        { params: activeFilters }
      );

      if (response.data.success) {
        setGrns(response.data.data);
      }
    } catch (error) {
      console.error('Error:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleFilterChange = (field, value) => {
    setFilters(prev => ({ ...prev, [field]: value }));
  };

  const handleSearch = () => {
    fetchGRNs();
  };

  return (
    <div>
      <div className="filters">
        <input
          type="text"
          placeholder="Supplier"
          value={filters.supplier}
          onChange={(e) => handleFilterChange('supplier', e.target.value)}
        />
        <input
          type="text"
          placeholder="Warehouse"
          value={filters.warehouse}
          onChange={(e) => handleFilterChange('warehouse', e.target.value)}
        />
        <input
          type="date"
          placeholder="Start Date"
          value={filters.start_date}
          onChange={(e) => handleFilterChange('start_date', e.target.value)}
        />
        <input
          type="date"
          placeholder="End Date"
          value={filters.end_date}
          onChange={(e) => handleFilterChange('end_date', e.target.value)}
        />
        <select
          value={filters.status}
          onChange={(e) => handleFilterChange('status', e.target.value)}
        >
          <option value="">All Statuses</option>
          <option value="Received">Received</option>
          <option value="To Bill">To Bill</option>
          <option value="Completed">Completed</option>
        </select>
        <button onClick={handleSearch}>Search</button>
      </div>

      {loading ? (
        <div>Loading...</div>
      ) : (
        <GRNTable grns={grns} />
      )}
    </div>
  );
};

export default GRNListWithFilters;
```

### 3. GRN Detail Component

```jsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';

const GRNDetail = ({ grnNo }) => {
  const [grn, setGrn] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchGRNDetails = async () => {
      if (!grnNo) return;

      setLoading(true);
      setError(null);

      try {
        const response = await axios.get(
          '/api/method/techsavanna_pos.api.reports.grn_detail_report',
          { params: { grn_no: grnNo } }
        );

        if (response.data.success) {
          setGrn(response.data.data);
        } else {
          throw new Error(response.data.message);
        }
      } catch (err) {
        setError(err.message);
        console.error('Error fetching GRN details:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchGRNDetails();
  }, [grnNo]);

  if (loading) return <div>Loading GRN details...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!grn) return <div>No GRN data available</div>;

  return (
    <div className="grn-detail">
      <h2>GRN Details: {grn.grn_no}</h2>
      
      <div className="grn-info">
        <div>
          <strong>Supplier:</strong> {grn.supplier_name}
        </div>
        <div>
          <strong>Date:</strong> {grn.posting_date}
        </div>
        <div>
          <strong>Warehouse:</strong> {grn.set_warehouse}
        </div>
        <div>
          <strong>Status:</strong> {grn.status}
        </div>
        <div>
          <strong>Total Amount:</strong> {grn.grand_total.toLocaleString()}
        </div>
        {grn.purchase_order && (
          <div>
            <strong>Purchase Order:</strong> {grn.purchase_order}
          </div>
        )}
      </div>

      <h3>Items</h3>
      <table>
        <thead>
          <tr>
            <th>Item Code</th>
            <th>Item Name</th>
            <th>Quantity</th>
            <th>Rate</th>
            <th>Amount</th>
            <th>Warehouse</th>
            <th>Stock Status</th>
          </tr>
        </thead>
        <tbody>
          {grn.items.map((item, index) => (
            <tr key={index}>
              <td>{item.item_code}</td>
              <td>{item.item_name}</td>
              <td>{item.qty}</td>
              <td>{item.rate.toLocaleString()}</td>
              <td>{item.amount.toLocaleString()}</td>
              <td>{item.warehouse}</td>
              <td>
                {item.applied_to_stock ? (
                  <span className="success">Applied</span>
                ) : (
                  <span className="pending">Pending</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default GRNDetail;
```

### 4. Custom Hook for GRN API

```jsx
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';

const useGRNList = (filters = {}, page = 1, pageSize = 20) => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [pagination, setPagination] = useState({
    page: 1,
    pageSize: 20,
    total: 0,
    totalPages: 0
  });

  const fetchGRNs = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const params = {
        page,
        page_size: pageSize,
        ...filters
      };

      const response = await axios.get(
        '/api/method/techsavanna_pos.api.reports.grn_list_report',
        { params }
      );

      if (response.data.success) {
        setData(response.data.data);
        setPagination({
          page,
          pageSize,
          ...response.data.meta
        });
      } else {
        throw new Error(response.data.message);
      }
    } catch (err) {
      setError(err.message);
      console.error('Error fetching GRNs:', err);
    } finally {
      setLoading(false);
    }
  }, [filters, page, pageSize]);

  useEffect(() => {
    fetchGRNs();
  }, [fetchGRNs]);

  return {
    grns: data,
    loading,
    error,
    pagination,
    refetch: fetchGRNs
  };
};

export default useGRNList;

// Usage in component:
const MyComponent = () => {
  const { grns, loading, error, pagination, refetch } = useGRNList(
    { supplier: 'Supplier ABC' },
    1,
    20
  );

  // Component implementation...
};
```

---

## TypeScript Types

For TypeScript projects, use these type definitions:

```typescript
// GRN Types
interface GRN {
  name: string;
  supplier: string;
  supplier_name: string;
  company: string;
  posting_date: string;
  posting_time: string;
  set_warehouse: string;
  purchase_order: string | null;
  grand_total: number;
  status: string;
  docstatus: number;
  is_return: number;
  per_received: number;
  per_billed: number;
  items_count: number;
  total_qty: number;
}

interface GRNItem {
  item_code: string;
  item_name: string;
  description: string;
  qty: number;
  received_qty: number;
  rejected_qty: number;
  rate: number;
  amount: number;
  warehouse: string;
  uom: string;
  purchase_order: string | null;
  purchase_order_item: string | null;
  applied_to_stock: boolean;
  stock_entry: string | null;
  stock_entry_date: string | null;
}

interface GRNDetail {
  grn_no: string;
  supplier: string;
  supplier_name: string;
  company: string;
  posting_date: string;
  posting_time: string;
  set_warehouse: string;
  purchase_order: string | null;
  status: string;
  docstatus: number;
  is_return: number;
  grand_total: number;
  net_total: number;
  total_qty: number;
  per_received: number;
  per_billed: number;
  items: GRNItem[];
  purchase_order_details?: {
    po_no: string;
    transaction_date: string;
    status: string;
    grand_total: number;
  };
}

interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

interface GRNListResponse {
  success: boolean;
  data: GRN[];
  meta: PaginationMeta;
}

interface GRNDetailResponse {
  success: boolean;
  data: GRNDetail;
}

interface APIError {
  success: false;
  message: string;
}

// Filter types
interface GRNFilters {
  company?: string;
  supplier?: string;
  purchase_order?: string;
  warehouse?: string;
  start_date?: string;
  end_date?: string;
  status?: string;
  docstatus?: number;
}
```

---

## Best Practices

### 1. Error Handling

Always check the `success` field before using the data:

```javascript
const response = await fetch(apiUrl);
const data = await response.json();

if (!data.success) {
  // Handle error
  throw new Error(data.message);
}

// Use data.data
const grns = data.data;
```

### 2. Loading States

Implement loading states for better UX:

```javascript
const [loading, setLoading] = useState(false);

const fetchData = async () => {
  setLoading(true);
  try {
    // Fetch data
  } finally {
    setLoading(false);
  }
};
```

### 3. Pagination

Use the pagination metadata to implement proper pagination:

```javascript
const handlePageChange = (newPage) => {
  fetchGRNs(newPage, filters);
};
```

### 4. Caching

Consider implementing caching for frequently accessed data:

```javascript
import { useQuery } from 'react-query';

const { data, isLoading, error } = useQuery(
  ['grns', filters, page],
  () => fetchGRNs(page, filters),
  { staleTime: 5 * 60 * 1000 } // Cache for 5 minutes
);
```

### 5. Debouncing Filters

Debounce filter inputs to avoid excessive API calls:

```javascript
import { useDebounce } from 'use-debounce';

const [filters, setFilters] = useState({});
const [debouncedFilters] = useDebounce(filters, 500);

useEffect(() => {
  fetchGRNs(1, debouncedFilters);
}, [debouncedFilters]);
```

---

## Testing

### Example Test with Jest and React Testing Library

```javascript
import { render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import GRNList from './GRNList';

jest.mock('axios');

test('renders GRN list', async () => {
  const mockGRNs = {
    success: true,
    data: [
      {
        name: 'MAT-PRE-2026-00001',
        supplier_name: 'Supplier ABC',
        posting_date: '2026-01-15',
        grand_total: 50000
      }
    ],
    meta: {
      page: 1,
      page_size: 20,
      total: 1,
      total_pages: 1
    }
  };

  axios.get.mockResolvedValue({ data: mockGRNs });

  render(<GRNList />);

  await waitFor(() => {
    expect(screen.getByText('MAT-PRE-2026-00001')).toBeInTheDocument();
  });
});
```

---

## Support

For issues or questions regarding the GRN API:

1. Check the error message in the response
2. Verify all required parameters are provided
3. Check network requests in browser DevTools
4. Review server logs for detailed error information

---

## Changelog

### Version 1.0.0 (2026-01-XX)
- Initial release of GRN API endpoints
- List GRNs with filtering and pagination
- Get detailed GRN information with item-level stock status

