# P4-6 contract: the Suppliers page calls

Two new calls in `murzak_pos/api/suppliers_list_api.py`; the rules are in `murzak_pos/api/suppliers_list_calc.py` (plain functions, no database). Nothing existing is changed: `supplier_api.py`, `dashboard_overview.py` and `overview_calc.py` are only imported. Create, update and the supplier groups keep using the existing calls in `supplier_api.py` (`create_supplier`, `update_supplier`, `get_supplier_groups`, `create_supplier_group`, `update_supplier_group`).

Both calls are `@frappe.whitelist()`, return `{ "success": true, "data": ... }`, and on a read failure `{ "success": false, "message": "<plain words>" }` (the detail goes to the error log, never to the browser).

## What "owed" means (one rule)

`figures_by_supplier` in `suppliers_list_calc.py` is the only place that works out what the shop owes. It is fed by one read of the business's unpaid supplier bills with the conditions of Home's "supplier bill due" item (`dashboard_overview._supplier_bill_rows`): `Purchase Invoice` with `docstatus = 1`, `is_return = 0`, `outstanding_amount > 0`, `company = <company>`, and no due-date cut-off. "Due soon" is Home's rule from `overview_calc` (`BILL_WARNING_DAYS`, 3): due today or within 3 days, or already late (a test checks that the bills counted here are exactly the bills `overview_calc.supplier_bill_items` lists). The list, the Owed filter, the summary row and the panel all take their figures from this one function, and money is added up in cents, so a total is exactly the sum of its rows.

Differences from Home, on purpose: Home's list is capped at 50 bills and drops nothing else; this page reads up to 5000 bills and only counts suppliers the business sees (see Visibility).

## list_suppliers_overview

`murzak_pos.api.suppliers_list_api.list_suppliers_overview`

| Parameter | Default | Meaning |
|---|---|---|
| `search` | none | Part of the supplier name, number or tax ID (not case sensitive, up to 60 characters) |
| `owed` | off | `true` (also `1`, `yes`, `owed`) lists only suppliers the shop owes more than zero. Off values: nothing, `false`, `0`, `all`. Anything else is refused |
| `supplier_group` | all | One supplier group |
| `status` | `active` (but `all` when `owed` is on) | `active`, `disabled` or `all`. With the Owed filter and no status, a disabled supplier that is still owed is listed |
| `page` | 1 | A page past the end gives the last page |
| `page_size` | 20 | Below 1 or not a number gives 20; never above 100 |
| `company` | the caller's | Must be one the caller belongs to |

Answer `data`:

```
rows: [ { id, name, type, group, tax_id, country, currency, disabled,
          owed, next_due, bills_due_soon } ],
summary: { suppliers, owed_total, suppliers_owed, bills_due_soon },
page, page_size, pages, total,        // total is exact for the same filters
role: "owner" | "manager" | "cashier"
```

- Order: by name; with the Owed filter the biggest debt first, then by name.
- `owed`: number (KES, 2 decimals), 0 when nothing is owed.
- `next_due`: `null`, or `{ date, amount, invoice, bill_no, state, days }`. It is the unpaid bill with the soonest due date, so a late bill always comes before any other (two bills due the same day: the bigger one). `amount` is that bill's unpaid amount (not the total owed). `state` is `overdue`, `soon` or `later`; `days` is the days until the due date (below 0 when late). A bill with no due date is counted in `owed` but is never the next bill due; a supplier whose bills all lack a due date has `next_due: null`.
- `bills_due_soon`: how many of the supplier's unpaid bills are late or due within 3 days.
- `summary`: `suppliers` is the number of active suppliers; `owed_total` is the total owed to all suppliers; `suppliers_owed` how many suppliers are owed; `bills_due_soon` the total count. The summary does not change with search, group, status or page.
- The Owed filter holds together: `total` (with `owed=true` and no other filter) equals `summary.suppliers_owed`, and the sum of `owed` over all pages equals `summary.owed_total`.

Roles: a cashier may list suppliers. For a cashier `owed`, `next_due` and `bills_due_soon` are left out of every row (not set to null), `summary` is just `{ suppliers }`, the bills are never read, and `owed=true` is refused with a permission error ("Only an owner or a manager can see this."). Owner and manager get everything (`role_tiers.can("money_owed")`).

Errors: not signed in (authentication error); `status` not one of the three, or `owed` not a yes or no word (validation error, plain words); another company (permission error).

## get_supplier_overview

`murzak_pos.api.suppliers_list_api.get_supplier_overview(supplier, company=None)`

`data`:

```
id, name, type, group, tax_id, country, currency, disabled, internal,
owed, next_due, bills_due_soon, bill_count,
bills: [ { invoice, bill_no, due_date, outstanding, state, days } ]
```

The last five (`owed` onwards) are the money fields, left out for a cashier. `bills` lists at most 20 unpaid bills, soonest due first, and `bill_count` says how many there are. The panel balance and next bill are the same figures as the list row (a test checks it). A cashier gets the facts only. A supplier that does not exist, or that the business does not see, gives `success: false` with "Supplier SUP-1 was not found."; a supplier owned by another business is a permission error (`ensure_record_access("Supplier", supplier)`, listed in `EXPECTED_GUARDS`).

## Visibility

Which suppliers a business sees is decided by the existing `supplier_api._visible_supplier_names(company)`: its own suppliers (`custom_company`) plus ownerless older suppliers it has bought from. Bills of any other supplier are left out of every figure (rows, summary, panel), so the page always adds up.

## Assumptions not verified (no database here)

- `Purchase Invoice` has the columns `name`, `supplier`, `bill_no`, `outstanding_amount`, `due_date`, `docstatus`, `is_return`, `company` (standard ERPNext 15; `bill_no` is the supplier's own invoice number).
- `Supplier` has `supplier_name`, `supplier_type`, `supplier_group`, `tax_id`, `country`, `default_currency`, `disabled`, `is_internal_supplier` (all read today by `get_suppliers`).
- Suppliers store no phone or email in the current API (`create_supplier` does not take them; ERPNext keeps them in Contact and Address records), so the list does not return them.
- A business has fewer than 5000 suppliers and 5000 unpaid bills; beyond that the extra rows are not read.
- "Today" is the date in Africa/Nairobi.

## Tests

`murzak_pos/api/test_suppliers_list_api.py`: no database; Frappe is mocked. Covers the request, the search, owed sums and cents, the next bill due (late first), Home's due-soon rule, the Owed filter equal to the summary and the sum of the rows, a cashier getting no money field anywhere, paging, the panel, roles, the shape of the queries, and the source keeping its promises (read only, no Frappe in the rules, no em dash).
