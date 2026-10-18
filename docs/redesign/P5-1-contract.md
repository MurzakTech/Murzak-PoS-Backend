# P5-1 contract: the Expenses page

Four new server methods (ticket P5-1, acceptance check AC-30, open question 21). No existing method, record, report,
account or setting is changed. They sit in new files and import existing helpers; nothing existing was edited except two
additions of rows (`translations/sw.csv`, `EXPECTED_GUARDS` in `test_record_access.py`).

- `murzak_pos.api.expenses_api.get_expense_options` : categories, the paid-from choices (and whether each can be used), today's date
- `murzak_pos.api.expenses_api.create_expense` : adds an expense (POST). ONE submitted Journal Entry, with an optional receipt photo
- `murzak_pos.api.expenses_api.list_expenses` : the Expenses list and its three numbers
- `murzak_pos.api.expenses_api.get_receipt` : the receipt photo of one expense, ready to show in an image

Code: `murzak_pos/api/expenses_api.py` (database reads and writes, role checks) and `murzak_pos/api/expenses_calc.py` (all
rules, no database). Tests: `test_expenses_api.py` and `test_expenses_calc.py` (138 tests, no database: every Frappe call is
mocked; the list tests run the real SQL on an in-memory SQLite copy of the ledger).

Period rules come from `murzak_pos/api/overview_calc.py` (on main). Replies follow the usual shape
`{"message": {"success": true, "data": {...}}}`. An expected refusal is HTTP 200 with
`{"success": false, "message": "<plain sentence>", "error_type": "<code>", "field": "<form field>"}` (`field` is only there
when the sentence belongs beside one field of the form: `amount`, `category`, `paid_from`, `date`, `note`, `receipt`). A
person who is not allowed raises a Frappe error (`PermissionError`, `AuthenticationError`).

## Who may use these calls

Owner and manager only (`role_tiers.get_tier`). A cashier is refused with `PermissionError` ("Only an owner or a manager
can use Expenses.") as the very first statement, before any record is read. A guest gets `AuthenticationError`.
`get_receipt` also checks the entry's company with `ensure_record_access` (listed in `EXPECTED_GUARDS`). The company is
resolved with `resolve_company`, which refuses a company the caller does not belong to.

An owner or manager does not need accounting roles to add an expense: after the check above, the Journal Entry and the
receipt file are made with permission checks switched off (`ignore_permissions`), because a manager role such as Sales
Manager cannot create journal entries on its own. This is the one place these calls act with more rights than the caller
holds; it is limited to making this one entry.

## What an expense is (the owner's decision for open question 21)

ONE submitted Journal Entry, `voucher_type` "Journal Entry", company, `posting_date` = the date chosen, with two lines:

| Line | Account | Debit | Credit |
|---|---|---|---|
| 1 | the category (an existing expense account) | amount | 0 |
| 2 | the account behind "paid from" | 0 | amount |

Line 1 carries the company's default cost centre (when the company has one). No party. `user_remark` is
`<note> [expense:<request_id>]` (`[expense]` with no request id; the note may be empty). The marker tells the entries made
on the Expenses page from every other entry, and lets a retry find the entry it already made. A note can never contain
`[` or `]` (they are changed to round brackets), so a note cannot imitate the marker.

No account is created, renamed, enabled or changed. `create_expense` makes exactly one Journal Entry and, when a photo came
with it, one File. It reads accounts and modes of payment only.

### Paid from

The three choices are `cash` (Cash), `mpesa` (M-Pesa) and `bank` (Bank). Each is found from the shop's own enabled Modes of
Payment that have a Mode of Payment Account row (default account) for the company:

| Choice | Mode of Payment that counts |
|---|---|
| `mpesa` | its name contains "mpesa" or "m-pesa" (any case) |
| `cash` | the name "Cash", or type Cash (for example Petty Cash) |
| `bank` | type Bank (bank draft, cheque, wire transfer), not M-Pesa; "Card", "Credit Card" and "On credit" never count |

When several modes of one kind have an account the preferred one wins (the exact name "Cash" or "M-Pesa"; for bank a name with
"bank" in it), then alphabetical. The account must belong to the company, not be a group, not be disabled, not be frozen and
be in the company's currency (an expense asks for no exchange rate). If no mode of that kind has a usable account the
choice is NOT available and `create_expense` refuses in plain words (`no_account` or `account_unusable`) without writing.

### Categories

Existing accounts only: `company` = the shop, `root_type` = Expense, a leaf (`is_group` 0), not disabled, not frozen, and
Home counts it as a running cost (see below). Left out: account types Cost of Goods Sold, Stock Adjustment, Stock Received
But Not Billed, Expenses Included In Valuation, Expenses Included In Asset Valuation and Round Off; the company's own
`default_expense_account`, `stock_adjustment_account` and `stock_received_but_not_billed`; and any account whose name says
cost of goods, cost of sales, stock adjustment, stock received but not billed or round off. A category outside this list is
refused (`bad_category`).

### How it reaches Net profit (AC-30)

Home's Running costs (`dashboard_api._get_operating_expenses`, used by `dashboard_overview` for the Net profit tile and by
`profit_api`) is the sum of `GL Entry.debit - credit` for the company on accounts with `root_type` = Expense whose
`account_type` is not one of the five types above, except the company's `default_expense_account` and
`stock_adjustment_account`, over non-cancelled entries posted in the period. Submitting the entry posts the debit line on an
account that passes that rule (every category does, by construction), so Running costs rise by the amount and Net profit
(`summarize_profit`: gross profit minus running costs) falls by it. The credit line is on a cash, bank or M-Pesa account (an
asset), which Home does not count. Home caches its overview for 60 seconds, so the Net profit tile follows after that.

