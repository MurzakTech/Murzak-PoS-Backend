# P3-6 contract: the Till open panel and the shift-end count

Back end, ticket P3-6. Two new calls in `murzak_pos/api/till_api.py`; the arithmetic is in `murzak_pos/api/till_calc.py` (no database). No existing call, record, report or setting is changed.

Both are reached as `/api/method/murzak_pos.api.till_api.<name>` and answer `{ "message": { ... } }` as every call of this app does. Below, "the answer" is the part inside `message`.

Spec: Build Pack, Module by module (Till row), Data and API needs (`till.state()`, `till.close(session, counted)`), AC-23.

## Who may call

| Person | `get_till_state` | `close_till` |
|---|---|---|
| Not signed in | Refused (`AuthenticationError`, "Please sign in first.") | Refused, same |
| Cashier | Their own open session only | Preview or close **their own** session only |
| Manager, Owner | Their own open session if they have one, else the newest open session of their business | Any session of their business |

Manager and Owner are the tiers of `role_tiers.get_tier`. The business is checked too: a session of another business is refused for everyone except the Administrator. A refused person gets a Frappe `PermissionError` (HTTP 403) with a message in words.

Note: the close itself goes through `sales_api.close_pos_opening_entry`, which keeps its own, narrower check (the cashier who opened the shift, or a System Manager, Accounts Manager or Sales Manager). A manager of another kind (for example a Stock Manager) passes the check above but is refused there with "Only the cashier who opened this shift (...) or a manager can close or cancel it." The result is a refusal, never a wrong close.

## `get_till_state(pos_profile=None, warehouse=None)`

`pos_profile` and `warehouse` are optional filters. Each is checked with `ensure_record_access`, so a record of another business is refused. With a warehouse, the session is the one on a POS Profile of that warehouse, and the held count is that store's bills plus bills held with no store (the same rule as `list_held_sales`).

Answer when a session is open:

```json
{
  "success": true,
  "data": {
    "state": "open",
    "session": {
      "name": "POS-OPE-2026-00007",
      "opened_on": "2026-10-13",
      "opened_at": "08:15",
      "cashier": "amina@shop.example",
      "cashier_name": "Amina Wanjiru",
      "pos_profile": "Main till",
      "opening": [{ "mode": "Cash", "amount": 5000.0 }]
    },
    "expected": [
      {
        "mode": "Cash", "key": "cash", "label": "Cash",
        "opening": 5000.0, "sales": 2350.0, "paid_in": 0.0,
        "refunds": 0.0, "paid_out": 0.0, "expected": 7350.0, "counts_cash": true
      },
      { "mode": "M-Pesa", "key": "mpesa", "label": "M-Pesa", "opening": 0.0, "sales": 800.0,
        "paid_in": 0.0, "refunds": 0.0, "paid_out": 0.0, "expected": 800.0, "counts_cash": true },
      { "mode": "On credit", "key": "credit", "label": "On credit", "opening": 0.0, "sales": 700.0,
        "paid_in": 0.0, "refunds": 0.0, "paid_out": 0.0, "expected": 0.0, "counts_cash": false }
    ],
    "cash_in_till": 7350.0,
    "held_count": 2,
    "recent_sales": [
      { "id": "ACC-PSINV-2026-00033", "receipt": "#0033", "time": "09:42",
        "customer": "Walk-in Customer", "total": 450.0, "status": "paid" }
    ],
    "errors": []
  }
}
```

Answer when no session is open for the caller (not an error):

```json
{ "success": true, "data": { "state": "closed", "session": null, "expected": [], "cash_in_till": null,
                             "held_count": 0, "recent_sales": [], "errors": [] } }
```

Fields:

