# Check that the WS6 to WS8 release arrived (read-only)

**For:** whoever can sign in to the live server over SSH (the same access the automatic deploy uses).
**Time:** about 5 minutes. **Risk:** none. The script only looks; it changes no setting, record, file or service.

## Why it is needed

The automatic deploy only proves that the update ran without stopping. It does not prove that everything the release was
meant to create is there. This check reads the live server and reports, in plain lines, whether:

1. the release code is installed (it reads the version, not just the file names);
2. the update created the new records: Approval Settings, Approval Request, the Sales Person fields, the payment
   matching switch and the desk report;
3. the five print formats exist and are switched on, and the empty default letter head is in place, so one business's
   header never prints on another business's documents;
4. Swahili is switched on and the translation file is on the server;
5. anything has been set up yet (approval limits, sales people, clearing accounts, recipes, wastage). Nothing is expected
   until a business starts using a feature, so empty answers here are normal;
6. any returns were recorded **before** the VAT fix and reversed no VAT (see below);
7. anything has gone wrong since the release (errors in the last 24 hours).

## What it does not do

- It never prints a password, API key, secret or the contents of `site_config.json`.
- It runs only `SELECT` queries and read-only commands (`bench execute` of two functions that only read, `bench doctor`,
  `wc`, `git`). It does not run `migrate`, restart anything or take a backup.
- Running it twice is harmless.

## Steps

1. **Sign in** to the server as the user that owns the bench (the user whose home holds `frappe-bench`).
2. **Get the script.** Copy `deploy/production/post_deploy_check.sh` from the repository onto the server, for example by
   pasting it into a new file called `post_deploy_check.sh`.
3. **Run it:**

   ```bash
   bash post_deploy_check.sh
   ```

   If the site is not called `api.pos.murzaktech.tech`, or the bench is not in `~/frappe-bench`, say so:

   ```bash
   SITE=your.site.name BENCH_DIR=/path/to/frappe-bench bash post_deploy_check.sh
   ```

4. **Read it through once.** It lists business names and counts. If you would rather not share a name, replace it before
   sending.
5. **Send back** the file `~/murzak_post_deploy_check_<date>.txt`.

## How to read the report

| Section | Good | What a problem looks like |
| --- | --- | --- |
| 1. New code | "OK: the release commit d0f5b8e is part of what is installed", and every file "present" | "PROBLEM" or "MISSING": the deploy did not reach the server, or only part of it did |
| 2. New records | Two record types, the column count 1, both Sales Person fields, one desk report | Fewer rows than the heading says: the update (migrate) did not finish |
| 3. Print formats | Five formats, `disabled` 0; one default letter head and it is "No Letter Head"; a test QR starting `data:image/png` | A business's own letter head marked as the default; fewer than five formats |
| 4. Swahili | `sw` enabled 1 | `sw` enabled 0 or missing |
| 5. Set up so far | Empty or small numbers are normal | Nothing to fix here; it is information |
| 6. Returns before the VAT fix | No rows | See below |
| 7. Errors | Few and expected | A repeated title naming approvals, printing, payment matching, recipes or sales people |

## Returns recorded before the VAT fix

Until this release, a sales return reversed the price but not the VAT. Section 6 counts the returns whose original sale
had VAT but that reversed none, and their value. The release does not change them. If the list is not empty, the owner and
the accountant should decide whether to correct them (a small adjustment entry per return), because the VAT figures in
those periods are slightly overstated. Do not change anything on the server for this; send the report back first.

## If something looks wrong

A line that says an error or "Unknown column" only means that table or column does not exist on this version; the rest
of the report is still valid. Nothing has been changed, so there is nothing to undo.
