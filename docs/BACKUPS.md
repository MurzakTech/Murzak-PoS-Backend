# Scheduled backups for the live server

**For:** whoever has SSH access to the live server. **Time:** about 15 minutes. **Risk:** low. It adds one scheduled job and changes one setting; it does not touch any data.

## Why not `bench setup backups`

That command schedules one job that backs up **all** sites in a single run, every 6 hours. Two problems on this server:

- A site with a broken database (`newsite.murzaktech.tech`) can stop the run, so the sites after it are never backed up.
- Frappe keeps only the **last 3** backups per site by default. At every 6 hours that is 18 hours of history.

`deploy/production/backup_sites.sh` backs up each site on its own, so one failure never stops the others, and reports which worked. The setting "Number of Backups" is raised so that a daily backup keeps two weeks.

## Steps (as the bench user, `murzakerp`)

1. **Save the script** as `~/backup_sites.sh` (copy it from `deploy/production/backup_sites.sh`).
2. **Test it once by hand** (it takes a minute or two):

   ```bash
   bash ~/backup_sites.sh
   ```

   Each site should end with `OK`. A site that is broken says `FAILED`; that is expected for `newsite.murzaktech.tech` if you add it, and does not stop the rest.
3. **Keep two weeks of backups** instead of three. For each site you back up:

   ```bash
   bench --site SITE execute frappe.client.set_value --kwargs '{"doctype":"System Settings","name":"System Settings","fieldname":"backup_limit","value":14}'
   ```

4. **Schedule it** to run every night at 23:30 server time (02:30 in Nairobi, when the shops are quiet). Running this twice never doubles the job:

   ```bash
   ( crontab -l 2>/dev/null | grep -v backup_sites.sh ; echo "30 23 * * * BENCH=$(command -v bench) bash \$HOME/backup_sites.sh >> \$HOME/frappe-bench/logs/backup.log 2>&1" ) | crontab -
   crontab -l
   ```

5. **Check it the next morning:**

   ```bash
   tail -20 ~/frappe-bench/logs/backup.log
   ls -lt ~/frappe-bench/sites/api.pos.murzaktech.tech/private/backups | head
   ```

## Copy backups off the server

Backups on the same disk do not survive a lost server. Until an automatic copy is set up, turn on the hosting provider's own server snapshots or backups in its control panel, and once a week copy the newest `.sql.gz` of the POS site to another computer with `scp`.

## Restoring

```bash
bench --site SITE restore sites/SITE/private/backups/<the .sql.gz file> --with-private-files <private .tar> --with-public-files <public .tar>
```

Practise this once on a copy, not on the live site.
