# MPESA Integration - Quick Reference Guide

## Quick Start

### 1. Register MPESA Settings

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

### 2. Initiate STK Push Payment

```bash
POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment
{
  "company": "Your Company",
  "phone_number": "254712345678",
  "amount": 100.00,
  "reference": "INV-001"
}
```

### 3. Check Payment Status

```bash
GET /api/method/techsavanna_pos.api.mpesa_api.check_payment_status?transaction_id=MPESA-TXN-001
```

### 4. Get Transaction History

```bash
GET /api/method/techsavanna_pos.api.mpesa_api.get_transactions?company=Your Company&status=Success&limit=50
```

---

## API Endpoints Summary

### Settings Management
- `POST /api/method/techsavanna_pos.api.mpesa_api.register_mpesa_settings` - Register/update settings
- `GET /api/method/techsavanna_pos.api.mpesa_api.get_mpesa_settings?company=Company` - Get settings
- `PUT /api/method/techsavanna_pos.api.mpesa_api.update_mpesa_settings` - Update settings
- `DELETE /api/method/techsavanna_pos.api.mpesa_api.deactivate_mpesa_settings?company=Company` - Deactivate

### Payment Processing
- `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment` - STK Push
- `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_b2c_payment` - B2C payment
- `POST /api/method/techsavanna_pos.api.mpesa_api.initiate_b2b_payment` - B2B payment
- `GET /api/method/techsavanna_pos.api.mpesa_api.check_payment_status` - Check status

### Transaction Management
- `GET /api/method/techsavanna_pos.api.mpesa_api.get_transactions` - Get history
- `GET /api/method/techsavanna_pos.api.mpesa_api.get_transaction?transaction_id=ID` - Get details

### Invoice Integration
- `POST /api/method/techsavanna_pos.overrides.mpesa_integration.initiate_mpesa_payment_from_invoice` - Pay from invoice

### Callbacks (Called by Daraja)
- `POST /api/method/techsavanna_pos.api.mpesa_api.stk_callback` - STK callback
- `POST /api/method/techsavanna_pos.api.mpesa_api.b2c_result_callback` - B2C result
- `POST /api/method/techsavanna_pos.api.mpesa_api.b2c_timeout_callback` - B2C timeout
- `POST /api/method/techsavanna_pos.api.mpesa_api.b2b_result_callback` - B2B result
- `POST /api/method/techsavanna_pos.api.mpesa_api.b2b_timeout_callback` - B2B timeout

---

## Common Daraja Result Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | Success | Process payment |
| 1032 | User cancelled | Mark as cancelled |
| 2001 | Insufficient funds | Mark as failed |
| 17 | Invalid MSISDN | Mark as failed |
| 1037 | Timeout | Query status |

---

## File Structure

```
techsavanna_pos/
├── api/
│   ├── mpesa_api.py              # API endpoints
│   ├── mpesa_client.py           # Daraja API client
│   ├── mpesa_constants.py        # Constants and codes
│   ├── test_mpesa_api.py         # API tests
│   └── test_mpesa_client.py      # Client tests
├── doctype/
│   ├── mpesa_settings/           # Settings DocType
│   └── mpesa_transaction_log/   # Transaction Log DocType
├── overrides/
│   └── mpesa_integration.py      # Invoice integration
└── docs/
    ├── MPESA_IMPLEMENTATION_PLAN.md
    ├── MPESA_TESTING_GUIDE.md
    └── MPESA_QUICK_REFERENCE.md
```

---

## Important Notes

1. **Callbacks:** All callback endpoints have `allow_guest=True` (Daraja calls directly)
2. **Idempotency:** Use `CheckoutRequestID` (STK) or `ConversationID` (B2C/B2B) for idempotency
3. **No Signatures:** Daraja doesn't provide HMAC signatures for callbacks
4. **Token Caching:** OAuth tokens are cached and auto-refreshed
5. **Company Isolation:** All data is company-scoped for multitenancy

---

**Version:** 1.0  
**Last Updated:** 2024

