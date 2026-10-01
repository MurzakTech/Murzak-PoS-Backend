# Loyalty Points Redemption API Documentation

This document provides comprehensive API documentation for Loyalty Points Redemption endpoints, designed for consumption in React.js applications.

## Table of Contents

- [Overview](#overview)
- [Endpoints](#endpoints)
  - [1. Get Customer Loyalty Details](#1-get-customer-loyalty-details)
  - [2. Calculate Loyalty Redemption](#2-calculate-loyalty-redemption)
- [TypeScript Interfaces](#typescript-interfaces)
- [Usage Examples](#usage-examples)
- [Error Handling](#error-handling)
- [Workflow](#workflow)

---

## Overview

The Loyalty Points Redemption API provides endpoints to retrieve customer loyalty points information and calculate redemption amounts. These endpoints integrate with ERPNext's built-in loyalty program system to enable customers to redeem their accumulated loyalty points as discounts on purchases.

**Base URL:** `/api/method/techsavanna_pos.api.loyalty`

**Key Concepts:**
- **Loyalty Points**: Points earned by customers through purchases
- **Conversion Factor**: The currency value per loyalty point (e.g., 1 point = 0.01 currency units)
- **Redemption**: Using loyalty points to reduce the invoice total amount
- **Important**: Loyalty points redemption is NOT a payment method - it reduces the invoice total. Customers must still pay the remaining amount using regular payment methods.

---

## Endpoints

### 1. Get Customer Loyalty Details

Retrieve customer's loyalty points balance, conversion factor, and redemption details. Use this endpoint to display loyalty information to users before they make a purchase.

**Endpoint:** `GET /api/method/techsavanna_pos.api.loyalty.get_customer_loyalty_details`

**Authentication:** Guest accessible (allow_guest=True)

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `customer_id` | string | Yes | Customer ID (e.g., "CUST-00001") |
| `company` | string | No | Company name (defaults to logged-in user's company) |
| `invoice_amount` | number | No | Invoice total amount (for calculating max redeemable based on invoice) |

#### Request Example

```typescript
// Using fetch API
const getCustomerLoyaltyDetails = async (customerId: string, invoiceAmount?: number) => {
  const params = new URLSearchParams({
    customer_id: customerId,
  });
  
  if (invoiceAmount) {
    params.append('invoice_amount', invoiceAmount.toString());
  }

  const response = await fetch(
    `/api/method/techsavanna_pos.api.loyalty.get_customer_loyalty_details?${params.toString()}`,
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

const getCustomerLoyaltyDetails = async (customerId: string, invoiceAmount?: number) => {
  const response = await axios.get(
    '/api/method/techsavanna_pos.api.loyalty.get_customer_loyalty_details',
    {
      params: {
        customer_id: customerId,
        invoice_amount: invoiceAmount,
      },
    }
  );

  return response.data;
};
```

#### Success Response Example

**Customer with Loyalty Program:**
```json
{
  "status": "success",
  "has_loyalty_program": true,
  "customer": "CUST-00001",
  "loyalty_program": "LP-00001",
  "loyalty_program_name": "Standard Loyalty Program",
  "loyalty_points": 5000,
  "conversion_factor": 0.01,
  "max_redeemable_amount": 50.00,
  "max_redeemable_points": 5000,
  "expense_account": "Loyalty Redemption - Company",
  "cost_center": "Main - Company",
  "tier_name": "Gold",
  "total_spent": 100000.00
}
```

**Customer without Loyalty Program:**
```json
{
  "status": "success",
  "has_loyalty_program": false,
  "message": "Customer is not enrolled in any loyalty program",
  "loyalty_points": 0,
  "conversion_factor": 0,
  "max_redeemable_amount": 0
}
```

**With Invoice Amount (Max Redeemable Limited by Invoice):**
```json
{
  "status": "success",
  "has_loyalty_program": true,
  "customer": "CUST-00001",
  "loyalty_program": "LP-00001",
  "loyalty_program_name": "Standard Loyalty Program",
  "loyalty_points": 5000,
  "conversion_factor": 0.01,
  "max_redeemable_amount": 30.00,
  "max_redeemable_points": 3000,
  "expense_account": "Loyalty Redemption - Company",
  "cost_center": "Main - Company",
  "tier_name": "Gold",
  "total_spent": 100000.00
}
```

#### Error Response Examples

**Customer Not Found:**
```json
{
  "status": "error",
  "message": "Customer CUST-99999 not found"
}
```

**Company Required:**
```json
{
  "status": "error",
  "message": "Company is required. Please set a default company or provide company parameter."
}
```

**Loyalty Program Company Mismatch:**
```json
{
  "status": "error",
  "message": "Customer's loyalty program does not belong to company Company A"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Response status: "success" or "error" |
| `has_loyalty_program` | boolean | Whether customer is enrolled in a loyalty program |
| `customer` | string | Customer ID |
| `loyalty_program` | string | Loyalty Program ID (if enrolled) |
| `loyalty_program_name` | string | Loyalty Program display name |
| `loyalty_points` | number | Available loyalty points balance |
| `conversion_factor` | number | Currency value per loyalty point (e.g., 0.01 = 1 point = 0.01 currency) |
| `max_redeemable_amount` | number | Maximum discount amount that can be redeemed |
| `max_redeemable_points` | number | Maximum points that can be redeemed (limited by invoice if provided) |
| `expense_account` | string | Account for loyalty redemption expense |
| `cost_center` | string | Cost center for loyalty redemption |
| `tier_name` | string | Current loyalty tier name (e.g., "Bronze", "Silver", "Gold") |
| `total_spent` | number | Total amount customer has spent (for tier calculation) |

---

### 2. Calculate Loyalty Redemption

Calculate the discount amount for redeeming loyalty points and validate if the redemption is possible. Use this endpoint when the user selects how many points to redeem.

**Endpoint:** `GET /api/method/techsavanna_pos.api.loyalty.calculate_loyalty_redemption`

**Authentication:** Guest accessible (allow_guest=True)

#### Request Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `customer_id` | string | Yes | Customer ID (e.g., "CUST-00001") |
| `points_to_redeem` | number | Yes | Number of loyalty points to redeem (positive integer) |
| `invoice_amount` | number | No | Invoice total amount (for validation against redemption amount) |
| `company` | string | No | Company name (defaults to logged-in user's company) |

#### Request Example

```typescript
// Using fetch API
const calculateRedemption = async (
  customerId: string,
  pointsToRedeem: number,
  invoiceAmount?: number
) => {
  const params = new URLSearchParams({
    customer_id: customerId,
    points_to_redeem: pointsToRedeem.toString(),
  });

  if (invoiceAmount) {
    params.append('invoice_amount', invoiceAmount.toString());
  }

  const response = await fetch(
    `/api/method/techsavanna_pos.api.loyalty.calculate_loyalty_redemption?${params.toString()}`,
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

const calculateRedemption = async (
  customerId: string,
  pointsToRedeem: number,
  invoiceAmount?: number
) => {
  const response = await axios.get(
    '/api/method/techsavanna_pos.api.loyalty.calculate_loyalty_redemption',
    {
      params: {
        customer_id: customerId,
        points_to_redeem: pointsToRedeem,
        invoice_amount: invoiceAmount,
      },
    }
  );

  return response.data;
};
```

#### Success Response Example

```json
{
  "status": "success",
  "points_to_redeem": 3000,
  "discount_amount": 30.00,
  "conversion_factor": 0.01,
  "remaining_points": 2000,
  "expense_account": "Loyalty Redemption - Company",
  "cost_center": "Main - Company",
  "loyalty_program": "LP-00001"
}
```

#### Error Response Examples

**Insufficient Points:**
```json
{
  "status": "error",
  "message": "Insufficient loyalty points. Available: 2000, Requested: 3000",
  "available_points": 2000,
  "requested_points": 3000
}
```

**Redemption Exceeds Invoice Amount:**
```json
{
  "status": "error",
  "message": "Redemption amount (50.00) exceeds invoice amount (30.00). Maximum redeemable: 3000 points",
  "discount_amount": 50.00,
  "invoice_amount": 30.00,
  "max_redeemable_points": 3000
}
```

**Invalid Points Value:**
```json
{
  "status": "error",
  "message": "Invalid points_to_redeem. Must be a positive integer."
}
```

**Customer Not Enrolled:**
```json
{
  "status": "error",
  "message": "Customer is not enrolled in any loyalty program"
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Response status: "success" or "error" |
| `points_to_redeem` | number | Number of points being redeemed |
| `discount_amount` | number | Discount amount in currency (points × conversion_factor) |
| `conversion_factor` | number | Currency value per loyalty point |
| `remaining_points` | number | Loyalty points balance after redemption |
| `expense_account` | string | Account for loyalty redemption expense |
| `cost_center` | string | Cost center for loyalty redemption |
| `loyalty_program` | string | Loyalty Program ID |

---

## TypeScript Interfaces

### Customer Loyalty Details Response

```typescript
interface CustomerLoyaltyDetailsResponse {
  status: "success" | "error";
  has_loyalty_program: boolean;
  customer?: string;
  loyalty_program?: string;
  loyalty_program_name?: string;
  loyalty_points: number;
  conversion_factor: number;
  max_redeemable_amount: number;
  max_redeemable_points: number;
  expense_account?: string;
  cost_center?: string;
  tier_name?: string;
  total_spent?: number;
  message?: string;
}
```

### Calculate Redemption Response

```typescript
interface CalculateRedemptionResponse {
  status: "success" | "error";
  points_to_redeem?: number;
  discount_amount?: number;
  conversion_factor?: number;
  remaining_points?: number;
  expense_account?: string;
  cost_center?: string;
  loyalty_program?: string;
  message?: string;
  available_points?: number;
  requested_points?: number;
  invoice_amount?: number;
  max_redeemable_points?: number;
}
```

### Redemption Error Response

```typescript
interface RedemptionErrorResponse {
  status: "error";
  message: string;
  available_points?: number;
  requested_points?: number;
  discount_amount?: number;
  invoice_amount?: number;
  max_redeemable_points?: number;
}
```

---

## Usage Examples

### React Hook for Customer Loyalty Details

```typescript
import { useState, useEffect } from 'react';
import axios from 'axios';

interface UseCustomerLoyaltyProps {
  customerId: string | null;
  invoiceAmount?: number;
  autoFetch?: boolean;
}

export const useCustomerLoyalty = ({
  customerId,
  invoiceAmount,
  autoFetch = true
}: UseCustomerLoyaltyProps) => {
  const [loyaltyDetails, setLoyaltyDetails] = useState<CustomerLoyaltyDetailsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchLoyaltyDetails = async () => {
    if (!customerId) {
      setLoyaltyDetails(null);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await axios.get<{ message: CustomerLoyaltyDetailsResponse }>(
        '/api/method/techsavanna_pos.api.loyalty.get_customer_loyalty_details',
        {
          params: {
            customer_id: customerId,
            invoice_amount: invoiceAmount,
          },
        }
      );

      setLoyaltyDetails(response.data.message);
    } catch (err: any) {
      setError(err.response?.data?.message?.message || err.message || 'Failed to fetch loyalty details');
      setLoyaltyDetails(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (autoFetch) {
      fetchLoyaltyDetails();
    }
  }, [customerId, invoiceAmount, autoFetch]);

  return {
    loyaltyDetails,
    loading,
    error,
    refetch: fetchLoyaltyDetails,
  };
};
```

### React Hook for Redemption Calculation

```typescript
import { useState } from 'react';
import axios from 'axios';

export const useLoyaltyRedemption = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const calculateRedemption = async (
    customerId: string,
    pointsToRedeem: number,
    invoiceAmount?: number
  ): Promise<CalculateRedemptionResponse | null> => {
    setLoading(true);
    setError(null);

    try {
      const response = await axios.get<{ message: CalculateRedemptionResponse }>(
        '/api/method/techsavanna_pos.api.loyalty.calculate_loyalty_redemption',
        {
          params: {
            customer_id: customerId,
            points_to_redeem: pointsToRedeem,
            invoice_amount: invoiceAmount,
          },
        }
      );

      if (response.data.message.status === 'error') {
        setError(response.data.message.message || 'Redemption calculation failed');
        return null;
      }

      return response.data.message;
    } catch (err: any) {
      const errorMessage = err.response?.data?.message?.message || err.message || 'Failed to calculate redemption';
      setError(errorMessage);
      return null;
    } finally {
      setLoading(false);
    }
  };

  return {
    calculateRedemption,
    loading,
    error,
  };
};
```

### React Component: Loyalty Points Redemption UI

```typescript
import React, { useState, useEffect } from 'react';
import { useCustomerLoyalty, useLoyaltyRedemption } from './hooks';

interface LoyaltyRedemptionProps {
  customerId: string;
  invoiceTotal: number;
  onRedemptionChange: (points: number, discountAmount: number) => void;
}

const LoyaltyRedemption: React.FC<LoyaltyRedemptionProps> = ({
  customerId,
  invoiceTotal,
  onRedemptionChange,
}) => {
  const { loyaltyDetails, loading: loadingDetails } = useCustomerLoyalty({
    customerId,
    invoiceAmount: invoiceTotal,
  });

  const { calculateRedemption, loading: calculating } = useLoyaltyRedemption();

  const [pointsToRedeem, setPointsToRedeem] = useState<number>(0);
  const [discountAmount, setDiscountAmount] = useState<number>(0);
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (pointsToRedeem > 0) {
      handlePointsChange(pointsToRedeem);
    } else {
      setDiscountAmount(0);
      onRedemptionChange(0, 0);
    }
  }, [pointsToRedeem, customerId, invoiceTotal]);

  const handlePointsChange = async (points: number) => {
    if (points <= 0) {
      setDiscountAmount(0);
      setValidationError(null);
      onRedemptionChange(0, 0);
      return;
    }

    const result = await calculateRedemption(customerId, points, invoiceTotal);

    if (result && result.status === 'success') {
      setDiscountAmount(result.discount_amount || 0);
      setValidationError(null);
      onRedemptionChange(points, result.discount_amount || 0);
    } else {
      setValidationError(result?.message || 'Invalid redemption');
      setDiscountAmount(0);
      onRedemptionChange(0, 0);
    }
  };

  const handleMaxRedeem = () => {
    if (loyaltyDetails?.max_redeemable_points) {
      setPointsToRedeem(loyaltyDetails.max_redeemable_points);
    }
  };

  if (loadingDetails) {
    return <div>Loading loyalty details...</div>;
  }

  if (!loyaltyDetails?.has_loyalty_program) {
    return (
      <div className="loyalty-section">
        <p>Customer is not enrolled in any loyalty program.</p>
      </div>
    );
  }

  return (
    <div className="loyalty-redemption-section">
      <h3>Redeem Loyalty Points</h3>
      
      <div className="loyalty-info">
        <div className="info-row">
          <span>Available Points:</span>
          <strong>{loyaltyDetails.loyalty_points.toLocaleString()}</strong>
        </div>
        <div className="info-row">
          <span>Conversion Rate:</span>
          <strong>1 point = {loyaltyDetails.conversion_factor.toFixed(4)}</strong>
        </div>
        <div className="info-row">
          <span>Max Redeemable:</span>
          <strong>
            {loyaltyDetails.max_redeemable_points.toLocaleString()} points 
            ({loyaltyDetails.max_redeemable_amount.toFixed(2)})
          </strong>
        </div>
        {loyaltyDetails.tier_name && (
          <div className="info-row">
            <span>Current Tier:</span>
            <strong>{loyaltyDetails.tier_name}</strong>
          </div>
        )}
      </div>

      <div className="redemption-controls">
        <label>
          Points to Redeem:
          <input
            type="number"
            min="0"
            max={loyaltyDetails.max_redeemable_points}
            value={pointsToRedeem}
            onChange={(e) => setPointsToRedeem(parseInt(e.target.value) || 0)}
            disabled={calculating}
          />
          <button 
            type="button" 
            onClick={handleMaxRedeem}
            disabled={calculating || !loyaltyDetails.max_redeemable_points}
          >
            Use Max
          </button>
        </label>

        {calculating && <div>Calculating...</div>}

        {validationError && (
          <div className="error-message" style={{ color: 'red', marginTop: '0.5rem' }}>
            {validationError}
          </div>
        )}

        {discountAmount > 0 && !validationError && (
          <div className="discount-preview" style={{ marginTop: '1rem', padding: '1rem', background: '#f0f0f0' }}>
            <div>
              <strong>Discount Amount:</strong> {discountAmount.toFixed(2)}
            </div>
            <div>
              <strong>Remaining Points:</strong> {loyaltyDetails.loyalty_points - pointsToRedeem}
            </div>
            <div>
              <strong>Amount to Pay:</strong> {(invoiceTotal - discountAmount).toFixed(2)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default LoyaltyRedemption;
```

### Complete POS Invoice Creation with Loyalty Redemption

```typescript
import React, { useState } from 'react';
import axios from 'axios';

interface CreateInvoiceWithLoyaltyProps {
  customerId: string;
  items: Array<{ item_code: string; qty: number; rate: number }>;
  payments: Array<{ mode_of_payment: string; amount: number; account: string }>;
  loyaltyPoints?: number;
}

const CreateInvoiceWithLoyalty: React.FC<CreateInvoiceWithLoyaltyProps> = ({
  customerId,
  items,
  payments,
  loyaltyPoints,
}) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invoice, setInvoice] = useState<any>(null);

  const handleCreateInvoice = async () => {
    setLoading(true);
    setError(null);

    try {
      const payload: any = {
        customer: customerId,
        items: items,
        payments: payments,
        update_stock: true,
      };

      // Add loyalty points redemption if provided
      if (loyaltyPoints && loyaltyPoints > 0) {
        payload.redeem_loyalty_points = true;
        payload.loyalty_points = loyaltyPoints;
      }

      const response = await axios.post(
        '/api/method/techsavanna_pos.api.sales.create_pos_invoice',
        payload
      );

      if (response.data.message?.success) {
        setInvoice(response.data.message.data);
      } else {
        setError(response.data.message?.message || 'Failed to create invoice');
      }
    } catch (err: any) {
      setError(err.response?.data?.message?.message || err.message || 'Failed to create invoice');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button onClick={handleCreateInvoice} disabled={loading}>
        {loading ? 'Creating Invoice...' : 'Create Invoice'}
      </button>

      {error && <div style={{ color: 'red' }}>{error}</div>}

      {invoice && (
        <div>
          <h3>Invoice Created Successfully</h3>
          <p>Invoice Number: {invoice.name}</p>
          <p>Grand Total: {invoice.grand_total}</p>
          {invoice.loyalty_amount && (
            <p>Loyalty Discount: {invoice.loyalty_amount}</p>
          )}
        </div>
      )}
    </div>
  );
};

export default CreateInvoiceWithLoyalty;
```

---

## Error Handling

### Common Error Codes

| Error Scenario | Status | Message Pattern |
|---------------|--------|-----------------|
| Customer not found | error | "Customer {id} not found" |
| Company required | error | "Company is required..." |
| No loyalty program | success | "Customer is not enrolled..." |
| Insufficient points | error | "Insufficient loyalty points..." |
| Redemption exceeds invoice | error | "Redemption amount exceeds invoice amount..." |
| Invalid points value | error | "Invalid points_to_redeem..." |
| Company mismatch | error | "Customer's loyalty program does not belong..." |

### Error Handling Best Practices

```typescript
const handleLoyaltyRedemption = async (
  customerId: string,
  points: number,
  invoiceAmount: number
) => {
  try {
    const result = await calculateRedemption(customerId, points, invoiceAmount);

    if (result?.status === 'error') {
      // Handle specific error cases
      if (result.message.includes('Insufficient')) {
        // Show available points to user
        alert(`You only have ${result.available_points} points available.`);
      } else if (result.message.includes('exceeds invoice')) {
        // Suggest maximum redeemable
        alert(`Maximum redeemable: ${result.max_redeemable_points} points`);
      } else {
        // Generic error
        alert(result.message);
      }
      return null;
    }

    return result;
  } catch (error) {
    console.error('Redemption error:', error);
    alert('Failed to calculate redemption. Please try again.');
    return null;
  }
};
```

---

## Workflow

### Complete Loyalty Points Redemption Flow

```
1. Customer Selects Items
   ↓
2. Calculate Invoice Total
   ↓
3. Get Customer Loyalty Details
   GET /api/method/techsavanna_pos.api.loyalty.get_customer_loyalty_details
   ↓
4. Display Available Points & Max Redeemable
   ↓
5. User Selects Points to Redeem (Optional)
   ↓
6. Calculate Redemption
   GET /api/method/techsavanna_pos.api.loyalty.calculate_loyalty_redemption
   ↓
7. Validate Redemption
   - Sufficient points?
   - Within invoice limit?
   ↓
8. Display Discount Preview
   - Discount amount
   - Remaining amount to pay
   - Remaining points
   ↓
9. User Confirms & Proceeds to Payment
   ↓
10. Create Invoice with Loyalty Redemption
    POST /api/method/techsavanna_pos.api.sales.create_pos_invoice
    {
      redeem_loyalty_points: true,
      loyalty_points: <selected_points>,
      payments: [...]
    }
    ↓
11. Invoice Created with Loyalty Discount Applied
    - loyalty_amount reduces grand_total
    - Customer pays remaining amount
    - Loyalty points deducted from balance
```

### Integration with POS Invoice Creation

When creating a POS Invoice with loyalty redemption:

1. **Set Redemption Flags:**
   ```typescript
   {
     redeem_loyalty_points: true,
     loyalty_points: 3000  // Points to redeem
   }
   ```

2. **ERPNext Automatically:**
   - Validates sufficient points
   - Calculates `loyalty_amount` (discount)
   - Reduces `grand_total` by `loyalty_amount`
   - Creates loyalty point entries on invoice submission
   - Updates customer's loyalty points balance

3. **Customer Still Pays:**
   - The remaining amount after loyalty discount
   - Using regular payment methods (Cash, Card, etc.)

### Important Notes

1. **Loyalty Points are NOT a Payment Method**
   - They reduce the invoice total (discount)
   - Customer must pay remaining amount with cash/card/etc.

2. **Redemption Happens at Invoice Creation**
   - Points are deducted when invoice is submitted
   - Cannot redeem points after invoice is created

3. **Validation is Automatic**
   - ERPNext validates sufficient points
   - ERPNext ensures redemption doesn't exceed invoice total
   - All validations happen server-side

4. **Accounting is Automatic**
   - GL entries created for loyalty redemption expense
   - Uses loyalty program's expense account and cost center

5. **Points Expiry**
   - Only non-expired points are available for redemption
   - Expiry dates are checked automatically

---

## Example: Complete Integration

```typescript
import React, { useState, useEffect } from 'react';
import { useCustomerLoyalty, useLoyaltyRedemption } from './hooks';

const POSCheckout: React.FC<{ customerId: string; items: any[] }> = ({
  customerId,
  items,
}) => {
  const [invoiceTotal, setInvoiceTotal] = useState(0);
  const [selectedPoints, setSelectedPoints] = useState(0);
  const [discountAmount, setDiscountAmount] = useState(0);
  const [payments, setPayments] = useState<any[]>([]);

  // Calculate invoice total
  useEffect(() => {
    const total = items.reduce((sum, item) => sum + item.amount, 0);
    setInvoiceTotal(total);
  }, [items]);

  // Get loyalty details
  const { loyaltyDetails } = useCustomerLoyalty({
    customerId,
    invoiceAmount: invoiceTotal,
  });

  // Calculate redemption
  const { calculateRedemption } = useLoyaltyRedemption();

  const handlePointsChange = async (points: number) => {
    setSelectedPoints(points);
    
    if (points > 0) {
      const result = await calculateRedemption(customerId, points, invoiceTotal);
      if (result?.status === 'success') {
        setDiscountAmount(result.discount_amount || 0);
      }
    } else {
      setDiscountAmount(0);
    }
  };

  const handleCreateInvoice = async () => {
    const payload = {
      customer: customerId,
      items: items,
      payments: payments,
      update_stock: true,
    };

    if (selectedPoints > 0) {
      payload.redeem_loyalty_points = true;
      payload.loyalty_points = selectedPoints;
    }

    // Create invoice...
  };

  const amountToPay = invoiceTotal - discountAmount;

  return (
    <div>
      <h2>Checkout</h2>
      
      <div>
        <h3>Invoice Summary</h3>
        <p>Subtotal: {invoiceTotal.toFixed(2)}</p>
        {discountAmount > 0 && (
          <p>Loyalty Discount: -{discountAmount.toFixed(2)}</p>
        )}
        <p><strong>Total to Pay: {amountToPay.toFixed(2)}</strong></p>
      </div>

      {loyaltyDetails?.has_loyalty_program && (
        <div>
          <h3>Redeem Loyalty Points</h3>
          <p>Available: {loyaltyDetails.loyalty_points} points</p>
          <input
            type="number"
            min="0"
            max={loyaltyDetails.max_redeemable_points}
            value={selectedPoints}
            onChange={(e) => handlePointsChange(parseInt(e.target.value) || 0)}
          />
          <button onClick={() => handlePointsChange(loyaltyDetails.max_redeemable_points)}>
            Use All
          </button>
        </div>
      )}

      <div>
        <h3>Payment</h3>
        {/* Payment method selection */}
      </div>

      <button onClick={handleCreateInvoice}>
        Complete Purchase
      </button>
    </div>
  );
};

export default POSCheckout;
```

---

## Summary

The Loyalty Points Redemption API provides a complete solution for integrating loyalty points redemption into your POS system:

1. **Get Customer Loyalty Details** - Retrieve points balance and redemption information
2. **Calculate Redemption** - Validate and calculate discount amounts
3. **Create Invoice with Redemption** - Use existing `create_pos_invoice` endpoint with loyalty parameters

All endpoints integrate seamlessly with ERPNext's built-in loyalty program system, ensuring proper accounting, validation, and point management.

