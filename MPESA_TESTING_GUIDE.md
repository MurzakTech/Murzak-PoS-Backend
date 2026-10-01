# MPESA Integration Testing Guide

## Overview
This guide provides comprehensive testing instructions for the MPESA (Daraja) payment integration, including unit tests, integration tests, and sandbox testing procedures.

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Unit Testing](#unit-testing)
3. [Integration Testing](#integration-testing)
4. [Sandbox Testing](#sandbox-testing)
5. [Manual Testing Scenarios](#manual-testing-scenarios)
6. [Performance Testing](#performance-testing)
7. [Security Testing](#security-testing)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### 1. Daraja Sandbox Account
- Register at https://developer.safaricom.co.ke/
- Create a sandbox app
- Get sandbox credentials:
  - Consumer Key
  - Consumer Secret
  - Passkey
- Sandbox shortcode: `174379`
- Test phone numbers (provided by Daraja)

### 2. Test Environment Setup
```bash
# Activate bench environment
cd /home/shavia/Documents/SavvyPOS/Backend/frappe-bench
source env/bin/activate

# Migrate DocTypes
bench --site [your-site] migrate

# Clear cache
bench --site [your-site] clear-cache
```

### 3. Test Data
- Create test company
- Create test cash account
- Create test customer with phone number

---

## Unit Testing

### Running Unit Tests

```bash
# Run all MPESA tests
bench --site [your-site] run-tests --module techsavanna_pos.api.test_mpesa_client
bench --site [your-site] run-tests --module techsavanna_pos.api.test_mpesa_api
bench --site [your-site] run-tests --module techsavanna_pos.techsavanna_pos.doctype.mpesa_settings.test_mpesa_settings
bench --site [your-site] run-tests --module techsavanna_pos.techsavanna_pos.doctype.mpesa_transaction_log.test_mpesa_transaction_log

# Run all tests
bench --site [your-site] run-tests --app techsavanna_pos
```

### Test Coverage

**MPESA Client Tests (`test_mpesa_client.py`):**
- ✅ Timestamp generation
- ✅ STK password generation (Base64 formula)
- ✅ OAuth token retrieval (success and error cases)
- ✅ STK Push initiation (success and validation)
- ✅ STK status query

**MPESA API Tests (`test_mpesa_api.py`):**
- ✅ STK Push payment API
- ✅ Settings registration API
- ✅ Settings retrieval API
- ✅ Transaction history API
- ✅ Transaction details API
- ✅ Callback idempotency

**DocType Tests:**
- ✅ MPESA Settings validations
- ✅ Transaction Log validations
- ✅ Field format validations

---

## Integration Testing

### 1. Settings Registration Flow

**Test Steps:**
1. Register MPESA settings via API
2. Verify settings are saved
3. Verify Mode of Payment is created
4. Verify payment account is linked

**API Call:**
```bash
curl -X POST http://localhost:8000/api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings \
  -H "Authorization: token YOUR_API_KEY:YOUR_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "company": "Test Company",
    "environment": "Sandbox",
    "shortcode": "174379",
    "shortcode_type": "Paybill",
    "consumer_key": "YOUR_SANDBOX_KEY",
    "consumer_secret": "YOUR_SANDBOX_SECRET",
    "passkey": "YOUR_SANDBOX_PASSKEY",
    "payment_account": "Cash - Test Company",
    "stk_callback_url": "https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.stk_callback"
  }'
```

**Expected Result:**
- Settings created successfully
- Mode of Payment "MPESA" exists
- Payment account linked

### 2. STK Push Payment Flow

**Test Steps:**
1. Initiate STK Push payment
2. Verify transaction log created
3. Complete payment on phone (sandbox)
4. Verify callback received
5. Verify payment entry created
6. Verify invoice updated

**API Call:**
```bash
curl -X POST http://localhost:8000/api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment \
  -H "Authorization: token YOUR_API_KEY:YOUR_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "company": "Test Company",
    "phone_number": "254712345678",
    "amount": 100.00,
    "reference": "TEST-001",
    "description": "Test payment"
  }'
```

**Expected Result:**
- Transaction log created with status "Pending"
- STK push sent to phone
- Payment completed on phone
- Callback received and processed
- Payment entry created
- Transaction status updated to "Success"

### 3. Payment Status Check

**Test Steps:**
1. Initiate STK Push
2. Check status via API
3. Verify status is returned correctly

**API Call:**
```bash
curl -X GET "http://localhost:8000/api/method/techsavanna_pos.api.mpesa_api.check_payment_status?transaction_id=MPESA-TXN-001" \
  -H "Authorization: token YOUR_API_KEY:YOUR_API_SECRET"
```

---

## Sandbox Testing

### Daraja Sandbox Setup

1. **Register Sandbox App:**
   - Go to https://developer.safaricom.co.ke/
   - Create new app
   - Get credentials

2. **Sandbox Credentials:**
   - Consumer Key: From Daraja portal
   - Consumer Secret: From Daraja portal
   - Passkey: From Daraja portal
   - Shortcode: `174379` (sandbox)

3. **Test Phone Numbers:**
   - Use test numbers provided by Daraja
   - Format: `254712345678`

### Test Scenarios

#### Scenario 1: STK Push Success
1. Register MPESA settings with sandbox credentials
2. Initiate STK Push with test phone number
3. Complete payment on phone
4. Verify:
   - Callback received
   - Transaction status = "Success"
   - Payment entry created
   - Receipt number stored

#### Scenario 2: STK Push Cancellation
1. Initiate STK Push
2. Cancel payment on phone
3. Verify:
   - Callback received with result code 1032
   - Transaction status = "Cancelled"
   - No payment entry created

#### Scenario 3: STK Push Timeout
1. Initiate STK Push
2. Wait for timeout (don't complete)
3. Use STK Query to check status
4. Verify:
   - Status query works
   - Transaction marked as timeout/failed

#### Scenario 4: Duplicate Callback
1. Process successful payment
2. Simulate duplicate callback (same CheckoutRequestID)
3. Verify:
   - Idempotency prevents duplicate processing
   - No duplicate payment entry
   - Returns success (idempotent)

#### Scenario 5: B2C Payment
1. Register settings with initiator credentials
2. Initiate B2C payment
3. Verify:
   - Result callback received
   - Transaction status updated
   - Payment entry created

#### Scenario 6: B2B Payment
1. Register settings with initiator credentials
2. Initiate B2B payment
3. Verify:
   - Result callback received
   - Transaction status updated
   - Payment entry created

#### Scenario 7: Error Scenarios
- Invalid credentials → Authentication error
- Insufficient funds → Result code 2001
- Invalid phone number → Result code 17
- Network timeout → Error handling

---

## Manual Testing Scenarios

### 1. Settings Management

**Test: Register Settings**
- Navigate to MPESA Settings
- Create new settings for company
- Fill all required fields
- Verify validations work
- Save and verify

**Test: Update Settings**
- Edit existing settings
- Change environment
- Update callback URLs
- Verify token is cleared on credential change

**Test: Deactivate Settings**
- Deactivate MPESA settings
- Verify is_active = 0
- Verify cannot initiate payments

### 2. Transaction Log

**Test: View Transactions**
- Navigate to MPESA Transaction Log
- View list of transactions
- Filter by status, type, company
- Verify pagination works

**Test: Transaction Details**
- Open transaction detail
- Verify all fields displayed
- Verify payloads are readable
- Verify links to payment entry/invoice work

### 3. POS Invoice Integration

**Test: Add MPESA to POS Profile**
- Edit POS Profile
- Verify MPESA appears in payment methods
- Select MPESA as payment method
- Save POS Profile

**Test: Payment from POS Invoice**
- Create POS Invoice
- Select MPESA payment
- Initiate payment via API
- Complete payment
- Verify invoice updated

### 4. Sales Invoice Integration

**Test: Payment Request from Sales Invoice**
- Create Sales Invoice
- Use API to initiate MPESA payment
- Complete payment
- Verify invoice updated
- Verify payment entry created

---

## Performance Testing

### Test Metrics

1. **STK Push Initiation:**
   - Target: < 2 seconds
   - Measure: Time from API call to Daraja response

2. **Callback Processing:**
   - Target: < 5 seconds
   - Measure: Time from callback to payment entry creation

3. **Token Refresh:**
   - Target: < 1 second
   - Measure: Time to get new token

4. **Transaction Query:**
   - Target: < 1 second
   - Measure: Time to query transaction status

### Load Testing

**Test: Concurrent STK Pushes**
- Initiate 10 concurrent STK pushes
- Verify all processed correctly
- Verify no race conditions
- Verify all transactions logged

**Test: High Transaction Volume**
- Process 100 transactions
- Verify performance remains acceptable
- Verify no memory leaks
- Verify database performance

---

## Security Testing

### 1. Authentication
- ✅ Verify all endpoints require authentication (except callbacks)
- ✅ Verify API key authentication works
- ✅ Verify session authentication works

### 2. Authorization
- ✅ Verify company-level isolation
- ✅ Verify users can only access their company's data
- ✅ Verify role-based permissions work

### 3. Data Protection
- ✅ Verify sensitive fields are masked in API responses
- ✅ Verify credentials are encrypted at rest
- ✅ Verify no sensitive data in logs

### 4. Callback Security
- ✅ Verify callbacks work without authentication (allow_guest)
- ✅ Verify idempotency prevents duplicate processing
- ✅ Verify malformed callbacks are handled gracefully

---

## Troubleshooting

### Common Issues

#### 1. OAuth Token Errors
**Symptom:** `MpesaAuthenticationError: Invalid MPESA credentials`

**Solutions:**
- Verify consumer key and secret are correct
- Check if credentials are for correct environment (Sandbox/Production)
- Verify credentials are not expired
- Check Daraja portal for credential status

#### 2. STK Push Not Received
**Symptom:** STK push initiated but not received on phone

**Solutions:**
- Verify phone number format (254712345678)
- Check if using test phone number in sandbox
- Verify shortcode is correct
- Check Daraja portal for transaction status

#### 3. Callback Not Received
**Symptom:** Payment completed but callback not received

**Solutions:**
- Verify callback URL is HTTPS
- Check callback URL is accessible from internet
- Verify callback URL is registered in Daraja portal
- Check server logs for callback attempts
- Use STK Query to check status manually

#### 4. Duplicate Payment Entries
**Symptom:** Multiple payment entries for same transaction

**Solutions:**
- Verify idempotency is working (check CheckoutRequestID)
- Check if callback is being called multiple times
- Verify transaction log status before creating payment entry

#### 5. Payment Entry Not Created
**Symptom:** Transaction successful but no payment entry

**Solutions:**
- Verify `auto_confirm_payments` is enabled in settings
- Check payment account exists and is valid
- Check error logs for payment entry creation errors
- Verify invoice link is correct

### Debugging Tips

1. **Enable Debug Logging:**
   ```python
   import frappe
   frappe.conf.developer_mode = 1
   ```

2. **Check Transaction Log:**
   - View request/response payloads
   - Check callback payload
   - Verify status transitions

3. **Check Error Logs:**
   - Navigate to Error Log in Frappe
   - Filter by "MPESA"
   - Review error details

4. **Test Callbacks Manually:**
   - Use Postman to send test callback
   - Verify callback handler processes correctly
   - Check idempotency behavior

---

## Test Checklist

### Phase 1: Foundation
- [ ] MPESA Settings DocType can be created
- [ ] Transaction Log DocType can be created
- [ ] Validations work correctly
- [ ] Permissions are enforced

### Phase 2: Core STK Push
- [ ] OAuth token retrieval works
- [ ] STK password generation is correct
- [ ] STK Push initiation works
- [ ] STK Query works
- [ ] Transaction log entries created

### Phase 3: Callbacks & B2C/B2B
- [ ] STK callback handler works
- [ ] B2C callbacks work
- [ ] B2B callbacks work
- [ ] Idempotency prevents duplicates
- [ ] Payment entries created automatically
- [ ] Invoices updated correctly

### Phase 4: APIs & Integration
- [ ] All API endpoints work
- [ ] Settings management works
- [ ] Transaction management works
- [ ] POS Invoice integration works
- [ ] Sales Invoice integration works
- [ ] Mode of Payment auto-setup works

### Phase 5: Testing
- [ ] All unit tests pass
- [ ] Integration tests pass
- [ ] Sandbox testing complete
- [ ] Performance requirements met
- [ ] Security requirements met

---

## Production Testing

Before going to production:

1. **Test with Production Credentials:**
   - Get production credentials from Safaricom
   - Test with small amounts
   - Verify all flows work

2. **Monitor Transactions:**
   - Set up monitoring
   - Configure alerts
   - Review transaction logs

3. **Load Testing:**
   - Test with expected transaction volume
   - Verify system handles load
   - Check database performance

4. **Backup and Recovery:**
   - Test backup procedures
   - Test recovery procedures
   - Verify transaction log retention

---

**Last Updated:** 2024  
**Version:** 1.0

