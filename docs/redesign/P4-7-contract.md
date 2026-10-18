# P4-7 contract: the Customers page calls

Four new server methods for the Customers page (ticket P4-7). Nothing existing is changed: the current
`customer_api` and `loyalty` calls keep working exactly as they do, and the new page still uses them for creating,
editing, credit limits and loyalty.

- `murzak_pos.api.customers_list_api.list_customers_overview` : the list, the Owing tab and the summary row
- `murzak_pos.api.customers_list_api.get_customer_overview` : the panel (facts, last purchase, balance owed)
- `murzak_pos.api.customers_list_api.get_customer_statement` : a statement with a running balance
- `murzak_pos.api.customers_list_api.get_customer_form_options` : whether the forms show the group and area, and their choices

Code: `murzak_pos/api/customers_list_api.py` (database reads, role checks), `murzak_pos/api/customers_list_calc.py` (all
rules, no database). Tests: `murzak_pos/api/test_customers_list_api.py` (149 tests, no database).

Dependencies: `overview_calc.py` and `dashboard_overview.py` (ticket P2-1, on `main`). They are imported and never edited. The
sales branch (P3-7) is not needed: the short receipt number rule is written again, in a few lines, in the calc file.

Replies follow the usual shape: `{"message": {"success": true, "data": {...}}}` over HTTP. A refusal raises a Frappe error
(HTTP 4xx, `exc_type` set), a read that fails returns `{"success": false, "message": "..."}` and the real error goes to the Error Log.

## What a customer owes (one rule, everywhere)

The balance is read with the code Home's "Money owed" tile uses (`dashboard_overview._money_owed_rows`): submitted Sales
Invoices plus submitted POS Invoices not yet merged into a Sales Invoice (`consolidated_invoice` empty), returns left out
(`is_return = 0`), only an unpaid amount above zero (`outstanding_amount > 0`), summed per customer. One customer's balance
(`_balance_of`, used by the panel and by the statement) reads the same query narrowed with `AND d.customer = ...`, built from
Home's own `_owed_union`, so the conditions cannot drift apart. Consequences, all tested:

- the Owing tab lists exactly the customers in those rows, most owed first;
- `summary.owing_total` and `summary.owing_customers` are exactly Home's money-owed amount and customer count (the summary is
  computed with `overview_calc.money_owed_summary` over the same rows);
- the total of the Owing tab, shown from the first page to the last, equals `summary.owing_total`, and the sum of the
  `balance` fields of all its rows equals it too;
- the closing balance of a statement that runs to today equals the `balance` of that customer in the list and the panel.

## Roles

The tier comes from `role_tiers.get_tier()`. The balance is allowed to `role_tiers.can("money_owed")` (owner and manager).

| Call | Owner, manager | Cashier |
|---|---|---|
| `list_customers_overview`, tab `all` | rows with `balance`, `last_purchase.amount`, summary with `owing_total` and `owing_customers` | rows without a `balance` field, `last_purchase` with date, receipt and id but no `amount`, summary with `customers` only |
| `list_customers_overview`, tab `owing` | allowed | refused (`PermissionError`, "Only an owner or a manager can see this.") before anything is read |
| `get_customer_overview` | `balance` and `last_purchase.amount` included | both left out of the answer (the keys are absent, not null) |
| `get_customer_statement` | allowed | refused (`PermissionError`) before anything is read |
| `get_customer_form_options` | allowed | allowed (a cashier adds customers at the till) |

A guest gets `AuthenticationError`. A customer of another business gets `PermissionError` through `ensure_record_access("Customer", customer)`
(listed in `EXPECTED_GUARDS` of `test_record_access.py`), and a customer record whose `custom_company` is another business is
reported as not found.

## `list_customers_overview`

### Request

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `tab` | text | `all` | `all` or `owing` (case ignored) |
| `search` | text | none | a name, customer number or email (part of it), or part of a mobile number. A search made of digits, spaces, `+`, `-` or brackets with at least 3 digits is also matched against the mobile number ignoring spaces, dashes, the leading 0 and the country code, so `0712 345`, `+254712345` and `712345` find the same people. At most 60 characters; `%` and `_` are plain characters |
| `customer_type` | text | none | `Individual`, `Company` or `Partnership` (case ignored) |
| `page` | integer | 1 | below 1 or not a number gives 1; past the last page gives the last page |
| `page_size` | integer | 20 | below 1 or not a number gives 20; at most 100 |
| `company` | text | the caller's | refused (`PermissionError`) if the caller does not belong to it |
| `status` | text | `active` | `active`, `disabled` or `all`. Applies to the All customers tab only: the Owing tab always lists everyone who owes, disabled or not, because the money is still owed |

