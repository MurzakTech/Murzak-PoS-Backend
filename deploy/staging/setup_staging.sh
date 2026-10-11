#!/usr/bin/env bash
#
# Builds a staging server for Murzak POS on a fresh Ubuntu 24.04 machine:
# MariaDB + Redis + Frappe 15 + ERPNext 15 + kenya-compliance + this app, with one site.
#
# Run it as root, from a checkout of this repository:
#
#   sudo DB_ROOT_PASSWORD='...' ADMIN_PASSWORD='...' deploy/staging/setup_staging.sh
#
# Both passwords are required and are never stored by this script. Optional settings:
#
#   BENCH_USER   Linux user that owns the bench (created if missing)       default: frappe
#   SITE         Name of the site to create                                default: staging.localhost
#   APP_BRANCH   Branch of this repository to install                      default: the branch checked out now
#   TEST_DATA    1 = also create ERPNext's sample company so the full test
#                suite can run (do NOT use on a site holding real data)    default: 1
#   BUILD_ASSETS 1 = build the web assets (slow; only needed to open the
#                Frappe desk in a browser, not for API work or tests)      default: 0
#
# It is safe to run again: steps that are already done are skipped.
# It leaves the bench ready to use but does not keep it running; see docs/STAGING_SERVER.md.

set -euo pipefail

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run this as root (it installs system packages)."
: "${DB_ROOT_PASSWORD:?Set DB_ROOT_PASSWORD to the password the database administrator account should have}"
: "${ADMIN_PASSWORD:?Set ADMIN_PASSWORD to the password for the Administrator login of the site}"

BENCH_USER="${BENCH_USER:-frappe}"
SITE="${SITE:-staging.localhost}"
TEST_DATA="${TEST_DATA:-1}"
BUILD_ASSETS="${BUILD_ASSETS:-0}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_BRANCH="${APP_BRANCH:-$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)}"
APP_NAME="$(sed -n 's/^app_name = "\(.*\)"/\1/p' "$REPO_DIR"/*/hooks.py | head -1)"
[ -n "$APP_NAME" ] || die "Could not read the app name from hooks.py"

say "1/7 System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
	mariadb-server mariadb-client libmariadb-dev pkg-config build-essential libffi-dev libssl-dev \
	redis-server git curl cron python3-venv python3-dev \
	xfonts-75dpi xfonts-base fontconfig libxrender1 libxext6 wkhtmltopdf nodejs npm >/dev/null
command -v yarn >/dev/null || npm install -g yarn >/dev/null 2>&1
# Frappe 15 needs Node 18 or newer
node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)' \
	|| die "Node 18 or newer is needed (found $(node -v)). Install it from nodesource.com, then run again."

say "2/7 Database"
cat > /etc/mysql/mariadb.conf.d/99-frappe.cnf <<'CNF'
[mysqld]
character-set-client-handshake = FALSE
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
# Only this machine may connect
bind-address = 127.0.0.1
[mysql]
default-character-set = utf8mb4
CNF
service mariadb restart >/dev/null 2>&1 || {
	mkdir -p /run/mysqld && chown mysql:mysql /run/mysqld
	nohup mysqld_safe --user=mysql >/dev/null 2>&1 &
	sleep 8
}
if ! mariadb -uroot -p"$DB_ROOT_PASSWORD" -e 'SELECT 1' >/dev/null 2>&1; then
	mariadb -uroot -e "ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING PASSWORD('${DB_ROOT_PASSWORD}'); FLUSH PRIVILEGES;" \
		|| die "Could not set the database password, and the existing one is not DB_ROOT_PASSWORD."
fi

say "3/7 Bench user"
id "$BENCH_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$BENCH_USER"
BENCH_HOME="$(getent passwd "$BENCH_USER" | cut -d: -f6)"

# Everything from here runs as the bench user (Frappe's installer refuses to run as root).
# The passwords travel through the environment, not the command line.
export DB_ROOT_PASSWORD ADMIN_PASSWORD SITE TEST_DATA BUILD_ASSETS APP_BRANCH APP_NAME REPO_DIR
runuser -u "$BENCH_USER" -- env HOME="$BENCH_HOME" USER="$BENCH_USER" bash -euo pipefail -c '
say() { printf "\n==> %s\n" "$*"; }
cd "$HOME"
git config --global safe.directory "*"

say "4/7 Bench tool and Python 3.11"
[ -d benchtool ] || python3 -m venv benchtool
. benchtool/bin/activate
pip install -q --upgrade pip uv frappe-bench
uv python install 3.11 >/dev/null
PY311="$(uv python find 3.11)"

say "5/7 Frappe 15, ERPNext 15, kenya-compliance and the app"
if [ ! -x frappe-bench/env/bin/python ]; then
	rm -rf frappe-bench   # a half-built bench left by an earlier failed run
	INIT_FLAGS="--skip-assets"; [ "$BUILD_ASSETS" = 1 ] && INIT_FLAGS=""
	bench init frappe-bench --frappe-branch version-15 --python "$PY311" $INIT_FLAGS
fi
cd frappe-bench
# The full address skips a name lookup on the GitHub web service, which is rate-limited
[ -d apps/erpnext ] || bench get-app https://github.com/frappe/erpnext.git --branch version-15 --skip-assets
# Fetched by its real repository name; "--resolve-deps" would look for an underscore and not find it.
[ -d apps/kenya_compliance ] || bench get-app https://github.com/navariltd/kenya-compliance.git --skip-assets
if [ ! -d "apps/$APP_NAME" ]; then
	# Install from a clone of the chosen branch, so the checkout it was run from is never touched
	rm -rf "$HOME/app_source" && git clone -q --branch "$APP_BRANCH" "$REPO_DIR" "$HOME/app_source"
	bench get-app "$HOME/app_source" --branch "$APP_BRANCH" --skip-assets
fi

say "6/7 Background services and the site"
for name in cache queue; do
	conf="config/redis_${name}.conf"
	port="$(sed -n "s/^port \([0-9]*\)/\1/p" "$conf")"
	redis-cli -p "$port" ping >/dev/null 2>&1 || redis-server "$conf" --daemonize yes >/dev/null
done
if [ ! -d "sites/$SITE" ]; then
	bench new-site "$SITE" --db-root-username root --db-root-password "$DB_ROOT_PASSWORD" \
		--admin-password "$ADMIN_PASSWORD" --mariadb-user-host-login-scope=localhost --no-mariadb-socket
fi
# Installing the app also installs ERPNext and kenya-compliance first, because it declares them as required
bench --site "$SITE" install-app "$APP_NAME"
bench --site "$SITE" set-config allow_tests true
bench set-config -g allow_tests true

say "7/7 Test data"
if [ "$TEST_DATA" = 1 ]; then
	# ERPNext rolls its first-run setup into a sample company; the test suite needs one
	bench --site "$SITE" execute erpnext.setup.utils.before_tests
fi
[ "$BUILD_ASSETS" = 1 ] && bench build --app "$APP_NAME"
bench --site "$SITE" list-apps
'

say "Done. Staging site: $SITE (bench in $BENCH_HOME/frappe-bench, signed in as Administrator)."
echo "Next: see docs/STAGING_SERVER.md for starting it, running the tests and updating it."
