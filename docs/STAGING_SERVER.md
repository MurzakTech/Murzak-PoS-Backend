# Staging server

A staging server is a private copy of the whole system (database, Frappe, ERPNext and this app) where changes are tried out before they reach the live server. Nothing on it is real: no real shops, sales or payments.

**What it gives you that the automatic checks on GitHub do not:** a real database. The checks on every pull request run the tests that need no database. The staging server runs *all* of them, rehearses a move to a new version of the app on a copy of the real structure, and lets you call the endpoints with real ERPNext documents.

## What is on it

| Part | Version |
|---|---|
| Frappe | 15 (`version-15`) |
| ERPNext | 15 (`version-15`) |
| kenya-compliance (Kenya's tax system, eTIMS) | latest from its `develop` branch |
| This app (`murzak_pos`) | the branch you choose |
| Database | MariaDB 10.11, text stored as `utf8mb4` |
| Python / Node | 3.11 / 18 or newer |

## Building it from nothing

On a fresh Ubuntu 24.04 machine, as the administrator (`root`), from a copy of this repository:

```bash
sudo DB_ROOT_PASSWORD='choose-a-long-one' ADMIN_PASSWORD='choose-another' deploy/staging/setup_staging.sh
```

The two passwords are asked for on purpose and are never stored by the script. It takes about 10 to 15 minutes, installs everything, creates a site called `staging.localhost`, and finishes by printing the installed apps. It can be run again safely: finished steps are skipped.

Options, set the same way as the passwords (`SITE=... APP_BRANCH=... sudo ...`):

| Option | What it does | Default |
|---|---|---|
| `BENCH_USER` | The Linux user that owns the bench | `frappe` |
| `SITE` | Name of the site | `staging.localhost` |
| `APP_BRANCH` | Which branch of this repository to install | the branch you are on |
| `TEST_DATA` | `1` adds ERPNext's sample company, which the full test run needs. Never use it on a site with real data | `1` |
| `BUILD_ASSETS` | `1` builds the web screens of the Frappe desk (slow). Not needed for API work or tests | `0` |

**The machine needs to reach** `github.com` and `codeload.github.com` (the code), `pypi.org` and `registry.npmjs.org` (libraries) and the Ubuntu package servers. If a build stops on a `403 Forbidden` for a `codeload.github.com` address, the network is blocking that host; ask for it to be allowed.

## Using it

Everything below is run as the bench user (`sudo -iu frappe`), inside `~/frappe-bench`, after `. ~/benchtool/bin/activate`.

### Starting it

```bash
cd ~/frappe-bench
redis-server config/redis_cache.conf --daemonize yes     # only if they are not running
redis-server config/redis_queue.conf --daemonize yes
bench start                                              # web server, workers and scheduler; Ctrl+C stops it
```

The site then answers on port 8000. For a staging server that must stay up by itself, ask a developer to run `bench setup production` once, which sets up proper service management.

### Updating it to a new version of the app

This is exactly what the live deploy does, so it doubles as a rehearsal:

```bash
cd ~/frappe-bench/apps/murzak_pos && git pull
cd ~/frappe-bench && bench --site staging.localhost migrate
```

Read what `migrate` prints. Two warnings that mean trouble: a line starting `Orphaned DocType(s) found` (a DocType is about to be deleted; this happened to the M-Pesa DocTypes before the class names were fixed), and any `Executing murzak_pos.patches...` line you did not expect.

### Running all the tests

The no-database tests (what GitHub runs) are quick and need no bench:

```bash
python .github/scripts/run_unit_tests.py
```

The database tests, one test file at a time, on the staging site:

```bash
cd ~/frappe-bench
for f in apps/murzak_pos/murzak_pos/api/test_*.py; do
  m=murzak_pos.api.$(basename $f .py)
  echo "$m: $(bench --site staging.localhost run-tests --module $m 2>&1 | grep -E '^(Ran|OK|FAILED)' | tr '\n' ' ')"
done
```

**Two files are meant for the no-database runner only and are expected to fail here:** `test_party_names` and `test_supplier_isolation`. They replace the database with a stand-in, which a real site's own machinery does not accept. They pass in the GitHub check and in `run_unit_tests.py`.

Do not use `bench run-tests --app murzak_pos` (the whole app at once). The six empty, auto-generated DocType test files make ERPNext create sample items, which this app's mandatory Item fields reject, so it stops before running anything.

### Trying an endpoint by hand

```bash
bench --site staging.localhost execute murzak_pos.api.<module>.<function> --kwargs '{"name": "value"}'
```

## Things worth knowing

- **eTIMS must be set up before any sale can be tested.** With kenya-compliance installed, a Sales Invoice is refused unless the site has an active eTIMS settings record for the cashier's default company, a Branch named exactly `00` or `01`, and the "Branch" accounting dimension (which kenya-compliance creates only when eTIMS settings are first activated). Every invoice line also needs seven eTIMS item fields. With `TEST_DATA=1`, the setup script runs `deploy/staging/etims_test_setup.py`, which creates the Branch, the dimension, a **placeholder** settings record that is inserted directly (saving it would phone KRA) and points at a dead local port so nothing is ever sent, and a Walk-in Customer. It can be run again at any time.
- **How a sale is posted, and why shift close skips some receipts.** The app posts a POS Invoice's stock and accounting entries at the moment of sale. ERPNext's own shift close would then merge the same receipts into a Sales Invoice and post them a second time (found on staging: stock 10, then 8 after the sale, then 6 after shift close). So a business with an active eTIMS settings record sells through Sales Invoice, other businesses use POS Invoice, and shift close leaves already-posted POS Invoices out of the merge. Both paths work on ERPNext 15.94.1 and 15.122. To test on staging, set `is_active` on the placeholder eTIMS record to 0 for the POS Invoice path and 1 for the Sales Invoice path.
- **Live updates need two Nginx rules on the page's own address.** The tested rule is `deploy/nginx/realtime-proxy.conf`; its header explains why.

- **It is not production.** No HTTPS, no backups unless you set them up, passwords you chose. Do not put real customer data on it.
- **Fetching kenya-compliance.** `bench get-app --resolve-deps` looks for a repository called `navariltd/kenya_compliance`, but the real one is `navariltd/kenya-compliance` (hyphen). The setup script fetches it by its full address; do the same by hand.
- **Installing the app installs its two required apps first** (ERPNext, then kenya-compliance), as long as they are already in the bench.
- **Frappe's installer refuses to run as `root`.** That is why the script makes a separate Linux user.
- **A new Frappe 15 release can change the rules.** Staging is where to find that out first: two problems in this app (a filter in the wrong shape, and the M-Pesa class names) only showed once it was installed on current Frappe 15.