- `session.opened_on` and `opened_at` are the session's start in the shop's local time (Africa/Nairobi, as Home shows it). `opening` lists the opening amounts counted when the till was opened.
- `expected` has one line per mode of payment used in the session, in this order: Cash, M-Pesa, Card, then other modes by name, then On credit last.
  - `key`: `cash`, `mpesa`, `card`, `credit` or `other`. It is the front end's `paymentKey` for the standard names, plus: a mode of ERPNext type Cash with an odd name (for example "Cash KES") is `cash`; "Customer Credit" is `credit`. Bank Draft, Cheque, Wire Transfer and anything unknown are `other`. This is the same reading Home uses for Cash in the till.
  - `label`: Cash, M-Pesa, Card, On credit, Other (Bank Draft, Cheque and Wire Transfer read Other); a mode with another name keeps its own name. Translated into the user's language.
  - `expected = opening + sales + paid_in - refunds - paid_out`. For cash, `sales` is the cash taken **minus the change given back** (so the sum shown adds up). `paid_in` is Payment Entries of type Receive made by the cashier since the session opened (a customer settling a debt); `paid_out` is Payment Entries of type Pay. `refunds` is returns paid back in that mode, never negative. This is the same rule as Home's "Cash in the till" (AC-09), so the two screens agree.
  - `counts_cash`: false only for On credit. A credit sale is not money in the till: the line shows what was sold on credit in `sales` (the unpaid part of the session's sales) and `expected` is 0. It is not counted at the shift end.
- `cash_in_till`: the expected of the cash lines, or null when the session has no cash line.
- `held_count`: held bills of the business (see above). Null if that part failed.
- `recent_sales`: up to 5 of **this session's** newest sales (returns included, with a negative total). `receipt` is the last run of digits of the invoice name, with a hash sign and at least four digits: `ACC-PSINV-2026-00033` gives `#0033`. Today's till shows the whole invoice name as "Receipt no."; this short form is derived only here, so the Sales list should use the same rule. `time` is HH:MM. `status` is `paid`, `unpaid` (an unpaid part remains) or `returned`. `returned` is not one of the front end's `StatusLabel` keys today.
- `errors`: names of the parts that failed (`held_count`, `recent_sales`) when the rest could be drawn; empty when all is well. The real error goes to the Error Log.

Errors: not signed in, a record of another business, or "Could not load the till. Please try again." (the details go to the Error Log).

## `close_till(session, counted=None, confirm=False)`

- `session`: the POS Opening Entry name, `session.name` from `get_till_state`.
- `counted`: a list `[{"mode": "Cash", "amount": 7250}]`, or the same as JSON text. `mode` matches a session line by name (any capitals) or, when only one line has that kind, by kind (`cash`, `mpesa`). Amounts are KES, not negative, same mode not twice. On credit cannot be counted. A non-cash mode left out is taken to be exactly what was expected, so nobody has to count M-Pesa.
- `confirm`: default false. `true`, `"true"`, `"1"`, `"yes"` close; false, `"false"`, `"0"`, empty do not.

### Preview (`confirm` false): changes nothing

```json
{
  "success": true,
  "data": {
    "closed": false,
    "lines": [
      { "mode": "Cash", "key": "cash", "label": "Cash", "expected": 7350.0, "counted": 7250.0, "difference": -100.0 },
      { "mode": "M-Pesa", "key": "mpesa", "label": "M-Pesa", "expected": 800.0, "counted": 800.0, "difference": 0.0 }
    ],
    "total_expected": 8150.0,
    "total_counted": 8050.0,
    "difference": -100.0,
    "word": "short",
    "by": 100.0,
    "needs_cash_count": false
  }
}
```

- `difference = counted - expected`: below zero is short, above zero is over. `word` is `short`, `over` or `matches` (a difference under half a cent matches). `by` is the size without the sign, for "Short by KES 100".
- Lines are the money lines only (On credit is left out), in the same order as `expected`.
- If the cash was not counted, the preview still answers: the Cash line has `counted` null and `difference` null, and `total_counted`, `difference`, `word` and `by` are null, `needs_cash_count` is true. The screen shows the expected cash and asks for the count.
- A session with no cash line at all never needs a cash count.

### Confirm (`confirm` true)

Refused with `error_type` `cash_count_required` if the cash was not counted (a count of 0 is a count). Otherwise the session is closed and the answer is the same as the preview plus:

```json
{ "success": true, "message": "Till closed. The shift has ended.",
  "data": { "closed": true, "outcome": "closed", "closing_entry": "POS-CLO-2026-00007", "lines": [ ... ], "...": "..." } }
```

`outcome` is `closed`, or `closing` when ERPNext merges the receipts in the background (10 or more receipts; `message` then says it can take a minute; `closed` is still true because the close has been accepted). The figures are worked out again at the moment of closing, so a sale made between the preview and the confirm is included and the lines in the answer are the final ones.

### Refusals (no crash)

These come back as `{ "success": false, "message": "...", "error_type": "..." }` (HTTP 200) in the user's language:

| `error_type` | When |
|---|---|
| `not_found` | No such session |
| `already_closed` | The session is already closed (`data.closing_entry` names the entry). Never closed twice, in preview or confirm |
| `not_open` | The session is cancelled, draft, or otherwise not open |
| `cash_count_required` | Confirm without a cash count |
| `bad_counted` | `counted` cannot be read, or an amount is missing or not a number |
| `negative_count` | A counted amount is below zero |
| `unknown_mode` | A counted mode is not one of the session's money lines |
| `duplicate_mode` | The same mode counted twice |
| `close_failed` or the existing path's own type (`validation_error`, ...) | The existing closing path refused or failed (for example a failed background merge: `closed` false, `outcome` `failed`) |

A person who may not close the session gets a `PermissionError`, as in the table above.

## What is written when a session closes

The close is the existing `sales_api.close_pos_opening_entry(session, do_not_submit=True)`, which builds the POS Closing Entry from ERPNext's `make_closing_entry_from_opening` exactly as today (period, POS Profile, user, company, grand total, net total, quantity, taxes, invoices, and one payment row per mode of payment). If a closing is already running, failed or finished it is handled by that code (retry, "closing", "closed") and nothing is counted again. When it leaves a new draft, this call then fills, on the rows of the draft's `payment_reconciliation` table:

- `closing_amount`: the counted amount (the expected amount for a non-cash mode left out)
- `difference`: counted minus the expected the cashier was shown
- `expected_amount`: the expected the cashier was shown

and saves the draft, then submits it the way `sales_api` does (as Administrator, so the receipts can be merged), and commits. `opening_amount` and every other field are left as ERPNext made them. A counted mode ERPNext did not list on the entry gets a new row with opening 0. The On credit line is not written.

Why `expected_amount` is overwritten: ERPNext adds each payment's amount and does not take off change given back, and it counts POS Invoices only (not till sales made as Sales Invoices, which eTIMS businesses use). Keeping its number would make "counted minus expected" on the entry disagree with the difference the cashier confirmed. These fields are information; they post nothing to the accounts. If the owner prefers ERPNext's own expected, remove `expected_amount` from `reconciliation_changes` in `till_calc.py`.

## Assumptions to check on a real site (no database was available)

- A session's sales are the submitted POS Invoices (not merged: `consolidated_invoice` empty) and the submitted Sales Invoices with `is_pos = 1` (adding the `is_created_using_pos`, `pos_closing_entry` and `is_consolidated` conditions only when the column exists), made by the session's cashier on the session's POS Profile since `period_start_date`. This is the rule of `sales_api._check_pos_opening_entry_has_invoices`.
- Payments are read from `tabSales Invoice Payment` (`base_amount`), change from `base_change_amount`, credit from `outstanding_amount`, as Home does. Payment Entries by the cashier since the session opened are read from `tabPayment Entry` (`base_paid_amount`, `payment_type`, `mode_of_payment`), as Home does.
- Times are read as the shop's local time, as Home does (the site timezone is assumed to be Africa/Nairobi).
- Held bills are `POS Held Sale` rows of the company.
- Sales made offline and not yet uploaded are not in the server's figures. The screen should ask the cashier to upload them before counting.
- The cashier's name comes from `User.full_name`.
- ERPNext's `POS Closing Entry Detail` has `closing_amount`, `difference`, `expected_amount` and `opening_amount` (read in the installed ERPNext 15 code); `difference` is only calculated in ERPNext's form script, never on the server, so writing it here is needed to record it.

## Words

Every message has a Swahili row in `murzak_pos/translations/sw.csv`. The labels (Cash, M-Pesa, Card, On credit, Other) and messages come back already translated for the signed-in user.
