# Business alerts

Owners and managers are told about things that need action without signing in. Each business has its own **Business Alert Settings** record (Frappe desk, or the `get_alert_settings` and `save_alert_settings` calls for the till). Every business gets one with the defaults when it is created, and the migration creates one for each existing business with **nobody to email yet**: nothing is sent to anyone until a person adds an address.

## The alerts

| Alert | When | What it says |
|---|---|---|
| Stock nearing expiry | Once a day, from the chosen hour (default 7) | Stock already expired, expiring within the urgent window (default 7 days) and within the long window (default 30). One email per business, not one per batch |
| Items at reorder level | Once a day, from the chosen hour | Items whose stock in a warehouse is at or below that warehouse's reorder level |
| Shift summary | When a shift is closed, in the background | Sales by payment method, expected and counted cash, refunds and cancelled sales. If the till records no cash count it says "not counted" |
| Invoices not sent to KRA | Checked hourly, at most once a day | How many invoices have waited over an hour, and the oldest. Only for businesses with active eTIMS settings |

Each alert can be switched off, and the whole business can be switched off, in the settings.

## How it avoids duplicates and cross-talk

- Every alert is first recorded in **Business Alert Log** under a unique key (business, alert, day or closing entry). Only the process that records it sends it, so the hourly job running twice, or two workers at once, sends one email.
- A send that fails removes its record so the next hourly run tries again, and the error is logged.
- Every query is limited to one business's warehouses and invoices, and one business failing never stops the next.
- The log doubles as the till's "recent alerts" list (`list_alerts`).

## What the server needs

- **A default outgoing email account** (Tools, Email Account). Without one every alert fails and is logged as "Business alert failed". The live server has one outgoing account enabled; check that it is the default.
- The Frappe **scheduler** must be on (the cutover runbook turns it off and on again; check it is on afterwards).
- SMS is optional. It is used only when SMS Settings has a gateway set up on the server.
- ERPNext's "Raise Material Request When Stock Reaches Re-order Level" is already on in production, so items with a reorder row raise requests on their own; the low-stock email is the warning that goes with it.

## Tested on staging

| Check | Result |
|---|---|
| One business with three low items, a fourth item well stocked, another business with two low items | The first got one email listing exactly the three; the second got one listing its own two; no item appeared in the other's email |
| Running the job again the same day | Nothing sent |
| Low-stock alert switched off | Nothing sent |
| Shift closed | One summary per shift; asking for the same shift again sent nothing |
| Email Queue after sending to a local mail sink | 4 sent, none in error |
