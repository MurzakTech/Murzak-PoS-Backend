# Server security setup

**For:** whoever has administrator access to the web server (Nginx) that serves the Murzak POS website.
**Time:** about 10 minutes. **Risk:** low, and easy to undo (see "Undoing it" at the end).

## What this is and why it is needed

Two protections can only be sent by the web server, because a browser only obeys them when they arrive with the page itself:

1. **Stopping other websites from showing the till inside their own page.** Without it, a criminal can put the till behind an invisible layer on their own website and trick a member of staff into clicking buttons (a trick called "clickjacking").
2. **Making browsers always use a secure connection.** Once a browser has visited the site over HTTPS, it refuses to talk to it over plain http again, which blocks a whole class of attacks on shop wifi.

## What is already done, so you do not need to do it

- **The backend** (the API, and the Frappe desk) now sends these protections on its own responses. A change in the code did this, and it is applied each time the backend is deployed.
- **The website's pages** already carry a content policy, added when the website is built.

What is left is the website's own Nginx sites, which is the part only you can do:

| Address | What it serves | Nginx folder |
|---|---|---|
| `pos.murzaktech.tech` | The shared till | `/var/www/pos/` |
| `<shop>.pos.murzaktech.tech` | One private till per paying shop | `/var/www/pos-tenant/` |

## Before you start

- You can sign in to the server and run `sudo`.
- **HTTPS works on every address above**, including a shop address. The secure-connection rule (step 4) is a one-way door: for a year, browsers that have seen it will refuse plain http. If one shop site does not have a working certificate yet, fix that first.

## Steps

### 1. Create the file with the headers

Create `/etc/nginx/snippets/murzak-security-headers.conf` with exactly this content (it is the same as `deploy/nginx/security-headers.conf` in the frontend repository):

```nginx
# Stop other websites from showing the till inside a frame to trick staff into clicking
add_header Content-Security-Policy "frame-ancestors 'none'" always;
add_header X-Frame-Options "DENY" always;

# Make browsers trust the declared file types, never guess them
add_header X-Content-Type-Options "nosniff" always;

# Send only the site name, never full addresses, when following links elsewhere
add_header Referrer-Policy "strict-origin-when-cross-origin" always;

# Do not let the page use the camera, microphone or location
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;

# Always use HTTPS for a year once a browser has visited over HTTPS.
# For the first test, change 31536000 to 300 (see step 4).
add_header Strict-Transport-Security "max-age=31536000" always;
```

### 2. Use it in every website

Add this one line inside **each** `server { ... }` block for `pos.murzaktech.tech` and for the shop sites (`*.pos.murzaktech.tech`):

```nginx
include /etc/nginx/snippets/murzak-security-headers.conf;
```

If a `location { ... }` inside that server already has its own `add_header` lines, add the same `include` line inside that `location` too. Nginx ignores the outer ones when a `location` has any of its own.

You do **not** need to change the backend's own Nginx site (`api.pos.murzaktech.tech` or the Frappe bench site): the backend sends these itself.

### 3. Check, then reload

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` must say "syntax is ok" and "test is successful" before the reload runs. If it does not, nothing has changed; read the message, fix the line it names, and try again.

### 4. Confirm it worked

From any computer with Python 3 (no installation needed), using the checker in this repository:

```bash
python3 murzak_pos/api/scripts/check_security_headers.py \
    https://pos.murzaktech.tech \
    https://<one-real-shop>.pos.murzaktech.tech \
    https://api.pos.murzaktech.tech
```

It prints PASS or FAIL for each protection on each address, and ends with "All protections are in place." when everything is right. A plain `curl -sI https://pos.murzaktech.tech` shows the same headers if you prefer to look yourself.

**Recommended for the secure-connection rule:** do step 1 with `max-age=300` (five minutes) first. The checker will report that line as FAIL with a note saying so, which is expected. Open the shared site and a shop site in a browser and sign in. If everything works, change `300` back to `31536000`, run step 3 again, and re-run the checker: now all lines should PASS.

### 5. Do it again for each new shop

New shop sites are created from the same template, so if the `include` line is part of the template they get it automatically. If new shop sites are created some other way, add the `include` line to the new `server` block and re-run the checker on it.

## If something looks wrong

| Problem | Cause and fix |
|---|---|
| The checker says a header is **missing** on one address | That address's `server` block (or a `location` with its own `add_header`) is missing the `include` line. Add it (step 2) and reload |
| The till stops loading **inside another page you control** (a portal, a preview) | That is the protection working as designed. If you really need to embed the till on a page of your own, change `'none'` and `DENY` in step 1 to `'self' https://your-page.example` and `SAMEORIGIN`, and tell the developers first |
| The barcode scanner **cannot use the phone camera** | The snippet switches the camera off. Change `camera=()` to `camera=(self)` in step 1 and reload |
| A shop's browser says the connection is **not secure** after step 4 | That shop site has no working HTTPS certificate. Fix the certificate; do not remove the header |

## Undoing it

Delete the `include` lines (or the whole snippet file), run `sudo nginx -t && sudo systemctl reload nginx`. The framing and file-type protections stop at once. The secure-connection rule keeps working in browsers that already saw it until its time runs out (up to a year, or five minutes if you followed the recommended test first), which is why the test in step 4 is worth doing.

To turn off only the backend's own headers, add `"murzak_pos_security_headers": 0` to the site's `site_config.json` and restart the bench. This is rarely needed.