All customers is ordered by customer name. The customer called "Walk-in Customer" (the till's name for a sale with no named
customer) is never listed on that tab. Customers are limited to the business through `custom_company` when that column exists, as the
current `list_customers` does. Owing is ordered by `balance` descending, then by name.

### Response

```json
{
  "success": true,
  "data": {
    "role": "owner",
    "rows": [
      {
        "id": "CUST-0007",
        "name": "Wanjiru Kamau",
        "type": "Individual",
        "mobile": "0712345678",
        "email": "w@demo.test",
        "disabled": false,
        "balance": 3500.0,
        "last_purchase": { "date": "2026-10-05", "amount": 250.0, "receipt": "#0012", "id": "ACC-PSINV-2026-00012" }
      }
    ],
    "total": 135,
    "page": 1,
    "page_size": 20,
    "pages": 7,
    "summary": { "customers": 135, "owing_total": 28500.0, "owing_customers": 3 }
  }
}
```

- `total` is the exact number of customers on that tab with those filters (not the size of the page). `pages` is at least 1.
- `balance` is `0.0` for a customer who owes nothing (All customers tab), and is absent for a cashier.
- `last_purchase` is `null` for a customer with no purchase. `amount` is absent for a cashier.
- `summary` is not narrowed by `search`, `customer_type` or `status`: `customers` is the number of active customers (walk-in
  customer excluded), `owing_total` and `owing_customers` are Home's money-owed figures. Cashier: `customers` only.
- `mobile` is as saved (`0712345678` or `+254712345678`); the browser formats it.

### Errors

| Case | Result |
|---|---|
| not signed in | `AuthenticationError` ("Please sign in first.") |
| unknown `tab`, `status` or `customer_type` | `ValidationError` with the allowed choices |
| `tab=owing` as a cashier | `PermissionError` |
| a read fails | `{"success": false, "message": "Could not load the customers. Please try again."}` |

## `get_customer_overview`

Request: `customer` (the customer number), optional `company`.

```json
{
  "success": true,
  "data": {
    "id": "CUST-0007", "name": "Wanjiru Kamau", "type": "Individual",
    "mobile": "0712345678", "email": "w@demo.test", "tax_id": "A012345678Z",
    "group": "Individual", "territory": "Nairobi", "loyalty_program": "Duka Points",
    "disabled": false,
    "balance": 3500.0,
    "last_purchase": { "date": "2026-10-05", "amount": 250.0, "receipt": "#0012", "id": "ACC-PSINV-2026-00012" },
    "features": { "groups": false, "territories": false }
  }
}
```

`features` says whether this business really uses customer groups and areas: `true` when its customers have more than one
different value. A shop whose customers all share the one default shows neither field in the forms. `balance` is absent for a
cashier. An unknown customer gives `{"success": false, "message": "Customer X was not found."}`.

## `get_customer_statement`

Request: `customer`, optional `from_date` and `to_date` (`YYYY-MM-DD`), optional `company`. With no `from_date` the statement covers
the last 90 days; with no `to_date` it ends today; an end date in the future is cut back to today; the period is at most
731 days. Days are Nairobi days.

```json
{
  "success": true,
  "data": {
    "customer": { "id": "CUST-0007", "name": "Wanjiru Kamau", "mobile": "0712345678" },
    "from": "2026-10-01", "to": "2026-10-13", "requested_from": "2026-10-01", "truncated": false,
    "opening_balance": 0.0,
    "lines": [
      { "date": "2026-10-05", "kind": "sale", "id": "ACC-SINV-2026-00004", "doctype": "Sales Invoice", "receipt": "#S0004", "debit": 1500.0, "credit": 0.0, "balance": 1500.0 },
      { "date": "2026-10-06", "kind": "sale", "id": "ACC-PSINV-2026-00012", "doctype": "POS Invoice", "receipt": "#0012", "debit": 250.0, "credit": 250.0, "balance": 1500.0 },
      { "date": "2026-10-08", "kind": "return", "id": "ACC-SINV-2026-00006", "doctype": "Sales Invoice", "receipt": "#S0006", "debit": 0.0, "credit": 100.0, "balance": 1400.0 },
      { "date": "2026-10-09", "kind": "payment", "id": "ACC-PAY-2026-00003", "doctype": "Payment Entry", "receipt": "#P0003", "debit": 0.0, "credit": 600.0, "balance": 800.0 }
    ],
    "total_debit": 1750.0, "total_credit": 950.0,
    "closing_balance": 800.0, "current_balance": 800.0,
    "last_purchase": { "date": "2026-10-06", "amount": 250.0, "receipt": "#0012", "id": "ACC-PSINV-2026-00012" },
    "role": "owner"
  }
}
```

