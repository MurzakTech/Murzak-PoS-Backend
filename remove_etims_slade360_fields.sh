#!/bin/bash
# Script to remove eTims Slade360 ID Mapping custom fields
# This fixes the error: "No module named 'frappe.core.doctype.etims_slade360_id_mapping'"
# when using techsavanna_pos without savanna_pos eTims integration

set -e  # Exit on error

SITE_NAME="$1"

if [ -z "$SITE_NAME" ]; then
    echo "Usage: $0 <site-name>"
    echo "Example: $0 production-site.example.com"
    exit 1
fi

echo "=========================================="
echo "Removing eTims Slade360 ID Mapping Custom Fields"
echo "Site: $SITE_NAME"
echo "=========================================="
echo ""

# Step 1: Delete Custom Fields
echo "Step 1: Deleting Custom Fields referencing 'eTims Slade360 ID Mapping'..."
bench --site "$SITE_NAME" execute frappe.db.sql --args "['DELETE FROM \`tabCustom Field\` WHERE options=%s',('eTims Slade360 ID Mapping',)]"
echo "✓ Custom Fields deleted"
echo ""

# Step 1b: Remove savanna_pos from installed apps (if present)
echo "Step 1b: Removing savanna_pos from installed apps list (if present)..."
bench --site "$SITE_NAME" execute frappe.db.sql --args "['DELETE FROM \`tabInstalled Application\` WHERE app_name=%s',('savanna_pos',)]" 2>/dev/null || true
echo "✓ Removed savanna_pos from installed apps"
echo ""

# Step 1c: Remove Savanna POS module definition (if present)
echo "Step 1c: Removing Savanna POS module definition (if present)..."
bench --site "$SITE_NAME" execute frappe.db.sql --args "['DELETE FROM \`tabModule Def\` WHERE app_name=%s',('savanna_pos',)]" 2>/dev/null || true
echo "✓ Removed Savanna POS module definition"
echo ""

# Step 2: Commit transaction
echo "Step 2: Committing transaction..."
bench --site "$SITE_NAME" execute frappe.db.commit
echo "✓ Transaction committed"
echo ""

# Step 3: Run migrate
echo "Step 3: Running migrate to sync schema..."
bench --site "$SITE_NAME" migrate
echo "✓ Migration completed"
echo ""

# Step 4: Clear cache
echo "Step 4: Clearing cache..."
bench --site "$SITE_NAME" clear-cache
echo "✓ Cache cleared"
echo ""

# Step 5: Verify fix
echo "Step 5: Verifying fix..."
RESULT=$(bench --site "$SITE_NAME" execute frappe.get_all --kwargs "{'doctype':'Custom Field','filters':{'options':'eTims Slade360 ID Mapping'},'fields':['name'] }")

if [ "$RESULT" = "[]" ]; then
    echo "✓ SUCCESS: All eTims Slade360 ID Mapping custom fields have been removed"
else
    echo "⚠ WARNING: Some custom fields still exist: $RESULT"
    exit 1
fi

echo ""
echo "=========================================="
echo "Fix applied successfully!"
echo "You can now use techsavanna_pos.api.product_seeding.create_seed_item"
echo "without eTims Slade360 ID Mapping errors."
echo "=========================================="

