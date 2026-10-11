# Moving the live server to the new app name (one-off)

**Delete this file and the `cutover/` folder once the live server has been moved.** It is the only place in the repository where the old name `techsavanna_pos` is kept on purpose, because the steps below have to refer to it.

**For:** whoever has administrator access to the live server (the one that deploys to `api.pos.murzaktech.tech`).
**Time:** about 30 minutes, plus the quiet time you choose. **Risk:** medium. Everything is reversible if you take the backup first (step 2), and the whole procedure was rehearsed on a staging copy (see the last section).

## What changed and why a plain deploy is not enough

The app used to be called `techsavanna_pos`. It is now `murzak_pos`. A Frappe app's name is not just a label. It is written in three places that a normal deploy does not touch:

1. **The folder and the Python package on the server.** The deploy script now looks for `~/frappe-bench/apps/murzak_pos`. Until that folder exists, the first deploy after the merge stops at its first line and changes nothing. **A red deploy run on that first merge is expected and harmless.**
2. **The site's database.** It records which apps are installed, which module each screen belongs to, which one-off fixes ("patches") have already run, and which background jobs to run, all under the old name. If these are left alone, the next `migrate` cannot find the app, or worse, runs every old patch a second time.
3. **Every web address the app serves.** An address such as `/api/method/techsavanna_pos.api.sales_api.xxx` becomes `/api/method/murzak_pos.api.sales_api.xxx`. Anything outside this repository that calls those addresses must change at the same moment: the frontend, and the payment providers (see step 7).

## Before you start

| Check | Why |
|---|---|
| The frontend change is ready to release at the same time | Every call to the backend uses the new address, so the till stops working until both are on the new name |
| Pick a quiet time, and ask shops to stop taking card or M-Pesa payments for the period | A payment prompt sent just before the switch comes back to an address that no longer exists, and the sale is not confirmed |
| You can sign in to the server as the user that owns `~/frappe-bench` | |
| The pull request that renames the app is **merged** | The steps pull it from `main` |

## Which sites are affected

Checked on the live server on 11 October 2026. This bench holds seven sites, and **two** have the app installed:

| Site | What it is | What to do |
|---|---|---|
| `api.pos.murzaktech.tech` | The live POS | Move it (steps 2 to 6) |
| `savanna-test.local` | A test copy. **Reviewed on 11 October 2026: empty** (0 users, 0 invoices, 0 items, 0 businesses, no activity ever, not in the web server settings) | Remove it first with `bench drop-site savanna-test.local` (needs the MariaDB root password). **Without that password, move it like the live site** (steps 5 and 6 for this site too); it is empty, so this is quick |

The other sites (`erp.murzaktech.tech`, `erp.mystylecarhire.com`, `matatuke.murzaktech.tech`, `shipstack.murzaktech.tech`) do not use the app. The folder rename and the Python change in step 4 are bench-wide, so a site that has the app and is **not** moved will stop loading. In the steps below, "SITE" means each of the two sites in turn, and steps 2, 3, 5 and 6 are run once per site.

Before starting, check that no other app on the bench refers to the old name. This command only reads, and should print nothing:

```bash
grep -rl techsavanna ~/frappe-bench/apps/murzak_custom ~/frappe-bench/apps/matatuke_api ~/frappe-bench/apps/logistics_core ~/frappe-bench/apps/csf_ke 2>/dev/null
```

(`newsite.murzaktech.tech` cannot connect to its database, which is unrelated to this move but worth looking at.)

## Steps

Run these on the live server, one at a time. `SITE` below means `api.pos.murzaktech.tech`.

### 1. Check what the database holds (optional, 2 minutes)

This lists every place in the database that mentions the old name. On the rehearsal it found 10 places, all of which the script in step 5 handles. If yours shows a table that is **not** in this list, stop and tell the developers before going on.

| Table | Column |
|---|---|
| `tabDocType` | `module` |
| `tabCustom Field` | `module` |
| `tabModule Def` | `name`, `module_name`, `app_name` |
| `tabInstalled Application` | `app_name` |
| `tabDefaultValue` | `defvalue` (the installed apps list) |
| `tabPatch Log` | `patch` |
| `tabScheduled Job Type` | `method` |
| `tabDeleted Document` | `data` (history only; left as it is) |

To produce the list yourself:

```bash
cd ~/frappe-bench
bench --site SITE mariadb -N -e "SELECT CONCAT('SELECT ''',table_name,''' t, ''',column_name,''' c, COUNT(*) n FROM \`',table_name,'\` WHERE \`',column_name,'\` LIKE ''%techsavanna%'' HAVING n>0') FROM information_schema.columns WHERE table_schema=DATABASE() AND data_type IN ('varchar','text','longtext','mediumtext','tinytext','json')" > /tmp/scan.sql
paste -sd'|' /tmp/scan.sql | sed 's/|/ UNION ALL /g' | bench --site SITE mariadb -N
```

Also look for stored payment addresses, which contain the old name and which the script does not change (step 7 explains):

