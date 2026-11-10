#!/usr/bin/env bash
#
# Checks backup_sites.sh against a pretend bench, with pretend `bench` and `rclone` commands that only
# write down what they were asked to do. Needs nothing installed and touches nothing real.
#
#     bash deploy/production/backup_sites.test.sh

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/backup_sites.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

passed=0
failed=0
ok() { # ok <description> <command...>
    local what="$1"; shift
    if "$@" >/dev/null 2>&1; then passed=$((passed + 1)); echo "  ok: $what"
    else failed=$((failed + 1)); echo "  FAIL: $what"; fi
}
has() { grep -qF -- "$1" "$2"; }
hasnt() { ! grep -qF -- "$1" "$2"; }

# A bench with: the shared POS site, two shops built by the website, a site without the POS app, the
# site with the broken database, and a folder that is not a site at all.
BENCH_DIR="$WORK/frappe-bench"
for site in api.pos.murzaktech.tech duka.erp.murzaktech.tech kiosk.erp.murzaktech.tech erp.murzaktech.tech newsite.murzaktech.tech; do
    mkdir -p "$BENCH_DIR/sites/$site/private/backups"
    echo '{}' > "$BENCH_DIR/sites/$site/site_config.json"
done
mkdir -p "$BENCH_DIR/sites/assets"

mkdir -p "$WORK/bin"
cat > "$WORK/bin/bench" <<'EOF'
#!/usr/bin/env bash
# bench --site <site> backup [--with-files]   |   bench --site <site> list-apps
site="$2"; action="$3"
if [ "$action" = "list-apps" ]; then
    echo "frappe 15.0.0"
    case "$site" in erp.murzaktech.tech) ;; *) echo "murzak_pos 1.0.0" ;; esac
    exit 0
fi
echo "bench $*" >> "$CALLS"
case " $BROKEN " in *" $site "*) exit 1 ;; esac
exit 0
EOF
cat > "$WORK/bin/rclone" <<'EOF'
#!/usr/bin/env bash
echo "rclone $*" >> "$CALLS"
[ "$1" = "copy" ] && [ -n "$RCLONE_FAILS" ] && exit 1
exit 0
EOF
chmod +x "$WORK/bin/bench" "$WORK/bin/rclone"

run() { # run <name> [VAR=value ...]   -> output in $WORK/<name>.out, calls in $WORK/<name>.calls
    local name="$1"; shift
    env -i HOME="$WORK" PATH="$WORK/bin:/usr/bin:/bin" BENCH_DIR="$BENCH_DIR" CALLS="$WORK/$name.calls" "$@" \
        bash "$SCRIPT" > "$WORK/$name.out" 2>&1
    echo $? > "$WORK/$name.exit"
    touch "$WORK/$name.calls"
}

echo "# every site is found without a list"
run plain
ok "the shared POS site is backed up" has "bench --site api.pos.murzaktech.tech backup --with-files" "$WORK/plain.calls"
ok "a shop built by the website is backed up, files included" has "bench --site duka.erp.murzaktech.tech backup --with-files" "$WORK/plain.calls"
ok "so is a second shop" has "bench --site kiosk.erp.murzaktech.tech backup --with-files" "$WORK/plain.calls"
ok "a site without the POS app gets its database only" grep -qx "bench --site erp.murzaktech.tech backup" "$WORK/plain.calls"
ok "the site with the broken database is skipped" hasnt "newsite.murzaktech.tech backup" "$WORK/plain.calls"
ok "the assets folder is not treated as a site" hasnt "assets" "$WORK/plain.calls"
ok "it says nothing left the server" has "NOTHING WAS COPIED OFF THIS SERVER" "$WORK/plain.out"
ok "and still ends as a success" grep -qx 0 "$WORK/plain.exit"

echo "# one broken site does not stop the others"
run broken BROKEN="duka.erp.murzaktech.tech"
ok "the broken one is reported" has "FAILED duka.erp.murzaktech.tech" "$WORK/broken.out"
ok "the site after it is still backed up" has "bench --site kiosk.erp.murzaktech.tech backup" "$WORK/broken.calls"
ok "the run ends as a failure" grep -qx 1 "$WORK/broken.exit"

echo "# copying off the server"
run remote BACKUP_REMOTE="b2:murzak/bench/" REMOTE_RETENTION_DAYS=14
ok "each site's backups go to its own folder on the remote" has "rclone copy sites/duka.erp.murzaktech.tech/private/backups b2:murzak/bench/duka.erp.murzaktech.tech" "$WORK/remote.calls"
ok "old copies are removed by age" has "rclone delete b2:murzak/bench/duka.erp.murzaktech.tech --min-age 14d" "$WORK/remote.calls"
ok "no warning when everything was copied" hasnt "NOTHING WAS COPIED" "$WORK/remote.out"
ok "ends as a success" grep -qx 0 "$WORK/remote.exit"

run remotefail BACKUP_REMOTE="b2:murzak/bench" RCLONE_FAILS=1
ok "a failed copy is reported per site" has "the copy off the server failed" "$WORK/remotefail.out"
ok "old copies are not removed when the new copy failed" hasnt "rclone delete" "$WORK/remotefail.calls"
ok "the run ends as a failure" grep -qx 1 "$WORK/remotefail.exit"

echo "# settings"
run only SITES="kiosk.erp.murzaktech.tech"
ok "SITES limits the run to the sites named" test "$(wc -l < "$WORK/only.calls")" -eq 1
run nofiles FILES_FOR="api.pos.murzaktech.tech"
ok "FILES_FOR can name the sites that get files" grep -qx "bench --site duka.erp.murzaktech.tech backup" "$WORK/nofiles.calls"
run noskip SKIP_SITES=""
ok "an empty SKIP_SITES skips nothing" has "bench --site newsite.murzaktech.tech backup" "$WORK/noskip.calls"
run badage REMOTE_RETENTION_DAYS=0
ok "a retention of 0 days is refused before anything runs" test ! -s "$WORK/badage.calls"

echo "# dry run"
env -i HOME="$WORK" PATH="$WORK/bin:/usr/bin:/bin" BENCH_DIR="$BENCH_DIR" CALLS="$WORK/dry.calls" BACKUP_REMOTE="b2:x" \
    bash "$SCRIPT" --dry-run > "$WORK/dry.out" 2>&1
touch "$WORK/dry.calls"
ok "lists what it would do" has "would back up duka.erp.murzaktech.tech --with-files" "$WORK/dry.out"
ok "backs nothing up and copies nothing" test ! -s "$WORK/dry.calls"

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]