How AC-30 is proved (tests, no database): `TestAC30NetProfitFallsByTheAmount` in `test_expenses_calc.py` (the debit line of
`journal_lines` is counted by `counts_as_running_cost`, the credit line is not, and `summarize_profit` with the Build
Pack's example gives KES 23,000 before and 20,500 after a KES 2,500 expense); `TestHomeRuleIsMirrored` (the copied rule and
excluded types equal `dashboard_api.NON_OPERATING_EXPENSE_TYPES`, so a change in Home is noticed); and
`TestTheListMatchesHomesRunningCosts` in `test_expenses_api.py`, which runs Home's real query and the list's real query on
the same made-up ledger and checks they return the same number.

## `get_expense_options` (GET)

Request: `company` (optional).

```json
{ "success": true, "data": {
  "company": "Acme", "today": "2026-10-17",
  "categories": [ { "id": "Office Rent - A", "label": "Office Rent", "group": "Indirect Expenses" } ],
  "paid_from": [
    { "key": "cash",  "label": "Cash",   "available": true,  "reason": null, "mode_of_payment": "Cash" },
    { "key": "mpesa", "label": "M-Pesa", "available": false, "reason": "no_account", "mode_of_payment": null },
    { "key": "bank",  "label": "Bank",   "available": true,  "reason": null, "mode_of_payment": "Bank Draft" } ],
  "limits": { "note_length": 140, "receipt_bytes": 5242880, "receipt_types": ["image/gif","image/jpeg","image/png","image/webp"] } } }
```

- `categories` is sorted by label; `id` is the account name to send as `category`; `group` is the parent account's name.
- `paid_from` is always the three choices in the order Cash, M-Pesa, Bank. `reason` when not available: `no_mode` (the shop
  has no mode of payment of that kind), `no_account` (it has one but no account for this company), `account_unusable`.
  The page should disable an unavailable choice and say why in a line under it.
- `today` is the date in Nairobi; it is the latest date the form accepts.
- Account names behind the choices are not returned.
- Failure of a read: `{ "success": false, "message": "Could not load the expense choices. Please try again." }`.

## `create_expense` (POST)

### Request

| Field | Meaning |
|---|---|
| `amount` | above 0, at most 100,000,000, at most 2 decimals (more are rounded). A number or text such as `"1,200.50"` or `"KES 1200"` |
| `category` | a category `id` from `get_expense_options` |
| `paid_from` | `cash`, `mpesa` or `bank` (the labels Cash, M-Pesa, Bank are read too) |
| `date` | `YYYY-MM-DD`, today or earlier (Nairobi) |
| `note` | optional, at most 140 characters after tidying; a longer note is refused, not cut |
| `receipt` | optional photo, see below |
| `company` | optional |
| `request_id` | optional, 8 to 64 characters of letters, digits, `.`, `:`, `-`. The page should send a new one for each form and keep it for a retry |

### Receipt photo

