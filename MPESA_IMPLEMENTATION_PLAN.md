# MPESA Payment Method - Multitenant Implementation Plan (Daraja-Compliant)

## Overview
This document outlines the **Daraja-compliant** implementation plan for adding MPESA payment method support with multitenant capabilities, allowing each company/tenant to register and configure their own MPESA credentials and settings. This implementation follows Safaricom Daraja API specifications.

**Implementation Approach:** Phased development with each phase building on the previous, ensuring incremental progress and early validation.

---

## Table of Contents
1. [Phase 1: Foundation & Data Model](#phase-1-foundation--data-model)
2. [Phase 2: Core STK Push Implementation](#phase-2-core-stk-push-implementation)
3. [Phase 3: B2C/B2B & Callback Processing](#phase-3-b2cb2b--callback-processing)
4. [Phase 4: API Endpoints & Integration](#phase-4-api-endpoints--integration)
5. [Phase 5: Testing & Quality Assurance](#phase-5-testing--quality-assurance)
6. [Phase 6: Documentation & Deployment](#phase-6-documentation--deployment)
7. [Appendix: Reference Materials](#appendix-reference-materials)

---

## Phase 1: Foundation & Data Model

**Duration:** Week 1-2  
**Objective:** Establish data structures, constants, and basic infrastructure for MPESA integration.

### 1.1 Tasks

#### 1.1.1 Create MPESA Settings DocType
**Location:** `techsavanna_pos/doctype/mpesa_settings/`

**Required Fields:**
- `company` (Link to Company) - Required, unique per company
- `is_active` (Check) - Enable/disable MPESA for this company
- `environment` (Select: Sandbox/Production) - MPESA environment
- `shortcode` (Data) - Business shortcode (Paybill or Till number)
- `shortcode_type` (Select: Paybill/BuyGoods) - Determines transaction type
- `consumer_key` (Password) - Daraja app consumer key
- `consumer_secret` (Password) - Daraja app consumer secret
- `passkey` (Password) - STK Push passkey
- `payment_account` (Link to Account) - Account to receive MPESA payments
- `mode_of_payment` (Link to Mode of Payment) - Associated mode of payment

**Optional Fields:**
- `initiator_name` (Data) - Initiator name (required for B2C/B2B)
- `initiator_password` (Password) - Encrypted initiator password
- `account_reference_prefix` (Data) - Account reference prefix (e.g., "INV")
- `transaction_description` (Data) - Default transaction description template
- `auto_confirm_payments` (Check) - Auto-reconcile successful payments
- `payment_timeout` (Int) - Payment timeout in seconds (default: 300)
- `test_phone_number` (Data) - Test phone number for sandbox testing

**Callback URLs:**
- `stk_callback_url` (Data) - STK Push callback URL
- `b2c_result_url` (Data) - B2C result callback URL
- `b2c_timeout_url` (Data) - B2C timeout callback URL
- `b2b_result_url` (Data) - B2B result callback URL
- `b2b_timeout_url` (Data) - B2B timeout callback URL

**Token Management:**
- `access_token` (Password) - Cached OAuth access token
- `token_expiry` (Datetime) - Token expiry timestamp
- `last_token_refresh` (Datetime) - Last token refresh time

**Permissions:**
- Read: All authenticated users
- Write: Accounts Manager, System Manager
- Create: Accounts Manager, System Manager
- Delete: System Manager only

**Validations:**
- Company must be unique
- Shortcode type must match shortcode format
- Callback URLs must be HTTPS
- Required fields validation

#### 1.1.2 Create MPESA Transaction Log DocType
**Location:** `techsavanna_pos/doctype/mpesa_transaction_log/`

**Required Fields:**
- `company` (Link to Company) - Required
- `transaction_type` (Select: STK Push/B2C/B2B) - Transaction type
- `phone_number` (Data) - Customer phone number (MSISDN format: 254712345678)
- `amount` (Currency) - Transaction amount
- `reference_number` (Data) - Internal reference (e.g., invoice number)
- `status` (Select: Pending/Success/Failed/Cancelled) - Transaction status

**MPESA Identifiers:**
- `merchant_request_id` (Data) - STK MerchantRequestID (STK only)
- `checkout_request_id` (Data) - STK CheckoutRequestID (STK only, used for idempotency)
- `conversation_id` (Data) - B2C/B2B ConversationID
- `mpesa_receipt_number` (Data) - MPESA receipt number (on success)

**Daraja Response:**
- `result_code` (Int) - Daraja result code
- `result_description` (Small Text) - Daraja result description

**Payload Storage:**
- `request_payload` (JSON) - Complete request sent to Daraja API
- `response_payload` (JSON) - Initial response from Daraja API
- `callback_payload` (JSON) - Complete callback data from Daraja

**ERP Integration:**
- `payment_entry` (Link to Payment Entry) - Linked payment entry
- `invoice` (Dynamic Link) - Related invoice (Sales Invoice/POS Invoice)
- `error_message` (Text) - Error details if failed

**Timestamps:**
- `created_at` (Datetime) - Transaction initiation time
- `completed_at` (Datetime) - Transaction completion time

**Permissions:**
- Read: Accounts User, Accounts Manager
- Write: System Manager only (for status updates)
- Create: System (via API)
- Delete: System Manager only

**Indexes:**
- Index on `checkout_request_id` (for idempotency checks)
- Index on `conversation_id` (for B2C/B2B idempotency)
- Index on `company` and `status` (for queries)

#### 1.1.3 Create Constants Module
**Location:** `techsavanna_pos/api/mpesa_constants.py`

**Daraja Result Codes:**
```python
# Success
MPESA_SUCCESS = 0

# User Actions
MPESA_USER_CANCELLED = 1032

# Insufficient Funds
MPESA_INSUFFICIENT_FUNDS = 2001
MPESA_INSUFFICIENT_BALANCE = 1

# Invalid Data
MPESA_INVALID_MSISDN = 17

# Timeout
MPESA_TIMEOUT = 1037

# Result Code Messages
RESULT_CODE_MESSAGES = {
    0: "Success",
    1032: "User cancelled the transaction",
    2001: "Insufficient funds in customer account",
    1: "Insufficient balance",
    17: "Invalid MSISDN format",
    1037: "Transaction timeout"
}
```

**Daraja API Endpoints:**
```python
# Base URLs
DARAJASANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
DARAJAPRODUCTION_BASE_URL = "https://api.safaricom.co.ke"

# Endpoints
OAUTH_TOKEN_ENDPOINT = "/oauth/v1/generate?grant_type=client_credentials"
STK_PUSH_ENDPOINT = "/mpesa/stkpush/v1/processrequest"
STK_PUSH_QUERY_ENDPOINT = "/mpesa/stkpushquery/v1/query"
B2C_ENDPOINT = "/mpesa/b2c/v1/paymentrequest"
B2B_ENDPOINT = "/mpesa/b2b/v1/paymentrequest"
```

**Transaction Types:**
```python
TRANSACTION_TYPE_PAYBILL = "CustomerPayBillOnline"
TRANSACTION_TYPE_BUYGOODS = "CustomerBuyGoodsOnline"
```

### 1.2 Deliverables
- ✅ MPESA Settings DocType created and migrated
- ✅ MPESA Transaction Log DocType created and migrated
- ✅ Constants module with all Daraja codes and endpoints
- ✅ Permissions configured
- ✅ Validations implemented
- ✅ Database indexes created

### 1.3 Testing
- Unit tests for DocType validations
- Test DocType creation and field validation
- Test permissions and access control

### 1.4 Success Criteria
- ✅ Both DocTypes can be created via Frappe UI
- ✅ All fields are properly configured
- ✅ Permissions work correctly
- ✅ Validations prevent invalid data
- ✅ Constants module is complete and importable

---

## Phase 2: Core STK Push Implementation

**Duration:** Week 2-3  
**Objective:** Implement OAuth token management and STK Push payment initiation with Daraja API.

### 2.1 Tasks

#### 2.1.1 Implement OAuth Token Management
**Location:** `techsavanna_pos/api/mpesa_client.py`

**Function:** `get_access_token(company)`

**Implementation:**
- Get MPESA settings for company
- Check if cached token exists and is valid (not expired)
- If expired or missing, request new token from Daraja
- **Daraja Endpoint:** `GET /oauth/v1/generate?grant_type=client_credentials`
- **Headers:** `Authorization: Basic {base64(consumer_key:consumer_secret)}`
- Cache token with expiry time in MPESA Settings
- Return access token string

**Error Handling:**
- Invalid credentials → Raise AuthenticationError
- Network errors → Retry with exponential backoff
- Token refresh failures → Log and raise

#### 2.1.2 Implement STK Push Password Generation
**Location:** `techsavanna_pos/api/mpesa_client.py`

**Function:** `generate_stk_password(shortcode, passkey, timestamp)`

**Implementation:**
- **Formula:** `Base64(shortcode + passkey + timestamp)`
- Generate timestamp in format: `YYYYMMDDHHmmss`
- Concatenate: shortcode + passkey + timestamp
- Base64 encode the concatenated string
- Return encoded password

#### 2.1.3 Implement STK Push Initiation
**Location:** `techsavanna_pos/api/mpesa_client.py`

**Function:** `initiate_stk_push(company, phone, amount, reference, description)`

**Implementation:**
- Get access token (using `get_access_token()`)
- Get MPESA settings for company
- Generate STK password (using `generate_stk_password()`)
- Determine transaction type based on `shortcode_type`:
  - If `Paybill` → `CustomerPayBillOnline`
  - If `BuyGoods` → `CustomerBuyGoodsOnline`
- Build request payload:
  ```json
  {
    "BusinessShortCode": "{shortcode}",
    "Password": "{generated_password}",
    "Timestamp": "{timestamp}",
    "TransactionType": "{transaction_type}",
    "Amount": "{amount}",
    "PartyA": "{phone}",
    "PartyB": "{shortcode}",
    "PhoneNumber": "{phone}",
    "CallBackURL": "{stk_callback_url}",
    "AccountReference": "{reference}",
    "TransactionDesc": "{description}"
  }
  ```
- **Daraja Endpoint:** `POST /mpesa/stkpush/v1/processrequest`
- Send request to Daraja API
- Store request payload in Transaction Log
- Store response payload in Transaction Log
- Extract MerchantRequestID and CheckoutRequestID
- Create Transaction Log entry with status "Pending"
- Return transaction details

**Error Handling:**
- Invalid phone number → Return error with code 17
- Network timeout → Retry, then mark as failed
- API errors → Store error in Transaction Log

#### 2.1.4 Implement STK Push Query
**Location:** `techsavanna_pos/api/mpesa_client.py`

**Function:** `query_stk_status(company, checkout_request_id)`

**Implementation:**
- Get access token
- Get MPESA settings
- Generate STK password
- Build query request:
  ```json
  {
    "BusinessShortCode": "{shortcode}",
    "Password": "{generated_password}",
    "Timestamp": "{timestamp}",
    "CheckoutRequestID": "{checkout_request_id}"
  }
  ```
- **Daraja Endpoint:** `POST /mpesa/stkpushquery/v1/query`
- Send request to Daraja
- Update Transaction Log with query response
- Return status

**Use Cases:**
- Timeout recovery
- Manual status checks
- Verification after delayed callback

#### 2.1.5 Create Basic API Endpoint for STK Push
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Function:** `initiate_stk_push()`

**Implementation:**
- Validate request parameters
- Check if MPESA settings exist for company
- Call `initiate_stk_push()` from client
- Return transaction details

**Endpoint:** `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push`

**Request:**
```json
{
  "company": "Company Name",
  "phone_number": "254712345678",
  "amount": 100.00,
  "reference": "INV-001",
  "description": "Payment for Invoice INV-001"
}
```

### 2.2 Deliverables
- ✅ OAuth token management working
- ✅ STK Push password generation correct
- ✅ STK Push initiation working
- ✅ STK Push query working
- ✅ Basic API endpoint for STK Push
- ✅ Transaction Log entries created

### 2.3 Testing
- Unit tests for OAuth token management
- Unit tests for password generation
- Unit tests for STK Push initiation
- Integration test: Initiate STK Push in sandbox
- Test token caching and refresh
- Test error scenarios

### 2.4 Success Criteria
- ✅ Can get OAuth token from Daraja sandbox
- ✅ Token is cached and auto-refreshed
- ✅ STK Push password generation matches Daraja requirements
- ✅ STK Push request is sent successfully
- ✅ Transaction Log entry is created with correct data
- ✅ STK Query returns correct status

---

## Phase 3: B2C/B2B & Callback Processing

**Duration:** Week 3-4  
**Objective:** Implement B2C/B2B payments and callback handlers with idempotency.

### 3.1 Tasks

#### 3.1.1 Implement B2C Payment
**Location:** `techsavanna_pos/api/mpesa_client.py`

**Function:** `process_b2c_payment(company, phone, amount, reference, remarks)`

**Implementation:**
- Get access token
- Get MPESA settings (must have initiator_name and initiator_password)
- Encrypt initiator password (if required by Daraja)
- Build request:
  ```json
  {
    "InitiatorName": "{initiator_name}",
    "SecurityCredential": "{encrypted_password}",
    "CommandID": "BusinessPayment",
    "Amount": "{amount}",
    "PartyA": "{shortcode}",
    "PartyB": "{phone}",
    "Remarks": "{remarks}",
    "QueueTimeOutURL": "{b2c_timeout_url}",
    "ResultURL": "{b2c_result_url}",
    "Occasion": ""
  }
  ```
- **Daraja Endpoint:** `POST /mpesa/b2c/v1/paymentrequest`
- Send request
- Store in Transaction Log
- Return ConversationID

#### 3.1.2 Implement B2B Payment
**Location:** `techsavanna_pos/api/mpesa_client.py`

**Function:** `process_b2b_payment(company, receiver_shortcode, amount, reference, remarks)`

**Implementation:**
- Similar to B2C but for business-to-business
- **Daraja Endpoint:** `POST /mpesa/b2b/v1/paymentrequest`
- Store in Transaction Log
- Return ConversationID

#### 3.1.3 Implement STK Callback Handler
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Function:** `stk_callback()`

**Implementation:**
- Receive callback from Daraja
- Extract `CheckoutRequestID` from callback
- **Idempotency Check:** Query Transaction Log by `CheckoutRequestID`
  - If transaction already processed → Return success (idempotent)
  - If not found → Create new transaction entry
- Extract callback data:
  - `ResultCode`
  - `ResultDesc`
  - `MpesaReceiptNumber`
  - `PhoneNumber`
  - `Amount`
- Update Transaction Log:
  - Set `status` based on `ResultCode`
  - Set `result_code` and `result_description`
  - Set `mpesa_receipt_number` if success
  - Store `callback_payload`
  - Set `completed_at`
- If `ResultCode = 0` (Success):
  - Create payment entry (if `auto_confirm_payments` enabled)
  - Update invoice status
- Return success response to Daraja

**Important:**
- **No signature validation** (Daraja doesn't provide)
- **Idempotency is critical** (prevent duplicate processing)
- Handle delayed callbacks gracefully

#### 3.1.4 Implement B2C Callback Handlers
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Functions:**
- `b2c_result_callback()` - Handle B2C result
- `b2c_timeout_callback()` - Handle B2C timeout

**Implementation:**
- Extract `ConversationID` from callback
- **Idempotency Check:** Query by `ConversationID`
- Update Transaction Log
- Create payment entry if successful
- Handle timeout scenarios

#### 3.1.5 Implement B2B Callback Handlers
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Functions:**
- `b2b_result_callback()` - Handle B2B result
- `b2b_timeout_callback()` - Handle B2B timeout

**Implementation:**
- Similar to B2C but for B2B transactions
- Extract `ConversationID`
- Implement idempotency
- Update Transaction Log
- Create payment entry if successful

#### 3.1.6 Implement Payment Entry Creation
**Location:** `techsavanna_pos/api/mpesa_api.py` (helper function)

**Function:** `create_payment_entry(transaction, invoice)`

**Implementation:**
- Get transaction from Transaction Log
- Get invoice (if provided)
- Create Payment Entry:
  - Payment type: "Receive"
  - Party type: "Customer" (if invoice)
  - Party: Customer from invoice
  - Paid from: Customer account
  - Paid to: MPESA payment account
  - Amount: Transaction amount
  - Mode of payment: MPESA
  - Reference: Transaction reference
- Link payment entry to transaction
- Submit payment entry (if auto-confirm enabled)
- Update invoice outstanding amount

#### 3.1.7 Implement Invoice Status Updates
**Location:** `techsavanna_pos/api/mpesa_api.py` (helper function)

**Function:** `update_invoice_payment_status(invoice, transaction)`

**Implementation:**
- Get invoice (Sales Invoice or POS Invoice)
- Update outstanding amount
- If fully paid, update status
- Link transaction to invoice

### 3.2 Deliverables
- ✅ B2C payment implementation
- ✅ B2B payment implementation
- ✅ STK callback handler with idempotency
- ✅ B2C callback handlers
- ✅ B2B callback handlers
- ✅ Payment entry creation
- ✅ Invoice status updates

### 3.3 Testing
- Test STK callback with success scenario
- Test STK callback idempotency (duplicate callbacks)
- Test B2C payment and callbacks
- Test B2B payment and callbacks
- Test payment entry creation
- Test invoice status updates
- Test delayed callback handling

### 3.4 Success Criteria
- ✅ B2C payments can be initiated
- ✅ B2B payments can be initiated
- ✅ STK callbacks are processed correctly
- ✅ Idempotency prevents duplicate processing
- ✅ Payment entries are created automatically
- ✅ Invoices are updated on successful payment
- ✅ All Daraja result codes are handled

---

## Phase 4: API Endpoints & Integration

**Duration:** Week 4-5  
**Objective:** Complete all API endpoints and integrate with POS/Sales Invoices.

### 4.1 Tasks

#### 4.1.1 Settings Management APIs
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Endpoints:**
- `register_mpesa_settings()` - Register/update settings
- `get_mpesa_settings(company)` - Get settings (masked)
- `update_mpesa_settings()` - Update existing settings
- `deactivate_mpesa_settings(company)` - Deactivate MPESA

**Implementation:**
- Validate all required fields
- Encrypt sensitive fields
- Validate callback URLs (must be HTTPS)
- Validate shortcode format
- Return masked sensitive data in GET

#### 4.1.2 Payment Processing APIs
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Endpoints:**
- `initiate_stk_push()` - Already implemented, enhance
- `initiate_b2c_payment()` - New endpoint
- `initiate_b2b_payment()` - New endpoint
- `check_payment_status(transaction_id)` - Check status

**Implementation:**
- Validate company has MPESA settings
- Validate parameters
- Call appropriate client function
- Return transaction details

#### 4.1.3 Transaction Management APIs
**Location:** `techsavanna_pos/api/mpesa_api.py`

**Endpoints:**
- `get_transactions(company, status, limit, offset)` - Get history
- `get_transaction(transaction_id)` - Get single transaction

**Implementation:**
- Filter by company (security)
- Support pagination
- Support status filtering
- Return complete transaction details

#### 4.1.4 POS Invoice Integration
**Location:** `techsavanna_pos/overrides/` or hooks

**Implementation:**
- Add MPESA to POS Profile payment methods
- Add MPESA payment option in POS Invoice UI
- On payment selection, initiate STK Push
- Link transaction to POS Invoice
- Update POS Invoice on successful payment

**Files:**
- Override POS Invoice class if needed
- Add custom fields if needed
- Add JavaScript for payment initiation

#### 4.1.5 Sales Invoice Integration
**Location:** `techsavanna_pos/api/` or hooks

**Implementation:**
- Add MPESA payment request option
- Create API endpoint to request payment from Sales Invoice
- Link transaction to Sales Invoice
- Update Sales Invoice on successful payment

#### 4.1.6 Mode of Payment Setup
**Location:** `techsavanna_pos/api/mpesa_api.py` (helper function)

**Function:** `setup_mpesa_mode_of_payment(company)`

**Implementation:**
- Check if "MPESA" Mode of Payment exists
- If not, create it
- Link to MPESA payment account from settings
- Set type as "Phone"
- Enable it

### 4.2 Deliverables
- ✅ All API endpoints implemented
- ✅ Settings management working
- ✅ Transaction management working
- ✅ POS Invoice integration complete
- ✅ Sales Invoice integration complete
- ✅ Mode of Payment auto-setup

### 4.3 Testing
- Test all API endpoints
- Test settings registration and retrieval
- Test POS Invoice payment flow
- Test Sales Invoice payment flow
- Test transaction history queries
- Test error handling in APIs

### 4.4 Success Criteria
- ✅ All APIs return correct responses
- ✅ Settings can be registered and retrieved
- ✅ MPESA appears in POS payment methods
- ✅ MPESA can be used for Sales Invoice payments
- ✅ Transaction history is queryable
- ✅ All integrations work end-to-end

---

## Phase 5: Testing & Quality Assurance

**Duration:** Week 5-6  
**Objective:** Comprehensive testing, bug fixes, and performance optimization.

### 5.1 Tasks

#### 5.1.1 Unit Tests
**Location:** `techsavanna_pos/doctype/mpesa_settings/test_*.py` and `techsavanna_pos/api/test_*.py`

**Test Coverage:**
- OAuth token management
- STK password generation
- STK Push initiation
- B2C/B2B payment processing
- Callback processing
- Payment entry creation
- Invoice status updates
- Error handling

#### 5.1.2 Integration Tests
**Test Scenarios:**
- End-to-end STK Push flow
- End-to-end B2C flow
- End-to-end B2B flow
- Callback handling
- Payment reconciliation
- Error scenarios

#### 5.1.3 Sandbox Testing
**Daraja Sandbox Setup:**
- Register at https://developer.safaricom.co.ke/
- Create sandbox app
- Get sandbox credentials
- Use sandbox shortcode: `174379`
- Use test phone numbers

**Test Scenarios:**
1. **STK Push Success:**
   - Initiate STK push
   - Complete payment on phone
   - Verify callback received
   - Verify payment entry created
   - Verify invoice updated

2. **STK Push Cancellation:**
   - Initiate STK push
   - Cancel on phone
   - Verify callback with code 1032
   - Verify transaction marked as cancelled

3. **STK Push Timeout:**
   - Initiate STK push
   - Wait for timeout
   - Use STK Query to check status
   - Verify timeout handling

4. **Duplicate Callback:**
   - Process successful payment
   - Simulate duplicate callback
   - Verify idempotency (no duplicate payment entry)

5. **B2C Payment:**
   - Initiate B2C payment
   - Verify result callback
   - Verify payment entry created

6. **B2B Payment:**
   - Initiate B2B payment
   - Verify result callback
   - Verify payment entry created

7. **Error Scenarios:**
   - Invalid credentials
   - Insufficient funds
   - Invalid phone number
   - Network timeout

#### 5.1.4 Performance Testing
- STK Push initiation: < 2 seconds
- Callback processing: < 5 seconds
- Token refresh: < 1 second
- Transaction queries: < 1 second

#### 5.1.5 Security Testing
- Verify credential encryption
- Verify company-level isolation
- Verify no sensitive data in logs
- Verify callback idempotency
- Test access control

#### 5.1.6 Bug Fixes
- Fix all identified bugs
- Address performance issues
- Fix security vulnerabilities
- Improve error messages

### 5.2 Deliverables
- ✅ All unit tests passing
- ✅ All integration tests passing
- ✅ Sandbox testing complete
- ✅ Performance benchmarks met
- ✅ Security audit passed
- ✅ All bugs fixed

### 5.3 Success Criteria
- ✅ Test coverage > 80%
- ✅ All critical paths tested
- ✅ Performance requirements met
- ✅ No security vulnerabilities
- ✅ All bugs resolved

---

## Phase 6: Documentation & Deployment

**Duration:** Week 6  
**Objective:** Complete documentation, deploy to production, and set up monitoring.

### 6.1 Tasks

#### 6.1.1 API Documentation
**Location:** `techsavanna_pos/docs/MPESA_API_DOCUMENTATION.md`

**Content:**
- All API endpoints with examples
- Request/response schemas
- Authentication requirements
- Error codes and handling
- Code examples

#### 6.1.2 User Guide
**Location:** `techsavanna_pos/docs/MPESA_USER_GUIDE.md`

**Content:**
- How to register MPESA settings
- How to initiate payments
- How to check transaction status
- How to view transaction history
- Troubleshooting common issues

#### 6.1.3 Admin Guide
**Location:** `techsavanna_pos/docs/MPESA_ADMIN_GUIDE.md`

**Content:**
- System configuration
- Daraja account setup
- Production deployment steps
- Monitoring setup
- Maintenance procedures

#### 6.1.4 Production Deployment
**Steps:**
1. Deploy code to production
2. Run migrations (DocTypes)
3. Configure production Daraja credentials
4. Set up production callback URLs
5. Test with small transaction
6. Monitor for issues

#### 6.1.5 Monitoring Setup
**Monitoring Points:**
- Transaction success/failure rates
- Average processing time
- Token refresh failures
- Callback processing time
- Error rates

**Alerts:**
- High failure rates (> 10%)
- Token refresh failures
- Callback processing failures
- System errors

#### 6.1.6 Backup Configuration
- Configure backup for Transaction Log
- Set up retention policy
- Test backup and restore

### 6.2 Deliverables
- ✅ Complete API documentation
- ✅ User guide published
- ✅ Admin guide published
- ✅ Production deployment complete
- ✅ Monitoring configured
- ✅ Alerts set up

### 6.3 Success Criteria
- ✅ Documentation is complete and accurate
- ✅ Production deployment successful
- ✅ Monitoring is working
- ✅ Alerts are configured
- ✅ System is production-ready

---

## Appendix: Reference Materials

### A.1 Daraja API Specifications

#### OAuth Token API
**Endpoint:** `GET /oauth/v1/generate?grant_type=client_credentials`  
**Headers:** `Authorization: Basic {base64(consumer_key:consumer_secret)}`  
**Response:** `{"access_token": "...", "expires_in": "3599"}`

#### STK Push API
**Endpoint:** `POST /mpesa/stkpush/v1/processrequest`  
**Password Formula:** `Base64(shortcode + passkey + timestamp)`  
**Transaction Types:**
- `CustomerPayBillOnline` (for Paybill)
- `CustomerBuyGoodsOnline` (for BuyGoods)

#### STK Push Query API
**Endpoint:** `POST /mpesa/stkpushquery/v1/query`

#### B2C API
**Endpoint:** `POST /mpesa/b2c/v1/paymentrequest`  
**Requires:** Result URL and Timeout URL

#### B2B API
**Endpoint:** `POST /mpesa/b2b/v1/paymentrequest`  
**Requires:** Result URL and Timeout URL

### A.2 Daraja Result Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Process payment |
| 1032 | User cancelled | Mark as cancelled |
| 2001 | Insufficient funds | Mark as failed |
| 17 | Invalid MSISDN | Mark as failed |
| 1 | Insufficient balance | Mark as failed |
| 1037 | Timeout | Query status |

### A.3 File Structure

```
techsavanna_pos/
├── api/
│   ├── mpesa_api.py          # API endpoints
│   ├── mpesa_client.py       # MPESA API client
│   ├── mpesa_utils.py        # Utility functions
│   └── mpesa_constants.py    # Constants
├── doctype/
│   ├── mpesa_settings/
│   │   ├── mpesa_settings.json
│   │   ├── mpesa_settings.py
│   │   └── mpesa_settings.js
│   └── mpesa_transaction_log/
│       ├── mpesa_transaction_log.json
│       ├── mpesa_transaction_log.py
│       └── mpesa_transaction_log.js
└── docs/
    ├── MPESA_API_DOCUMENTATION.md
    ├── MPESA_USER_GUIDE.md
    └── MPESA_ADMIN_GUIDE.md
```

### A.4 Security Considerations

1. **Credential Storage:** Password fieldtype, encrypted at rest
2. **Access Control:** Company-level isolation, role-based permissions
3. **Callback Validation:** No signatures (Daraja doesn't provide), use idempotency
4. **Token Management:** Cache with expiry, auto-refresh
5. **Data Privacy:** Mask sensitive data, minimal logging

### A.5 Configuration Requirements

**Daraja Developer Account:**
- Register at https://developer.safaricom.co.ke/
- Create app (Sandbox/Production)
- Get credentials (Consumer Key, Secret, Passkey)
- Configure callback URLs (must be HTTPS)
- Production requires Safaricom approval

**System Configuration:**
- HTTPS for all callbacks
- Firewall rules (if needed)
- Monitoring and logging
- Backup configuration

---

## Overall Success Criteria

### Functional Requirements
1. ✅ Companies can register MPESA credentials
2. ✅ STK push payments work end-to-end
3. ✅ B2C payments work end-to-end
4. ✅ B2B payments work end-to-end
5. ✅ Callbacks are processed correctly (with idempotency)
6. ✅ Payment entries are created automatically
7. ✅ Invoices are updated on successful payment
8. ✅ Transaction history is maintained
9. ✅ STK Query works for timeout recovery
10. ✅ All Daraja result codes are handled

### Technical Requirements
11. ✅ OAuth token management works (auto-refresh)
12. ✅ STK password generation is correct
13. ✅ Transaction types are correct
14. ✅ Idempotency prevents duplicate processing
15. ✅ Error handling is robust
16. ✅ Security best practices followed
17. ✅ No sensitive data logged
18. ✅ Company-level isolation maintained

### Quality Requirements
19. ✅ Documentation is complete
20. ✅ All tests pass
21. ✅ System is production-ready
22. ✅ Monitoring and alerts configured
23. ✅ Performance is acceptable
24. ✅ Callback processing is reliable

---

**Document Version:** 2.0 (Phased Structure)  
**Last Updated:** 2024  
**Status:** Ready for Implementation
