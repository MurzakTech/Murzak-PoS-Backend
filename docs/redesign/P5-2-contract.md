# P5-2: the Reports calls (`reports_api.run_report` and `reports_api.list_reports`)

Two new server methods behind the Reports landing page and the report pages of the New look. Nothing that exists today
is changed: the six current reports and their 13 sub-reports keep their own calls (`murzak_pos.api.reports.*`).

Files: `murzak_pos/api/reports_api.py` (the calls, the database reads, the cache), `murzak_pos/api/reports_calc.py` (the
arithmetic, no database), `murzak_pos/api/test_reports_api.py` (tests, no database).

Both calls answer `{ "success": true, "data": { ... } }`, or `{ "success": false, "message": "..." }` when the report
could not be worked out (the real error goes to the Error Log, never to the browser). A refused person gets a
Frappe error (`PermissionError`, `AuthenticationError`, `ValidationError`, `DoesNotExistError`) with a plain sentence.

## The six questions and the seven reports

| # | Question | Opens | Reports under it | Who | Period | Back end it reads |
|---|---|---|---|---|---|---|
| 1 | How much did I sell? | `sales` | `sales` | everyone (a cashier: own sales only) | yes | Home's readers (`dashboard_overview`): sales by day, payments, invoice lines |
| 2 | What is my profit? | `profit` | `profit` | owner, manager | yes | Home's profit rows and `summarize_profit`, plus the general ledger for running costs |
| 3 | Who owes me money? | `owed` | `owed` | owner, manager | no (as at today) | Home's unpaid-sales filter (`_owed_union`) |
| 4 | Where did my money go? | `running-costs` | `running-costs` | owner, manager | yes | General ledger expense accounts, the rule of `dashboard_api._get_operating_expenses` |
| 5 | What is in stock, and what is it worth? | `stock-summary` | `stock-summary`, `stock-value` | owner, manager | no | Current `reports.inventory_summary_report` and `reports.inventory_value_by_category_report` |
| 6 | What is not selling? | `aging-stock` | `aging-stock` | owner, manager | no | Current `reports.stock_aging_report`, plus Home's "sold recently" read |

A cashier may open only `sales`. The capability behind each report is in `reports_calc.REPORTS` and a test keeps it equal
to `role_tiers.CAPABILITIES`: `profit` for profit and running costs, `money_owed` for owed, `cost_of_goods` for the three
stock reports (they show what stock is worth).

## `murzak_pos.api.reports_api.run_report`

Request (GET or POST):

| Parameter | Meaning |
|---|---|
| `name` | `sales`, `profit`, `owed`, `running-costs`, `stock-summary`, `stock-value` or `aging-stock` (case and spaces ignored) |
| `period` | `today`, `week`, `7d`, `30d` (default), `month` or `custom`. Same rules as the Home overview (`overview_calc.resolve_period`) |
| `from_date`, `to_date` | `YYYY-MM-DD`, for `custom` only; a future end is cut back to today; at most 366 days |
| `store` | a Warehouse name to narrow to one store (checked with `ensure_record_access`); empty for all |
| `company` | the company; empty for the person's own (checked with `resolve_company`) |
| `group_by` | `day` or `month`, for `profit` only. Default: days up to 62 days, months after that |

The reports about the shop as it is now (`owed`, `stock-summary`, `stock-value`, `aging-stock`) accept and ignore `period`.

Answer `data` (the same keys for every report):

```json
{
  "name": "profit",
  "role": "owner",
  "period": { "key": "30d", "from": "2026-09-14", "to": "2026-10-13", "days": 30,
              "previous_from": "2026-08-15", "previous_to": "2026-09-13", "days_of_data": null },
  "as_of": "2026-10-13",
  "updated_at": "14:52",
  "cached": false,
  "summary": { },
  "series": { },
  "rows": [ ],
  "rows_total": 30,
  "errors": { }
}
```

- `period` is `null` for the reports about now. `days_of_data` is filled for `sales` only (columns below 7, a line from 7).
- `summary` holds the headline numbers (below). `series` holds what the charts draw. `rows` is the table, `rows_total`
  how many rows exist (the customer and product lists are cut at 500).
