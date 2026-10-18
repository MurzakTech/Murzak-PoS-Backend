# P3-7 contract: `sales_list_api.list_sales` and `sales_list_api.get_sale`

Three new server methods for the Sales page (ticket P3-7). No existing method, record or report is changed. They read
both kinds of sale document, so the Sales page shows a POS invoice from the moment it is made, and keeps showing it
after the till is closed.

- `murzak_pos.api.sales_list_api.list_sales` : the list, the summary row and the Till sessions tab
- `murzak_pos.api.sales_list_api.get_sale` : the receipt panel for one sale
- `murzak_pos.api.sales_list_api.take_payment` : record money received on an unpaid sale (a financial write, see its section)

Code: `murzak_pos/api/sales_list_api.py` (database reads, role checks), `murzak_pos/api/sales_list_calc.py` (all rules,
no database). Tests: `murzak_pos/api/test_sales_list_api.py`.

The period rules come from `murzak_pos/api/overview_calc.py` (ticket P2-1, already on main).

Replies follow the usual shape: `{"message": {"success": true, "data": {...}}}` over HTTP. A refusal raises a Frappe
error (HTTP 4xx, `exc_type` set), a read that fails returns `{"success": false, "message": "..."}`.

## What counts as a sale

Every **submitted POS Invoice, merged or not**, and every submitted Sales Invoice **except the consolidated one made
when a till was closed**. Drafts and cancelled documents are never listed. Returns (credit notes) are listed as their
own rows.

Closing a till makes ERPNext merge that shift's POS Invoices into one Sales Invoice. The owner decided that the
individual receipts stay in the list (they are what customers hold) and the consolidated invoice is hidden, so nothing
is counted twice in the rows, in `total` or in the summary.

How the consolidated invoice is recognised (the rule the existing code uses):

- `Sales Invoice.is_consolidated = 1` (what `sales_api._check_pos_opening_entry_has_invoices` reads; used only if the column exists), or
- a POS Invoice names it in `consolidated_invoice` (what `sales_figures.py` and the Home overview read on POS Invoices)

Either one hides the invoice. A POS Invoice Merge Log is not read; it is not needed. Home counts the other way round
(the consolidated invoice instead of its receipts), so the two count the same sales (Home adds up the net figure, this list the full amount with VAT).

**Money still owed on a merged receipt.** After a merge the debt lives on the consolidated invoice; a later payment
reduces that invoice, not the receipt. So for a merged receipt the list reads `outstanding` as: 0 once the
consolidated invoice is paid, otherwise the receipt's own balance but never more than the consolidated invoice still
owes (`effective_outstanding`; the same rule in the Unpaid tab, the summary, the row and the receipt). Approximation to
know about: a payment is not tied to one receipt, so when one customer has two unpaid merged receipts and pays part of
the total, both may keep showing a balance until the consolidated invoice is paid off; the amounts a person can take
are capped by what the consolidated invoice owes, so the shop never takes more than is owed in total.

## Money: full amount, VAT included

Home's revenue is net of VAT. The Sales list is a list of what customers paid, so every row and the receipt carry the
full `grand_total` as `total`, VAT included. The net figure is not shown on this page. A return has a **negative**
`total`. The summary's `returned` is a positive number.

The list row and the receipt read their money through one function (`sale_amounts`), so the same sale has the same
`total`, `paid` and `outstanding` in both (AC-24 at the endpoint level; tested).

- `total` = `grand_total`
- `outstanding` = `outstanding_amount` (for a merged POS Invoice, see above)
- `paid` = `total` - `outstanding` (what has been settled; change handed back is not part of it)

## `list_sales`

### Request

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `tab` | text | `all` | `all`, `unpaid`, `returns` or `sessions` (case is ignored) |
| `period` | text | `30d` | `today`, `week`, `7d`, `30d`, `month` or `custom` (the same keys and rules as the Home overview; days are Nairobi days) |
| `from_date`, `to_date` | `YYYY-MM-DD` | none | needed (both) for `custom`; an end date in the future is cut back to today; at most 366 days |
| `search` | text | none | see Search |
| `cashier` | user id (email) | none | owner and manager only: only that person's sales or sessions. Ignored for a cashier |
| `store` | Warehouse name | none | only sales with at least one line from that store; for sessions, the till settings that use that store |
| `page` | number | 1 | from 1 |
| `page_size` | number | 20 | 1 to 100; a missing or wrong size is 20, above 100 is 100 |

