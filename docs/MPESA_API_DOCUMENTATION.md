# MPESA (Daraja) API Documentation

## Overview
Complete API documentation for MPESA payment integration. All endpoints support multitenant operations with company-level data isolation.

**Base URL:** `https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api`

**Authentication:** All endpoints (except callbacks) require authentication via:
- Session cookie (web interface)
- API Key/Secret token (API requests)

---

## Table of Contents
1. [Settings Management APIs](#settings-management-apis)
2. [Payment Processing APIs](#payment-processing-apis)
3. [Transaction Management APIs](#transaction-management-apis)
4. [Callback Endpoints](#callback-endpoints)
5. [Error Handling](#error-handling)
6. [Code Examples](#code-examples)

---

## Settings Management APIs

### Register/Update MPESA Settings

Register or update MPESA settings for a company.

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings`

**Authentication:** Required

**Request Body:**
```json
{
  "company": "Company Name",
  "environment": "Sandbox",
  "shortcode": "174379",
  "shortcode_type": "Paybill",
  "consumer_key": "your_consumer_key",
  "consumer_secret": "your_consumer_secret",
  "passkey": "your_passkey",
  "payment_account": "Cash - Company",
  "stk_callback_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.stk_callback",
  "b2c_result_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2c_result_callback",
  "b2c_timeout_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2c_timeout_callback",
  "b2b_result_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2b_result_callback",
  "b2b_timeout_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2b_timeout_callback",
  "initiator_name": "Initiator Name",
  "initiator_password": "Initiator Password",
  "account_reference_prefix": "INV",
  "transaction_description": "Payment for Invoice",
  "auto_confirm_payments": true,
  "payment_timeout": 300,
  "test_phone_number": "254712345678"
}
```

**Required Fields:**
- `company` - Company name
- `environment` - "Sandbox" or "Production"
- `shortcode` - Business shortcode
- `shortcode_type` - "Paybill" or "BuyGoods"
- `consumer_key` - Daraja consumer key
- `consumer_secret` - Daraja consumer secret
- `passkey` - STK Push passkey
- `payment_account` - Account to receive payments

**Optional Fields:**
- `initiator_name` - Required for B2C/B2B
- `initiator_password` - Required for B2C/B2B
- `stk_callback_url` - Required for STK Push
- `b2c_result_url`, `b2c_timeout_url` - Required for B2C
- `b2b_result_url`, `b2b_timeout_url` - Required for B2B
- `account_reference_prefix` - Prefix for references
- `transaction_description` - Default description
- `auto_confirm_payments` - Auto-create payment entries (default: true)
- `payment_timeout` - Timeout in seconds (default: 300)
- `test_phone_number` - For sandbox testing

**Response:**
```json
{
  "success": true,
  "message": "MPESA settings registered successfully",
  "settings": {
    "name": "MPESA-SET-001",
    "company": "Company Name",
    "is_active": true,
    "environment": "Sandbox"
  }
}
```

**Error Response:**
```json
{
  "success": false,
  "error_code": "VALIDATION_ERROR",
  "message": "Shortcode must be numeric"
}
```

---

### Get MPESA Settings

Retrieve MPESA settings for a company (sensitive fields masked).

**Endpoint:** `GET /api/method/techsavanna_pos.api.mpesa_api.get_mpesa_settings?company=Company Name`

**Authentication:** Required

**Query Parameters:**
- `company` (optional) - Company name (defaults to user's default company)

**Response:**
```json
{
  "success": true,
  "settings": {
    "name": "MPESA-SET-001",
    "company": "Company Name",
    "is_active": true,
    "environment": "Sandbox",
    "shortcode": "174379",
    "shortcode_type": "Paybill",
    "payment_account": "Cash - Company",
    "mode_of_payment": "MPESA",
    "consumer_key": "***",
    "consumer_secret": "***",
    "passkey": "***"
  }
}
```

---

### Update MPESA Settings

Update existing MPESA settings (alias for register_mpesa_settings).

**Endpoint:** `PUT /api/method/techsavanna_pos.api.mpesa_api.update_mpesa_settings`

**Authentication:** Required

**Request Body:** Same as register_mpesa_settings

**Response:** Same as register_mpesa_settings

---

### Deactivate MPESA Settings

Deactivate MPESA for a company.

**Endpoint:** `DELETE /api/method/techsavanna_pos.api.mpesa_api.deactivate_mpesa_settings?company=Company Name`

**Authentication:** Required

**Query Parameters:**
- `company` - Company name

**Response:**
```json
{
  "success": true,
  "message": "MPESA settings deactivated successfully",
  "settings": {
    "name": "MPESA-SET-001",
    "company": "Company Name",
    "is_active": false
  }
}
```

---

## Payment Processing APIs

### Initiate STK Push Payment

Initiate STK Push payment request (Customer to Business).

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment`

**Authentication:** Required

**Request Body:**
```json
{
  "company": "Company Name",
  "phone_number": "254712345678",
  "amount": 100.00,
  "reference": "INV-001",
  "description": "Payment for Invoice INV-001",
  "invoice_type": "Sales Invoice",
  "invoice_name": "INV-001"
}
```

**Required Fields:**
- `phone_number` - MSISDN format (254712345678)
- `amount` - Transaction amount (must be > 0)
- `reference` - Internal reference

**Optional Fields:**
- `company` - Company name (defaults to user's default)
- `description` - Transaction description
- `invoice_type` - "Sales Invoice" or "POS Invoice"
- `invoice_name` - Invoice name to link

**Response:**
```json
{
  "success": true,
  "message": "STK push initiated successfully",
  "transaction": {
    "transaction_id": "MPESA-TXN-001",
    "merchant_request_id": "ws_CO_1912202310203631234567890",
    "checkout_request_id": "ws_CO_1912202310203631234567890",
    "status": "Pending",
    "phone_number": "254712345678",
    "amount": 100.00,
    "reference": "INV-001"
  }
}
```

---

### Initiate B2C Payment

Initiate B2C payment (Business to Customer payout).

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_b2c_payment`

**Authentication:** Required

**Request Body:**
```json
{
  "company": "Company Name",
  "phone_number": "254712345678",
  "amount": 100.00,
  "reference": "PAY-001",
  "remarks": "Salary payment"
}
```

**Required Fields:**
- `phone_number` - Customer phone number
- `amount` - Transaction amount
- `reference` - Internal reference

**Response:**
```json
{
  "success": true,
  "message": "B2C payment initiated successfully",
  "transaction": {
    "transaction_id": "MPESA-TXN-002",
    "conversation_id": "AG_20231219_abc123",
    "status": "Pending",
    "phone_number": "254712345678",
    "amount": 100.00,
    "reference": "PAY-001"
  }
}
```

---

### Initiate B2B Payment

Initiate B2B payment (Business to Business transfer).

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_b2b_payment`

**Authentication:** Required

**Request Body:**
```json
{
  "company": "Company Name",
  "receiver_shortcode": "600000",
  "amount": 100.00,
  "reference": "B2B-001",
  "remarks": "Payment to supplier"
}
```

**Required Fields:**
- `receiver_shortcode` - Receiver business shortcode
- `amount` - Transaction amount
- `reference` - Internal reference

**Response:**
```json
{
  "success": true,
  "message": "B2B payment initiated successfully",
  "transaction": {
    "transaction_id": "MPESA-TXN-003",
    "conversation_id": "AG_20231219_xyz789",
    "status": "Pending",
    "receiver_shortcode": "600000",
    "amount": 100.00,
    "reference": "B2B-001"
  }
}
```

---

### Check Payment Status

Check status of STK Push payment.

**Endpoint:** `GET /api/method/techsavanna_pos.api.mpesa_api.check_payment_status`

**Authentication:** Required

**Query Parameters:**
- `transaction_id` (optional) - MPESA Transaction Log ID
- `checkout_request_id` (optional) - STK Push CheckoutRequestID

**Note:** Either `transaction_id` or `checkout_request_id` is required.

**Response:**
```json
{
  "success": true,
  "transaction": {
    "transaction_id": "MPESA-TXN-001",
    "checkout_request_id": "ws_CO_1912202310203631234567890",
    "status": "Success",
    "result_code": 0,
    "result_description": "The service request is processed successfully.",
    "mpesa_receipt_number": "RCT123456",
    "amount": 100.00,
    "phone_number": "254712345678",
    "reference": "INV-001"
  }
}
```

---

## Transaction Management APIs

### Get Transactions

Get transaction history with filters and pagination.

**Endpoint:** `GET /api/method/techsavanna_pos.api.mpesa_api.get_transactions`

**Authentication:** Required

**Query Parameters:**
- `company` (optional) - Company name (defaults to user's default)
- `status` (optional) - Filter by status (Pending, Success, Failed, Cancelled)
- `transaction_type` (optional) - Filter by type (STK Push, B2C, B2B)
- `limit` (optional) - Number of records (default: 50)
- `offset` (optional) - Offset for pagination (default: 0)

**Response:**
```json
{
  "success": true,
  "transactions": [
    {
      "name": "MPESA-TXN-001",
      "transaction_type": "STK Push",
      "status": "Success",
      "phone_number": "254712345678",
      "amount": 100.00,
      "reference_number": "INV-001",
      "mpesa_receipt_number": "RCT123456",
      "result_code": 0,
      "created_at": "2024-01-01 10:00:00",
      "completed_at": "2024-01-01 10:02:00",
      "payment_entry": "ACC-PAY-001",
      "invoice_type": "Sales Invoice",
      "invoice": "INV-001"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

---

### Get Transaction

Get complete details of a single transaction.

**Endpoint:** `GET /api/method/techsavanna_pos.api.mpesa_api.get_transaction?transaction_id=MPESA-TXN-001`

**Authentication:** Required

**Query Parameters:**
- `transaction_id` - MPESA Transaction Log ID

**Response:**
```json
{
  "success": true,
  "transaction": {
    "name": "MPESA-TXN-001",
    "company": "Company Name",
    "transaction_type": "STK Push",
    "status": "Success",
    "phone_number": "254712345678",
    "amount": 100.00,
    "reference_number": "INV-001",
    "merchant_request_id": "ws_CO_1912202310203631234567890",
    "checkout_request_id": "ws_CO_1912202310203631234567890",
    "mpesa_receipt_number": "RCT123456",
    "result_code": 0,
    "result_description": "Success",
    "request_payload": {
      "BusinessShortCode": "174379",
      "Amount": "100",
      "PhoneNumber": "254712345678"
    },
    "response_payload": {
      "MerchantRequestID": "ws_CO_1912202310203631234567890",
      "ResponseCode": "0"
    },
    "callback_payload": {
      "Body": {
        "stkCallback": {
          "ResultCode": 0,
          "MpesaReceiptNumber": "RCT123456"
        }
      }
    },
    "payment_entry": "ACC-PAY-001",
    "invoice_type": "Sales Invoice",
    "invoice": "INV-001",
    "created_at": "2024-01-01 10:00:00",
    "completed_at": "2024-01-01 10:02:00"
  }
}
```

---

## Callback Endpoints

These endpoints are called by Daraja (Safaricom) and should not be called directly by clients.

### STK Push Callback

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.stk_callback`

**Authentication:** Not required (allow_guest=True)

**Request Body:** (Sent by Daraja)
```json
{
  "Body": {
    "stkCallback": {
      "CheckoutRequestID": "ws_CO_1912202310203631234567890",
      "ResultCode": 0,
      "ResultDesc": "The service request is processed successfully.",
      "CallbackMetadata": {
        "Item": [
          {"Name": "MpesaReceiptNumber", "Value": "RCT123456"},
          {"Name": "Amount", "Value": 100},
          {"Name": "PhoneNumber", "Value": "254712345678"}
        ]
      }
    }
  }
}
```

**Response:**
```json
{
  "ResultCode": 0,
  "ResultDesc": "Success"
}
```

---

### B2C Result Callback

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.b2c_result_callback`

**Authentication:** Not required

**Request Body:** (Sent by Daraja)
```json
{
  "Result": {
    "ConversationID": "AG_20231219_abc123",
    "ResultCode": 0,
    "ResultDesc": "The service request is processed successfully.",
    "ResultParameters": {
      "ResultParameter": {
        "TransactionReceipt": "RCT123456",
        "TransactionAmount": 100,
        "B2CWorkingAccountAvailableFunds": 1000,
        "B2CUtilityAccountAvailableFunds": 500,
        "TransactionCompletedDateTime": "2024-01-01 10:02:00",
        "ReceiverPartyPublicName": "254712345678",
        "B2CChargesPaidAccountAvailableFunds": 0,
        "B2CRecipientIsRegisteredCustomer": "Y"
      }
    }
  }
}
```

---

### B2C Timeout Callback

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.b2c_timeout_callback`

**Authentication:** Not required

**Request Body:** (Sent by Daraja when timeout occurs)

---

### B2B Result Callback

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.b2b_result_callback`

**Authentication:** Not required

**Request Body:** (Sent by Daraja)

---

### B2B Timeout Callback

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.b2b_timeout_callback`

**Authentication:** Not required

**Request Body:** (Sent by Daraja when timeout occurs)

---

## Error Handling

### Error Response Format

All errors follow this format:

```json
{
  "success": false,
  "error_code": "ERROR_CODE",
  "error_type": "ErrorType",
  "message": "Human-readable error message",
  "daraja_result_code": null,
  "daraja_result_description": null,
  "details": {
    "additional": "context"
  }
}
```

### Common Error Codes

| Error Code | Description |
|------------|-------------|
| `MPESA_AUTHENTICATION_ERROR` | Invalid credentials |
| `MPESA_API_ERROR` | Daraja API error |
| `MPESA_CLIENT_ERROR` | Client-side error |
| `VALIDATION_ERROR` | Input validation failed |
| `UNEXPECTED_ERROR` | Unexpected system error |

### HTTP Status Codes

- `200` - Success
- `400` - Bad Request (validation error)
- `401` - Unauthorized (authentication required)
- `403` - Forbidden (permission denied)
- `404` - Not Found
- `500` - Internal Server Error

---

## Code Examples

### Python Example

```python
import requests

# Register MPESA Settings
url = "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings"
headers = {
    "Authorization": "token YOUR_API_KEY:YOUR_API_SECRET",
    "Content-Type": "application/json"
}
data = {
    "company": "Your Company",
    "environment": "Sandbox",
    "shortcode": "174379",
    "shortcode_type": "Paybill",
    "consumer_key": "your_key",
    "consumer_secret": "your_secret",
    "passkey": "your_passkey",
    "payment_account": "Cash - Your Company",
    "stk_callback_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.stk_callback"
}
response = requests.post(url, json=data, headers=headers)
print(response.json())

# Initiate STK Push
url = "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment"
data = {
    "company": "Your Company",
    "phone_number": "254712345678",
    "amount": 100.00,
    "reference": "INV-001"
}
response = requests.post(url, json=data, headers=headers)
print(response.json())
```

### JavaScript/Node.js Example

```javascript
const axios = require('axios');

// Register MPESA Settings
const registerSettings = async () => {
  const response = await axios.post(
    'https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings',
    {
      company: 'Your Company',
      environment: 'Sandbox',
      shortcode: '174379',
      shortcode_type: 'Paybill',
      consumer_key: 'your_key',
      consumer_secret: 'your_secret',
      passkey: 'your_passkey',
      payment_account: 'Cash - Your Company',
      stk_callback_url: 'https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.stk_callback'
    },
    {
      headers: {
        'Authorization': 'token YOUR_API_KEY:YOUR_API_SECRET',
        'Content-Type': 'application/json'
      }
    }
  );
  console.log(response.data);
};

// Initiate STK Push
const initiatePayment = async () => {
  const response = await axios.post(
    'https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment',
    {
      company: 'Your Company',
      phone_number: '254712345678',
      amount: 100.00,
      reference: 'INV-001'
    },
    {
      headers: {
        'Authorization': 'token YOUR_API_KEY:YOUR_API_SECRET',
        'Content-Type': 'application/json'
      }
    }
  );
  console.log(response.data);
};
```

### cURL Examples

```bash
# Register Settings
curl -X POST https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings \
  -H "Authorization: token YOUR_API_KEY:YOUR_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "company": "Your Company",
    "environment": "Sandbox",
    "shortcode": "174379",
    "shortcode_type": "Paybill",
    "consumer_key": "your_key",
    "consumer_secret": "your_secret",
    "passkey": "your_passkey",
    "payment_account": "Cash - Your Company"
  }'

# Initiate STK Push
curl -X POST https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment \
  -H "Authorization: token YOUR_API_KEY:YOUR_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "company": "Your Company",
    "phone_number": "254712345678",
    "amount": 100.00,
    "reference": "INV-001"
  }'

# Get Transactions
curl -X GET "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.get_transactions?company=Your Company&status=Success" \
  -H "Authorization: token YOUR_API_KEY:YOUR_API_SECRET"
```

---

## Rate Limits

Currently, no rate limits are enforced by the API. However, Daraja API has its own rate limits:
- OAuth Token: No specific limit (but tokens are cached)
- STK Push: Subject to Daraja limits
- B2C/B2B: Subject to Daraja limits

---

## Best Practices

1. **Error Handling:**
   - Always check `success` field in response
   - Handle all error codes appropriately
   - Log errors for debugging

2. **Idempotency:**
   - Use unique references for each transaction
   - Handle duplicate callbacks gracefully
   - Check transaction status before retrying

3. **Security:**
   - Never expose API keys in client-side code
   - Use HTTPS for all callbacks
   - Validate all input data

4. **Performance:**
   - Cache MPESA settings per company
   - Use transaction IDs for status checks (faster than checkout_request_id)
   - Implement retry logic with exponential backoff

---

**Document Version:** 1.0  
**Last Updated:** 2024

