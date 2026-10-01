# MPESA Integration - Production Deployment Checklist

## Pre-Deployment

### Phase 1: Preparation
- [ ] Review implementation plan
- [ ] Verify all phases are complete
- [ ] Review test results
- [ ] Prepare deployment plan
- [ ] Schedule maintenance window

### Phase 2: Daraja Account Setup
- [ ] Register with Safaricom Developer Portal
- [ ] Create sandbox app and test
- [ ] Submit for production approval
- [ ] Receive production credentials
- [ ] Get production shortcode
- [ ] Verify callback URLs in Daraja portal

### Phase 3: System Preparation
- [ ] Verify Frappe/ERPNext version compatibility
- [ ] Ensure HTTPS is enabled
- [ ] Verify SSL certificate is valid
- [ ] Test callback URL accessibility
- [ ] Create payment account
- [ ] Set up monitoring tools

---

## Deployment

### Step 1: Code Deployment
- [ ] Pull latest code from repository
- [ ] Verify all files are present
- [ ] Check file permissions
- [ ] Verify no syntax errors

### Step 2: Database Migration
- [ ] Backup database
- [ ] Run migrations: `bench --site [site] migrate`
- [ ] Verify DocTypes created
- [ ] Verify permissions set
- [ ] Test DocType creation

### Step 3: Configuration
- [ ] Create MPESA Settings for each company
- [ ] Configure production credentials
- [ ] Set callback URLs
- [ ] Verify payment account
- [ ] Test Mode of Payment creation

### Step 4: Testing
- [ ] Test OAuth token retrieval
- [ ] Test STK Push initiation
- [ ] Test callback reception
- [ ] Test payment entry creation
- [ ] Test invoice updates
- [ ] Verify idempotency

### Step 5: Integration
- [ ] Verify POS Profile has MPESA
- [ ] Test POS Invoice payment
- [ ] Test Sales Invoice payment
- [ ] Verify all integrations work

---

## Post-Deployment

### Monitoring Setup
- [ ] Configure transaction monitoring
- [ ] Set up error alerts
- [ ] Configure performance monitoring
- [ ] Set up callback monitoring
- [ ] Test alert notifications

### Documentation
- [ ] Update API documentation
- [ ] Create user training materials
- [ ] Document production URLs
- [ ] Create runbook for operations

### Training
- [ ] Train administrators
- [ ] Train end users
- [ ] Create FAQ document
- [ ] Set up support channels

---

## Rollback Plan

If issues occur:

1. **Deactivate MPESA:**
   ```bash
   # Via API or UI
   Deactivate MPESA Settings for affected companies
   ```

2. **Disable Payment Method:**
   - Remove MPESA from POS Profiles
   - Disable Mode of Payment

3. **Restore Database:**
   ```bash
   bench --site [site] restore [backup-file]
   ```

---

## Success Criteria

- [ ] All DocTypes migrated successfully
- [ ] MPESA Settings configured for all companies
- [ ] Test transaction successful
- [ ] Callbacks received and processed
- [ ] Payment entries created correctly
- [ ] Invoices updated correctly
- [ ] Monitoring configured and working
- [ ] Documentation complete
- [ ] Team trained

---

**Checklist Version:** 1.0  
**Last Updated:** 2024