Tabs:

- `all` : every sale and every return
- `unpaid` : sales (not returns) with an amount outstanding above 0
- `returns` : return documents only
- `sessions` : Till sessions (see below)

### Response

```json
{
  "success": true,
  "data": {
    "rows": [ ... ],
    "summary": { "sold": 6350.0, "unpaid": 1000.0, "returned": 500.0 },
    "total": 3,
    "page": 1,
    "page_size": 20,
    "pages": 1
  }
}
```

`total` is the number of rows for the tab and filters (not just this page). `page` is the page actually returned: a
page past the end becomes the last page and a page below 1 the first, so the list is never blank because of an old page
number. An empty list has `pages: 1`.

`summary` is for the **same filters (period, search, cashier, store) but not the tab**, so the three numbers do not
change when the tab changes:

- `sold` : total of the submitted sales that are not returns
- `unpaid` : the amount still outstanding on those sales (a credit balance never reduces it)
- `returned` : the value of the return documents, as a positive number

Order: newest first by posting date, then posting time, then document name (all descending). Both kinds of document are
one list.

### Row for the tabs `all`, `unpaid`, `returns`

```json
{
  "id": "ACC-PSINV-2026-00033",
  "doctype": "POS Invoice",
  "receipt": "#0033",
  "reference": "ACC-PSINV-2026-00033",
  "date": "2026-10-13",
  "time": "10:00",
  "customer": "Wanjiru Kamau",
  "cashier": "Amina Hassan",
  "total": 2350.0,
  "paid": 2000.0,
  "outstanding": 350.0,
  "status": "unpaid",
  "methods": ["M-Pesa", "On credit"],
  "is_return": false
}
```