Sent in the same call so the entry and its photo are saved together (nothing is left half done). `receipt` is a data URL
(`"data:image/jpeg;base64,...."`) or an object `{ "content": "<data URL or base64>" }` (a JSON string of that object is read
too). The front end reads the chosen file with `FileReader.readAsDataURL`. Rules: at most 5 MB (5,242,880 bytes) after
decoding; the file must really be a JPG, PNG, GIF or WebP picture, decided by its first bytes, not by its name or the type the
browser claims (so a PDF or an SVG renamed `.png` is refused). Phones usually hand over JPG; a HEIC photo is refused (the page
should say so). The file is stored as a PRIVATE Frappe `File` attached to the Journal Entry (`attached_to_doctype` "Journal
Entry", `attached_to_name` the entry), named `receipt-<entry>.<ext>` by the server; the name the person's file had is never used.
Because no separate upload call is needed, the front end does NOT use `upload_file` for receipts.

### Response

```json
{ "success": true, "data": { "repeat": false, "expense": {
  "id": "ACC-JV-2026-00042", "key": "Journal Entry:ACC-JV-2026-00042:Office Rent - A", "date": "2026-10-16", "amount": 1500.0,
  "category": { "id": "Office Rent - A", "label": "Office Rent" }, "note": "Rent for October",
  "kind": "expense", "source": "Added on the Expenses page", "paid_from": { "key": "cash", "label": "Cash" },
  "has_receipt": false, "added_by": "Amina Owner", "reference_type": "Journal Entry", "reference": "ACC-JV-2026-00042" } } }
```

`expense` is the same row `list_expenses` returns. A repeat (same `request_id` and company, an entry already submitted with that
marker) returns the first entry with `"repeat": true` and changes nothing (no entry, no file, no commit); the amount, category,
date and receipt of the repeat are NOT compared with the first.

### Refusals (`error_type`, `field`)

`no_amount`, `bad_amount`, `not_above_zero`, `too_big` (amount); `no_date`, `bad_date`, `future_date` (date); `note_too_long`
(note); `no_category`, `bad_category` (category); `no_paid_from`, `bad_paid_from` (paid_from); `bad_receipt`,
`receipt_too_big`, `receipt_not_image` (receipt); `no_account`, `account_unusable` (paid_from; for example "This shop has no
M-Pesa account set up, so an expense cannot be paid from M-Pesa. Choose another way, or ask your accountant to link an account
to it."); `bad_request_id`; `server_error` ("The expense was not saved, so nothing was changed." plus the reason when the books
gave one, such as a date outside every fiscal year or a frozen period, otherwise "Please try again."). The typed values are
checked first, before any record is read or written.

### Transaction

Typed values are checked → company resolved → (when a `request_id` is given) the Company row is locked and the earlier entry
looked up → category and paid-from account checked → entry inserted and submitted → photo attached → ONE `frappe.db.commit()`.
Any exception rolls everything back (`frappe.db.rollback()`), logs the error and returns `server_error`.

## `list_expenses` (GET)

### Request

| Field | Meaning |
|---|---|
| `period` | `today`, `week`, `7d`, `30d` (default), `month` or `custom` (rules of `overview_calc.resolve_period`; the page offers Today, 7 days, 30 days, Custom) |
| `from_date`, `to_date` | `YYYY-MM-DD`, for `custom` (at most a year; a future end is cut back to today) |
| `category` | an account `id`; limits the rows (not the three numbers) |
| `search` | up to 60 characters. Looks in the category name, the note and the system reference. The marker in a remark is not searchable |
| `page`, `page_size` | from 1; size 20 by default, at most 100; a page past the end is the last page |
| `company` | optional |

A bad period raises `ValidationError` with a plain sentence. Dates filter on the ledger posting date, newest first (then newest
created).

### Response

```json
{ "success": true, "data": {
  "rows": [ { "id": "ACC-JV-2026-00042", "key": "Journal Entry:ACC-JV-2026-00042:Office Rent - A", "date": "2026-10-16", "amount": 1500.0,
              "category": { "id": "Office Rent - A", "label": "Office Rent" }, "note": "Rent for October", "kind": "expense",
              "source": "Added on the Expenses page", "paid_from": { "key": "cash", "label": "Cash" }, "has_receipt": true,
              "added_by": "Amina Owner", "reference_type": "Journal Entry", "reference": "ACC-JV-2026-00042" } ],
  "total": 5, "page": 1, "page_size": 20, "pages": 1,
  "period": { "key": "30d", "from": "2026-09-18", "to": "2026-10-17", "days": 30 },
  "summary": { "total": 6300.0, "count": 5, "top_category": { "id": "Electricity - A", "label": "Electricity", "amount": 4000.0 } },
  "spent_in_list": 6300.0 } }
```

- **Which rows.** The list reads the general ledger exactly as Home reads Running costs, so it holds the entries made on the Expenses page
  AND every other entry Home counts (a supplier bill for power, a journal entry the accountant made, a refund). A row is one
  entry and one expense account (several lines of the same entry on the same account are added together). An entry that nets
  to 0 on an account is not shown. `kind` is `"expense"` for an entry made on the Expenses page and `"other"` for the rest.
  This is wider than "journal entry lines" because Home counts every ledger line on an expense account, whatever made it;
  listing only journal entries would make the total differ from Home's.
- `total` is the exact number of rows matching the filters (a `COUNT(*)` over the same query as the rows). `spent_in_list` is the
  sum of those rows (what "x of y" covers); it follows the category and search.
- `summary` follows the period only (not the category, search or page), so `summary.total` equals Home's Running costs for the
  same days. `count` is the number of rows in the period. `top_category` is the category that cost most (null when there is none).
- `amount` is what Home counts: debit minus credit. A refund or correction is negative.
- Fields of a row from another source (`kind: "other"`): `source` names it in plain words (Supplier bill, Journal entry, Payment,
  Expense claim, Stock entry, Sale, Asset, else Other entry); `paid_from` is null; `has_receipt` false; `added_by` null;
  `note` is the entry's own remark, cut to 140 characters. `reference_type` and `reference` are the system record (shown under
  Details, never in the list).
