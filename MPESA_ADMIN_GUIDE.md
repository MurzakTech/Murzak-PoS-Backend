# MPESA Payment Integration - Administrator Guide

## Overview
Complete guide for administrators to configure, deploy, and maintain MPESA payment integration in a multitenant environment.

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Daraja Account Setup](#daraja-account-setup)
3. [System Configuration](#system-configuration)
4. [Production Deployment](#production-deployment)
5. [Monitoring & Maintenance](#monitoring--maintenance)
6. [Troubleshooting](#troubleshooting)
7. [Security Best Practices](#security-best-practices)

---

## Prerequisites

### System Requirements
- Frappe/ERPNext installation
- HTTPS enabled (required for callbacks)
- Python 3.8+
- MySQL/MariaDB database
- Internet connectivity for Daraja API

### Required Permissions
- System Manager role (for configuration)
- Accounts Manager role (for payment operations)
- Access to Frappe backend

---

## Daraja Account Setup

### Step 1: Register with Safaricom Developer Portal

1. **Create Account:**
   - Go to https://developer.safaricom.co.ke/
   - Register for developer account
   - Verify email address

2. **Create Application:**
   - Log in to developer portal
   - Navigate to "My Apps"
   - Click "Create App"
   - Fill in application details

3. **Get Credentials:**
   - Consumer Key (from app details)
   - Consumer Secret (from app details)
   - Passkey (for STK Push, from app details)

### Step 2: Sandbox Testing

1. **Use Sandbox Environment:**
   - Sandbox shortcode: `174379`
   - Use sandbox credentials
   - Test with provided test phone numbers

2. **Test Callbacks:**
   - Configure callback URLs
   - Test STK Push flow
   - Verify callbacks are received

### Step 3: Production Approval

1. **Submit for Approval:**
   - Complete application form
   - Provide business registration documents
   - Submit for Safaricom review

2. **Get Production Credentials:**
   - Wait for approval (typically 1-2 weeks)
   - Receive production credentials
   - Get production shortcode

---

## System Configuration

### Step 1: Register MPESA Settings

**Via Frappe UI:**
1. Navigate to MPESA Settings
2. Create new record
3. Fill in all required fields:
   - Company
   - Environment (Sandbox/Production)
   - Shortcode
   - Shortcode Type (Paybill/BuyGoods)
   - Consumer Key
   - Consumer Secret
   - Passkey
   - Payment Account
   - Callback URLs

**Via API:**
```bash
POST /api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings
{
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
```

### Step 2: Configure Callback URLs

**Required Callback URLs:**
- STK Callback: `https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.stk_callback`
- B2C Result: `https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2c_result_callback`
- B2C Timeout: `https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2c_timeout_callback`
- B2B Result: `https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2b_result_callback`
- B2B Timeout: `https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.b2b_timeout_callback`

**Important:**
- All URLs must be HTTPS
- URLs must be accessible from internet
- Register URLs in Daraja portal

### Step 3: Configure Payment Account

1. **Create/Select Account:**
   - Navigate to Chart of Accounts
   - Create or select cash account
   - Ensure account is linked to company

2. **Link to MPESA Settings:**
   - Set payment_account in MPESA Settings
   - Mode of Payment is auto-created
   - Account mapping is auto-configured

### Step 4: Configure POS Profile

1. **Edit POS Profile:**
   - Navigate to POS Profile
   - MPESA is automatically added to payment methods
   - Set as default if needed

2. **Verify Payment Methods:**
   - Check MPESA appears in list
   - Verify account mapping is correct

---

## Production Deployment

### Pre-Deployment Checklist

- [ ] Production Daraja credentials obtained
- [ ] Production shortcode received
- [ ] Callback URLs configured and tested
- [ ] HTTPS certificate installed
- [ ] Payment account created
- [ ] Test transaction completed successfully
- [ ] Monitoring configured
- [ ] Backup procedures in place

### Deployment Steps

#### Step 1: Migrate DocTypes

```bash
cd /home/shavia/Documents/SavvyPOS/Backend/frappe-bench
bench --site [your-site] migrate
```

#### Step 2: Configure Production Settings

1. **Update MPESA Settings:**
   - Change environment to "Production"
   - Update with production credentials
   - Update callback URLs (if different)
   - Verify all fields are correct

2. **Test Connection:**
   - Test OAuth token retrieval
   - Verify credentials work
   - Test with small transaction

#### Step 3: Configure Callbacks

1. **Update Daraja Portal:**
   - Log in to Daraja portal
   - Update callback URLs
   - Verify URLs are registered

2. **Test Callbacks:**
   - Send test callback (if possible)
   - Verify callback handler works
   - Check transaction log

#### Step 4: Enable MPESA

1. **Activate Settings:**
   - Set `is_active = 1` in MPESA Settings
   - Verify settings are active

2. **Test End-to-End:**
   - Initiate test payment
   - Complete payment
   - Verify payment entry created
   - Verify invoice updated

---

## Monitoring & Maintenance

### Key Metrics to Monitor

1. **Transaction Success Rate:**
   - Target: > 95%
   - Monitor: Failed vs successful transactions
   - Alert: If success rate drops below 90%

2. **Callback Processing Time:**
   - Target: < 5 seconds
   - Monitor: Time from callback to payment entry creation
   - Alert: If processing time exceeds 10 seconds

3. **Token Refresh Failures:**
   - Monitor: OAuth token refresh errors
   - Alert: On any token refresh failure

4. **Error Rates:**
   - Monitor: Error types and frequencies
   - Alert: On high error rates

### Monitoring Setup

#### 1. Transaction Monitoring

**Create Custom Report:**
```python
# Monitor transaction success rate
transactions = frappe.get_all(
    "MPESA Transaction Log",
    filters={"company": "Your Company", "created_at": [">", "today"]},
    fields=["status", "transaction_type"]
)

success_count = len([t for t in transactions if t.status == "Success"])
total_count = len(transactions)
success_rate = (success_count / total_count * 100) if total_count > 0 else 0

if success_rate < 90:
    # Send alert
    pass
```

#### 2. Error Monitoring

**Check Error Logs:**
- Navigate to Error Log in Frappe
- Filter by "MPESA"
- Review errors regularly
- Set up alerts for critical errors

#### 3. Performance Monitoring

**Monitor API Response Times:**
- Track STK Push initiation time
- Track callback processing time
- Alert on slow responses

### Maintenance Tasks

#### Daily
- Review transaction logs
- Check for failed transactions
- Verify callbacks are being received
- Check error logs

#### Weekly
- Review transaction success rates
- Analyze error patterns
- Verify token refresh is working
- Check callback URL accessibility

#### Monthly
- Review transaction volumes
- Analyze payment patterns
- Verify backup procedures
- Review security logs

---

## Troubleshooting

### Common Issues

#### Issue 1: OAuth Token Errors

**Symptoms:**
- `MpesaAuthenticationError: Invalid credentials`
- Token refresh failures

**Solutions:**
1. Verify credentials in MPESA Settings
2. Check credentials match environment (Sandbox/Production)
3. Verify credentials in Daraja portal
4. Test credentials manually
5. Clear cached token and retry

#### Issue 2: Callbacks Not Received

**Symptoms:**
- Payment completed but callback not received
- Transaction stuck in "Pending" status

**Solutions:**
1. Verify callback URL is HTTPS
2. Check callback URL is accessible from internet
3. Verify URL is registered in Daraja portal
4. Check server logs for callback attempts
5. Use STK Query to check status
6. Verify firewall allows incoming connections

#### Issue 3: Payment Entries Not Created

**Symptoms:**
- Transaction successful but no payment entry

**Solutions:**
1. Verify `auto_confirm_payments` is enabled
2. Check payment account exists and is valid
3. Review error logs for payment entry creation errors
4. Verify invoice link is correct
5. Check permissions for payment entry creation

#### Issue 4: Duplicate Transactions

**Symptoms:**
- Multiple transactions for same payment
- Duplicate payment entries

**Solutions:**
1. Verify idempotency is working
2. Check CheckoutRequestID/ConversationID uniqueness
3. Review callback logs for duplicates
4. Verify transaction log constraints

#### Issue 5: High Failure Rates

**Symptoms:**
- Many transactions failing
- Low success rate

**Solutions:**
1. Analyze failure reasons (result codes)
2. Check for common patterns (phone numbers, amounts)
3. Verify MPESA settings are correct
4. Check Daraja portal for service status
5. Review network connectivity

### Debugging Procedures

#### Enable Debug Logging

```python
import frappe
frappe.conf.developer_mode = 1
frappe.log_error("Debug message", "MPESA Debug")
```

#### Check Transaction Log

1. Navigate to MPESA Transaction Log
2. Open failed transaction
3. Review:
   - Request payload
   - Response payload
   - Callback payload
   - Error message
   - Result code

#### Test Callbacks Manually

Use Postman or curl to send test callback:

```bash
curl -X POST https://your-domain.com/api/method/techsavanna_pos.api.mpesa_api.stk_callback \
  -H "Content-Type: application/json" \
  -d '{
    "Body": {
      "stkCallback": {
        "CheckoutRequestID": "test_checkout_123",
        "ResultCode": 0,
        "ResultDesc": "Success"
      }
    }
  }'
```

---

## Security Best Practices

### 1. Credential Management

- **Never commit credentials to version control**
- Store credentials securely (Frappe Password fieldtype)
- Rotate credentials regularly
- Use different credentials for Sandbox and Production
- Limit access to MPESA Settings

### 2. Access Control

- **Company-level isolation:**
  - Users can only access their company's data
  - Verify company filtering in all queries
  - Test multitenant isolation

- **Role-based permissions:**
  - System Manager: Full access
  - Accounts Manager: Payment operations
  - Accounts User: Read-only access

### 3. Network Security

- **HTTPS only:**
  - All callbacks must use HTTPS
  - Verify SSL certificate is valid
  - Use strong TLS versions

- **Firewall rules:**
  - Allow incoming connections for callbacks
  - Restrict access to admin endpoints
  - Monitor for suspicious activity

### 4. Data Protection

- **Sensitive data:**
  - Never log credentials
  - Mask sensitive fields in API responses
  - Encrypt credentials at rest

- **Transaction data:**
  - Store transaction logs securely
  - Implement retention policies
  - Regular backups

### 5. Monitoring & Auditing

- **Log all operations:**
  - Transaction initiation
  - Callback processing
  - Payment entry creation
  - Error events

- **Audit trail:**
  - Track all changes to MPESA Settings
  - Monitor access to sensitive data
  - Review logs regularly

---

## Backup & Recovery

### Backup Procedures

1. **Transaction Log Backup:**
   ```bash
   # Export transaction logs
   bench --site [your-site] export-doc "MPESA Transaction Log"
   ```

2. **Settings Backup:**
   ```bash
   # Export MPESA Settings
   bench --site [your-site] export-doc "MPESA Settings"
   ```

3. **Database Backup:**
   ```bash
   # Full database backup
   bench --site [your-site] backup --with-files
   ```

### Recovery Procedures

1. **Restore Transaction Logs:**
   - Import from backup
   - Verify data integrity
   - Check for missing transactions

2. **Restore Settings:**
   - Import MPESA Settings
   - Verify credentials
   - Test connection

---

## Performance Optimization

### 1. Token Caching

- Tokens are automatically cached
- Auto-refresh before expiry
- No manual intervention needed

### 2. Database Optimization

- Indexes on:
  - `checkout_request_id` (for idempotency)
  - `conversation_id` (for B2C/B2B)
  - `company` and `status` (for queries)

### 3. Query Optimization

- Use transaction_id for status checks (faster)
- Limit transaction history queries
- Use pagination for large datasets

---

## Support & Resources

### Documentation
- Implementation Plan: `MPESA_IMPLEMENTATION_PLAN.md`
- Testing Guide: `MPESA_TESTING_GUIDE.md`
- API Documentation: `MPESA_API_DOCUMENTATION.md`
- Quick Reference: `MPESA_QUICK_REFERENCE.md`

### External Resources
- Daraja Documentation: https://developer.safaricom.co.ke/
- Safaricom Support: support@safaricom.co.ke

### Internal Support
- Check error logs in Frappe
- Review transaction logs
- Contact development team

---

**Document Version:** 1.0  
**Last Updated:** 2024