- `id` : the document name. This is the key (use it for `get_sale`, for the row's address and for the focus return)
- `doctype` : `POS Invoice` or `Sales Invoice`
- `receipt` : the short number, see the rule below
- `reference` : the full system reference. **Show it only under Details, never in the list**
- `date`, `time` : posting date `YYYY-MM-DD` and posting time `HH:MM`
- `customer` : the customer's name, or `Walk-in Customer` when the sale has none
- `cashier` : the full name of the person who made the sale (their user id if they have no full name)
- `total`, `paid`, `outstanding` : see Money
- `status` : one of
  - `returned` : a return document, or a sale whose returns add up to all of it
  - `overdue` : money outstanding and the due date has passed (the customer's payment terms if they have any, otherwise 14 days after the sale, as on Home)
  - `unpaid` : money outstanding, not yet late (this includes a part payment)
  - `part-returned` : settled, but some of it was returned
  - `paid`
  Money owed comes before a part return, because it is what the owner has to act on.
- `methods` : how it was paid, each once, in the till's order: `M-Pesa`, `Cash`, `Card`, `On credit`, `Other` (Credit Card is Card; Bank Draft, Cheque and Wire Transfer are Other). A sale with money still owed also lists `On credit`. A return lists the methods of its refund rows
- `is_return` : true for a return document

### Row for the tab `sessions`

```json
{
  "id": "POS-OPE-2026-00004",
  "cashier": "amina@shop-a.test",
  "cashier_name": "Amina Hassan",
  "opened_on": "2026-10-12",
  "opened_at": "08:05",
  "closed_on": "2026-10-12",
  "closed_at": "18:40",
  "status": "closed",
  "sales_count": 14,
  "expected": 5500.0,
  "counted": 5400.0,
  "difference": -100.0,
  "closing_entry": "POS-CLO-2026-00004"
}
```

- `status` : `open` or `closed` (the POS Opening Entry's status)
- `opened_on`, `opened_at`, `closed_on`, `closed_at` : from the opening entry's start and the closing entry's end; `closed_*` are null while open. `closed_on` is extra to the proposal, for a shift that ends after midnight
- `sales_count` : receipts (not returns) the cashier made on that till between opening and closing
- `expected`, `counted`, `difference` : **cash only** (the Cash rows of the closing entry; other methods are left out). `difference` is counted minus expected, so a shortage is negative
- all three are `null` while the session is open, and `null` when it was closed without a closing entry. `counted` and `difference` are also `null` when the till recorded no count (ERPNext then leaves both at 0; this is the same rule the shift summary uses). A count that matches exactly is `difference: 0`, not null
- `closing_entry` : the submitted POS Closing Entry, or null
- Sessions are filtered by the day the session opened (`period`), by `cashier` (the session's user), by `store` and by `search` (matches the session id and the user)
- A cancelled opening entry is not listed

### Search

`search` (60 characters at most) is matched against:

1. the customer name and customer id (part of the text, any case)
2. the **short receipt number**, when the text looks like one: `33`, `0033`, `#0033`, `S33`, `#S0033`. A plain number matches that receipt of either kind; with the letter S only Sales Invoices. The number is matched against the end of the system reference, never as text, so `26` does not match every sale of 2026
3. the **system reference**, when the text is not a receipt number (for example `PSINV-2026-00033`)

### Errors

| When | What happens |
|---|---|
| not signed in | `AuthenticationError`, "Please sign in first." |
| `store` belongs to another business | `PermissionError` (the usual record access check) |
| unknown `tab` | `ValidationError`, "Choose a tab: all, unpaid, returns or sessions." |
| unknown `period`, missing or bad dates, start after end, more than a year | `ValidationError` with a plain message |
| a read fails | `{"success": false, "message": "Could not load the sales. Please try again."}`; the detail goes to the Error Log and is never returned |

### Role rules (enforced by the server)

| Who | Sees |
|---|---|
| Owner, Manager | every sale and session of their business; may filter by `cashier` |
| Cashier | only sales whose owner is them and only their own sessions, whatever `cashier` says. The summary is theirs too |

Cashiers get no profit, cost or net figure here, because the call has none.

## `get_sale`

### Request

| Parameter | Type | Meaning |
|---|---|---|
| `id` | text | the document name (`ACC-PSINV-2026-00033`) |
| `doctype` | text, optional | `POS Invoice` or `Sales Invoice`. Without it the server looks in POS Invoices, then Sales Invoices |

### Response

```json
{
  "success": true,
  "data": {
    "id": "ACC-PSINV-2026-00033",
    "doctype": "POS Invoice",
    "receipt": "#0033",
    "reference": "ACC-PSINV-2026-00033",
    "date": "2026-10-13",
    "time": "10:00",
    "customer": { "name": "Wanjiru Kamau", "mobile": "0712345678" },
    "cashier": "Amina Hassan",
    "status": "part-returned",
    "is_return": false,
    "return_against": null,
    "merged_into": null,
    "lines": [
      { "item_code": "ITM-A", "item_name": "Sugar 1kg", "qty": 2.0, "rate": 180.0, "amount": 360.0 }
    ],
    "totals": {
      "subtotal": 2126.0, "discount": 100.0, "tax": 324.14,
      "total": 2350.0, "paid": 2350.0, "outstanding": 0.0, "change": 150.0
    },
    "payments": [
      { "mode": "Mpesa", "label": "M-Pesa", "amount": 1000.0, "reference": "SGH7K2L9QX" }
    ],
    "returns": [
      { "id": "ACC-PSINV-2026-00040", "receipt": "#0040", "total": -350.0 }
    ],
    "can": {
      "take_payment": false, "take_payment_blocked": null,
      "return_items": true, "print": true, "whatsapp": true
    }
  }
}
```

- `status`, `receipt`, `reference`, `date`, `time`, `cashier`: as in the list row
- `customer.mobile` : the customer's mobile number, or the number on the invoice; null when there is none
- `return_against` : the id of the original sale, for a return; null otherwise
- `merged_into` : for a POS Invoice that was merged at till close, the id of the consolidated Sales Invoice (show it under Details only); null otherwise
- `lines` : quantity, rate and line amount (negative quantities on a return)
- `totals.subtotal` : the lines added up (after item offers); `discount` : the extra discount on the whole sale (positive); `tax` : tax and charges added; `total` : the full amount, the same as the list row's `total`; `change` : change handed back
- `payments` : one per payment row with a non-zero amount. `mode` is the payment mode as stored, `label` the group (M-Pesa, Cash, Card, On credit, Other), `reference` the M-Pesa code or other reference number (null when none). A cash payment shows the cash given; `totals.change` is what went back
- `returns` : the submitted returns made against this sale, oldest first, with the return's own receipt number and a negative total
- `can.take_payment` : `take_payment` would accept a payment on this sale now: it is not a return, `outstanding` is above 0, and it is a Sales Invoice or a merged POS Invoice
- `can.take_payment_blocked` : `pay_after_close` when the sale owes money but is a POS Invoice still on an open shift; `consolidated` for the invoice made at till close; otherwise null. The page can show the reason beside a disabled button
- `can.return_items` : the person's role may record returns (System Manager, Sales Manager, Accounts Manager or the Administrator, as in `sales_api.create_sales_return`; so a Cashier never gets true), the sale is not itself a return and not yet fully returned
- `can.print` : always true
- `can.whatsapp` : the customer has a mobile number

A POS Invoice that has been merged into a consolidated Sales Invoice can still be opened here by its id (the same for a cashier who made it).

### Errors

| When | What happens |
|---|---|
| not signed in | `AuthenticationError` |
| the sale belongs to another business | `PermissionError` ("You do not have access to this POS Invoice." or Sales Invoice) |
| a Cashier asks for a sale made by someone else | `PermissionError`, "You can only see your own sales." |
| no such sale, or it is a draft or cancelled | `{"success": false, "message": "Sale <id> was not found.", "error_type": "not_found"}` |
| `doctype` is neither of the two | `ValidationError`, "Choose POS Invoice or Sales Invoice." |
| a read fails | `{"success": false, "message": "Could not load this sale. Please try again."}` |

Owner and Manager may read any sale of their business.

## The short receipt number

The till has no short number today: `ReceiptDialog.jsx` prints `invoice.name`, the full system reference. The Build Pack
asks for `ACC-PSINV-2026-00005` to become `#0005`, so the number is **worked out from the name**, the same way every
time, and nothing is stored:

1. take the last run of digits of the name (`ACC-PSINV-2026-00005` gives 00005, that is 5)
2. write it with at least 4 digits after a hash: `#0005`. A longer number is kept whole: `#12345`
3. a **POS Invoice** has no letter; a **Sales Invoice** has an `S`: `ACC-SINV-2026-00005` is `#S0005`. ERPNext numbers the two kinds separately, so without the letter `#0005` could be two different sales
4. an amended copy (`ACC-PSINV-2026-00005-1`) keeps the number of its original (the original is cancelled)
5. a name with no number at the end is shown as it is

A return has its own document and its own number (`#0040`), not the number of the sale it returns.

**Collision risk.** ERPNext's default invoice series restarts every year (`ACC-PSINV-.YYYY.-`), so the 5th POS invoice
of 2026 and of 2027 both read `#0005`. The year is not in the short number, because the Spec shows `#0033`. Use the
`id` as the key everywhere, and show the `date` beside the number (the list has a date column). A shop with a series
that does not restart has no collision. Within one year, and within one kind, numbers are unique. If a shop that sells
through Sales Invoices only finds `#S0033` odd, the S is the price of keeping the two kinds apart.

## Assumptions about ERPNext fields (not verifiable without a site)

POS Invoice and Sales Invoice (read through SQL and `frappe.get_doc`): `name`, `docstatus`, `company`, `owner`,
`posting_date`, `posting_time`, `customer`, `customer_name`, `grand_total`, `outstanding_amount`, `change_amount`,
`total`, `discount_amount`, `total_taxes_and_charges`, `is_return`, `return_against`, `due_date`, `contact_mobile`;
child tables `items` (`item_code`, `item_name`, `qty`, `rate`, `amount`, `warehouse`) and `payments`
(`mode_of_payment`, `amount`, `reference_no`; the table is `Sales Invoice Payment` for both). POS Invoice only:
`consolidated_invoice`. Sales Invoice only: `is_consolidated` (read only if the column exists), `customer`. Customer: `mobile_no`,
`payment_terms`. User: `full_name`.

POS Opening Entry: `name`, `docstatus`, `company`, `user`, `pos_profile`, `status` (`Open`/`Closed`), `posting_date`,
`period_start_date`. POS Profile: `warehouse`, `company`.

POS Closing Entry: `name`, `docstatus`, `pos_opening_entry`, `period_end_date`; child table `POS Closing Entry Detail`
(`payment_reconciliation`) with `mode_of_payment`, `expected_amount`, `closing_amount`, `difference`.

Other assumptions: only submitted closing entries are used; a session is joined to the receipts of the same user and
the same till settings whose posting time lies between the opening and the closing (the rule the Home till card
already uses); a payment row mode is grouped by the same function as on Home (`overview_calc.classify_method`).

## `take_payment`

Records money received on an unpaid sale as an ERPNext Payment Entry. The page calls
`murzak_pos.api.sales_list_api.take_payment` with `{id, doctype, amount, mode, reference, request_id}`.

### Request

| Parameter | Type | Meaning |
|---|---|---|
| `id` | text | the sale's document name |
| `doctype` | text | `POS Invoice` or `Sales Invoice` (required) |
| `amount` | number or text | shillings, more than 0, at most 2 decimals (more are rounded); a thousands comma in text is accepted |
| `mode` | text | `cash`, `mpesa` or `card` (also `M-Pesa`, `m-pesa`: case and dashes are ignored) |
| `reference` | text, optional | **required for `mpesa`**: the M-Pesa code, 8 to 12 letters and numbers, saved in capitals. Optional for cash and card (kept as typed, 40 characters at most) |
| `request_id` | text, optional | a unique string the page makes for each tap (letters, numbers, `.`, `:`, `-`, 64 characters at most). Send it every time |
| `company` | text, optional | the business; must be the sale's own business |

`mode` is a **kind**, not a mode of payment name. The server maps it to the shop's own enabled Mode of Payment of that kind
(the ones `business_lists.modes_of_payment_for(company)` allows), using the naming rule Home uses
(`overview_calc.classify_method`): "Credit Card" is Card, "Mpesa" and "M-Pesa" are M-Pesa. A mode named exactly like the
kind wins; otherwise the first of that kind in alphabetical order.

### Response

```json
{ "success": true, "data": { "payment": "ACC-PAY-2026-00012", "sale": { ...the same shape as get_sale, after the payment... } } }
```

A refusal that is expected is `{"success": false, "message": "<plain sentence>", "error_type": "<code>"}` (HTTP 200).
Nothing is recorded for any refusal. `error_type` values:

| error_type | When |
|---|---|
| `bad_amount` | not a number, zero, negative, NaN, infinite, a yes/no value |
| `too_much` | more than is still owed (no overpayment). For a merged receipt: more than the consolidated invoice still owes |
| `bad_mode` | `mode` is not cash, mpesa or card, or the shop has no enabled mode of that kind |
| `no_account` | the shop's mode has no account for this business (or one in another currency) |
| `reference_required` | M-Pesa with no code |
| `bad_reference` | M-Pesa code not 8 to 12 letters and numbers |
| `bad_request_id` | `request_id` has other characters or is too long |
| `not_found` | no such sale, a draft or cancelled sale, or a `company` that is not the sale's |
| `not_payable` | the sale is a return, or owes nothing |
| `pay_after_close` | a POS Invoice that has not been merged yet: "This sale is still on the till's open shift. Close the shift, then take the payment." |
| `consolidated` | the invoice made at till close: take the payment on a customer's receipt |
| `other_customer` | a merged receipt whose consolidated invoice belongs to another customer |
| `server_error` | the write failed. It was rolled back, so nothing was taken. The real error is in the Error Log, never in the reply |

Errors raised as Frappe errors (not a reply): a guest (`AuthenticationError`), a sale of another business, a
Cashier on a sale made by someone else ("You can only see your own sales."), an unknown `doctype` (`PermissionError` /
`ValidationError`).

### Who and in what order

Signed in; the sale must belong to the caller's business (`ensure_record_access`); a **Cashier only on their own sales**
(the sale's owner is looked up first and nothing about money is read or written before that check); Manager and Owner on any sale
of the business. Only after that is the request checked and the sale read.

### Which cases are supported

| Sale | What happens |
|---|---|
| **Sales Invoice** (not the consolidated one) that owes money | A Payment Entry against that invoice |
| **POS Invoice merged into a Sales Invoice** (till already closed) that owes money | A Payment Entry against its **consolidated Sales Invoice**, for the amount, capped by what that invoice still owes. The consolidated invoice must be submitted and be the same customer's |
| **POS Invoice still on an open shift** | **Refused, `pay_after_close`.** ERPNext cannot point a Payment Entry at a POS Invoice (it accepts Sales Invoices, orders and journal entries), and the existing app has no way to reduce a POS Invoice's balance either: its customer-credit payments (`sales_api.create_payment_entry_for_invoice`, the M-Pesa settle in `payment_callbacks.py`) all work on Sales Invoices. Recording money any other way would not reduce what is owed |
| Consolidated Sales Invoice made at close | Refused, `consolidated` |
| Return, draft, cancelled, nothing owed | Refused |

### How the Payment Entry is made

Built with ERPNext's own `get_payment_entry("Sales Invoice", <target>, party_amount=<amount>)` (the same builder the
existing code uses), then:

- `posting_date` and `reference_date` : today in Africa/Nairobi
- `mode_of_payment` : the shop's mode picked as above
- `paid_to` (and its currency) : the company's account for that mode (`get_default_bank_cash_account`); a mode with no account is refused rather than posted to a default account
- `paid_amount` and `received_amount` : the amount; each reference's `allocated_amount` : the amount
- `reference_no` : the M-Pesa code (capitals) or the reference given; **a card payment with no reference gets the sale's id**, because ERPNext requires a reference number on payments into a bank account; cash gets none
- `remarks` : `Payment taken at the till for #0033 [pay-req:<request_id>:<sale id>]` (the marker only when a `request_id` was sent)
- inserted with `ignore_permissions=True` and submitted once. The call does not commit (Frappe commits when the request ends); on any error it rolls back and logs with `frappe.log_error`

### Never twice

The sale's row is locked, then the call looks for a **submitted** Payment Entry of the same business whose remarks hold
`[pay-req:<request_id>:<sale id>]`. If one exists the call records nothing and returns that payment and the sale as it is
now (also after the sale has been paid in full). A different `request_id`, or the same one on another sale, is a new payment. Without a
`request_id` the call cannot recognise a retry. A cancelled payment does not block a new one with the same id.

### Assumptions

ERPNext 15 `get_payment_entry` and `get_default_bank_cash_account` (in `journal_entry`) exist with these signatures;
Payment Entry has `remarks`, `reference_no`, `reference_date`, `paid_to`, `paid_to_account_currency`, `mode_of_payment`
and `references[].allocated_amount`; the company works in one currency (a mode whose account is in another currency is
refused). The merge puts one customer's receipts in one consolidated invoice (the customer is checked, not assumed).
None of this could be run without a site.

## Departures from the proposal

- `period` also accepts `week`, because it reuses the Home resolution as is
- session rows have an extra `closed_on`, for a shift that ends after midnight
- session `expected`, `counted`, `difference` are null for a session that is open, closed with no closing entry, or (counted and difference) closed without a recorded count
- the error messages for a bad period are their own sentences ("... for the sales list")
- `get_sale` has `merged_into` and `can.take_payment_blocked`
- the earlier `consolidated` flag on list rows is gone: the consolidated invoice is never listed
