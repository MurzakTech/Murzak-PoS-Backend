# TechSavanna POS - Standalone Deployment Guide

## 🎉 Status: 100% Standalone App

`techsavanna_pos` is now completely independent of `savanna_pos` and can be deployed without it.

---

## Quick Deployment Checklist

### On Your Server (Without savanna_pos):

#### 1. Deploy the Code
```bash
cd /path/to/frappe-bench/apps/techsavanna_pos
git pull origin main  # or your branch
```

#### 2. Run the eTims Cleanup Script
```bash
cd /path/to/frappe-bench
chmod +x apps/techsavanna_pos/remove_etims_slade360_fields.sh
./apps/techsavanna_pos/remove_etims_slade360_fields.sh your-site-name
```

**What it does:**
- Removes eTims Slade360 ID Mapping custom fields from Item, Customer, Supplier, Currency, Mode of Payment
- Runs migrate to sync schema
- Clears cache
- Verifies cleanup was successful

#### 3. Install/Update the App
```bash
# If techsavanna_pos is already installed
bench --site your-site-name migrate
bench --site your-site-name clear-cache

# If installing fresh
bench --site your-site-name install-app techsavanna_pos
```

#### 4. Restart Services
```bash
bench restart

# Or if using supervisor
sudo supervisorctl restart all

# Or if using systemd
sudo systemctl restart frappe-bench-web frappe-bench-workers
```

#### 5. Verify Installation
```bash
# Test inventory discounts
bench --site your-site-name execute techsavanna_pos.api.inventory_api.bulk_get_inventory_discounts \
  --kwargs '{"company":"Your Company Name"}' --args '[[]]'

# Should return: {"success": true, "data": [], "message": "Discount lookup completed"}
```

---

## What Was Fixed

### Critical Dependencies Removed (6 Issues)

1. **eTims Slade360 ID Mapping** - Database custom fields removed
2. **Inventory Discount Rule** - Copied to techsavanna_pos, imports updated
3. **Onboarding API** - All imports redirected to techsavanna_pos
4. **Auth APIs** - Verification, warehouse, and POS profile imports fixed
5. **Product API** - Global seeding made optional with fallback
6. **Documentation** - All docstring examples updated

### Files Modified

**Core API Files:**
- `api/inventory_api.py`
- `api/sales_api.py`
- `api/onboarding_api.py`
- `api/auth_api.py`
- `api/auth.py`
- `api/product_api.py`
- `api/account_provisioning_api.py`

**New DocTypes:**
- `techsavanna_pos/doctype/inventory_discount_rule/` (complete DocType with controller)

**Scripts:**
- `remove_etims_slade360_fields.sh` (cleanup script for eTims fields)

---

## Testing Your Deployment

### Test Key Endpoints

```bash
# 1. Product Seeding
curl -X POST https://your-domain.com/api/method/techsavanna_pos.api.product_seeding.create_seed_item \
  -H "Content-Type: application/json" \
  -H "Authorization: token YOUR_API_KEY:YOUR_SECRET" \
  -d '{
    "company": "Your Company",
    "price_list": "Standard Selling",
    "items": [{
      "item_code": "TEST-001",
      "item_name": "Test Product",
      "item_price": 100,
      "item_group": "All Item Groups",
      "uom": "Nos"
    }]
  }'

# 2. Inventory Discounts
curl -X POST https://your-domain.com/api/method/techsavanna_pos.api.inventory_api.bulk_get_inventory_discounts \
  -H "Content-Type: application/json" \
  -H "Authorization: token YOUR_API_KEY:YOUR_SECRET" \
  -d '{
    "company": "Your Company",
    "items": []
  }'

# 3. User Registration (if using verification)
curl -X POST https://your-domain.com/api/method/techsavanna_pos.api.auth_api.register_user \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "first_name": "Test",
    "last_name": "User",
    "password": "SecurePass123!",
    "require_email_verification": false
  }'
```

### Expected Results

All endpoints should return success responses without any `savanna_pos` import errors.

---

## Troubleshooting

### Error: "No module named 'savanna_pos'"

**Cause:** Old cache or Python bytecode still references savanna_pos

**Fix:**
```bash
# Clear cache
bench --site your-site-name clear-cache

# Remove Python cache
find apps/techsavanna_pos -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Restart
bench restart
```

### Error: "eTims Slade360 ID Mapping not found"

**Cause:** Cleanup script not run or failed

**Fix:**
```bash
# Run cleanup script again
./apps/techsavanna_pos/remove_etims_slade360_fields.sh your-site-name

# Or manual cleanup
bench --site your-site-name execute frappe.db.sql \
  --args "['DELETE FROM \`tabCustom Field\` WHERE options=%s',('eTims Slade360 ID Mapping',)]"
bench --site your-site-name execute frappe.db.commit
bench --site your-site-name migrate
bench --site your-site-name clear-cache
```

### Error: "Inventory Discount Rule DocType not found"

**Cause:** Migration not run after code update

**Fix:**
```bash
bench --site your-site-name migrate
bench --site your-site-name clear-cache
```

---

## Rollback Plan

If you need to revert (not recommended):

```bash
# 1. Reinstall savanna_pos
bench --site your-site-name install-app savanna_pos

# 2. Run migrate
bench --site your-site-name migrate

# 3. Restart
bench restart
```

---

## Support

For issues or questions:
1. Check `STANDALONE_FIXES.md` for detailed fix history
2. Review error logs: `bench --site your-site-name logs`
3. Check Frappe error log: Site → Error Log (in UI)

---

## Maintenance Notes

### When Updating techsavanna_pos

```bash
cd /path/to/frappe-bench/apps/techsavanna_pos
git pull origin main
cd /path/to/frappe-bench
bench --site your-site-name migrate
bench --site your-site-name clear-cache
bench restart
```

### Monitoring

No special monitoring required. All endpoints work exactly as before, just without savanna_pos dependency.

---

## Performance Impact

**None.** The changes only affect:
- Import paths (same functions, different locations)
- Optional feature (global product seeding) has graceful fallback
- All other functionality identical

---

## Security Considerations

- Email/phone verification still works (now uses techsavanna_pos.api.verification_api)
- User authentication unchanged
- API key generation unchanged
- Role assignment unchanged

---

## Production Readiness

✅ **Ready for Production**

All changes:
- Maintain backward compatibility
- Have graceful fallbacks
- Are thoroughly tested
- Preserve all functionality

---

**Last Updated:** 2026-01-07  
**App Version:** techsavanna_pos (standalone)  
**Tested On:** Frappe v15+, ERPNext v15+

