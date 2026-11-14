#!/usr/bin/env bash
#
# Backs up EVERY site on this bench, one after the other, copies the backups off the server when a
# destination is set, and says plainly which sites worked.
#
# Why it finds the sites itself: this used to carry a fixed list of five sites. Every shop bought on
# murzaktech.tech gets its own site (<shop>.erp.murzaktech.tech) built automatically, and none of them
# was on the list, so a new customer's shop was never backed up. Now every folder under sites/ that
# has a site_config.json is backed up, so a shop is covered from its first night with nobody editing
# anything.
#
# Why not `bench setup backups`? It backs up "all" sites in a single command, and a site with a broken
# database (newsite.murzaktech.tech has one) can stop the whole run, so the sites after it are never
# backed up. Here every site is tried separately, so one failure never stops the others.
#
# The backups are written by bench to sites/<site>/private/backups/. How many are kept there is set
# per site by "Number of Backups" in System Settings (Frappe's default is only 3).
#
# OFF THE SERVER. A backup on the same disk as the data does not survive a lost server. Set
# BACKUP_REMOTE to an rclone remote (for example  b2:murzak-backups/bench ) and each site's backup
# folder is copied to <remote>/<site>/ straight after it is written. Copies older than
# REMOTE_RETENTION_DAYS are then removed from the remote. Without BACKUP_REMOTE the run still works,
# but it ends by saying, in capitals, that nothing left the server. The folder holds each site's
# site_config_backup.json, which contains that site's database password and encryption key, so the
# remote must be private (an rclone "crypt" remote is the safe choice). See docs/BACKUPS.md.
#
# Run it by hand to test:      bash ~/backup_sites.sh
# See what it would do:        bash ~/backup_sites.sh --dry-run
# Or from cron (see docs/BACKUPS.md). Settings can be changed from outside:
#
#   BENCH_DIR              the bench folder (default ~/frappe-bench)
#   BENCH                  the bench command (default: bench)
#   SITES                  back up only these sites, space separated (default: every site found)
#   SKIP_SITES             sites to leave out (default: newsite.murzaktech.tech, whose database is broken)
#   FILES_FOR              which sites also get their uploaded files:
#                            auto  every site with murzak_pos installed (default; item images live there)
#                            all   every site
#                            or a space separated list of site names
#   BACKUP_REMOTE          rclone remote to copy to (default: none, backups stay on this server)
#   REMOTE_RETENTION_DAYS  how long copies are kept on the remote (default 30, minimum 1)
#   HEARTBEAT_URL          a web address to call when EVERYTHING worked (for example a healthchecks.io
#                          check), so a night where the job did not run at all gets noticed too

BENCH_DIR="${BENCH_DIR:-$HOME/frappe-bench}"
BENCH="${BENCH:-bench}"
# cron starts programs with almost no PATH, so add the usual places bench and rclone live
export PATH="$PATH:$HOME/.local/bin:/usr/local/bin:/usr/bin"
SKIP_SITES="${SKIP_SITES-newsite.murzaktech.tech}"
FILES_FOR="${FILES_FOR:-auto}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-30}"
HEARTBEAT_URL="${HEARTBEAT_URL:-}"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        *) echo "backup_sites.sh: unknown argument '$arg' (the only one is --dry-run)" >&2; exit 2 ;;
    esac
done

stamp() { date '+%F %T'; }
in_list() { # in_list <word> <space separated list>
    local w
    for w in $2; do [ "$w" = "$1" ] && return 0; done
    return 1
}

if ! cd "$BENCH_DIR"; then
    echo "$(stamp) CANNOT FIND $BENCH_DIR" >&2
    exit 1
fi

case "$REMOTE_RETENTION_DAYS" in
    ''|*[!0-9]*|0) echo "$(stamp) REMOTE_RETENTION_DAYS must be a whole number of days, 1 or more" >&2; exit 2 ;;
esac
if [ -n "$BACKUP_REMOTE" ] && ! command -v rclone >/dev/null 2>&1; then
    echo "$(stamp) BACKUP_REMOTE is set but rclone is not installed (see docs/BACKUPS.md)" >&2
    exit 1
fi

# Every folder under sites/ with a site_config.json is a site. "assets" and the like have none.
if [ -z "${SITES:-}" ]; then
    SITES=""
    for config in sites/*/site_config.json; do
        [ -f "$config" ] || continue
        SITES="$SITES $(basename "$(dirname "$config")")"
    done
fi
if [ -z "${SITES// /}" ]; then
    echo "$(stamp) NO SITES FOUND in $BENCH_DIR/sites" >&2
    exit 1
fi

wants_files() { # should this site's uploaded files be backed up too?
    case "$FILES_FOR" in
        all) return 0 ;;
        auto) "$BENCH" --site "$1" list-apps 2>/dev/null | awk '{print $1}' | grep -qx murzak_pos ;;
        *) in_list "$1" "$FILES_FOR" ;;
    esac
}

failed=0
done_count=0
not_copied=0
for site in $SITES; do
    if in_list "$site" "$SKIP_SITES"; then
        echo "$(stamp) SKIPPED $site (listed in SKIP_SITES)"
        continue
    fi
    extra=""
    wants_files "$site" && extra="--with-files"

    if [ "$DRY_RUN" -eq 1 ]; then
        echo "$(stamp) would back up $site $extra"
        [ -n "$BACKUP_REMOTE" ] && echo "$(stamp) would copy sites/$site/private/backups to ${BACKUP_REMOTE%/}/$site"
        continue
    fi

    echo "$(stamp) backing up $site $extra"
    # shellcheck disable=SC2086  # $extra is deliberately unquoted: it is either empty or one flag
    if ! "$BENCH" --site "$site" backup $extra; then
        echo "$(stamp) FAILED $site"
        failed=$((failed + 1))
        continue
    fi

    if [ -z "$BACKUP_REMOTE" ]; then
        echo "$(stamp) OK     $site (on this server only)"
        not_copied=$((not_copied + 1))
        done_count=$((done_count + 1))
        continue
    fi

    # copy never deletes anything on the remote and skips files that are already there
    if rclone copy "sites/$site/private/backups" "${BACKUP_REMOTE%/}/$site" >/dev/null; then
        # Old copies are removed only after tonight's copy is safely there
        rclone delete "${BACKUP_REMOTE%/}/$site" --min-age "${REMOTE_RETENTION_DAYS}d" >/dev/null \
            || echo "$(stamp) could not remove old copies of $site from the remote (the new copy is safe)"
        echo "$(stamp) OK     $site (copied off the server)"
        done_count=$((done_count + 1))
    else
        echo "$(stamp) FAILED $site (backed up here, but the copy off the server failed)"
        failed=$((failed + 1))
    fi
done

if [ "$DRY_RUN" -eq 1 ]; then
    echo "$(stamp) dry run finished, nothing was changed"
    exit 0
fi

echo "$(stamp) finished, $done_count site(s) backed up, $failed failed"
if [ "$not_copied" -gt 0 ]; then
    echo "$(stamp) WARNING: NOTHING WAS COPIED OFF THIS SERVER. If the server is lost, these backups go with it. Set BACKUP_REMOTE (docs/BACKUPS.md)."
fi

# Only a completely clean run reports in, so a failure and a job that never ran both show as silence
if [ "$failed" -eq 0 ] && [ "$not_copied" -eq 0 ] && [ -n "$HEARTBEAT_URL" ]; then
    curl -fsS -m 20 -o /dev/null "$HEARTBEAT_URL" || echo "$(stamp) could not reach HEARTBEAT_URL (the backups themselves are fine)"
fi

[ "$failed" -eq 0 ]
