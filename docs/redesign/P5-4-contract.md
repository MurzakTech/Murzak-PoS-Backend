# P5-4 contract: first-time set-up (`murzak_pos.api.setup_api`)

Four new calls, all in `murzak_pos/api/setup_api.py`, with the plain rules in `murzak_pos/api/setup_calc.py`. Nothing existing
is changed: no record, report, setting or method. Every reply is `{ "success": true, "data": { ... } }` (Frappe wraps it in
`message`). A refusal is an ordinary Frappe error (`frappe.throw`) whose message is a plain sentence.

## Where the answers are kept

In Frappe's own `DefaultValue` table, one key per business and kind of answer, exactly like the New look switch
(`new_look_api.py`). That table exists on every site, so there is no new document type, custom field or migration.

| Key | Holds |
|---|---|
| `murzak_setup_kind:<company>` | `pharmacy`, `retail`, `services` or `other`. Absent until the owner answers |
| `murzak_setup_progress:<company>` | JSON `{"done":[steps],"skipped":[steps],"finished":bool}` |
| `murzak_setup_cash:<company>` | The usual opening cash, a number as text. Absent or `0` means no suggestion |

A company name too long for the 140-character key is replaced by a SHA-1 fingerprint, as in `new_look_api.py`.

The kind only ADDS reasons to show a thing. A business that never answers has every feature off, which is today's screens
exactly. Nothing a business sees today is hidden by it.

## Who may do what

- Read (`get_setup_state`, `get_business_features`): anyone signed in, for a company they belong to (`resolve_company`).
- Write (`save_*`): owners only (`access_control.require_manager`: a System Manager, or the Administrator), POST only, and only
  for a company the person belongs to. The owner check is the first line, before anything is read or written. Refusal:
  `PermissionError` "Only a business owner or System Manager can do this." (cashiers and managers alike), or
  `AuthenticationError` for a guest.

## The steps and the kinds

Steps, in wizard order: `business` ("What do you sell?"), `shop`, `cash`, `payments`, `products`, `staff`.
Kinds: `pharmacy`, `retail`, `services`, `other`.

Features per kind (every name is always present in `features`, `true` or `false`):

| Feature | pharmacy | retail | services | other |
|---|---|---|---|---|
| `expiry` (expiry tracking and the Expiry tab) | true | false | false | false |
| `prescription_only` (prescription-only products) | true | false | false | false |
| `barcodes_first` | false | true | false | false |
| `units_first` | false | true | false | false |
| `services_first` | false | false | true | false |

## `get_business_features(company=None)`

Reply `data`:

```json
{ "company": "Shop A", "kind": "pharmacy", "features": { "expiry": true, "prescription_only": true, "barcodes_first": false,
  "units_first": false, "services_first": false }, "opening_cash": 5000.0 }
```

`kind` is `null` when never answered. `opening_cash` is the usual opening cash (0 when none); a cashier receives it because the
Open till dialog uses it as a suggestion.

## `get_setup_state(company=None)`

Reply `data` holds everything of `get_business_features` plus:

| Field | Meaning |
|---|---|
| `needs_setup` | The one rule for a NEW store: no business kind recorded (by the wizard, or chosen at sign-up), no submitted sale, fewer than 5 products, wizard not finished |
| `in_progress` | The wizard was started (a step passed) and not finished, and the business still has no submitted sale and under 5 products |
| `offer` | `needs_setup` or `in_progress`: whether Home should offer "Set up now" |
| `suggested_kind` | The kind that the sign-up industry suggests (`PHARMACY` gives `pharmacy`, `GROCERY` gives `retail`, `SALON` gives `services`, and so on), or `null`. A suggestion only; no feature is turned on by it |
| `done_steps`, `skipped_steps` | Steps completed and steps skipped, in wizard order |
| `next_step` | The first step not passed (where a refresh continues), or `null` when all six were passed |
| `finished` | "Finish" or "Skip for now" was pressed |
| `can_edit` | The caller is an owner |

The counts: "submitted sale" is any submitted Sales Invoice or POS Invoice of the company (`docstatus = 1`, returns included, as
only existence matters); "products" is the number of undisabled `Item` records with `custom_company` = the company, counted only
up to 5. When the wizard is finished neither is read. If a count cannot be read, it counts as "has data", so a failure never
offers the wizard to a real shop (the error goes to the Error Log).

## `save_business_kind(kind, company=None)` (POST, owners)

Keeps the answer and marks step `business` done. `kind`: one of the four (case ignored). Reply `data`: the
`get_business_features` fields plus `done_steps`, `skipped_steps`, `finished`, `next_step`. Error `ValidationError`
"Choose what you sell: pharmacy, retail, services or other." for anything else; nothing is written.

## `save_setup_progress(step, skipped=0, company=None)` (POST, owners)

`step` is one of the six step names, or `finish`. A step is recorded as done, or as skipped when `skipped` is `1`/`true`; a step
already done is not undone by skipping it, and a skipped step done later leaves the skipped list. `finish` marks the whole
wizard finished: the wizard's "Finish" and "Skip for now" both send it. Reply `data`: `company`, `done_steps`,
`skipped_steps`, `finished`, `next_step`. Error `ValidationError` "Choose a set-up step." for an unknown step.

## `save_opening_cash(amount, company=None)` (POST, owners)

Keeps the usual opening cash and marks step `cash` done. `amount` is zero or more, at most two decimals (commas are ignored);
blank or `0` clears the suggestion. Reply `data`: `company`, `opening_cash`, and the progress fields. Error `ValidationError`
"Enter the opening cash as an amount of zero or more." for text, a negative or a huge number. The amount is only a suggestion:
the till's Open dialog prefills it and the person opening the till can change it; no accounting entry is made.

## Assumptions that could not be checked (no database exists here)

- `Item` carries `custom_company` and `disabled` (used by `access_control.OWNER_FIELD` and the Items list calls).
- `Sales Invoice` and `POS Invoice` carry `company` and `docstatus`.
- `User.custom_pos_industry` links to a `POS Industry` whose `industry_code` is one of the fixture codes; read inside a
  try/except, so a site without the field simply gets no suggestion.
- `frappe.db.get_default` / `set_default` behave as for the New look switch (a text value per key, committed with the request).

## Tests

`murzak_pos/api/test_setup_calc.py` (the rules) and `murzak_pos/api/test_setup_api.py` (the calls with every Frappe call
mocked): who may read and write, nothing written for a refusal, one business never touching another, the new-store rule, a
count that fails, the source-level check that each writing call checks for an owner first. Swahili rows for the three
messages are at the end of `murzak_pos/translations/sw.csv`.
