# Scheduled backups for the live server

**For:** whoever has SSH access to the live server. **Time:** about 15 minutes. **Risk:** low. It adds one scheduled job and changes one setting; it does not touch any data.

## Why not `bench setup backups`

That command schedules one job that backs up **all** sites in a single run, every 6 hours. Two problems on this server:

- A site with a broken database (`newsite.murzaktech.tech`) can stop the run, so the sites after it are never backed up.
- Frappe keeps only the **last 3** backups per site by default. At every 6 hours that is 18 hours of history.

`deploy/production/backup_sites.sh` backs up each site on its own, so one failure never stops the others, and reports which worked. The setting "Number of Backups" is raised so that a daily backup keeps two weeks.

## Every shop is covered automatically

Each shop bought on murzaktech.tech gets its own site (`<shop>.erp.murzaktech.tech`), built by the website with nobody involved. The script used to carry a fixed list of five sites, so those shops were never backed up. It now backs up **every** site it finds on the server, which means a new shop is covered from its first night and there is no list to keep up to date.

- Sites with Murzak POS installed also get their uploaded files (item images). Other sites get the database only.
- `newsite.murzaktech.tech` is left out because its database is broken. Change this with `SKIP_SITES`.
- To see what a run would do without doing it: `bash ~/backup_sites.sh --dry-run`.

**The copy on the server is a copy of the file in this repository, made by hand.** After this file changes, copy it to `~/backup_sites.sh` again (step 1 below), or the server keeps running the old one.

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

Backups on the same disk do not survive a lost server. The script copies each site's backups to storage somewhere else as soon as they are written, once it is told where. Until that is set up, every run ends with a warning in capitals that nothing left the server.

It uses `rclone`, a free tool that copies files to almost any storage service (Backblaze B2, Amazon S3, Google Drive and many more). One-time setup, as the bench user (`murzakerp`):

1. **Install rclone** (needs `sudo`): `sudo apt install rclone`
2. **Create a private bucket** with a storage provider, and an access key that can write to it. Backblaze B2 is a low-cost choice.
3. **Tell rclone about it.** Run `rclone config` and follow the questions to add the storage under a name, for example `b2`. Then add a second remote of type `crypt` on top of it (for example named `murzak-backups`, pointing at `b2:your-bucket/bench`). The crypt remote encrypts everything before it leaves the server. This matters: each backup folder includes `site_config_backup.json`, which holds that site's database password and encryption key.
4. **Write the crypt password down somewhere that is not this server** (a password manager). Without it the copies cannot be read back, and a backup that cannot be restored is not a backup.
5. **Try it:**

   ```bash
   BACKUP_REMOTE=murzak-backups: bash ~/backup_sites.sh
   rclone lsd murzak-backups:
   ```

   Every site should end with `OK ... (copied off the server)`, and the second command should list one folder per site.
6. **Add it to the nightly job.** This replaces the scheduled line from step 4 above with one that names the destination:

   ```bash
   ( crontab -l 2>/dev/null | grep -v backup_sites.sh ; echo "30 23 * * * BENCH=$(command -v bench) BACKUP_REMOTE=murzak-backups: bash \$HOME/backup_sites.sh >> \$HOME/frappe-bench/logs/backup.log 2>&1" ) | crontab -
   ```

Copies are kept on the remote for 30 days (`REMOTE_RETENTION_DAYS` changes this). Old copies are removed only after that night's new copy has arrived.

### Knowing when it stops working

A backup job that quietly stops is the usual way backups fail. Two things help:

- The script exits with an error if any site failed or any copy off the server failed, and the reason is in `~/frappe-bench/logs/backup.log`.
- Set `HEARTBEAT_URL` to a check address from a service such as healthchecks.io (add it to the cron line the same way as `BACKUP_REMOTE`). The script calls it only after a completely clean run, and the service emails you when a night goes by with no call. That catches a failed run and a run that never started.

## Restoring

```bash
bench --site SITE restore sites/SITE/private/backups/<the .sql.gz file> --with-private-files <private .tar> --with-public-files <public .tar>
```

Practise this once on a copy, not on the live site.

To restore from the copy off the server, first bring the files back, then restore as above:

```bash
rclone copy murzak-backups:SITE /tmp/restore-SITE
```

If the whole server was lost, build a new bench, create the site again (`bench new-site SITE`), restore into it, and then put the `encryption_key` from that site's `site_config_backup.json` into the new `sites/SITE/site_config.json`. Without the original key, saved passwords (email accounts, payment gateway keys) cannot be read.
