# Reports API Documentation

Complete API documentation for **Inventory Reports** and **Sales Analytics Reports** endpoints in the TechSavanna POS system.

---

## Table of Contents

1. [Overview](#overview)
2. [Base URL & Authentication](#base-url--authentication)
3. [Sales Analytics Reports](#sales-analytics-reports)
4. [Inventory Valuation Reports](#inventory-valuation-reports)
5. [Stock Movement Analysis Reports](#stock-movement-analysis-reports)
6. [Aging Stock Reports](#aging-stock-reports)
7. [Performance Metrics Reports](#performance-metrics-reports)
8. [React.js Usage Examples](#reactjs-usage-examples)
9. [Error Handling](#error-handling)

---

## Overview

This API provides comprehensive reporting capabilities for:
- **Sales Analytics**: Daily sales, revenue by item group, sales summaries
- **Inventory Valuation**: Value by category, cost method comparison, value trends
- **Stock Movement**: Turnover rates, days on hand, movement patterns
- **Aging Stock**: Stock aging, obsolescence risk, aging recommendations
- **Performance Metrics**: Accuracy, variance, adjustment trends, transfer efficiency

All endpoints return data in a consistent format:
```json
{
  "success": true,
  "data": { ... }
}
```

---

## Base URL & Authentication

### Base URL
```
/api/method/techsavanna_pos.api.reports.{endpoint_name}
```

**Important:** If you encounter an error like "Failed to get method for command api with 'api'", try:
1. Restart your Frappe bench: `bench restart`
2. Clear Frappe cache: `bench clear-cache`
3. Ensure the `__init__.py` file exists in the `api` directory
4. Verify the endpoint name matches exactly (case-sensitive)

### Authentication

All endpoints require authentication (except where noted with `allow_guest=True`):

**Bearer Token (Recommended):**
```http
Authorization: Bearer <access_token>
```

**API Key/Secret:**
```http
Authorization: token <api_key>:<api_secret>
```

**Session Cookie:**
```
Cookie: sid=<session_id>
```

---

## Sales Analytics Reports

### 1. Sales Analytics Report

Get aggregated sales data for analytics including daily sales, revenue by item group, and summary statistics.

**Endpoint:** `sales_analytics_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | No | Start date (YYYY-MM-DD) |
| `end_date` | string | No | End date (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |
| `item_group` | string | No | Item group filter |
| `customer` | string | No | Customer filter |
| `group_by` | string | No | Group by: `date`, `item_group`, or `customer` (default: `date`) |

#### Request Example

```javascript
// GET Request
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.sales_analytics_report?company=Your Company&start_date=2024-01-01&end_date=2024-01-31`,
  {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  }
);

// POST Request
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.sales_analytics_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      item_group: 'Electronics'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": {
    "daily_sales": [
      {
        "date": "2024-01-01",
        "total_amount": 15000.00,
        "total_qty": 45.0,
        "invoice_count": 12,
        "average_order_value": 1250.00
      }
    ],
    "revenue_by_item_group": [
      {
        "item_group": "Electronics",
        "total_revenue": 50000.00,
        "total_qty": 120.0,
        "item_count": 25,
        "percentage": 33.33
      }
    ],
    "summary": {
      "total_revenue": 150000.00,
      "total_quantity": 450.0,
      "total_invoices": 150,
      "average_order_value": 1000.00
    },
    "table_data": [
      {
        "invoice_no": "SINV-00001",
        "posting_date": "2024-01-01",
        "customer": "Customer A",
        "item_group": "Electronics",
        "total_amount": 5000.00,
        "total_qty": 10.0
      }
    ]
  }
}
```

---

### 2. Export Sales Analytics Report

Export sales analytics report in various formats (CSV, Excel, PDF).

**Endpoint:** `export_sales_analytics_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

Same as `sales_analytics_report` plus:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `format` | string | No | Export format: `csv`, `excel`, or `pdf` (default: `csv`) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.export_sales_analytics_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      format: 'excel'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "message": "Export in excel format is not yet fully implemented. Use the sales_analytics_report endpoint to get data.",
  "data": { ... },
  "format": "excel"
}
```

---

## Inventory Valuation Reports

### 3. Inventory Value by Category Report

Get inventory value breakdown by item category/group.

**Endpoint:** `inventory_value_by_category_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `warehouse` | string | No | Warehouse filter |
| `item_group` | string | No | Item group filter |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_value_by_category_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      warehouse: 'Main Warehouse'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "item_group": "Electronics",
      "total_value": 50000.00,
      "total_qty": 120.0,
      "item_count": 25,
      "percentage": 33.33
    },
    {
      "item_group": "Clothing",
      "total_value": 30000.00,
      "total_qty": 200.0,
      "item_count": 50,
      "percentage": 20.00
    }
  ]
}
```

---

### 4. Inventory Cost Method Comparison Report

Compare inventory valuation using different cost methods (FIFO, LIFO, Weighted Average).

**Endpoint:** `inventory_cost_method_comparison_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `warehouse` | string | No | Warehouse filter |
| `cost_methods` | array | No | Array of cost methods: `["FIFO", "LIFO", "Weighted Average"]` |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_cost_method_comparison_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      cost_methods: ['FIFO', 'LIFO', 'Weighted Average']
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": {
    "FIFO": {
      "total_value": 150000.00,
      "item_count": 100
    },
    "LIFO": {
      "total_value": 0.0,
      "item_count": 100,
      "note": "Calculation not implemented - requires separate valuation logic"
    }
  },
  "message": "Currently showing FIFO method. Other methods require separate calculation logic."
}
```

---

### 5. Inventory Value Trends Report

Get historical inventory values over time with period grouping.

**Endpoint:** `inventory_value_trends_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | Yes | Start date (YYYY-MM-DD) |
| `end_date` | string | Yes | End date (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |
| `period` | string | No | Period grouping: `daily`, `weekly`, or `monthly` (default: `daily`) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_value_trends_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      period: 'daily'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "period": "2024-01-01",
      "total_value": 150000.00,
      "change": 0.00,
      "change_percentage": 0.00
    },
    {
      "period": "2024-01-02",
      "total_value": 152000.00,
      "change": 2000.00,
      "change_percentage": 1.33
    }
  ]
}
```

---

## Stock Movement Analysis Reports

### 6. Inventory Turnover Report

Calculate turnover rates for inventory items.

**Endpoint:** `inventory_turnover_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | Yes | Start date (YYYY-MM-DD) |
| `end_date` | string | Yes | End date (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |
| `item_group` | string | No | Item group filter |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_turnover_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      warehouse: 'Main Warehouse'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "item_code": "ITEM-001",
      "item_name": "Product 1",
      "average_stock": 100.00,
      "cost_of_sales": 5000.00,
      "turnover_rate": 50.00,
      "turnover_days": 7.30
    }
  ]
}
```

---

### 7. Inventory Days on Hand Report

Calculate days of stock remaining based on current stock and average consumption.

**Endpoint:** `inventory_days_on_hand_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `warehouse` | string | No | Warehouse filter |
| `item_group` | string | No | Item group filter |
| `period_days` | integer | No | Days to calculate average consumption (default: `30`) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_days_on_hand_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      warehouse: 'Main Warehouse',
      period_days: 30
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "item_code": "ITEM-001",
      "item_name": "Product 1",
      "warehouse": "Main Warehouse",
      "current_stock": 100.00,
      "avg_daily_sales": 10.00,
      "days_on_hand": 10.00,
      "status": "normal"
    },
    {
      "item_code": "ITEM-002",
      "item_name": "Product 2",
      "warehouse": "Main Warehouse",
      "current_stock": 50.00,
      "avg_daily_sales": 8.00,
      "days_on_hand": 6.25,
      "status": "low"
    }
  ]
}
```

**Status Values:**
- `"normal"`: Days on hand >= 14
- `"low"`: Days on hand >= 7 and < 14
- `"critical"`: Days on hand < 7

---

### 8. Inventory Movement Patterns Report

Get movement trends and patterns over time.

**Endpoint:** `inventory_movement_patterns_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | Yes | Start date (YYYY-MM-DD) |
| `end_date` | string | Yes | End date (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |
| `analysis_type` | string | No | Analysis type: `seasonal`, `trend`, or `forecast` (default: `trend`) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_movement_patterns_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      analysis_type: 'trend'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": {
    "trends": [
      {
        "item_code": "ITEM-001",
        "item_name": "Product 1",
        "trend": "increasing",
        "change_percentage": 15.5
      },
      {
        "item_code": "ITEM-002",
        "item_name": "Product 2",
        "trend": "decreasing",
        "change_percentage": -8.2
      }
    ],
    "time_series": [
      {
        "date": "2024-01-01",
        "received": 100.00,
        "issued": 80.00,
        "net_movement": 20.00
      }
    ]
  }
}
```

**Trend Values:**
- `"increasing"`: Net movement is positive
- `"decreasing"`: Net movement is negative
- `"stable"`: Net movement is neutral

---

## Aging Stock Reports

### 9. Stock Aging Report (Enhanced)

Get stock aging report with movement rate and slow-moving threshold.

**Endpoint:** `stock_aging_report`  
**Method:** `GET` or `POST`  
**Auth Required:** No (allow_guest=True)

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `slow_moving_threshold` | number | No | Threshold for slow moving items (movement rate) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.stock_aging_report?slow_moving_threshold=5`,
  {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  }
);
```

#### Response

```json
{
  "success": true,
  "data": {
    "0-30": [
      {
        "item_code": "ITEM-001",
        "warehouse": "Main Warehouse",
        "actual_qty": 100.00,
        "age_days": 15,
        "movement_rate": 10.00,
        "is_slow_moving": false
      }
    ],
    "31-60": [],
    "61-90": [],
    "90+": [
      {
        "item_code": "ITEM-002",
        "warehouse": "Main Warehouse",
        "actual_qty": 50.00,
        "age_days": 120,
        "movement_rate": 0.5,
        "is_slow_moving": true
      }
    ]
  }
}
```

---

### 10. Inventory Obsolescence Risk Report

Identify items with high obsolescence risk.

**Endpoint:** `inventory_obsolescence_risk_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `warehouse` | string | No | Warehouse filter |
| `risk_level` | string | No | Filter by risk level: `low`, `medium`, or `high` (default: all) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_obsolescence_risk_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      risk_level: 'high'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "item_code": "ITEM-001",
      "item_name": "Product 1",
      "warehouse": "Main Warehouse",
      "age_days": 120,
      "last_movement_date": "2024-01-01",
      "current_stock": 50.00,
      "risk_score": 85.5,
      "risk_level": "high",
      "risk_factors": ["high_age", "no_recent_movement"]
    }
  ]
}
```

**Risk Levels:**
- `"high"`: Risk score >= 70
- `"medium"`: Risk score >= 40 and < 70
- `"low"`: Risk score < 40

**Risk Factors:**
- `"high_age"`: Age > 90 days
- `"medium_age"`: Age > 60 days
- `"no_recent_movement"`: No movement in last 90 days
- `"high_stock"`: Current stock > 100 units

---

### 11. Inventory Aging Recommendations Report

Get recommended actions for aging stock items.

**Endpoint:** `inventory_aging_recommendations_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `warehouse` | string | No | Warehouse filter |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_aging_recommendations_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      warehouse: 'Main Warehouse'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "item_code": "ITEM-001",
      "item_name": "Product 1",
      "warehouse": "Main Warehouse",
      "age_bracket": "90+",
      "age_days": 120,
      "current_stock": 50.00,
      "recommended_action": "discount",
      "priority": "high",
      "reason": "Item has been in stock for 120 days, consider discounting to move inventory"
    }
  ]
}
```

**Recommended Actions:**
- `"dispose"`: Age > 180 days
- `"discount"`: Age > 120 days or > 60 days
- `"transfer"`: Age > 90 days
- `"monitor"`: Age <= 60 days

**Priority Levels:**
- `"high"`: Age > 90 days
- `"medium"`: Age > 60 days
- `"low"`: Age <= 60 days

---

## Performance Metrics Reports

### 12. Inventory Accuracy Report

Get stock accuracy metrics (book vs actual).

**Endpoint:** `inventory_accuracy_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | No | Start date for stock counts (YYYY-MM-DD) |
| `end_date` | string | No | End date for stock counts (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_accuracy_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": {
    "warehouse": "Main Warehouse",
    "total_items_counted": 100,
    "items_with_variance": 5,
    "accuracy_rate": 95.00,
    "variance_count": 5,
    "total_variance_value": 500.00
  }
}
```

---

### 13. Inventory Variance Report

Get detailed variance analysis from stock counts.

**Endpoint:** `inventory_variance_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | No | Start date for stock counts (YYYY-MM-DD) |
| `end_date` | string | No | End date for stock counts (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |
| `variance_threshold` | number | No | Minimum variance value to include |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_variance_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      variance_threshold: 100.00
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "item_code": "ITEM-001",
      "item_name": "Product 1",
      "book_qty": 100.00,
      "counted_qty": 95.00,
      "variance_qty": -5.00,
      "variance_value": -250.00,
      "variance_percentage": -5.00,
      "reconciliation_date": "2024-01-15"
    }
  ]
}
```

---

### 14. Inventory Adjustment Trends Report

Get trends in stock adjustments over time.

**Endpoint:** `inventory_adjustment_trends_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | Yes | Start date (YYYY-MM-DD) |
| `end_date` | string | Yes | End date (YYYY-MM-DD) |
| `warehouse` | string | No | Warehouse filter |
| `adjustment_type` | string | No | Filter by: `increase`, `decrease`, or `all` (default: `all`) |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_adjustment_trends_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31',
      adjustment_type: 'all'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": [
    {
      "period": "2024-01-01",
      "adjustment_count": 5,
      "total_adjusted_qty": 25.00,
      "total_adjusted_value": 1250.00,
      "increase_count": 3,
      "decrease_count": 2
    }
  ]
}
```

---

### 15. Inventory Transfer Efficiency Report

Get metrics on stock transfer performance.

**Endpoint:** `inventory_transfer_efficiency_report`  
**Method:** `GET` or `POST`  
**Auth Required:** Yes

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `company` | string | Yes | Company name |
| `start_date` | string | Yes | Start date (YYYY-MM-DD) |
| `end_date` | string | Yes | End date (YYYY-MM-DD) |
| `from_warehouse` | string | No | Source warehouse filter |
| `to_warehouse` | string | No | Destination warehouse filter |

#### Request Example

```javascript
const response = await fetch(
  `/api/method/techsavanna_pos.api.reports.inventory_transfer_efficiency_report`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      company: 'Your Company',
      start_date: '2024-01-01',
      end_date: '2024-01-31'
    })
  }
);
```

#### Response

```json
{
  "success": true,
  "data": {
    "total_transfers": 50,
    "completed_transfers": 48,
    "pending_transfers": 2,
    "cancelled_transfers": 0,
    "average_completion_time_hours": 24.5,
    "transfer_accuracy": 98.00,
    "on_time_rate": 95.00
  }
}
```

---

## React.js Usage Examples

### Setup

Create an API utility file:

```javascript
// utils/api.js
const API_BASE = '/api/method/techsavanna_pos.api.reports';

export const fetchReport = async (endpoint, params = {}, token) => {
  const url = `${API_BASE}.${endpoint}`;
  
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(params)
  });
  
  if (!response.ok) {
    throw new Error(`API Error: ${response.statusText}`);
  }
  
  const data = await response.json();
  
  if (!data.success) {
    throw new Error(data.message || 'Report generation failed');
  }
  
  return data.data;
};
```

### React Hook Example

```javascript
// hooks/useSalesAnalytics.js
import { useState, useEffect } from 'react';
import { fetchReport } from '../utils/api';

export const useSalesAnalytics = (company, startDate, endDate, token) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  
  useEffect(() => {
    const loadData = async () => {
      if (!company || !startDate || !endDate) return;
      
      setLoading(true);
      setError(null);
      
      try {
        const result = await fetchReport(
          'sales_analytics_report',
          {
            company,
            start_date: startDate,
            end_date: endDate
          },
          token
        );
        setData(result);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };
    
    loadData();
  }, [company, startDate, endDate, token]);
  
  return { data, loading, error };
};
```

### Component Example

```javascript
// components/SalesAnalyticsReport.jsx
import React, { useState } from 'react';
import { useSalesAnalytics } from '../hooks/useSalesAnalytics';

const SalesAnalyticsReport = ({ company, token }) => {
  const [startDate, setStartDate] = useState('2024-01-01');
  const [endDate, setEndDate] = useState('2024-01-31');
  
  const { data, loading, error } = useSalesAnalytics(
    company,
    startDate,
    endDate,
    token
  );
  
  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;
  if (!data) return null;
  
  return (
    <div>
      <h2>Sales Analytics Report</h2>
      
      <div>
        <label>Start Date:</label>
        <input
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
        />
      </div>
      
      <div>
        <label>End Date:</label>
        <input
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
        />
      </div>
      
      <div>
        <h3>Summary</h3>
        <p>Total Revenue: ${data.summary.total_revenue.toLocaleString()}</p>
        <p>Total Invoices: {data.summary.total_invoices}</p>
        <p>Average Order Value: ${data.summary.average_order_value.toLocaleString()}</p>
      </div>
      
      <div>
        <h3>Daily Sales</h3>
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Total Amount</th>
              <th>Invoice Count</th>
            </tr>
          </thead>
          <tbody>
            {data.daily_sales.map((day) => (
              <tr key={day.date}>
                <td>{day.date}</td>
                <td>${day.total_amount.toLocaleString()}</td>
                <td>{day.invoice_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      <div>
        <h3>Revenue by Item Group</h3>
        <table>
          <thead>
            <tr>
              <th>Item Group</th>
              <th>Total Revenue</th>
              <th>Percentage</th>
            </tr>
          </thead>
          <tbody>
            {data.revenue_by_item_group.map((group) => (
              <tr key={group.item_group}>
                <td>{group.item_group}</td>
                <td>${group.total_revenue.toLocaleString()}</td>
                <td>{group.percentage}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default SalesAnalyticsReport;
```

### Multiple Reports Hook

```javascript
// hooks/useInventoryReports.js
import { useState, useCallback } from 'react';
import { fetchReport } from '../utils/api';

export const useInventoryReports = (token) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  
  const getValueByCategory = useCallback(async (company, warehouse) => {
    setLoading(true);
    setError(null);
    try {
      return await fetchReport(
        'inventory_value_by_category_report',
        { company, warehouse },
        token
      );
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [token]);
  
  const getTurnoverReport = useCallback(async (company, startDate, endDate, warehouse) => {
    setLoading(true);
    setError(null);
    try {
      return await fetchReport(
        'inventory_turnover_report',
        { company, start_date: startDate, end_date: endDate, warehouse },
        token
      );
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [token]);
  
  const getDaysOnHand = useCallback(async (company, warehouse, periodDays = 30) => {
    setLoading(true);
    setError(null);
    try {
      return await fetchReport(
        'inventory_days_on_hand_report',
        { company, warehouse, period_days: periodDays },
        token
      );
    } catch (err) {
      setError(err.message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, [token]);
  
  return {
    loading,
    error,
    getValueByCategory,
    getTurnoverReport,
    getDaysOnHand
  };
};
```

---

## Error Handling

All endpoints return errors in a consistent format:

```json
{
  "success": false,
  "message": "Error description here"
}
```

### Common Error Scenarios

1. **Missing Required Parameter:**
```json
{
  "success": false,
  "message": "Company is required"
}
```

2. **Authentication Error:**
```http
HTTP 401 Unauthorized
```

3. **Server Error:**
```json
{
  "success": false,
  "message": "Error generating report: [error details]"
}
```

### Error Handling in React

```javascript
const handleReportError = (error) => {
  if (error.message.includes('Company is required')) {
    // Show company selection dialog
    return;
  }
  
  if (error.message.includes('Authentication')) {
    // Redirect to login
    return;
  }
  
  // Show generic error message
  alert(`Error: ${error.message}`);
};

// Usage
try {
  const data = await fetchReport('sales_analytics_report', params, token);
  // Handle success
} catch (error) {
  handleReportError(error);
}
```

---

## Additional Notes

### Date Formats
- All dates should be in `YYYY-MM-DD` format
- Example: `2024-01-15`

### Number Formats
- All monetary values are in decimal format (e.g., `15000.00`)
- Quantities are in decimal format (e.g., `45.0`)

### Pagination
- Most endpoints return all matching records
- For large datasets, consider implementing client-side pagination or requesting date-range filtering

### Performance Tips
1. Use date range filters to limit data volume
2. Filter by warehouse or item_group when possible
3. Cache report data on the client side when appropriate
4. Use React Query or SWR for automatic caching and refetching

---

## Quick Reference

### Endpoint URLs

| Endpoint | URL Pattern |
|----------|-------------|
| Sales Analytics | `/api/method/techsavanna_pos.api.reports.sales_analytics_report` |
| Export Sales Analytics | `/api/method/techsavanna_pos.api.reports.export_sales_analytics_report` |
| Value by Category | `/api/method/techsavanna_pos.api.reports.inventory_value_by_category_report` |
| Cost Method Comparison | `/api/method/techsavanna_pos.api.reports.inventory_cost_method_comparison_report` |
| Value Trends | `/api/method/techsavanna_pos.api.reports.inventory_value_trends_report` |
| Turnover Report | `/api/method/techsavanna_pos.api.reports.inventory_turnover_report` |
| Days on Hand | `/api/method/techsavanna_pos.api.reports.inventory_days_on_hand_report` |
| Movement Patterns | `/api/method/techsavanna_pos.api.reports.inventory_movement_patterns_report` |
| Stock Aging | `/api/method/techsavanna_pos.api.reports.stock_aging_report` |
| Obsolescence Risk | `/api/method/techsavanna_pos.api.reports.inventory_obsolescence_risk_report` |
| Aging Recommendations | `/api/method/techsavanna_pos.api.reports.inventory_aging_recommendations_report` |
| Accuracy Report | `/api/method/techsavanna_pos.api.reports.inventory_accuracy_report` |
| Variance Report | `/api/method/techsavanna_pos.api.reports.inventory_variance_report` |
| Adjustment Trends | `/api/method/techsavanna_pos.api.reports.inventory_adjustment_trends_report` |
| Transfer Efficiency | `/api/method/techsavanna_pos.api.reports.inventory_transfer_efficiency_report` |

---

**Last Updated:** 2024-01-15  
**Version:** 1.0.0

