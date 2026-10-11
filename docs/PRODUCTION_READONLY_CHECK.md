# Read-only check of the live server

**For:** whoever can sign in to the live server over SSH (the same access the automatic deploy uses).
**Time:** about 5 minutes. **Risk:** none. The script only looks; it changes no setting, record, file or service.

## Why it is needed

Before the developers build the tax and eTIMS work, they need three facts that can only be read from the live server:

1. **Which ERPNext and Frappe versions it runs.** The till's default sale path (POS Invoice) fails on the newest ERPNext 15 release, so the version decides whether that is a problem today or only after an upgrade.
2. **Which kind of invoice each business really uses** (POS Invoice, or Sales Invoice made at the till), and whether any of them already send invoices to KRA.
3. **Whether the eTIMS prerequisites exist** (a Branch named `00` or `01`, the Branch dimension, an eTIMS settings record).

It also reports the server's health: free disk space, background programs running, recent errors and the newest backups.

## What it does not do

- It never prints a password, API key, secret or the contents of `site_config.json`.
- It runs only `SELECT` queries and read-only commands (`bench version`, `bench doctor`, `df`, `ls`). It does not run `migrate`, restart anything or take a backup.
- Running it twice is harmless.

## Steps

1. **Sign in** to the server as the user that owns the bench (the user whose home holds `frappe-bench`).
2. **Get the script.** Copy `deploy/production/readonly_check.sh` from the repository onto the server, for example by pasting it into a new file called `readonly_check.sh`.
3. **Run it:**

   ```bash
   bash readonly_check.sh
   ```

   If the site is not called `api.pos.murzaktech.tech`, or the bench is not in `~/frappe-bench`, say so:

   ```bash
   SITE=your.site.name BENCH_DIR=/path/to/frappe-bench bash readonly_check.sh
   ```

4. **Read it through once.** It lists business names and invoice counts. If you would rather not share a name, replace it before sending.
5. **Send back** the file `~/murzak_production_check_<date>.txt`.

## If something looks wrong

A line that says an error or "Unknown column" only means that table or column does not exist on this version; the rest of the report is still valid. Nothing has been changed, so there is nothing to undo.
