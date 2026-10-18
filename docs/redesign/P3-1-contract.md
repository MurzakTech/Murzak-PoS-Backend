# P3-1 contract: payment methods and the payment switches in Settings

Files: `murzak_pos/api/payment_methods_api.py` (the calls), `murzak_pos/api/payment_methods_calc.py` (plain rules, no database), tests `test_payment_methods_calc.py` and `test_payment_methods_api.py` (84 tests, no database).

All three calls return `{"message": {"success": true, "data": {...}}}` over HTTP (Frappe wraps the return value in `message`). Errors are the usual Frappe error replies (`exc_type` and a plain sentence in `_server_messages`).

## What the switches mean

Four switches always exist, in this order: Cash, M-Pesa, Card, On credit. Every other Mode of Payment the shop has is listed under Other, each with its own switch.

A Mode of Payment name belongs to a switch by the same rule as `paymentKey` in `src/newlook/components/Chip.jsx`:

| Name (any case, trimmed) | Switch |
| --- | --- |
| contains `mpesa` or `m-pesa` (Mpesa, M-Pesa, MPESA, M-PESA Till) | mpesa |
| exactly `Cash` | cash |
| `Card` or `Credit Card` | card |
| `Credit` or `On credit` | credit |
| anything else (Bank Draft, Cheque, Mobile Money, Debit Card) | other |

`available` means the shop has at least one enabled Mode of Payment of that kind that its business may use (the same list the till already loads through `sales_api.list_payment_methods`, from `business_lists.modes_of_payment_for`). `enabled` is true only when the method is available and has not been switched off. Default when nothing was ever saved: every available method is on, so every till behaves as today.

## get_payment_methods

`GET murzak_pos.api.payment_methods_api.get_payment_methods?company=<optional>`

Any signed-in user of the business may call it (the till reads it). Guest: authentication error. A company the user does not belong to: permission error. `company` is optional when the user belongs to one business.

```json
{"success": true, "data": {
  "company": "Shop A",
  "methods": [
    {"key": "cash",   "label": "Cash",      "enabled": true,  "available": true,  "modes": ["Cash"],            "needs_code": false},
    {"key": "mpesa",  "label": "M-Pesa",    "enabled": false, "available": true,  "modes": ["M-Pesa", "Mpesa"], "needs_code": true},
    {"key": "card",   "label": "Card",      "enabled": false, "available": false, "modes": [],                  "needs_code": false},
    {"key": "credit", "label": "On credit", "enabled": false, "available": false, "modes": [],                  "needs_code": false}
  ],
  "other": [
    {"mode": "Cheque", "label": "Cheque", "enabled": true, "available": true}
  ]
}}
```

`methods` always has the four entries in that order, even for a shop with none. `modes` are the real Mode of Payment names that switch controls, alphabetical, and are what the till must send as `mode_of_payment`. `other` is alphabetical and every entry is available.

## set_payment_method

`POST murzak_pos.api.payment_methods_api.set_payment_method` with `key`, `enabled`, optional `mode`, optional `company`.

- `key`: `cash`, `mpesa`, `card`, `credit` or `other` (case and spaces forgiven).
- `enabled`: `true`/`false` (also `"true"`, `"1"`, `1`, `"on"` and the off spellings).
- `mode`: required when `key` is `other`; the exact Mode of Payment name from `data.other[].mode`. Ignored for the four fixed keys.
- Returns the same `data` shape as `get_payment_methods`, after the change.
- Idempotent: asking for what is already so writes nothing and returns the current state. Switching off something the shop does not have is also a no-op.

Role rule: owners only (System Manager or Administrator), checked first with `role_tiers.require_access("team")`, before anything is read or written. A cashier or manager gets a permission error ("Only a business owner or System Manager can do this."); a guest gets an authentication error. Only for a business the owner belongs to.

Refusals (validation error, nothing written, plain sentence):

| Case | Message |
| --- | --- |
| `enabled` is not an on/off value | Say whether this payment method should be on or off. |
| `key` is not one of the five | Choose Cash, M-Pesa, Card, On credit or Other. |
| `key` is `other` with no `mode` | Say which of the other payment methods you mean. |
| `mode` belongs to a fixed switch (for example `Cash`) | {0} has its own switch, so it cannot be changed under Other. |
| switching ON a method or mode the shop does not have | This shop has not set up {0} yet, so it cannot be switched on. |
| switching OFF the last way of taking money | At least one way of taking money must stay on. Switch another one on first. |

The last-method rule counts every Mode of Payment that is offered (a switch with two modes counts two), across the four switches and Other.

## get_till_payment_modes

`GET murzak_pos.api.payment_methods_api.get_till_payment_modes?company=<optional>`

Any signed-in user of the business. Returns `{"success": true, "data": {"company": "Shop A", "modes": ["Cash", "Mpesa", "Credit Card", "Credit", "Cheque"]}}`: Cash modes, then M-Pesa, Card, On credit, then the Other modes that are on. An empty list means the shop has no Mode of Payment at all; the till should then keep its own list as it does today.

## Where it is stored

One Frappe `DefaultValue` row per business, key `murzak_payment_methods:<company>` (a long company name is shortened to a SHA-1 fingerprint, as the New look switch does). The value is small text that lists only what is OFF: `{"off":["mpesa"],"off_other":["Cheque"]}`. No row means everything is on. No document type, custom field, migration or existing record is touched. Unreadable text is treated as "nothing off". A payment method the shop adds later starts on.

Switching only hides a method at the till. The Mode of Payment, its accounts and past sales are untouched.

## Assumptions not verified (no database here)

- `frappe.db.get_default` and `frappe.db.set_default` store a string of about 50 characters in `DefaultValue.defvalue` (a text column), the same way the New look switch does.
- `business_lists.modes_of_payment_for` is what the till loads (via `list_payment_methods`, enabled only); the till does not use POS Profile payments.
- The capability: `role_tiers.CAPABILITIES` has no settings entry, and "team" is the only owner-only one, so it is used through the constant `payment_methods_api.CAPABILITY`. No role_tiers behaviour was changed. If a `settings` capability is added later, change that one constant.
- The "Credit" Mode of Payment only exists once `create_credit_mode_of_payment` has run; until then On credit is unavailable and cannot be switched on.
- Name matching copies the front end rule exactly, so "M Pesa" (with a space) or "Mobile Money" counts as Other.
