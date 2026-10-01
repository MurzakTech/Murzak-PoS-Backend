# Troubleshooting Reports API Endpoints

## Error: "Failed to get method for command api with 'api'"

This error typically occurs when Frappe cannot properly resolve the module path. Follow these steps:

### Step 1: Verify Module Structure

Ensure the following files exist:
- `apps/techsavanna_pos/techsavanna_pos/api/__init__.py` ✅ (should exist)
- `apps/techsavanna_pos/techsavanna_pos/api/reports.py` ✅ (should exist)

### Step 2: Restart Frappe Bench

```bash
cd /path/to/frappe-bench
bench restart
```

### Step 3: Clear Cache

```bash
bench clear-cache
bench clear-website-cache
```

### Step 4: Verify Function is Accessible

Test the endpoint using curl or your API client:

```bash
# Test with GET request
curl -X GET "http://your-domain:8000/api/method/techsavanna_pos.api.reports.sales_analytics_report?company=Your%20Company" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json"

# Test with POST request
curl -X POST "http://your-domain:8000/api/method/techsavanna_pos.api.reports.sales_analytics_report" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "company": "Your Company",
    "start_date": "2024-01-01",
    "end_date": "2024-01-31"
  }'
```

### Step 5: Check Frappe Logs

Check the Frappe logs for more detailed error information:

```bash
bench --site your-site-name logs
```

### Step 6: Verify Function is Whitelisted

You can verify the function is properly whitelisted by checking in Frappe console:

```python
# In Frappe console (bench console)
import frappe
from techsavanna_pos.api import reports

# Check if function exists
print(hasattr(reports, 'sales_analytics_report'))

# Check if it's whitelisted
from frappe import is_whitelisted
func = getattr(reports, 'sales_analytics_report')
print(is_whitelisted(func))
```

### Step 7: Alternative - Use Direct Module Path

If the issue persists, you can try accessing the function directly in Python to test:

```python
# In Frappe console
from techsavanna_pos.api.reports import sales_analytics_report

result = sales_analytics_report(
    company="Your Company",
    start_date="2024-01-01",
    end_date="2024-01-31"
)
print(result)
```

### Common Issues and Solutions

#### Issue 1: Module Not Found
**Error:** `ModuleNotFoundError: No module named 'techsavanna_pos.api.reports'`

**Solution:**
- Ensure you're in the correct bench directory
- Verify the app is installed: `bench --site your-site-name list-apps`
- Reinstall the app if needed: `bench --site your-site-name install-app techsavanna_pos`

#### Issue 2: Function Not Whitelisted
**Error:** `Method not whitelisted`

**Solution:**
- Verify the `@frappe.whitelist()` decorator is present
- Check that the function is not inside a class
- Ensure there are no syntax errors in the file

#### Issue 3: Authentication Required
**Error:** `401 Unauthorized` or `Not authenticated`

**Solution:**
- Ensure you're passing a valid Bearer token
- Check that the user has proper permissions
- Verify the endpoint doesn't require `allow_guest=True` if you're not authenticated

#### Issue 4: Parameter Validation Errors
**Error:** `Company is required` or similar validation errors

**Solution:**
- Ensure all required parameters are provided
- Check parameter types match expected types
- Verify date formats are `YYYY-MM-DD`

### Testing Endpoint in React

If you're testing from React and getting this error, try:

```javascript
// Make sure the URL is properly encoded
const endpoint = 'techsavanna_pos.api.reports.sales_analytics_report';
const url = `/api/method/${endpoint}`;

// Use POST instead of GET for complex parameters
const response = await fetch(url, {
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
});
```

### Still Having Issues?

If you're still experiencing issues after following these steps:

1. Check the exact error message in Frappe logs
2. Verify the endpoint name matches exactly (case-sensitive)
3. Ensure there are no syntax errors in `reports.py`
4. Try accessing a simpler endpoint first (like `inventory_summary_report`) to verify the module is accessible
5. Check if other endpoints in the same module work (like existing inventory reports)

### Quick Verification Script

Run this in Frappe console to verify everything is set up correctly:

```python
import frappe
from techsavanna_pos.api import reports

# Check module
print("Module:", reports.__name__)
print("File:", reports.__file__)

# Check functions
functions = [f for f in dir(reports) if not f.startswith('_') and callable(getattr(reports, f))]
print("Available functions:", functions)

# Check specific function
if hasattr(reports, 'sales_analytics_report'):
    func = getattr(reports, 'sales_analytics_report')
    print("Function found:", func)
    print("Is whitelisted:", frappe.is_whitelisted(func))
else:
    print("Function NOT found!")
```


