# POS Sale Flow - React App Implementation Guide

This document provides a complete guide for implementing POS (Point of Sale) sales functionality in a React application using the TechSavanna POS API.

## Table of Contents
1. [Overview](#overview)
2. [Complete Flow Diagram](#complete-flow-diagram)
3. [API Endpoints](#api-endpoints)
4. [Request/Response Examples](#requestresponse-examples)
5. [Error Handling](#error-handling)
6. [React Implementation Guide](#react-implementation-guide)

---

## Overview

The POS sale flow involves several steps:
1. **Initialization**: Get company, warehouse, and user defaults
2. **Product Selection**: Search/browse and select products
3. **Customer Selection**: Select or create a customer
4. **Cart Management**: Add items, apply discounts, manage quantities
5. **Payment Processing**: Process payment(s)
6. **Invoice Creation**: Create and submit the POS invoice
7. **Receipt/Confirmation**: Display invoice details

---

## Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     POS Sale Flow                                │
└─────────────────────────────────────────────────────────────────┘

1. App Initialization
   ├─ Get Default Company (from user defaults)
   ├─ Get Warehouse (from POS Profile or company)
   └─ Initialize POS Session

2. Product Selection
   ├─ List/Search Products → get_products()
   ├─ Get Product Details → get_product_details()
   └─ Get Product Price → get_product_price() [Optional]

3. Customer Selection
   ├─ List Customers → list_customers()
   └─ Get Customer Details → get_customer() [Optional]

4. Cart Management (Client-side)
   ├─ Add Items to Cart
   ├─ Update Quantities
   ├─ Apply Item Discounts
   ├─ Apply Document Discounts
   └─ Calculate Totals

5. Payment Processing
   ├─ Prepare Payment Data
   └─ Validate Payment Totals

6. Create POS Invoice
   └─ create_pos_invoice()

7. Display Receipt
   └─ Show Invoice Details
```

---

## API Endpoints

### 1. Get Products List

**Endpoint:** `techsavanna_pos.api.product_api.get_products`

**Method:** `POST`

**Description:** Retrieves a paginated list of products with filtering options.

**Request Body:**
```json
{
  "company": "Hassis",
  "warehouse": "Hassis - HA",
  "item_group": "All Item Groups",
  "brand": null,
  "is_stock_item": true,
  "is_sales_item": true,
  "disabled": false,
  "search_term": "",
  "page": 1,
  "page_size": 20,
  "price_list": "Standard Selling"
}
```

**Response:**
```json
{
  "message": {
    "products": [
      {
        "name": "HA-FASH-021",
        "item_code": "HA-FASH-021",
        "item_name": "Men's Cap",
        "item_group": "All Item Groups",
        "stock_uom": "Nos",
        "standard_rate": 0.0,
        "is_stock_item": 1,
        "is_sales_item": 1,
        "disabled": 0,
        "brand": null,
        "image": null,
        "warranty_period": 30,
        "price": 500.0,
        "price_currency": "KES",
        "price_list": "Standard Selling",
        "price_source": "price_list",
        "stock_qty": 50.0,
        "buying_price": null,
        "selling_price": null,
        "warranty_period_unit": "Days"
      }
    ],
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 2,
      "total_pages": 1
    },
    "price_list": "Standard Selling",
    "warehouse": "Hassis - HA"
  }
}
```

---

### 2. Get Product Details

**Endpoint:** `techsavanna_pos.api.product_api.get_product_details`

**Method:** `GET` or `POST`

**Description:** Get detailed information about a specific product.

**Parameters:**
- `item_code` (required): Product item code
- `company` (optional): Company name

**Example Request:**
```
GET /api/method/techsavanna_pos.api.product_api.get_product_details?item_code=HA-FASH-021&company=Hassis
```

**Response:**
```json
{
  "item_code": "HA-FASH-021",
  "item_name": "Men's Cap",
  "item_group": "All Item Groups",
  "stock_uom": "Nos",
  "standard_rate": 0.0,
  "description": "High-quality men's cap",
  "is_stock_item": 1,
  "is_sales_item": 1,
  "disabled": 0,
  "brand": null,
  "image": null,
  "weight_per_unit": 0.1,
  "has_variants": 0
}
```

---

### 3. Get Product Price

**Endpoint:** `techsavanna_pos.api.product_api.get_product_price`

**Method:** `GET` or `POST`

**Description:** Get the price of a product from a specific price list.

**Parameters:**
- `item_code` (required): Product item code
- `price_list` (optional): Price list name (uses customer's default if not provided)
- `company` (optional): Company name

**Example Request:**
```
GET /api/method/techsavanna_pos.api.product_api.get_product_price?item_code=HA-FASH-021&price_list=Standard Selling&company=Hassis
```

**Response:**
```json
{
  "item_code": "HA-FASH-021",
  "price_list": "Standard Selling",
  "price": 500.0,
  "currency": "KES",
  "uom": "Nos"
}
```

---

### 4. List Customers

**Endpoint:** `techsavanna_pos.api.customer_api.list_customers`

**Method:** `GET` or `POST`

**Description:** Get a paginated list of customers with optional filters.

**Request Body (POST):**
```json
{
  "company": "Hassis",
  "customer_group": null,
  "territory": null,
  "customer_type": null,
  "disabled": false,
  "search_term": "",
  "limit": 20,
  "offset": 0,
  "filter_by_company_transactions": false
}
```

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "name": "CUST-00001",
      "customer_name": "John Doe",
      "customer_type": "Individual",
      "customer_group": "Individual",
      "territory": "All Territories",
      "tax_id": null,
      "mobile_no": "+254712345678",
      "email_id": "john@example.com",
      "disabled": 0,
      "default_currency": "KES",
      "default_price_list": "Standard Selling",
      "credit_limit": 100000.0,
      "outstanding_amount": 0.0,
      "available_credit": 100000.0,
      "credit_utilization_percent": 0.0,
      "is_over_limit": false
    }
  ],
  "count": 1
}
```

**Note:** 
- The `"name"` field is the **Customer ID** (unique identifier in ERPNext/Frappe)
- The `"data"` field contains the list of customers directly (not wrapped in an object)
- The `"count"` field shows the total number of customers returned
- Credit limit fields (`credit_limit`, `outstanding_amount`, etc.) are only included if `company` parameter is provided

---

### 5. Get Customer Details

**Endpoint:** `techsavanna_pos.api.customer_api.get_customer`

**Method:** `GET` or `POST`

**Description:** Get detailed information about a specific customer.

**Parameters:**
- `name` (required): Customer ID/name

**Example Request:**
```
GET /api/method/techsavanna_pos.api.customer_api.get_customer?name=CUST-00001
```

**Response:**
```json
{
  "success": true,
  "data": {
    "name": "CUST-00001",
    "customer_name": "John Doe",
    "customer_type": "Individual",
    "customer_group": "Individual",
    "territory": "All Territories",
    "tax_id": null,
    "mobile_no": "+254712345678",
    "email_id": "john@example.com",
    "disabled": 0,
    "default_currency": "KES",
    "default_price_list": "Standard Selling"
  }
}
```

---

### 6. Create POS Invoice

**Endpoint:** `techsavanna_pos.api.sales_api.create_pos_invoice`

**Method:** `POST`

**Description:** Create a POS invoice (walk-in sale) with items and payments.

**Request Body:**
```json
{
  "customer": "CUST-00001",
  "items": [
    {
      "item_code": "HA-FASH-021",
      "qty": 2,
      "rate": 500.0,
      "uom": "Nos",
      "warehouse": "Hassis - HA",
      "discount_percentage": 0,
      "discount_amount": 0,
      "batch_no": null,
      "serial_no": null
    },
    {
      "item_code": "HA-FASH-015",
      "qty": 1,
      "rate": 3500.0,
      "uom": "Nos",
      "warehouse": "Hassis - HA",
      "discount_percentage": 10,
      "discount_amount": 0,
      "batch_no": null,
      "serial_no": null
    }
  ],
  "posting_date": "2025-01-20",
  "company": "Hassis",
  "pos_profile": null,
  "warehouse": "Hassis - HA",
  "update_stock": true,
  "payments": [
    {
      "mode_of_payment": "Cash",
      "amount": 4500.0,
      "base_amount": 4500.0,
      "account": null
    }
  ],
  "apply_discount_on": "Net Total",
  "additional_discount_percentage": 0,
  "discount_amount": 0,
  "do_not_submit": false
}
```

**Response (Success):**
```json
{
  "success": true,
  "message": "POS Invoice created successfully",
  "data": {
    "name": "PI-00001",
    "customer": "CUST-00001",
    "company": "Hassis",
    "posting_date": "2025-01-20",
    "grand_total": 4500.0,
    "rounded_total": 4500.0,
    "outstanding_amount": 0.0,
    "docstatus": 1,
    "is_pos": true
  }
}
```

**Response (Error):**
```json
{
  "success": false,
  "message": "Validation error: Item HA-FASH-021 does not exist",
  "error_type": "validation_error"
}
```

---

## Request/Response Examples

### Item Object Structure

Each item in the `items` array should have the following structure:

```typescript
interface PosInvoiceItem {
  item_code: string;              // Required: Product item code
  qty: number;                     // Required: Quantity (must be > 0)
  rate?: number;                   // Optional: Unit price (if null, uses price list price)
  uom?: string;                    // Optional: Unit of measure (defaults to stock_uom)
  warehouse?: string;              // Optional: Warehouse (defaults to document warehouse)
  discount_percentage?: number;    // Optional: Item-level discount percentage (0-100)
  discount_amount?: number;        // Optional: Item-level flat discount amount
  batch_no?: string | null;        // Optional: Batch number (for batch-tracked items)
  serial_no?: string | null;       // Optional: Serial number (for serial-tracked items)
}
```

**Important Notes:**
- If `rate` is not provided or is `null`, the system will automatically fetch the price from the customer's price list
- If `warehouse` is not provided, it uses the document-level warehouse
- Either `discount_percentage` OR `discount_amount` can be used, not both
- If neither discount is provided, the system may apply automatic inventory discount rules

---

### Payment Object Structure

Each payment in the `payments` array should have the following structure:

```typescript
interface PosInvoicePayment {
  mode_of_payment: string;        // Required: Mode of payment (e.g., "Cash", "Card", "MPESA")
  amount: number;                  // Required: Payment amount
  base_amount?: number;            // Optional: Base currency amount (defaults to amount)
  account?: string | null;         // Optional: Payment account (auto-resolved if not provided)
  use_receivable_account?: boolean; // Optional: Use receivable account (for POS Invoice only)
}
```

**Important Notes:**
- `mode_of_payment` must exist in the system (e.g., "Cash", "Card", "MPESA")
- `amount` should match the `grand_total` or sum of all payment amounts
- If `account` is not provided, the system will resolve it from the Mode of Payment configuration
- For POS Invoice type, `use_receivable_account` can be set to `true` to use the receivable account

---

## Error Handling

### Common Error Responses

**1. Validation Error**
```json
{
  "success": false,
  "message": "Validation error: Customer is required",
  "error_type": "validation_error"
}
```

**2. Item Not Found**
```json
{
  "success": false,
  "message": "Validation error: Item HA-FASH-999 does not exist",
  "error_type": "validation_error"
}
```

**3. Insufficient Stock**
```json
{
  "success": false,
  "message": "Validation error: Insufficient stock for item HA-FASH-021",
  "error_type": "validation_error"
}
```

**4. Company Not Found**
```json
{
  "success": false,
  "message": "Company is required. Please set a default company or provide company parameter.",
  "error_type": "validation_error"
}
```

**5. Generic Error**
```json
{
  "success": false,
  "message": "Error creating POS Invoice: [error details]"
}
```

### Error Handling Best Practices

1. **Always check `success` field** before processing response data
2. **Check `error_type`** to determine how to handle the error
3. **Display user-friendly messages** from the `message` field
4. **Log detailed errors** for debugging
5. **Handle network errors** separately (timeout, connection issues)

---

## React Implementation Guide

### 1. API Client Setup

```typescript
// api/client.ts
const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || '/api/method';

interface ApiResponse<T> {
  success: boolean;
  message?: string;
  data?: T;
  error_type?: string;
}

async function apiCall<T>(
  endpoint: string,
  method: 'GET' | 'POST' = 'POST',
  body?: any
): Promise<ApiResponse<T>> {
  const url = `${API_BASE_URL}/${endpoint}`;
  
  const options: RequestInit = {
    method,
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include', // Important for session cookies
  };

  if (body && method === 'POST') {
    options.body = JSON.stringify(body);
  }

  try {
    const response = await fetch(url, options);
    const data = await response.json();
    
    if (!response.ok) {
      throw new Error(data.message || 'API request failed');
    }
    
    return data;
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
}
```

### 2. Product Service

```typescript
// services/productService.ts
import { apiCall } from '../api/client';

export interface Product {
  name: string;
  item_code: string;
  item_name: string;
  item_group: string;
  stock_uom: string;
  price: number;
  price_currency: string;
  stock_qty: number;
  image: string | null;
  // ... other fields
}

export interface ProductListRequest {
  company: string;
  warehouse: string;
  item_group?: string;
  brand?: string | null;
  is_stock_item?: boolean;
  is_sales_item?: boolean;
  disabled?: boolean;
  search_term?: string;
  page?: number;
  page_size?: number;
  price_list?: string;
}

export interface ProductListResponse {
  products: Product[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
  price_list: string;
  warehouse: string;
}

export const productService = {
  async getProducts(params: ProductListRequest) {
    const response = await apiCall<{ message: ProductListResponse }>(
      'techsavanna_pos.api.product_api.get_products',
      'POST',
      params
    );
    return response.message || response.data;
  },

  async getProductDetails(itemCode: string, company?: string) {
    const params = company ? `?item_code=${itemCode}&company=${company}` : `?item_code=${itemCode}`;
    const response = await apiCall(
      `techsavanna_pos.api.product_api.get_product_details${params}`,
      'GET'
    );
    return response.data;
  },

  async getProductPrice(itemCode: string, priceList?: string, company?: string) {
    let params = `?item_code=${itemCode}`;
    if (priceList) params += `&price_list=${priceList}`;
    if (company) params += `&company=${company}`;
    
    const response = await apiCall(
      `techsavanna_pos.api.product_api.get_product_price${params}`,
      'GET'
    );
    return response.data;
  },
};
```

### 3. Customer Service

```typescript
// services/customerService.ts
import { apiCall } from '../api/client';

export interface Customer {
  name: string;              // Customer ID (unique identifier)
  customer_name: string;     // Display name
  customer_type: string;     // "Individual", "Company", or "Partnership"
  customer_group: string;
  mobile_no?: string;
  email_id?: string;
  default_price_list?: string;
  credit_limit?: number;     // Only included if company parameter is provided
  outstanding_amount?: number; // Only included if company parameter is provided
  available_credit?: number;  // Only included if company parameter is provided
}

export interface CustomerListRequest {
  company?: string;
  customer_group?: string;
  territory?: string;
  customer_type?: string;
  disabled?: boolean;
  search_term?: string;
  limit?: number;
  offset?: number;
}

export const customerService = {
  async listCustomers(params: CustomerListRequest = {}) {
    const response = await apiCall<{ data: Customer[]; count: number }>(
      'techsavanna_pos.api.customer_api.list_customers',
      'POST',
      params
    );
    // Note: response.data is already the array of customers, not wrapped in an object
    return response.data || [];
  },

  async getCustomer(name: string) {
    const response = await apiCall<{ data: Customer }>(
      `techsavanna_pos.api.customer_api.get_customer?name=${name}`,
      'GET'
    );
    return response.data;
  },
};
```

### 4. Sales Service

```typescript
// services/salesService.ts
import { apiCall } from '../api/client';

export interface PosInvoiceItem {
  item_code: string;
  qty: number;
  rate?: number | null;
  uom?: string;
  warehouse?: string;
  discount_percentage?: number;
  discount_amount?: number;
  batch_no?: string | null;
  serial_no?: string | null;
}

export interface PosInvoicePayment {
  mode_of_payment: string;
  amount: number;
  base_amount?: number;
  account?: string | null;
  use_receivable_account?: boolean;
}

export interface CreatePosInvoiceRequest {
  customer: string;
  items: PosInvoiceItem[];
  posting_date?: string;
  company?: string;
  pos_profile?: string | null;
  warehouse?: string;
  update_stock?: boolean;
  payments: PosInvoicePayment[];
  apply_discount_on?: string;
  additional_discount_percentage?: number;
  discount_amount?: number;
  do_not_submit?: boolean;
}

export interface PosInvoiceResponse {
  name: string;
  customer: string;
  company: string;
  posting_date: string;
  grand_total: number;
  rounded_total: number;
  outstanding_amount: number;
  docstatus: number;
  is_pos?: boolean;
}

export const salesService = {
  async createPosInvoice(request: CreatePosInvoiceRequest) {
    const response = await apiCall<{ data: PosInvoiceResponse }>(
      'techsavanna_pos.api.sales_api.create_pos_invoice',
      'POST',
      request
    );
    
    if (!response.success) {
      throw new Error(response.message || 'Failed to create POS invoice');
    }
    
    return response.data;
  },
};
```

### 5. React Component Example

```typescript
// components/PosSale.tsx
import React, { useState, useEffect } from 'react';
import { productService, Product } from '../services/productService';
import { customerService, Customer } from '../services/customerService';
import { salesService, PosInvoiceItem, PosInvoicePayment } from '../services/salesService';

interface CartItem extends PosInvoiceItem {
  item_name: string;
  stock_qty: number;
}

const PosSale: React.FC = () => {
  const [company, setCompany] = useState<string>('Hassis');
  const [warehouse, setWarehouse] = useState<string>('Hassis - HA');
  const [products, setProducts] = useState<Product[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [cart, setCart] = useState<CartItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load products on mount
  useEffect(() => {
    loadProducts();
    loadCustomers();
  }, []);

  const loadProducts = async () => {
    try {
      setLoading(true);
      const response = await productService.getProducts({
        company,
        warehouse,
        disabled: false,
        page: 1,
        page_size: 50,
      });
      setProducts(response.products || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load products');
    } finally {
      setLoading(false);
    }
  };

  const loadCustomers = async () => {
    try {
      const customerList = await customerService.listCustomers({
        company,
        disabled: false,
        limit: 50,
      });
      setCustomers(customerList);
    } catch (err: any) {
      console.error('Failed to load customers:', err);
    }
  };

  const addToCart = (product: Product) => {
    const existingItem = cart.find(item => item.item_code === product.item_code);
    
    if (existingItem) {
      // Update quantity
      setCart(cart.map(item =>
        item.item_code === product.item_code
          ? { ...item, qty: item.qty + 1 }
          : item
      ));
    } else {
      // Add new item
      const cartItem: CartItem = {
        item_code: product.item_code,
        item_name: product.item_name,
        qty: 1,
        rate: product.price || null,
        uom: product.stock_uom,
        warehouse,
        stock_qty: product.stock_qty || 0,
        discount_percentage: 0,
        discount_amount: 0,
        batch_no: null,
        serial_no: null,
      };
      setCart([...cart, cartItem]);
    }
  };

  const updateCartItem = (itemCode: string, updates: Partial<CartItem>) => {
    setCart(cart.map(item =>
      item.item_code === itemCode
        ? { ...item, ...updates }
        : item
    ));
  };

  const removeFromCart = (itemCode: string) => {
    setCart(cart.filter(item => item.item_code !== itemCode));
  };

  const calculateSubtotal = () => {
    return cart.reduce((total, item) => {
      const rate = item.rate || 0;
      const discount = item.discount_percentage
        ? (rate * item.qty * item.discount_percentage / 100)
        : (item.discount_amount || 0);
      return total + (rate * item.qty) - discount;
    }, 0);
  };

  const handleCheckout = async () => {
    if (!selectedCustomer) {
      setError('Please select a customer');
      return;
    }

    if (cart.length === 0) {
      setError('Cart is empty');
      return;
    }

    try {
      setLoading(true);
      setError(null);

      const subtotal = calculateSubtotal();
      const grandTotal = subtotal; // Add tax if needed

      const invoiceItems: PosInvoiceItem[] = cart.map(item => ({
        item_code: item.item_code,
        qty: item.qty,
        rate: item.rate || null,
        uom: item.uom,
        warehouse: item.warehouse,
        discount_percentage: item.discount_percentage || 0,
        discount_amount: item.discount_amount || 0,
        batch_no: item.batch_no,
        serial_no: item.serial_no,
      }));

      const payments: PosInvoicePayment[] = [
        {
          mode_of_payment: 'Cash',
          amount: grandTotal,
          base_amount: grandTotal,
        },
      ];

      const invoice = await salesService.createPosInvoice({
        customer: selectedCustomer.name,
        items: invoiceItems,
        company,
        warehouse,
        update_stock: true,
        payments,
        apply_discount_on: 'Net Total',
        do_not_submit: false,
      });

      // Success! Show receipt/invoice
      alert(`Invoice ${invoice.name} created successfully! Total: ${invoice.grand_total}`);
      
      // Clear cart
      setCart([]);
    } catch (err: any) {
      setError(err.message || 'Failed to create invoice');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="pos-sale">
      <h1>POS Sale</h1>

      {error && (
        <div className="error" style={{ color: 'red', padding: '10px' }}>
          {error}
        </div>
      )}

      <div className="pos-layout" style={{ display: 'flex', gap: '20px' }}>
        {/* Products Section */}
        <div className="products-section" style={{ flex: 2 }}>
          <h2>Products</h2>
          {loading ? (
            <div>Loading...</div>
          ) : (
            <div className="product-grid">
              {products.map(product => (
                <div
                  key={product.item_code}
                  className="product-card"
                  onClick={() => addToCart(product)}
                  style={{
                    border: '1px solid #ccc',
                    padding: '10px',
                    margin: '5px',
                    cursor: 'pointer',
                  }}
                >
                  <h3>{product.item_name}</h3>
                  <p>Code: {product.item_code}</p>
                  <p>Price: {product.price_currency} {product.price}</p>
                  <p>Stock: {product.stock_qty}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Cart Section */}
        <div className="cart-section" style={{ flex: 1 }}>
          <h2>Cart</h2>
          
          <div className="customer-selector">
            <label>Customer:</label>
            <select
              value={selectedCustomer?.name || ''}
              onChange={(e) => {
                const customer = customers.find(c => c.name === e.target.value);
                setSelectedCustomer(customer || null);
              }}
            >
              <option value="">Select Customer</option>
              {customers.map(customer => (
                <option key={customer.name} value={customer.name}>
                  {customer.customer_name}
                </option>
              ))}
            </select>
          </div>

          <div className="cart-items">
            {cart.map(item => (
              <div key={item.item_code} className="cart-item">
                <h4>{item.item_name}</h4>
                <div>
                  <label>Qty:</label>
                  <input
                    type="number"
                    value={item.qty}
                    min={1}
                    onChange={(e) =>
                      updateCartItem(item.item_code, { qty: parseInt(e.target.value) || 1 })
                    }
                  />
                </div>
                <div>
                  <label>Rate:</label>
                  <input
                    type="number"
                    value={item.rate || ''}
                    onChange={(e) =>
                      updateCartItem(item.item_code, { rate: parseFloat(e.target.value) || null })
                    }
                  />
                </div>
                <div>
                  <label>Discount %:</label>
                  <input
                    type="number"
                    value={item.discount_percentage || 0}
                    min={0}
                    max={100}
                    onChange={(e) =>
                      updateCartItem(item.item_code, {
                        discount_percentage: parseFloat(e.target.value) || 0,
                        discount_amount: 0,
                      })
                    }
                  />
                </div>
                <button onClick={() => removeFromCart(item.item_code)}>Remove</button>
              </div>
            ))}
          </div>

          <div className="cart-total">
            <h3>Subtotal: {calculateSubtotal().toFixed(2)}</h3>
            <h3>Total: {calculateSubtotal().toFixed(2)}</h3>
          </div>

          <button
            onClick={handleCheckout}
            disabled={loading || cart.length === 0 || !selectedCustomer}
            style={{
              padding: '15px',
              fontSize: '16px',
              width: '100%',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Processing...' : 'Checkout'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default PosSale;
```

### 6. Complete Flow Implementation

```typescript
// hooks/usePosSale.ts
import { useState, useCallback } from 'react';
import { salesService, PosInvoiceItem, PosInvoicePayment } from '../services/salesService';
import { Customer } from '../services/customerService';

interface CartItem extends PosInvoiceItem {
  item_name: string;
  stock_qty: number;
}

export const usePosSale = () => {
  const [cart, setCart] = useState<CartItem[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [company, setCompany] = useState<string>('Hassis');
  const [warehouse, setWarehouse] = useState<string>('Hassis - HA');

  const addToCart = useCallback((product: any) => {
    // Implementation
  }, []);

  const updateCartItem = useCallback((itemCode: string, updates: Partial<CartItem>) => {
    // Implementation
  }, []);

  const removeFromCart = useCallback((itemCode: string) => {
    // Implementation
  }, []);

  const calculateTotals = useCallback(() => {
    const subtotal = cart.reduce((total, item) => {
      const rate = item.rate || 0;
      const discount = item.discount_percentage
        ? (rate * item.qty * item.discount_percentage / 100)
        : (item.discount_amount || 0);
      return total + (rate * item.qty) - discount;
    }, 0);

    return {
      subtotal,
      tax: 0, // Calculate tax if needed
      total: subtotal,
    };
  }, [cart]);

  const processSale = useCallback(async (
    payments: PosInvoicePayment[],
    applyDiscountOn?: string,
    additionalDiscountPercentage?: number,
    discountAmount?: number
  ) => {
    if (!selectedCustomer) {
      throw new Error('Customer is required');
    }

    if (cart.length === 0) {
      throw new Error('Cart is empty');
    }

    const invoiceItems: PosInvoiceItem[] = cart.map(item => ({
      item_code: item.item_code,
      qty: item.qty,
      rate: item.rate || null,
      uom: item.uom,
      warehouse: item.warehouse || warehouse,
      discount_percentage: item.discount_percentage || 0,
      discount_amount: item.discount_amount || 0,
      batch_no: item.batch_no,
      serial_no: item.serial_no,
    }));

    const invoice = await salesService.createPosInvoice({
      customer: selectedCustomer.name,
      items: invoiceItems,
      company,
      warehouse,
      update_stock: true,
      payments,
      apply_discount_on: applyDiscountOn || 'Net Total',
      additional_discount_percentage,
      discount_amount: discountAmount,
      do_not_submit: false,
    });

    // Clear cart after successful sale
    setCart([]);

    return invoice;
  }, [cart, selectedCustomer, company, warehouse]);

  return {
    cart,
    selectedCustomer,
    setSelectedCustomer,
    company,
    setCompany,
    warehouse,
    setWarehouse,
    addToCart,
    updateCartItem,
    removeFromCart,
    calculateTotals,
    processSale,
  };
};
```

---

## Best Practices

1. **Always validate input** before making API calls
2. **Handle loading states** for better UX
3. **Show user-friendly error messages** from API responses
4. **Calculate totals on the client side** before submission
5. **Validate stock availability** before adding to cart
6. **Handle batch/serial numbers** for tracked items
7. **Support multiple payment methods** (Cash, Card, MPESA, etc.)
8. **Clear cart after successful sale**
9. **Store draft invoices** if `do_not_submit: true`
10. **Implement receipt printing** after successful sale

---

## Testing

```typescript
// Example test for creating POS invoice
describe('POS Sale Flow', () => {
  it('should create a POS invoice successfully', async () => {
    const mockItems: PosInvoiceItem[] = [
      {
        item_code: 'HA-FASH-021',
        qty: 2,
        rate: 500.0,
        uom: 'Nos',
        warehouse: 'Hassis - HA',
      },
    ];

    const mockPayments: PosInvoicePayment[] = [
      {
        mode_of_payment: 'Cash',
        amount: 1000.0,
      },
    ];

    const invoice = await salesService.createPosInvoice({
      customer: 'CUST-00001',
      items: mockItems,
      company: 'Hassis',
      warehouse: 'Hassis - HA',
      update_stock: true,
      payments: mockPayments,
    });

    expect(invoice).toBeDefined();
    expect(invoice.name).toMatch(/^PI-/);
    expect(invoice.docstatus).toBe(1); // Submitted
  });
});
```

---

## Troubleshooting

### Common Issues

1. **"Company is required" error**
   - Ensure user has a default company set
   - Pass `company` parameter explicitly

2. **"Customer is required" error**
   - Always select a customer before checkout
   - Ensure customer exists in the system

3. **"Item does not exist" error**
   - Verify item_code is correct
   - Check if item is disabled

4. **"Insufficient stock" error**
   - Check available stock quantity
   - Reduce quantity or select different item

5. **"No receivable account" error**
   - Configure receivable account for customer
   - Set company's default receivable account

---

## Summary

This guide provides a complete implementation guide for POS sales in React. The key steps are:

1. **Load products and customers** on initialization
2. **Build cart** by adding products with quantities
3. **Select customer** (required for checkout)
4. **Configure payments** (amounts must match total)
5. **Create invoice** via `create_pos_invoice` endpoint
6. **Handle response** (success or error)
7. **Clear cart** and show receipt on success

For additional API documentation, refer to:
- Product API: `techsavanna_pos.api.product_api`
- Customer API: `techsavanna_pos.api.customer_api`
- Sales API: `techsavanna_pos.api.sales_api`

