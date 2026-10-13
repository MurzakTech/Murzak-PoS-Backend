#!/usr/bin/env bash
#
# Read-only check that the WS6 to WS8 release (recipes, payment matching, Swahili, approvals, print formats,
# sales people) arrived on the live server and is set up.
#
# It only LOOKS. It never changes a setting, a record, a file or a running service, and it never prints
# a password, API key, secret or the contents of site_config.json. It writes a plain-text report that is
# safe to send back to the developers.
#
# Run it as the Linux user that owns the bench, from anywhere:
#
#     bash post_deploy_check.sh
#     SITE=other.site BENCH_DIR=/path/to/bench bash post_deploy_check.sh
#
# The report is saved to ~/murzak_post_deploy_check_<date>.txt and also shown on screen.

BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
SITE="${SITE:-api.pos.murzaktech.tech}"
EXPECTED_COMMIT="${EXPECTED_COMMIT:-d0f5b8e}"
REPORT="${REPORT:-$HOME/murzak_post_deploy_check_$(date +%F).txt}"

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
	echo "Murzak POS post-deploy check (WS6 to WS8), $(date '+%F %T %Z')"
	echo "Site: $SITE   Bench: $BENCH_DIR   Run by: $(whoami) on $(hostname)"

	heading "1. Is the new code on the server?"
	APP="apps/murzak_pos"
	if [ -d "$APP/.git" ]; then
		echo "  murzak_pos is at commit $(git -C "$APP" rev-parse --short HEAD 2>/dev/null) on branch $(git -C "$APP" rev-parse --abbrev-ref HEAD 2>/dev/null)"
		if git -C "$APP" merge-base --is-ancestor "$EXPECTED_COMMIT" HEAD 2>/dev/null; then
			echo "  OK: the release commit $EXPECTED_COMMIT is part of what is installed."
		else
			echo "  PROBLEM: the release commit $EXPECTED_COMMIT is not part of what is installed."
		fi
	else
		echo "  PROBLEM: $APP is not a git checkout, so the version cannot be read."
	fi
	for f in murzak_pos/api/approvals_api.py murzak_pos/api/payment_matching_api.py murzak_pos/api/print_api.py \
		murzak_pos/api/recipes_api.py murzak_pos/api/sales_person_api.py murzak_pos/api/language_api.py \
		murzak_pos/translations/sw.csv; do
		if [ -f "$APP/$f" ]; then echo "  present: $f"; else echo "  MISSING: $f"; fi
	done
	echo "  Swahili file lines: $(wc -l < "$APP/murzak_pos/translations/sw.csv" 2>/dev/null)"
	echo

	heading "2. Did the update (migrate) create the new records?"
	sql "New record types (expect 2 rows: Approval Request, Approval Settings)" "SELECT name FROM tabDocType WHERE name IN ('Approval Request','Approval Settings') ORDER BY name"
	sql "Payment matching switch on the alert settings (expect 1)" "SELECT COUNT(*) AS column_present FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'tabBusiness Alert Settings' AND column_name = 'alert_payment_matching'"
	sql "Sales Person fields (expect custom_company and custom_user)" "SELECT fieldname FROM \`tabCustom Field\` WHERE dt='Sales Person' AND fieldname IN ('custom_company','custom_user')"
	sql "Desk report (expect 1 row)" "SELECT name FROM tabReport WHERE name='Murzak Payment Matching Exceptions'"
	echo "  Hooks run after every update (expect the print formats and Swahili among them):"
	run bench --site "$SITE" execute frappe.get_hooks --kwargs "{'hook': 'after_migrate'}"

	heading "3. Print formats and letter heads"
	sql "Print formats (expect 5, none disabled)" "SELECT name, doc_type, disabled FROM \`tabPrint Format\` WHERE name LIKE 'Murzak%' ORDER BY name"
	sql "Letter heads. Exactly one should be the default, and it should be the empty 'No Letter Head', so one business's header never prints for another" "SELECT name, is_default, disabled FROM \`tabLetter Head\` ORDER BY is_default DESC, name"
	sql "Which business uses which letter head" "SELECT name AS company, default_letter_head FROM tabCompany"
	echo "  QR code and Jinja helper (draws a test QR, changes nothing; expect a long text starting with data:image/png):"
	bench --site "$SITE" execute murzak_pos.api.print_api.murzak_qr --args "['test']" 2>&1 | cut -c1-60 | sed 's/^/      /'
	echo

	heading "4. Swahili"
	sql "Languages switched on (expect en and sw)" "SELECT name, enabled FROM tabLanguage WHERE name IN ('en','sw')"
	sql "How many users have chosen each language" "SELECT IFNULL(language,'(not chosen)') AS language, COUNT(*) AS users FROM tabUser WHERE enabled=1 GROUP BY language"

	heading "5. Approvals, sales people, payment matching, recipes (what has been set up so far)"
	sql "Approval limits set (0 means off; no rows means nobody has set any yet, which is normal)" "SELECT company, refund_limit, purchase_order_limit FROM \`tabApproval Settings\`"
	sql "Approval requests by status" "SELECT company, kind, status, COUNT(*) AS requests FROM \`tabApproval Request\` GROUP BY company, kind, status"
	sql "Sales people by business" "SELECT custom_company AS company, COUNT(*) AS sales_people, SUM(enabled) AS active FROM \`tabSales Person\` WHERE is_group=0 GROUP BY custom_company"
	sql "Clearing accounts made for payment matching (none yet is normal until set up per business)" "SELECT company, account_name FROM tabAccount WHERE account_name IN ('M-Pesa Clearing','Card Clearing','PayPal Clearing')"
	sql "Statement bank accounts and imported statement lines" "SELECT ba.name AS bank_account, COUNT(bt.name) AS statement_lines FROM \`tabBank Account\` ba LEFT JOIN \`tabBank Transaction\` bt ON bt.bank_account = ba.name AND bt.docstatus = 1 WHERE ba.bank = 'M-Pesa' GROUP BY ba.name"
	sql "Dishes with a recipe (Product Bundles)" "SELECT COUNT(*) AS recipes FROM \`tabProduct Bundle\`"
	sql "Wastage entries" "SELECT e.company, COUNT(*) AS entries FROM \`tabStock Entry\` e JOIN \`tabStock Entry Type\` t ON t.name = e.stock_entry_type WHERE t.name='Wastage' AND e.docstatus=1 GROUP BY e.company"

	heading "6. Returns made before the VAT fix"
	echo "  Returns recorded before this release carried no VAT lines. This lists returns whose original sale had VAT but"
	echo "  that reversed none. They are not corrected automatically; the owner and accountant should decide what to do."
	sql "Returns with no VAT against a sale that had VAT (count and value, by business)" "SELECT r.company, COUNT(*) AS returns_without_vat, ROUND(SUM(-r.grand_total),2) AS value FROM \`tabSales Invoice\` r JOIN \`tabSales Invoice\` o ON o.name = r.return_against WHERE r.is_return=1 AND r.docstatus=1 AND IFNULL(r.total_taxes_and_charges,0)=0 AND IFNULL(o.total_taxes_and_charges,0)<>0 GROUP BY r.company"

	heading "7. Any trouble since the release?"
	sql "Errors logged in the last 24 hours (title and count)" "SELECT method AS title, COUNT(*) AS times FROM \`tabError Log\` WHERE creation >= NOW() - INTERVAL 1 DAY GROUP BY method ORDER BY times DESC LIMIT 15"
	run bench --site "$SITE" doctor

	echo "End of report. Nothing was changed on this server."
} 2>&1 | tee "$REPORT"

echo
echo "Report saved to: $REPORT"