How the lines are made:

- A sale is one line: the whole `grand_total` as `debit`, and what was paid on the spot (`paid_amount - change_amount`) as `credit`
  on the same line. A cash sale therefore reads debit 250, credit 250 and does not move the balance.
- A return (a credit note, `is_return = 1`) is one line: `kind: "return"`, the credited amount as `credit`; cash handed back with it
  is a `debit` on the same line.
- A payment is one line per Payment Entry (`payment_type = Receive`, `party_type = Customer`, submitted) with the amount
  allocated to the customer's invoices as `credit`. Payments not allocated to an invoice (advances) are not lines.
- Documents counted are the ones Home counts: Sales Invoices (including the invoice ERPNext makes when a till is closed) and POS
  invoices not merged into one. A POS receipt merged at till close therefore appears inside that Sales Invoice line.
- `receipt` is the short number: the last digits of the document name, at least four, after `#`; `S` for a Sales Invoice, `P` for a
  Payment Entry, nothing for a POS Invoice (`#0012`, `#S0004`, `#P0003`). The system reference is `id` and belongs under Details.

How the balance is kept honest: the closing balance is not added up from history. `opening_balance` is worked back from what the
customer owes now:

```
opening_balance = current_balance - (everything that happened from the first day to today)
```

where `current_balance` is the one balance function the list and the panel use. So a statement that runs to today ends at
exactly the `balance` of the list, whatever else is in the customer's history (a write-off, an advance, an old credit note).
A statement that stops earlier than today ends at the balance of its last day (`closing_balance`), and `current_balance` still says what
is owed now. `opening_balance` may therefore be negative, which means the customer was ahead (had credit) at the start.

Long histories: at most 2,000 rows are read for each kind of document (newest first). When one read hits that cap, the statement
starts the day after the oldest day that read reached, `truncated` is `true`, `from` is that day and `requested_from` is the day asked for.

Errors: not signed in (`AuthenticationError`), a cashier (`PermissionError`), a customer of another business (`PermissionError`),
`from_date` after `to_date`, more than 731 days or a date that is not `YYYY-MM-DD` (`ValidationError`), an unknown customer or a failed
read (`success: false` with a plain message).

## `get_customer_form_options`

Request: optional `company`. Response: `{"features": {"groups": bool, "territories": bool}, "groups": ["Individual", ...], "territories": ["Nairobi", ...]}`
(the customer groups and areas that are not folders, by name, at most 200 each). The New and Edit customer forms show Group and
Area under More options only when the matching feature is `true`.

## Assumptions about ERPNext tables and fields (no database was available to check them)

1. `Customer` has `customer_name`, `customer_type`, `mobile_no`, `email_id`, `tax_id`, `customer_group`, `territory`,
   `loyalty_program`, `disabled`, and (added by this app) `custom_company`. The existing `customer_api` reads the same fields.
2. `Sales Invoice` and `POS Invoice` have `customer`, `company`, `docstatus`, `is_return`, `posting_date`, `posting_time`,
   `grand_total`, `paid_amount`, `change_amount`, `outstanding_amount`; `POS Invoice` has `consolidated_invoice`; `Sales Invoice` has
   `is_consolidated` (the sales list code reads the same field).
3. `Payment Entry` has `party_type`, `party`, `payment_type`, `posting_date`, `creation`, `company`, `docstatus`; `Payment Entry Reference`
   has `parent`, `reference_doctype`, `allocated_amount` (the existing credit history reads the same).
4. `Customer Group` and `Territory` have `is_group`.
5. `paid_amount - change_amount` is what was settled on the spot for a POS-style sale (0 for a normal Sales Invoice). If a shop uses
   write-offs or rounding adjustments, the difference lands in the opening balance, not in a line.
6. A credit note made against an invoice lowers that invoice's `outstanding_amount` (ERPNext's normal way). If a shop records returns with
   `update_outstanding_for_self`, Home's rule (and so the list) ignores the return's own negative amount; the statement still ends at the
   list's balance because the opening balance absorbs the difference.
7. The existing `get_customer_credit_limit` and `get_customer_credit_history` calls are not limited by role on the server (they never
   were); the new page hides the Credit section from a cashier but those two old calls are unchanged by this ticket.

## Departures from the brief

- `list_customers_overview` has one more parameter, `status`, so the page can show disabled customers (the old page had "Include
  disabled"). It defaults to `active` and is ignored on the Owing tab.
- One more method, `get_customer_form_options`, because the form needs to know whether to show Group and Area without loading a list.
- The cashier's `last_purchase` keeps the date and the receipt number but not the amount (the amount is another person's sale).