- `errors` names a part that could not be loaded (`{"payments": "Could not load this."}`); that part is `null` and the
  rest is still returned. An answer with errors is not cached.
- Money is a number with at most 2 decimals; a percentage change is a number with one decimal or `null` when there is
  nothing to compare with (a previous value of 0). Dates are `YYYY-MM-DD` (a month is `YYYY-MM`).

### `sales`

- `summary`: `revenue`, `gross_total`, `previous_revenue`, `change_pct`, `count`, `previous_count`, `count_change_pct`,
  `average_basket`, `days_with_sales`, `best_day` (`{date, revenue}` or null).
- `series.sales`: one point for every day, oldest first, in the shape of the Home sales chart: `{date, revenue, count,
  previous: {revenue, count}, so_far?}` (`so_far` on today). `series.days_of_data`.
- `series.payments`: `[{method, amount}]`, the Home "How customers paid" figures (M-Pesa, Cash, Card, On credit, Other).
- `series.top_products`: `[{item_code, name, revenue}]`, the five highest, as on Home.
- `rows`: one per day, oldest first: `date, revenue, count, average_basket, previous_revenue, previous_count, so_far`.

### `profit`

- `summary`: `revenue` (all sales, the Home Revenue tile), `previous_revenue`, `revenue_change_pct`, `sales_counted` (the
  sales of products that have a cost), `sales_not_counted`, `cost_of_goods`, `previous_cost_of_goods`, `gross_profit`,
  `gross_margin_pct`, `running_costs`, `previous_running_costs`, `net_profit`, `previous_net_profit`, `change_pct`,
  `margin_pct`, `previous_margin_pct`, `uncosted_products`, `estimated`.
  `net_profit` equals `sales_counted - cost_of_goods - running_costs`; `sales_counted` equals `revenue` unless
  `uncosted_products > 0`, when `sales_not_counted` is the difference. Build Pack check: revenue 100,000, cost of goods
  62,000, running costs 15,000 gives net profit 23,000 and margin 23.0.
- `group_by`: `day` or `month`.
- `rows`: one per day or month, oldest first: `period, sales, cost_of_goods, gross_profit, running_costs, net_profit,
  margin_pct, so_far`.
- `series.waterfall`: `[{key: sales | cost_of_goods | running_costs | net_profit, amount}]`;
  `series.over_time`: `[{period, net_profit, so_far}]`.

### `owed`

