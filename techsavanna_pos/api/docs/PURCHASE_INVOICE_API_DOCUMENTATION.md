# Purchase Invoice API Documentation

This document provides comprehensive API documentation for Purchase Invoice endpoints, designed for consumption in React.js applications.

## Table of Contents

- [Overview](#overview)
- [Endpoints](#endpoints)
  - [1. List Purchase Invoices](#1-list-purchase-invoices)
  - [2. Get Purchase Invoice Details](#2-get-purchase-invoice-details)
  - [3. Pay Purchase Invoice](#3-pay-purchase-invoice)
- [TypeScript Interfaces](#typescript-interfaces)
- [Usage Examples](#usage-examples)
- [Error Handling](#error-handling)

---

## Overview

The Purchase Invoice API provides endpoints to list, retrieve detailed information, and make payments for Purchase Invoices in the system. These endpoints support filtering, pagination, detailed item-level information, and payment processing.

**Base URL:** `/api/method/techsavanna_pos.api.purchase`

---

## Endpoints

### 1. List Purchase Invoices

Retrieve a paginated list of Purchase Invoices with optional filtering.

**Endpoint:** `GET /api/method/techsavanna_pos.api.purchase.list_purchase_invoices`

**Authentication:** Required (not guest accessible)

#### Request Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | number | No | 1 | Page number for pagination |
| `page_size` | number | No | 20 | Number of records per page (max: 100) |
| `supplier` | string | No | - | Filter by supplier name |
| `company` | string | No | - | Filter by company name |
| `purchase_order` | string | No | - | Filter by Purchase Order number |
| `purchase_receipt` | string | No | - | Filter by Purchase Receipt (GRN) number |
| `from_date` | string | No | - | Filter from date (YYYY-MM-DD) |
| `to_date` | string | No | - | Filter to date (YYYY-MM-DD) |
| `docstatus` | number \| string | No | - | Document status: `0` (Draft), `1` (Submitted), `2` (Cancelled), `"all"` (All) |
| `status` | string | No | - | Invoice status (e.g., "Draft", "Unpaid", "Paid", "Overdue") |
| `bill_no` | string | No | - | Filter by supplier invoice number (partial match) |

#### Request Example

```typescript
// Using fetch API
const fetchPurchaseInvoices = async () => {
  const params = new URLSearchParams({
    page: '1',
    page_size: '20',
    supplier: 'Supplier ABC',
    status: 'Unpaid',
    from_date: '2026-01-01',
    to_date: '2026-01-31'
  });

  const response = await fetch(
    `/api/method/techsavanna_pos.api.purchase.list_purchase_invoices?${params}`,
    {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    }
  );

  const data = await response.json();
  return data;
};
```

```typescript
// Using axios
import axios from 'axios';

const fetchPurchaseInvoices = async (filters: PurchaseInvoiceFilters) => {
  const response = await axios.get(
    '/api/method/techsavanna_pos.api.purchase.list_purchase_invoices',
    { params: filters }
  );
  return response.data;
};
```

#### Response Structure

```typescript
{
  status: "success" | "error";
  message: string;
  data: PurchaseInvoiceListItem[];
  meta: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
}
```

#### Success Response Example

```json
{
  "status": "success",
  "message": "Purchase Invoices fetched successfully",
  "data": [
    {
      "name": "ACC-PINV-2026-00001",
      "supplier": "Supplier ABC",
      "supplier_name": "Supplier ABC",
      "company": "Your Company",
      "posting_date": "2026-01-15",
      "due_date": "2026-02-14",
      "bill_no": "SUP-INV-2026-001",
      "bill_date": "2026-01-15",
      "grand_total": 10000.00,
      "outstanding_amount": 10000.00,
      "status": "Unpaid",
      "docstatus": 1,
      "currency": "KES"
    },
    {
      "name": "ACC-PINV-2026-00002",
      "supplier": "Supplier XYZ",
      "supplier_name": "Supplier XYZ",
      "company": "Your Company",
      "posting_date": "2026-01-16",
      "due_date": "2026-02-15",
      "bill_no": "SUP-INV-2026-002",
      "bill_date": "2026-01-16",
      "grand_total": 15000.00,
      "outstanding_amount": 5000.00,
      "status": "Partly Paid",
      "docstatus": 1,
      "currency": "KES"
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

#### Error Response Example

```json
{
  "status": "error",
  "message": "No permission to access Purchase Invoices"
}
```

---

### 2. Get Purchase Invoice Details

Retrieve detailed information for a specific Purchase Invoice, including all items, taxes, linked documents, and attachments.

**Endpoint:** `GET /api/method/techsavanna_pos.api.purchase.get_purchase_invoice_details`

**Authentication:** Required (not guest accessible)

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `invoice_no` | string | Yes | Purchase Invoice name/number (e.g., "ACC-PINV-2026-00001") |

#### Request Example

```typescript
// Using fetch API
const fetchPurchaseInvoiceDetails = async (invoiceNo: string) => {
  const response = await fetch(
    `/api/method/techsavanna_pos.api.purchase.get_purchase_invoice_details?invoice_no=${invoiceNo}`,
    {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    }
  );

  const data = await response.json();
  return data;
};
```

```typescript
// Using axios
import axios from 'axios';

const fetchPurchaseInvoiceDetails = async (invoiceNo: string) => {
  const response = await axios.get(
    '/api/method/techsavanna_pos.api.purchase.get_purchase_invoice_details',
    { params: { invoice_no: invoiceNo } }
  );
  return response.data;
};
```

#### Response Structure

```typescript
{
  status: "success" | "error";
  message: string;
  data: PurchaseInvoiceDetail;
}
```

#### Success Response Example

```json
{
  "status": "success",
  "message": "Purchase Invoice details fetched successfully",
  "data": {
    "invoice_no": "ACC-PINV-2026-00001",
    "supplier": "Supplier ABC",
    "supplier_name": "Supplier ABC",
    "company": "Your Company",
    "posting_date": "2026-01-15",
    "posting_time": "10:30:00",
    "due_date": "2026-02-14",
    "bill_no": "SUP-INV-2026-001",
    "bill_date": "2026-01-15",
    "status": "Unpaid",
    "docstatus": 1,
    "is_return": 0,
    "is_paid": 0,
    "currency": "KES",
    "conversion_rate": 1.0,
    "grand_total": 10000.00,
    "net_total": 8500.00,
    "total_taxes_and_charges": 1500.00,
    "outstanding_amount": 10000.00,
    "paid_amount": 0.00,
    "write_off_amount": 0.00,
    "items": [
      {
        "item_code": "ITEM-001",
        "item_name": "Product Name",
        "description": "Product Description",
        "qty": 10.00,
        "rate": 850.00,
        "amount": 8500.00,
        "warehouse": "Warehouse - Company",
        "uom": "Nos",
        "stock_qty": 10.00,
        "purchase_receipt": "MAT-PRE-2026-00001",
        "purchase_receipt_item": "item-row-123",
        "purchase_order": "PO-00045",
        "purchase_order_item": "po-item-456"
      },
      {
        "item_code": "ITEM-002",
        "item_name": "Another Product",
        "description": "Another Product Description",
        "qty": 5.00,
        "rate": 200.00,
        "amount": 1000.00,
        "warehouse": "Warehouse - Company",
        "uom": "Nos",
        "stock_qty": 5.00,
        "purchase_receipt": "MAT-PRE-2026-00001",
        "purchase_order": "PO-00045"
      }
    ],
    "taxes": [
      {
        "charge_type": "On Net Total",
        "account_head": "VAT - Company",
        "description": "VAT @ 18%",
        "rate": 18.00,
        "tax_amount": 1530.00,
        "total": 10030.00
      }
    ],
    "purchase_orders": [
      "PO-00045"
    ],
    "purchase_receipts": [
      "MAT-PRE-2026-00001"
    ],
    "attachments": [
      {
        "name": "file-123",
        "file_name": "supplier_invoice.pdf",
        "file_url": "/files/supplier_invoice.pdf",
        "is_private": 0,
        "file_size": 245678
      },
      {
        "name": "file-124",
        "file_name": "delivery_note.pdf",
        "file_url": "/files/delivery_note.pdf",
        "is_private": 0,
        "file_size": 189234
      }
    ]
  }
}
```

#### Error Response Examples

**Invoice Not Found:**
```json
{
  "status": "error",
  "code": "LPO_NOT_FOUND",
  "message": "Purchase Invoice ACC-PINV-2026-99999 not found",
  "data": {}
}
```

**Missing Parameter:**
```json
{
  "status": "error",
  "code": "INVALID_REQUEST",
  "message": "Purchase Invoice number is required",
  "data": {}
}
```

**Permission Denied:**
```json
{
  "status": "error",
  "code": "PERMISSION_DENIED",
  "message": "No permission to access this Purchase Invoice",
  "data": {}
}
```

---

### 3. Pay Purchase Invoice

Create a Payment Entry against a Purchase Invoice to record payment made to the supplier. This moves the invoice from "Partly Paid" or "Unpaid" to "Paid" status when fully paid.

**Endpoint:** `POST /api/method/techsavanna_pos.api.purchase.pay_purchase_invoice`

**Authentication:** Required (guest accessible)

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `invoice_no` | string | Yes | Purchase Invoice name/number (e.g., "ACC-PINV-2026-00001") |
| `paid_amount` | number | No | Amount to pay (defaults to outstanding_amount if not provided) |
| `mode_of_payment` | string | No | Mode of payment name (e.g., "Cash", "Bank Transfer", "Cheque") |
| `bank_account` | string | No | Bank account name (required for bank payments) |
| `posting_date` | string | No | Posting date (YYYY-MM-DD, defaults to today) |
| `reference_no` | string | No | Payment reference number (e.g., cheque number, transaction ID) |
| `reference_date` | string | No | Reference date (YYYY-MM-DD) |
| `remarks` | string | No | Additional remarks/notes |
| `submit` | boolean | No | Whether to submit the payment entry (default: true) |

#### Request Example

```typescript
// Using fetch API
const payPurchaseInvoice = async (invoiceNo: string, amount: number) => {
  const response = await fetch(
    '/api/method/techsavanna_pos.api.purchase.pay_purchase_invoice',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        invoice_no: invoiceNo,
        paid_amount: amount,
        mode_of_payment: 'Cash',
        posting_date: '2026-01-15',
        remarks: 'Payment made in full'
      }),
    }
  );

  const data = await response.json();
  return data;
};
```

```typescript
// Using axios
import axios from 'axios';

const payPurchaseInvoice = async (invoiceNo: string, amount: number) => {
  const response = await axios.post(
    '/api/method/techsavanna_pos.api.purchase.pay_purchase_invoice',
    {
      invoice_no: invoiceNo,
      paid_amount: amount,
      mode_of_payment: 'Bank Transfer',
      bank_account: 'Main Bank Account - Company',
      reference_no: 'CHQ-12345',
      reference_date: '2026-01-15',
      remarks: 'Payment via cheque',
      submit: true
    }
  );

  return response.data;
};
```

#### Success Response Example

```json
{
  "status": "success",
  "message": "Payment Entry created successfully",
  "data": {
    "payment_entry": {
      "name": "ACC-PAY-2026-00001",
      "payment_type": "Pay",
      "party": "Supplier ABC",
      "party_type": "Supplier",
      "paid_amount": 5000.00,
      "received_amount": 5000.00,
      "posting_date": "2026-01-15",
      "mode_of_payment": "Cash",
      "docstatus": 1,
      "submitted": true
    },
    "purchase_invoice": {
      "name": "ACC-PINV-2026-00001",
      "outstanding_amount": 0.00,
      "status": "Paid",
      "paid_amount": 5000.00,
      "grand_total": 5000.00
    }
  }
}
```

#### Partial Payment Example

```json
{
  "status": "success",
  "message": "Payment Entry created successfully",
  "data": {
    "payment_entry": {
      "name": "ACC-PAY-2026-00002",
      "payment_type": "Pay",
      "party": "Supplier ABC",
      "party_type": "Supplier",
      "paid_amount": 3000.00,
      "received_amount": 3000.00,
      "posting_date": "2026-01-15",
      "mode_of_payment": "Bank Transfer",
      "docstatus": 1,
      "submitted": true
    },
    "purchase_invoice": {
      "name": "ACC-PINV-2026-00001",
      "outstanding_amount": 2000.00,
      "status": "Partly Paid",
      "paid_amount": 3000.00,
      "grand_total": 5000.00
    }
  }
}
```

#### Error Response Examples

**Invoice Not Found:**
```json
{
  "status": "error",
  "code": "LPO_NOT_FOUND",
  "message": "Purchase Invoice ACC-PINV-2026-99999 not found",
  "data": {}
}
```

**Invoice Not Submitted:**
```json
{
  "status": "error",
  "code": "INVALID_REQUEST",
  "message": "Purchase Invoice ACC-PINV-2026-00001 must be submitted before making payment",
  "data": {}
}
```

**Already Fully Paid:**
```json
{
  "status": "error",
  "code": "INVALID_REQUEST",
  "message": "Purchase Invoice ACC-PINV-2026-00001 is already fully paid",
  "data": {}
}
```

**Amount Exceeds Outstanding:**
```json
{
  "status": "error",
  "code": "INVALID_REQUEST",
  "message": "Paid amount (6000.00) cannot be greater than outstanding amount (5000.00)",
  "data": {}
}
```

**No Mode of Payment:**
```json
{
  "status": "error",
  "code": "INVALID_REQUEST",
  "message": "No mode of payment found. Please configure at least one Mode of Payment.",
  "data": {}
}
```

#### Notes

1. **Payment Types:**
   - For Purchase Invoices, the payment type is automatically set to "Pay" (paying money to supplier)
   - The party type is automatically set to "Supplier"

2. **Payment Amount:**
   - If `paid_amount` is not provided, it defaults to the full outstanding amount
   - Partial payments are supported - you can pay less than the outstanding amount
   - The invoice status will update to "Partly Paid" for partial payments and "Paid" when fully paid

3. **Payment Entry Submission:**
   - By default, payment entries are automatically submitted (`submit: true`)
   - Set `submit: false` to create a draft payment entry that can be reviewed before submission
   - Only submitted payment entries update the invoice outstanding amount

4. **Accounting Impact:**
   - When a payment entry is submitted, it creates General Ledger entries:
     - **Debit**: Supplier Account (Accounts Payable) - reduces liability
     - **Credit**: Cash/Bank Account - reduces cash/bank balance

5. **Mode of Payment:**
   - If not provided, the system will try to use the company's default mode of payment
   - If no default is set, it will use the first available enabled mode of payment
   - For bank payments, you may need to specify `bank_account`

6. **Payment References:**
   - Use `reference_no` for cheque numbers, transaction IDs, or other payment references
   - Use `reference_date` for the date associated with the reference (e.g., cheque date)

---

## TypeScript Interfaces

### Purchase Invoice List Item

```typescript
interface PurchaseInvoiceListItem {
  name: string;
  supplier: string;
  supplier_name: string;
  company: string;
  posting_date: string;
  due_date: string | null;
  bill_no: string | null;
  bill_date: string | null;
  grand_total: number;
  outstanding_amount: number;
  status: string;
  docstatus: number;
  currency: string;
}
```

### Purchase Invoice Detail

```typescript
interface PurchaseInvoiceDetail {
  invoice_no: string;
  supplier: string;
  supplier_name: string;
  company: string;
  posting_date: string | null;
  posting_time: string | null;
  due_date: string | null;
  bill_no: string | null;
  bill_date: string | null;
  status: string;
  docstatus: number;
  is_return: number;
  is_paid: number;
  currency: string;
  conversion_rate: number;
  grand_total: number;
  net_total: number;
  total_taxes_and_charges: number;
  outstanding_amount: number;
  paid_amount: number;
  write_off_amount: number;
  items: PurchaseInvoiceItem[];
  taxes: PurchaseInvoiceTax[];
  purchase_orders: string[];
  purchase_receipts: string[];
  payment_entries: PaymentEntry[];
  attachments: FileAttachment[];
}
```

### Purchase Invoice Item

```typescript
interface PurchaseInvoiceItem {
  item_code: string;
  item_name: string;
  description: string | null;
  qty: number;
  rate: number;
  amount: number;
  warehouse: string | null;
  uom: string | null;
  stock_qty: number | null;
  purchase_receipt?: string;
  purchase_receipt_item?: string;
  purchase_order?: string;
  purchase_order_item?: string;
}
```

### Purchase Invoice Tax

```typescript
interface PurchaseInvoiceTax {
  charge_type: string;
  account_head: string;
  description: string | null;
  rate: number | null;
  tax_amount: number;
  total: number;
}
```

### File Attachment

```typescript
interface FileAttachment {
  name: string;
  file_name: string;
  file_url: string;
  is_private: number;
  file_size: number | null;
}
```

### Payment Entry

```typescript
interface PaymentEntry {
  name: string;
  payment_type: "Pay" | "Receive" | "Internal Transfer";
  posting_date: string | null;
  mode_of_payment: string | null;
  paid_amount: number;
  received_amount: number;
  allocated_amount: number | null;
  reference_no: string | null;
  reference_date: string | null;
  remarks: string | null;
  docstatus: number;
  status: string;
  submitted: boolean;
}
```

### Pay Purchase Invoice Request

```typescript
interface PayPurchaseInvoiceRequest {
  invoice_no: string;
  paid_amount?: number;
  mode_of_payment?: string;
  bank_account?: string;
  posting_date?: string;
  reference_no?: string;
  reference_date?: string;
  remarks?: string;
  submit?: boolean;
}
```

### Pay Purchase Invoice Response

```typescript
interface PayPurchaseInvoiceResponse {
  status: "success" | "error";
  message: string;
  data: {
    payment_entry: {
      name: string;
      payment_type: "Pay";
      party: string;
      party_type: "Supplier";
      paid_amount: number;
      received_amount: number;
      posting_date: string;
      mode_of_payment: string;
      docstatus: number;
      submitted: boolean;
    };
    purchase_invoice: {
      name: string;
      outstanding_amount: number;
      status: string;
      paid_amount: number | null;
      grand_total: number;
    };
  };
}
```

### Purchase Invoice Filters

```typescript
interface PurchaseInvoiceFilters {
  page?: number;
  page_size?: number;
  supplier?: string;
  company?: string;
  purchase_order?: string;
  purchase_receipt?: string;
  from_date?: string;
  to_date?: string;
  docstatus?: number | string;
  status?: string;
  bill_no?: string;
}
```

### API Response Types

```typescript
interface PurchaseInvoiceListResponse {
  status: "success" | "error";
  message: string;
  data: PurchaseInvoiceListItem[];
  meta: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
}

interface PurchaseInvoiceDetailResponse {
  status: "success" | "error";
  message: string;
  data: PurchaseInvoiceDetail;
}

interface PayPurchaseInvoiceResponse {
  status: "success" | "error";
  message: string;
  data: {
    payment_entry: PaymentEntry;
    purchase_invoice: {
      name: string;
      outstanding_amount: number;
      status: string;
      paid_amount: number | null;
      grand_total: number;
    };
  };
}
```

---

## Usage Examples

### React Hook for Listing Purchase Invoices

```typescript
import { useState, useEffect } from 'react';
import axios from 'axios';

interface UsePurchaseInvoiceListProps {
  filters?: PurchaseInvoiceFilters;
  autoFetch?: boolean;
}

export const usePurchaseInvoiceList = ({
  filters = {},
  autoFetch = true
}: UsePurchaseInvoiceListProps = {}) => {
  const [invoices, setInvoices] = useState<PurchaseInvoiceListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pagination, setPagination] = useState({
    page: 1,
    page_size: 20,
    total: 0,
    total_pages: 0
  });

  const fetchInvoices = async (page: number = 1, customFilters?: PurchaseInvoiceFilters) => {
    setLoading(true);
    setError(null);

    try {
      const params = {
        page,
        page_size: pagination.page_size,
        ...filters,
        ...customFilters
      };

      const response = await axios.get(
        '/api/method/techsavanna_pos.api.purchase.list_purchase_invoices',
        { params }
      );

      if (response.data.status === 'success') {
        setInvoices(response.data.data);
        setPagination(response.data.meta);
      } else {
        setError(response.data.message);
      }
    } catch (err: any) {
      setError(err.response?.data?.message || 'Failed to fetch purchase invoices');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (autoFetch) {
      fetchInvoices();
    }
  }, []);

  return {
    invoices,
    loading,
    error,
    pagination,
    refetch: () => fetchInvoices(pagination.page),
    fetchPage: (page: number) => fetchInvoices(page)
  };
};
```

### React Component Example

```typescript
import React, { useState } from 'react';
import { usePurchaseInvoiceList } from './hooks/usePurchaseInvoiceList';

const PurchaseInvoiceList: React.FC = () => {
  const [filters, setFilters] = useState<PurchaseInvoiceFilters>({
    status: 'Unpaid',
    page: 1,
    page_size: 20
  });

  const { invoices, loading, error, pagination, fetchPage } = usePurchaseInvoiceList({
    filters,
    autoFetch: true
  });

  if (loading) return <div>Loading purchase invoices...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div>
      <h2>Purchase Invoices</h2>
      
      {/* Filters */}
      <div className="filters">
        <input
          type="text"
          placeholder="Supplier"
          onChange={(e) => setFilters({ ...filters, supplier: e.target.value })}
        />
        <select
          value={filters.status || ''}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
        >
          <option value="">All Status</option>
          <option value="Unpaid">Unpaid</option>
          <option value="Paid">Paid</option>
          <option value="Overdue">Overdue</option>
        </select>
      </div>

      {/* Invoice List */}
      <table>
        <thead>
          <tr>
            <th>Invoice No</th>
            <th>Supplier</th>
            <th>Date</th>
            <th>Bill No</th>
            <th>Amount</th>
            <th>Outstanding</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((invoice) => (
            <tr key={invoice.name}>
              <td>{invoice.name}</td>
              <td>{invoice.supplier_name}</td>
              <td>{invoice.posting_date}</td>
              <td>{invoice.bill_no || '-'}</td>
              <td>{invoice.grand_total.toLocaleString()}</td>
              <td>{invoice.outstanding_amount.toLocaleString()}</td>
              <td>{invoice.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Pagination */}
      <div className="pagination">
        <button
          disabled={pagination.page === 1}
          onClick={() => fetchPage(pagination.page - 1)}
        >
          Previous
        </button>
        <span>
          Page {pagination.page} of {pagination.total_pages}
        </span>
        <button
          disabled={pagination.page >= pagination.total_pages}
          onClick={() => fetchPage(pagination.page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
};

export default PurchaseInvoiceList;
```

### React Hook for Purchase Invoice Details

```typescript
import { useState, useEffect } from 'react';
import axios from 'axios';

export const usePurchaseInvoiceDetails = (invoiceNo: string | null) => {
  const [invoice, setInvoice] = useState<PurchaseInvoiceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!invoiceNo) return;

    const fetchDetails = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await axios.get(
          '/api/method/techsavanna_pos.api.purchase.get_purchase_invoice_details',
          { params: { invoice_no: invoiceNo } }
        );

        if (response.data.status === 'success') {
          setInvoice(response.data.data);
        } else {
          setError(response.data.message);
        }
      } catch (err: any) {
        setError(err.response?.data?.message || 'Failed to fetch purchase invoice details');
      } finally {
        setLoading(false);
      }
    };

    fetchDetails();
  }, [invoiceNo]);

  return { invoice, loading, error };
};
```

### Purchase Invoice Detail Component

```typescript
import React from 'react';
import { usePurchaseInvoiceDetails } from './hooks/usePurchaseInvoiceDetails';

interface PurchaseInvoiceDetailProps {
  invoiceNo: string;
}

const PurchaseInvoiceDetail: React.FC<PurchaseInvoiceDetailProps> = ({ invoiceNo }) => {
  const { invoice, loading, error } = usePurchaseInvoiceDetails(invoiceNo);

  if (loading) return <div>Loading invoice details...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!invoice) return <div>No invoice data available</div>;

  return (
    <div className="invoice-detail">
      <h2>Purchase Invoice: {invoice.invoice_no}</h2>
      
      <div className="invoice-info">
        <div>
          <strong>Supplier:</strong> {invoice.supplier_name}
        </div>
        <div>
          <strong>Date:</strong> {invoice.posting_date}
        </div>
        <div>
          <strong>Due Date:</strong> {invoice.due_date}
        </div>
        <div>
          <strong>Bill No:</strong> {invoice.bill_no || '-'}
        </div>
        <div>
          <strong>Status:</strong> {invoice.status}
        </div>
        <div>
          <strong>Total Amount:</strong> {invoice.grand_total.toLocaleString()} {invoice.currency}
        </div>
        <div>
          <strong>Outstanding:</strong> {invoice.outstanding_amount.toLocaleString()} {invoice.currency}
        </div>
      </div>

      {/* Items Table */}
      <h3>Items</h3>
      <table>
        <thead>
          <tr>
            <th>Item Code</th>
            <th>Item Name</th>
            <th>Qty</th>
            <th>Rate</th>
            <th>Amount</th>
            <th>Warehouse</th>
          </tr>
        </thead>
        <tbody>
          {invoice.items.map((item, index) => (
            <tr key={index}>
              <td>{item.item_code}</td>
              <td>{item.item_name}</td>
              <td>{item.qty}</td>
              <td>{item.rate.toLocaleString()}</td>
              <td>{item.amount.toLocaleString()}</td>
              <td>{item.warehouse || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Taxes */}
      {invoice.taxes.length > 0 && (
        <>
          <h3>Taxes</h3>
          <table>
            <thead>
              <tr>
                <th>Description</th>
                <th>Rate</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {invoice.taxes.map((tax, index) => (
                <tr key={index}>
                  <td>{tax.description}</td>
                  <td>{tax.rate ? `${tax.rate}%` : '-'}</td>
                  <td>{tax.tax_amount.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Linked Documents */}
      {invoice.purchase_receipts.length > 0 && (
        <div>
          <h3>Linked GRNs</h3>
          <ul>
            {invoice.purchase_receipts.map((grn, index) => (
              <li key={index}>{grn}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Payment Entries */}
      {invoice.payment_entries && invoice.payment_entries.length > 0 && (
        <div>
          <h3>Payment History</h3>
          <table>
            <thead>
              <tr>
                <th>Payment Entry</th>
                <th>Date</th>
                <th>Mode</th>
                <th>Amount</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {invoice.payment_entries.map((payment, index) => (
                <tr key={index}>
                  <td>{payment.name}</td>
                  <td>{payment.posting_date}</td>
                  <td>{payment.mode_of_payment}</td>
                  <td>{payment.allocated_amount?.toFixed(2) || payment.paid_amount.toFixed(2)}</td>
                  <td>{payment.submitted ? 'Submitted' : 'Draft'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Attachments */}
      {invoice.attachments.length > 0 && (
        <div>
          <h3>Attachments</h3>
          <ul>
            {invoice.attachments.map((file, index) => (
              <li key={index}>
                <a href={file.file_url} target="_blank" rel="noopener noreferrer">
                  {file.file_name}
                </a>
                {file.file_size && <span> ({(file.file_size / 1024).toFixed(2)} KB)</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

export default PurchaseInvoiceDetail;
```

### React Component for Paying Purchase Invoice

```typescript
import React, { useState } from 'react';
import axios from 'axios';

interface PayInvoiceFormProps {
  invoiceNo: string;
  outstandingAmount: number;
  onPaymentSuccess?: () => void;
}

const PayInvoiceForm: React.FC<PayInvoiceFormProps> = ({
  invoiceNo,
  outstandingAmount,
  onPaymentSuccess
}) => {
  const [formData, setFormData] = useState<PayPurchaseInvoiceRequest>({
    invoice_no: invoiceNo,
    paid_amount: outstandingAmount,
    mode_of_payment: '',
    posting_date: new Date().toISOString().split('T')[0],
    submit: true
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(false);

    try {
      const response = await axios.post<PayPurchaseInvoiceResponse>(
        '/api/method/techsavanna_pos.api.purchase.pay_purchase_invoice',
        formData
      );

      if (response.data.status === 'success') {
        setSuccess(true);
        if (onPaymentSuccess) {
          onPaymentSuccess();
        }
      } else {
        setError(response.data.message || 'Payment failed');
      }
    } catch (err: any) {
      setError(
        err.response?.data?.message || 
        err.message || 
        'An error occurred while processing payment'
      );
    } finally {
      setLoading(false);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: name === 'paid_amount' || name === 'submit' 
        ? (name === 'submit' ? (e.target as HTMLInputElement).checked : parseFloat(value))
        : value
    }));
  };

  return (
    <form onSubmit={handleSubmit} className="pay-invoice-form">
      <h3>Pay Purchase Invoice</h3>
      
      {error && (
        <div className="error-message" style={{ color: 'red', marginBottom: '1rem' }}>
          {error}
        </div>
      )}

      {success && (
        <div className="success-message" style={{ color: 'green', marginBottom: '1rem' }}>
          Payment processed successfully!
        </div>
      )}

      <div className="form-group">
        <label>
          Invoice Number:
          <input
            type="text"
            name="invoice_no"
            value={formData.invoice_no}
            disabled
            style={{ opacity: 0.6 }}
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          Outstanding Amount:
          <input
            type="text"
            value={outstandingAmount.toFixed(2)}
            disabled
            style={{ opacity: 0.6 }}
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          Amount to Pay:
          <input
            type="number"
            name="paid_amount"
            value={formData.paid_amount || ''}
            onChange={handleChange}
            min="0"
            max={outstandingAmount}
            step="0.01"
            required
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          Mode of Payment:
          <select
            name="mode_of_payment"
            value={formData.mode_of_payment || ''}
            onChange={handleChange}
            required
          >
            <option value="">Select Mode of Payment</option>
            <option value="Cash">Cash</option>
            <option value="Bank Transfer">Bank Transfer</option>
            <option value="Cheque">Cheque</option>
          </select>
        </label>
      </div>

      <div className="form-group">
        <label>
          Posting Date:
          <input
            type="date"
            name="posting_date"
            value={formData.posting_date || ''}
            onChange={handleChange}
            required
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          Reference Number (Optional):
          <input
            type="text"
            name="reference_no"
            value={formData.reference_no || ''}
            onChange={handleChange}
            placeholder="e.g., Cheque number, Transaction ID"
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          Reference Date (Optional):
          <input
            type="date"
            name="reference_date"
            value={formData.reference_date || ''}
            onChange={handleChange}
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          Remarks (Optional):
          <textarea
            name="remarks"
            value={formData.remarks || ''}
            onChange={(e) => setFormData(prev => ({ ...prev, remarks: e.target.value }))}
            rows={3}
          />
        </label>
      </div>

      <div className="form-group">
        <label>
          <input
            type="checkbox"
            name="submit"
            checked={formData.submit !== false}
            onChange={handleChange}
          />
          Submit payment entry immediately
        </label>
      </div>

      <button type="submit" disabled={loading}>
        {loading ? 'Processing...' : 'Process Payment'}
      </button>
    </form>
  );
};

export default PayInvoiceForm;
```

### React Hook for Paying Purchase Invoice

```typescript
import { useState } from 'react';
import axios from 'axios';

export const usePayPurchaseInvoice = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const payInvoice = async (request: PayPurchaseInvoiceRequest): Promise<PayPurchaseInvoiceResponse | null> => {
    setLoading(true);
    setError(null);

    try {
      const response = await axios.post<PayPurchaseInvoiceResponse>(
        '/api/method/techsavanna_pos.api.purchase.pay_purchase_invoice',
        request
      );

      if (response.data.status === 'success') {
        return response.data;
      } else {
        setError(response.data.message || 'Payment failed');
        return null;
      }
    } catch (err: any) {
      const errorMessage = 
        err.response?.data?.message || 
        err.message || 
        'An error occurred while processing payment';
      setError(errorMessage);
      return null;
    } finally {
      setLoading(false);
    }
  };

  return { payInvoice, loading, error };
};

// Usage in a component:
const PaymentButton: React.FC<{ invoiceNo: string; outstandingAmount: number }> = ({
  invoiceNo,
  outstandingAmount
}) => {
  const { payInvoice, loading, error } = usePayPurchaseInvoice();

  const handlePay = async () => {
    const result = await payInvoice({
      invoice_no: invoiceNo,
      paid_amount: outstandingAmount,
      mode_of_payment: 'Cash',
      submit: true
    });

    if (result) {
      alert('Payment processed successfully!');
      // Refresh invoice data or navigate
    }
  };

  return (
    <div>
      {error && <div style={{ color: 'red' }}>{error}</div>}
      <button onClick={handlePay} disabled={loading}>
        {loading ? 'Processing...' : `Pay ${outstandingAmount.toFixed(2)}`}
      </button>
    </div>
  );
};
```

---

## Error Handling

### Common Error Codes

| Code | Description |
|------|-------------|
| `INVALID_REQUEST` | Missing or invalid request parameters |
| `LPO_NOT_FOUND` | Purchase Invoice not found |
| `PERMISSION_DENIED` | User doesn't have permission to access the resource |
| `UNKNOWN_ERROR` | Unexpected server error |

### Error Handling Example

```typescript
const handleApiError = (error: any) => {
  if (error.response) {
    const { status, data } = error.response;
    
    switch (data.code) {
      case 'INVALID_REQUEST':
        return 'Invalid request. Please check your input.';
      case 'LPO_NOT_FOUND':
        return 'Purchase Invoice not found.';
      case 'PERMISSION_DENIED':
        return 'You do not have permission to access this resource.';
      default:
        return data.message || 'An error occurred. Please try again.';
    }
  } else if (error.request) {
    return 'Network error. Please check your connection.';
  } else {
    return 'An unexpected error occurred.';
  }
};
```

---

## Notes

1. **Authentication**: All endpoints require authentication. Ensure you include authentication headers in your requests.

2. **Date Format**: All dates should be in `YYYY-MM-DD` format.

3. **Pagination**: The maximum `page_size` is 100. Default is 20.

4. **Status Values**: Common status values include:
   - `Draft` - Invoice is in draft state
   - `Unpaid` - Invoice is submitted but not paid
   - `Paid` - Invoice is fully paid
   - `Partly Paid` - Invoice is partially paid
   - `Overdue` - Invoice is past due date
   - `Cancelled` - Invoice is cancelled

5. **Document Status**: 
   - `0` = Draft
   - `1` = Submitted
   - `2` = Cancelled

6. **Currency Formatting**: All monetary values are returned as numbers. Format them in your React components using `toLocaleString()` or a currency formatting library.

---

## Support

For issues or questions regarding the Purchase Invoice API:
- Check the error message in the response
- Verify your authentication credentials
- Ensure you have the necessary permissions
- Contact your system administrator

---

**Last Updated:** 2026-01-15  
**API Version:** 1.0