- `paid_from` of a row made here is the choice its credit account belongs to, or `{ "key": "other", "label": "<account name>" }`
  when that account is not behind any choice now.
- Failure of a read: `{ "success": false, "message": "Could not load the expenses. Please try again." }`.

## `get_receipt` (GET)

Request: `expense` (the row `id` of an expense made on the Expenses page). Response:

```json
{ "success": true, "data": { "data_url": "data:image/png;base64,....", "content_type": "image/png", "size": 123456 } }
```

Refusals: `not_found` (no such submitted entry made on the Expenses page; an entry made another way has no receipt here),
`no_receipt`. The photo is a private file; it is read on the server after the owner or manager check and sent as a data URL,
so a manager without accounting roles (who cannot open the private file address) can see it. Other people's businesses are
refused by `ensure_record_access`.

## Assumptions that could not be verified (no database)

- ERPNext 15 names: `tabGL Entry` (`voucher_type`, `voucher_no`, `account`, `posting_date`, `debit`, `credit`, `company`,
  `is_cancelled`, `remarks`, `creation`), `tabAccount` (`root_type`, `account_type`, `account_name`, `is_group`, `disabled`,
  `freeze_account`, `account_currency`, `parent_account`), `tabJournal Entry` (`user_remark`, `owner`, `docstatus`), `Journal Entry
  Account` (`account`, `debit_in_account_currency`, `credit_in_account_currency`, `cost_center`), `Mode of Payment`
  (`enabled`, `type`), `Mode of Payment Account` (`default_account`, `company`), Company fields `default_currency`, `abbr`,
  `cost_center`, `default_expense_account`, `stock_adjustment_account`, `stock_received_but_not_billed`.
- A Journal Entry with two lines on accounts of the company currency submits without an exchange rate, party or reference date; a
  cost centre is needed on a profit-and-loss line only when the company has one, which is why the default is set when present.
- The GL Entry of a submitted entry is written immediately, so a new expense shows in Running costs at once (Home's 60 second
  cache is the only delay).
- `GROUP BY ... HAVING` over `GL Entry` joined to `Account` and `Journal Entry` runs on MariaDB with `ONLY_FULL_GROUP_BY` (every
  selected column is grouped or aggregated); the tests run the same text on SQLite, which is not MariaDB.
- A `File` made with `content` bytes and `is_private` 1 is stored under `private/files`; an upload type allow-list in System
  Settings (if the shop set one) could refuse a type; the failure is reported as "not saved" and everything is rolled back.
- `SUBSTRING_INDEX`, `NULLIF` and `LIKE` with a backslash escape behave on MariaDB as the tests assume.
- If the books have a closed period or a fiscal year gap for the chosen date, the Journal Entry refuses and the person sees the
  books' own sentence after "The expense was not saved, so nothing was changed."