- `summary`: `amount`, `customers` (Home's Money owed tile), `oldest_days`, `over_30_amount`, `over_30_share_pct`, and
  `bands`: `[{key: 0_7 | 8_14 | 15_30 | over_30, from, to, amount, customers}]`. Age is the days since the sale.
- `rows`: one per customer, biggest debt first: `customer, name, mobile, amount, oldest_days, oldest_sale,
  bands: {0_7, 8_14, 15_30, over_30}`. `series.age_bands` is the same list as `summary.bands`.

### `running-costs`

- `summary`: `total`, `previous_total`, `change_pct`, `categories`, `daily_average`, `biggest` (`{category, amount,
  share_pct}` or null). `total` is what comes off Net profit on Home.
- `rows`: one per expense account with money in the period, biggest first: `category` (the account's own name),
  `account`, `amount`, `share_pct`, `entries`.
- `series.categories`: the six biggest and the rest folded into one item with `other: true` (the chart draws at most
  seven bars). `series.by_day`: `[{date, amount, so_far}]` for every day.

### `stock-summary` and `stock-value`

- `stock-summary` `summary`: `stores`, `quantity`, `value`; `rows`: `store, warehouse, quantity, value, share_pct`;
  `series.by_store`: `[{store, amount}]`.
- `stock-value` `summary`: `value`, `categories`, `items`, `biggest`; `rows`: `category, value, quantity, items,
  share_pct`; `series.by_category`: as `series.categories` above.

### `aging-stock`

- A product is not selling when nothing of it left the stores in the last 30 days and Home does not count it as sold in
  the last 30 days (that read includes till sales not yet merged, which have no stock movement yet).
- `summary`: `products` (not selling), `products_in_stock`, `value` (tied up), `oldest_days`, `share_of_products_pct`.
- `rows`: `item_code, name, quantity, value, age_days, sold_30_days (0), stores, age_band`, biggest value first.
- `series.age_bands`: `[{key: 0_30 | 31_60 | 61_90 | over_90, from, to, value, products}]`.

### Errors

| When | Error |
|---|---|
| Not signed in | `AuthenticationError`: "Please sign in first." |
| A store of another business | `PermissionError` (from `ensure_record_access`) |
| Unknown report name | `DoesNotExistError`: "That report does not exist." |
| A cashier asks for any report but `sales` | `PermissionError`: "Only an owner or a manager can see this." (from `role_tiers.require_access`, before anything is read or looked up in the cache) |
| A bad period | `ValidationError` with the Home overview's sentences |
| Anything inside the report fails | `{ "success": false, "message": "Could not load this report. Please try again." }` |

The order inside the call is fixed: signed in, store check, report exists, role check, period, then the cache and the reads.

## `murzak_pos.api.reports_api.list_reports`

Request: `preview` (optional, `1`, `true` or `yes`).

Answer `data`: `{ "role": "owner", "questions": [ { "key": "sold", "opens": "sales", "reports": ["sales"], "as_of": false, "preview": {...} } ] }`.

- Only the questions the person may open are listed, in the landing page's order: `sold`, `profit`, `owed`, `costs`,
  `stock`, `not_selling`. A cashier gets `sold` only.
- `as_of` is true for a question about the shop as it is now (no period filter).
- Without `preview` there is no database work at all. With `preview`, each question that is cheap to answer carries
  `preview`, worked out for the last 30 days through the same cached answer as `run_report` (so a click after the landing
  page is instant): `sold` `{revenue, count, change_pct}`, `profit` `{net_profit, margin_pct, change_pct}`, `owed`
  `{amount, customers}`, `costs` `{total, change_pct}`, `stock` `{value, quantity, stores}`. `not_selling` never has a
  preview (it scans a month of stock movements). A preview that fails is `null`; the others are still returned.

## Cache

60 seconds, following `dashboard_overview`: key `murzak_pos:reports:<report>:<role>:<company>:<store>:<person>:<period key>:<from>:<to>:<group_by>`.
The role, the company, the store and the dates are part of the key; `<person>` is the cashier's own login (empty for owners
and managers) so a cashier's answer is never shared; the reports about now leave the period out of the key. Only an answer
with no `errors` is kept. The role check happens before the cache is read, so an owner's cached answer can never reach a
cashier. A test covers each of these.

## Agreement with Home

For the same period: the sales summary equals Home's Revenue tile, `series.payments` and `series.top_products` equal
Home's payments and top products, the profit summary equals Home's Net profit tile (value, margin, previous, change, the
count of products without a cost price, estimated), `owed.summary` equals Home's Money owed tile. These are tests
(`TestReportsAgreeWithHome`) that run Home and the report on the same fixed data.
It holds because the reports call Home's own readers and pure functions rather than repeating them. The one exception is
running costs, which Home reads as a single total: `reports_api._expense_rows` repeats the rule of
`dashboard_api._get_operating_expenses` (same ledger filter, same excluded accounts) but keeps the account and the day.

## Assumptions I could not verify (no database was available)

1. `Account.account_name` exists on ERPNext 15 (it does in the standard Account doctype); a missing name falls back to the
   account without its business code.
2. `fn.Count("*")` and `fn.Max(...)` work in `frappe.qb` as used elsewhere in this app.
3. `reports.stock_aging_report` measures the age of stock by the age of the Bin record (`Bin.creation`), not of the last
   receipt. It is the current report and is reused as it is; "not selling" is therefore reliable, "how old" is approximate.
4. The 60 second cache uses `frappe.cache()` exactly as `dashboard_overview` does.
5. The three stock reports and running costs are shown to owners and managers only (the brief says a cashier gets only the
   sales report); the Spec's "value is for owners and managers" is satisfied by the same rule.
