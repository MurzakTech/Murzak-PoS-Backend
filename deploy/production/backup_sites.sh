#!/usr/bin/env bash
#
# Backs up each live site on its own, one after the other, and says plainly which ones worked.
#
# Why not `bench setup backups`? It backs up "all" sites in a single command, and a site with a broken
# database (newsite.murzaktech.tech has one) can stop the whole run, so the sites after it are never
# backed up. Here every site is tried separately, so one failure never stops the others.
#
# The backups are written by bench to sites/<site>/private/backups/. How many are kept is set per site
# by "Number of Backups" in System Settings (Frappe's default is only 3).
#
# Run it by hand to test:      bash ~/backup_sites.sh
# Or from cron (see docs/BACKUPS.md). Settings can be changed from outside:
#     SITES="a.site b.site" BENCH=/full/path/to/bench BENCH_DIR=/path/to/frappe-bench bash backup_sites.sh

BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
BENCH="${BENCH:-bench}"
# cron starts programs with almost no PATH, so add the usual places bench lives
export PATH="$PATH:$HOME/.local/bin:/usr/local/bin:/usr/bin"
# The live POS site gets its uploaded files too; the others get the database only
FILES_FOR="${FILES_FOR:-api.pos.murzaktech.tech}"
SITES="${SITES:-api.pos.murzaktech.tech erp.murzaktech.tech erp.mystylecarhire.com matatuke.murzaktech.tech shipstack.murzaktech.tech}"

stamp() { date '+%F %T'; }

if ! cd "$BENCH_DIR"; then
    echo "$(stamp) CANNOT FIND $BENCH_DIR" >&2
    exit 1
fi

failed=0
for site in $SITES; do
    extra=""
    for with_files in $FILES_FOR; do
        [ "$site" = "$with_files" ] && extra="--with-files"
    done
    echo "$(stamp) backing up $site $extra"
    if "$BENCH" --site "$site" backup $extra; then
        echo "$(stamp) OK     $site"
    else
        echo "$(stamp) FAILED $site"
        failed=$((failed + 1))
    fi
done

echo "$(stamp) finished, $failed site(s) failed"
[ "$failed" -eq 0 ]
