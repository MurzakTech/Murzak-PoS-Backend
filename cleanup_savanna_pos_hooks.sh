#!/bin/bash
# Script to remove savanna_pos hooks and references from site database
# Run this on your server where savanna_pos is NOT installed but hooks might still be registered

set -e

SITE_NAME="$1"

if [ -z "$SITE_NAME" ]; then
    echo "Usage: $0 <site-name>"
    echo "Example: $0 production-site.example.com"
    exit 1
fi

echo "=========================================="
echo "Cleaning up savanna_pos hooks and references"
echo "Site: $SITE_NAME"
echo "=========================================="
echo ""

# Step 1: Check if savanna_pos is in installed apps
echo "Step 1: Checking installed apps..."
INSTALLED_APPS=$(bench --site "$SITE_NAME" list-apps 2>/dev/null || echo "")
echo "Currently installed apps:"
echo "$INSTALLED_APPS"
echo ""

# Step 2: Remove savanna_pos from installed apps if present
echo "Step 2: Removing savanna_pos from installed apps list..."
bench --site "$SITE_NAME" execute frappe.db.sql --args "['DELETE FROM \`tabInstalled Application\` WHERE app_name=%s',('savanna_pos',)]" 2>/dev/null || true
bench --site "$SITE_NAME" execute frappe.db.commit
echo "✓ Removed savanna_pos from installed apps"
echo ""

# Step 3: Remove savanna_pos from Module Def
echo "Step 3: Removing Savanna POS module definition..."
bench --site "$SITE_NAME" execute frappe.db.sql --args "['DELETE FROM \`tabModule Def\` WHERE app_name=%s',('savanna_pos',)]" 2>/dev/null || true
bench --site "$SITE_NAME" execute frappe.db.commit
echo "✓ Removed Savanna POS module definition"
echo ""

# Step 4: Remove eTims Slade360 custom fields (if not already done)
echo "Step 4: Removing eTims Slade360 ID Mapping custom fields..."
bench --site "$SITE_NAME" execute frappe.db.sql --args "['DELETE FROM \`tabCustom Field\` WHERE options=%s',('eTims Slade360 ID Mapping',)]" 2>/dev/null || true
bench --site "$SITE_NAME" execute frappe.db.commit
echo "✓ Removed eTims Slade360 custom fields"
echo ""

# Step 5: Clear all caches
echo "Step 5: Clearing all caches..."
bench --site "$SITE_NAME" clear-cache
echo "✓ Cache cleared"
echo ""

# Step 6: Run migrate to sync schema
echo "Step 6: Running migrate..."
bench --site "$SITE_NAME" migrate
echo "✓ Migration completed"
echo ""

# Step 7: Clear cache again after migrate
echo "Step 7: Final cache clear..."
bench --site "$SITE_NAME" clear-cache
echo "✓ Final cache cleared"
echo ""

# Step 8: Verify cleanup
echo "Step 8: Verifying cleanup..."
REMAINING=$(bench --site "$SITE_NAME" execute frappe.get_all --kwargs "{'doctype':'Custom Field','filters':{'options':'eTims Slade360 ID Mapping'},'fields':['name'] }")
if [ "$REMAINING" = "[]" ]; then
    echo "✓ All eTims custom fields removed"
else
    echo "⚠ Some custom fields still exist: $REMAINING"
fi

INSTALLED_APPS_AFTER=$(bench --site "$SITE_NAME" execute frappe.get_installed_apps)
echo "Installed apps after cleanup: $INSTALLED_APPS_AFTER"
echo ""

echo "=========================================="
echo "Cleanup completed!"
echo ""
echo "Now restart your services:"
echo "  bench restart"
echo "  # or: sudo supervisorctl restart all"
echo "  # or: sudo systemctl restart frappe-bench-web frappe-bench-workers"
echo ""
echo "Then test the create_seed_item endpoint."
echo "=========================================="