```bash
bench --site SITE mariadb -e "SELECT name, stk_callback_url FROM \`tabMPESA Settings\` WHERE stk_callback_url LIKE '%techsavanna_pos%'"
bench --site SITE mariadb -e "SELECT name, ipn_url FROM \`tabPOS Gateway Settings\` WHERE ipn_url LIKE '%techsavanna_pos%'"
```

### 2. Take a backup and copy it somewhere else

```bash
cd ~/frappe-bench
bench --site SITE backup --with-files
ls -la sites/SITE/private/backups/
```

Copy the newest files in that folder (the `.sql.gz` database file and the two `.tar` files) to another computer. **Do not continue until you have done this.** On 11 October 2026 the server had no scheduled backups at all and the newest was from 12 September, so this backup is the only safety net. Do it for both sites.

### 3. Stop the shop traffic

```bash
bench --site SITE set-maintenance-mode on
bench --site SITE disable-scheduler
sudo supervisorctl stop all        # or however this server stops its workers
```

### 4. Rename the folder, pull the new code, and tell Python about it

```bash
cd ~/frappe-bench/apps
mv techsavanna_pos murzak_pos
cd murzak_pos
git checkout main
git pull
rm -rf techsavanna_pos             # leftover compiled files of the old folder

cd ~/frappe-bench
./env/bin/pip uninstall -y techsavanna_pos
./env/bin/pip install -e apps/murzak_pos
sed -i 's/^techsavanna_pos$/murzak_pos/' sites/apps.txt
rm -f sites/assets/techsavanna_pos
cat sites/apps.txt                 # must now list murzak_pos and no techsavanna_pos
```

### 5. Update the database (once per site)

The script is in `docs/cutover/rename_in_database.sql`. It changes the 8 kinds of rows listed in step 1 inside one transaction (all or nothing), and running it twice changes nothing the second time.

```bash
cd ~/frappe-bench
bench --site SITE mariadb < apps/murzak_pos/docs/cutover/rename_in_database.sql
```

No output means success. An error message means nothing was changed; send it to the developers.

### 6. Let Frappe catch up, then switch everything back on (migrate once per site)

```bash
bench --site SITE migrate
bench --site SITE clear-cache
bench build --app murzak_pos
bench --site SITE enable-scheduler
bench --site SITE set-maintenance-mode off
sudo supervisorctl start all       # or bench restart / systemctl, as this server does
```

`migrate` should **not** print any line starting `Executing murzak_pos.patches.`. If it does, step 5 did not take effect; stop and ask.

### 7. Payment providers (do not skip)

The app tells Safaricom, Pesapal and PayPal where to send results, and some of those addresses were stored in the old form.

- **M-Pesa, new payments:** the app builds the address afresh for each request, so new prompts use the new name automatically.
- **M-Pesa, "Confirmation URL" registered on the Daraja portal for a shop's paybill or till:** this is stored by Safaricom, not by us. For each company, run *Register URLs* again from the MPESA Settings screen (or the `register_mpesa_settings` call). **If Safaricom refuses a second registration for a live shortcode, ask Safaricom to update it. Until it is updated, walk-in payments to that paybill will not be confirmed in the till.**
- **MPESA Settings with a typed-in "STK callback URL":** the check in step 1 lists them. Edit each to use `murzak_pos` in place of `techsavanna_pos`.
- **Pesapal:** the IPN address is stored on the POS Gateway Settings record (listed by the step 1 check). Re-register it from that screen.
- **PayPal:** if a webhook was set up on the PayPal developer dashboard, change its address the same way.

### 8. Release the frontend

Replace `techsavanna_pos.` with `murzak_pos.` in every call to the backend, and release it now.

### 9. Check it worked

Sign in on a till and:

- [ ] Make a small cash sale.
- [ ] Make a small M-Pesa sale to a phone you control, and confirm the sale completes after you approve it.
- [ ] Open the held-sales and reports screens.
- [ ] On the server: `bench --site SITE list-apps` shows `murzak_pos`, and `bench --site SITE mariadb -e "SELECT name FROM tabDocType WHERE name LIKE 'MPESA%'"` lists both MPESA DocTypes.

Then deploy once more from GitHub (re-run the failed deploy) to confirm the normal route works from now on.

## If something goes wrong

If it fails **before step 5**, put the folder back (`mv murzak_pos techsavanna_pos`, reinstall it with `pip install -e`, restore `sites/apps.txt`), run `bench restart`, and switch maintenance mode off. Nothing in the database has changed.

If it fails **after step 5**, restore the backup from step 2:

```bash
bench --site SITE restore sites/SITE/private/backups/<the .sql.gz file> --with-private-files <private tar> --with-public-files <public tar>
```

and then put the old folder back as above.

## How this was tested

The whole procedure was run on a staging server built from the live app (old name, with a database created by installing it), using the same files that are in this folder. Results: the app, its 11 DocTypes, 10 custom fields and 7 recorded patches all moved to the new name; no old patch ran again; the daily clean-up jobs pointed at the new paths; and the M-Pesa DocTypes survived the migrate. One snag was found and is fixed in the script: the database client refuses these updates unless its "safe update" mode is switched off, which the first line of the script now does.

What staging could not prove: the real Safaricom, Pesapal and PayPal portals (step 7), which only you can check, and the frontend.
