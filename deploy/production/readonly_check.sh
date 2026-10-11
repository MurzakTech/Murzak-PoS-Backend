#!/usr/bin/env bash
#
# Read-only health and settings check of the live Murzak POS server.
#
# It only LOOKS. It never changes a setting, a record, a file or a running service, and it never prints
# a password, API key, secret or the contents of site_config.json. It writes a plain-text report that is
# safe to send back to the developers.
#
# Run it as the Linux user that owns the bench (the one that runs `bench`), from anywhere:
#
#     bash readonly_check.sh                       # the usual case
#     SITE=other.site BENCH_DIR=/path/to/bench bash readonly_check.sh
#
# The report is saved to ~/murzak_production_check_<date>.txt and also shown on screen.

BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
SITE="${SITE:-api.pos.murzaktech.tech}"
REPORT="${REPORT:-$HOME/murzak_production_check_$(date +%F).txt}"

if ! command -v bench >/dev/null 2>&1; then
	echo "Cannot find the 'bench' command. Run this as the user that owns the bench, or add bench to PATH." >&2
	exit 1
fi
if [ ! -d "$BENCH_DIR/sites/$SITE" ]; then
	echo "Cannot find the site folder $BENCH_DIR/sites/$SITE." >&2
	echo "Set SITE and BENCH_DIR, for example: SITE=my.site BENCH_DIR=/home/me/frappe-bench bash $0" >&2
	exit 1
fi
cd "$BENCH_DIR" || exit 1

# A SELECT only. Anything that fails (a table this version does not have) is reported, never fatal.
sql() {
	echo "  $1"
	bench --site "$SITE" mariadb -B -e "$2" 2>&1 | sed 's/^/      /' | head -60
	echo
}
heading() { printf '\n==== %s ====\n' "$1"; }
run() { # a read-only shell command, output indented
	echo "  \$ $*"
	"$@" 2>&1 | sed 's/^/      /' | head -40
	echo
}

{
	echo "Murzak POS production check, $(date '+%F %T %Z')"
	echo "Site: $SITE   Bench: $BENCH_DIR   Run by: $(whoami) on $(hostname)"

	heading "1. Versions"
	run bench version
	for app in apps/*/; do
		name="$(basename "$app")"
		if [ -d "$app/.git" ]; then
			echo "  $name: commit $(git -C "$app" rev-parse --short HEAD 2>/dev/null), branch $(git -C "$app" rev-parse --abbrev-ref HEAD 2>/dev/null), files changed locally: $(git -C "$app" status --porcelain --untracked-files=no 2>/dev/null | wc -l)"
		fi
	done
	echo
	run ./env/bin/python --version
	run node --version
	run mariadb --version
	run redis-server --version
	run nginx -v
	run lsb_release -d

	heading "2. Installed on the site"
	run bench --site "$SITE" list-apps
	sql "Installed Application records" "SELECT app_name FROM \`tabInstalled Application\`"
	sql "Recorded patches for this app (the name tells which app name is in use)" "SELECT SUBSTRING_INDEX(patch,'.',1) AS app, COUNT(*) AS patches FROM \`tabPatch Log\` GROUP BY app ORDER BY patches DESC LIMIT 10"

	heading "3. Settings that decide how sales are made"
	sql "The till's default invoice kind (POS Settings, invoice_type)" "SELECT IFNULL((SELECT value FROM tabSingles WHERE doctype='POS Settings' AND field='invoice_type'), '(never saved, so ERPNext default applies)') AS invoice_type"
	sql "Stock Settings that matter for batches, serials and reordering" "SELECT field, value FROM tabSingles WHERE doctype='Stock Settings' AND field IN ('use_serial_batch_fields','auto_indent','pick_serial_and_batch_based_on')"
	sql "Does POS Invoice have an update_stock field? (1 = yes, 0 = no)" "SELECT (SELECT COUNT(*) FROM tabDocField WHERE parent='POS Invoice' AND fieldname='update_stock') AS standard_field, (SELECT COUNT(*) FROM \`tabCustom Field\` WHERE dt='POS Invoice' AND fieldname='update_stock') AS custom_field"
	sql "Businesses" "SELECT name, country, default_currency FROM tabCompany"
	sql "Outgoing email accounts enabled (needed for alerts)" "SELECT COUNT(*) AS enabled_outgoing_accounts FROM \`tabEmail Account\` WHERE enable_outgoing=1"

	heading "4. eTIMS (kenya-compliance) set-up"
	sql "Environment (Sandbox or Production)" "SELECT value FROM tabSingles WHERE doctype='Navari KRA eTims Environment Identifier' AND field='environment'"
	sql "eTIMS settings records (no keys or secrets are selected)" "SELECT company, bhfid, vendor, env, sandbox, is_active FROM \`tabNavari KRA eTims Settings\`"
	sql "Accounting dimensions (the Branch dimension adds a branch field to invoices)" "SELECT name, document_type, disabled FROM \`tabAccounting Dimension\`"
	sql "Branch records (kenya-compliance needs the name to be 00 or 01)" "SELECT name FROM tabBranch"

	heading "5. Which kind of invoice each business actually uses (last 60 days, submitted)"
	sql "POS Invoices" "SELECT company, COUNT(*) AS invoices FROM \`tabPOS Invoice\` WHERE docstatus=1 AND posting_date >= CURDATE() - INTERVAL 60 DAY GROUP BY company"
	sql "Sales Invoices made at the till (is_pos=1, not merged)" "SELECT company, COUNT(*) AS invoices FROM \`tabSales Invoice\` WHERE docstatus=1 AND is_pos=1 AND IFNULL(is_consolidated,0)=0 AND posting_date >= CURDATE() - INTERVAL 60 DAY GROUP BY company"
	sql "Sales Invoices made by merging POS Invoices at shift close" "SELECT company, COUNT(*) AS invoices FROM \`tabSales Invoice\` WHERE docstatus=1 AND IFNULL(is_consolidated,0)=1 AND posting_date >= CURDATE() - INTERVAL 60 DAY GROUP BY company"
	sql "Other Sales Invoices (not made at the till)" "SELECT company, COUNT(*) AS invoices FROM \`tabSales Invoice\` WHERE docstatus=1 AND IFNULL(is_pos,0)=0 AND IFNULL(is_consolidated,0)=0 AND posting_date >= CURDATE() - INTERVAL 60 DAY GROUP BY company"
	sql "Sales Invoices sent to KRA or not (1 = sent)" "SELECT company, custom_successfully_submitted AS sent_to_kra, COUNT(*) AS invoices FROM \`tabSales Invoice\` WHERE docstatus=1 AND posting_date >= CURDATE() - INTERVAL 60 DAY GROUP BY company, custom_successfully_submitted"

	heading "6. Is the server healthy?"
	run bench --site "$SITE" doctor
	echo "  Background programs running (names only, no arguments):"
	ps -eo comm | sort | uniq -c | grep -E 'gunicorn|node|redis|nginx|supervisor|mariadb|mysqld|python' | sed 's/^/      /'
	echo
	run df -h /
	run free -h
	sql "Errors logged in the last 24 hours (title and count)" "SELECT method AS title, COUNT(*) AS times FROM \`tabError Log\` WHERE creation >= NOW() - INTERVAL 1 DAY GROUP BY method ORDER BY times DESC LIMIT 10"
	echo "  Newest backups (names and sizes only):"
	ls -lt "sites/$SITE/private/backups" 2>/dev/null | head -6 | sed 's/^/      /'
	echo

	echo "End of report. Nothing was changed on this server."
} 2>&1 | tee "$REPORT"

echo
echo "Report saved to: $REPORT"
