# MPESA Payment Integration - User Guide

## Overview
This guide helps end users understand how to use MPESA payments in the POS system for processing customer payments and managing transactions.

---

## Table of Contents
1. [Getting Started](#getting-started)
2. [Making Payments](#making-payments)
3. [Checking Payment Status](#checking-payment-status)
4. [Viewing Transaction History](#viewing-transaction-history)
5. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Prerequisites
- MPESA settings must be configured by your administrator
- You must have access to the POS system
- Customer phone number must be in MSISDN format (254712345678)

### Understanding MPESA Payment Types

**STK Push (Customer to Business):**
- Customer initiates payment from their phone
- Most common payment method
- Customer enters PIN on their phone
- Payment is confirmed automatically

**B2C (Business to Customer):**
- Business sends money to customer
- Used for refunds or payouts
- Requires initiator credentials

**B2B (Business to Business):**
- Business sends money to another business
- Used for supplier payments
- Requires initiator credentials

---

## Making Payments

### From POS Invoice

1. **Create POS Invoice:**
   - Create invoice as usual
   - Add items and calculate total

2. **Select MPESA Payment:**
   - In payment methods, select "MPESA"
   - Enter customer phone number (254712345678)
   - Click "Pay with MPESA"

3. **Customer Completes Payment:**
   - Customer receives STK push on their phone
   - Customer enters MPESA PIN
   - Payment is confirmed automatically

4. **Verify Payment:**
   - Check invoice status
   - Payment entry is created automatically
   - Invoice outstanding amount is updated

### From Sales Invoice

1. **Create Sales Invoice:**
   - Create invoice as usual
   - Submit invoice

2. **Request MPESA Payment:**
   - Use API endpoint to initiate payment
   - Provide customer phone number
   - Customer receives STK push

3. **Payment Confirmation:**
   - Payment is processed automatically
   - Invoice is updated when payment succeeds

### Using API

**Example: Initiate Payment**
```bash
POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment
{
  "company": "Your Company",
  "phone_number": "254712345678",
  "amount": 100.00,
  "reference": "INV-001",
  "invoice_type": "Sales Invoice",
  "invoice_name": "INV-001"
}
```

---

## Checking Payment Status

### Via Transaction Log

1. **Navigate to MPESA Transaction Log:**
   - Go to MPESA Transaction Log list
   - Filter by invoice reference or transaction ID
   - View transaction status

2. **Transaction Statuses:**
   - **Pending:** Payment initiated, waiting for customer
   - **Success:** Payment completed successfully
   - **Failed:** Payment failed (insufficient funds, cancelled, etc.)
   - **Cancelled:** Customer cancelled the payment

### Via API

**Check Status:**
```bash
GET /api/method/techsavanna_pos.api.mpesa_api.check_payment_status?transaction_id=MPESA-TXN-001
```

**Response:**
```json
{
  "success": true,
  "transaction": {
    "status": "Success",
    "mpesa_receipt_number": "RCT123456",
    "amount": 100.00
  }
}
```

---

## Viewing Transaction History

### In Frappe UI

1. **Navigate to MPESA Transaction Log:**
   - Search for "MPESA Transaction Log"
   - View list of all transactions

2. **Filter Transactions:**
   - Filter by status (Success, Failed, Pending)
   - Filter by transaction type (STK Push, B2C, B2B)
   - Filter by date range
   - Filter by company

3. **View Transaction Details:**
   - Click on transaction to view details
   - See request/response payloads
   - View linked payment entry
   - View linked invoice

### Via API

**Get Transaction History:**
```bash
GET /api/method/techsavanna_pos.api.mpesa_api.get_transactions?company=Your Company&status=Success&limit=50
```

**Get Single Transaction:**
```bash
GET /api/method/techsavanna_pos.api.mpesa_api.get_transaction?transaction_id=MPESA-TXN-001
```

---

## Common Scenarios

### Scenario 1: Customer Payment Successful

1. Initiate STK push
2. Customer receives prompt on phone
3. Customer enters PIN
4. Payment confirmed
5. Payment entry created
6. Invoice updated

**Result:** Invoice marked as paid, payment entry created

### Scenario 2: Customer Cancels Payment

1. Initiate STK push
2. Customer receives prompt
3. Customer cancels
4. Transaction marked as cancelled

**Result:** No payment entry created, invoice remains unpaid

### Scenario 3: Payment Timeout

1. Initiate STK push
2. Customer doesn't respond
3. Transaction times out
4. Use STK Query to check status

**Result:** Transaction marked as failed, can retry payment

### Scenario 4: Insufficient Funds

1. Initiate STK push
2. Customer enters PIN
3. MPESA returns insufficient funds error
4. Transaction marked as failed

**Result:** No payment entry, invoice remains unpaid, notify customer

---

## Troubleshooting

### Payment Not Received on Phone

**Possible Causes:**
- Invalid phone number format
- Phone number not registered with MPESA
- Network issues

**Solutions:**
- Verify phone number format: 254712345678
- Ask customer to check phone network
- Verify customer has MPESA account
- Retry payment

### Payment Completed But Invoice Not Updated

**Possible Causes:**
- Callback not received
- Payment entry creation failed
- Auto-confirm disabled

**Solutions:**
- Check transaction log status
- Manually check payment status
- Verify auto_confirm_payments is enabled
- Check error logs

### Duplicate Payment Entries

**Possible Causes:**
- Duplicate callback received
- Manual payment entry creation

**Solutions:**
- Idempotency should prevent this
- Check transaction log for duplicates
- Verify CheckoutRequestID is unique
- Contact administrator if issue persists

### Payment Status Shows Pending Forever

**Possible Causes:**
- Callback not received
- Transaction timed out
- Network issues

**Solutions:**
- Use STK Query to check status
- Check callback URL is accessible
- Verify Daraja portal for transaction status
- Contact administrator

---

## Best Practices

1. **Always Verify Phone Number:**
   - Use correct format (254712345678)
   - Confirm with customer before initiating

2. **Monitor Transactions:**
   - Check transaction log regularly
   - Verify payments are processed
   - Follow up on pending transactions

3. **Handle Errors Gracefully:**
   - Inform customers of payment status
   - Retry failed payments if appropriate
   - Keep records of all transactions

4. **Security:**
   - Never share MPESA credentials
   - Verify customer identity before payment
   - Keep transaction records secure

---

## Support

For issues or questions:
1. Check transaction log for error details
2. Review error logs in Frappe
3. Contact system administrator
4. Refer to troubleshooting section

---

**Document Version:** 1.0  
**Last Updated:** 2024

