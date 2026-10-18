# P4-3 and P4-4 contract: Stock levels page, Stock in, Stock out or write-off, Count stock

Server module `murzak_pos.api.stock_levels_api` (reads and writes) with the pure rules in `murzak_pos.api.stock_levels_calc`.
All calls are new. No existing call, record, report or setting is changed; the three helpers that create stock records
(`inventory_api.create_material_receipt`, `create_material_issue`, `create_stock_reconciliation`) are imported and called, never edited.

Every answer is `{ "success": true, "data": ... }`. A refusal is a normal Frappe error with a plain sentence (also in Swahili in `sw.csv`);
a failure inside a helper that was called is `{ "success": false, "message": "plain sentence" }`.

## Who may do what

| Call | Owner | Manager | Cashier |
|---|---|---|---|
| `get_stock_levels` (lists and tiles) | yes | yes | yes, without any cost figure |
| `find_products`, `get_count_sheet`, `stock_in`, `stock_out`, `submit_count` | yes | yes if the person holds a stock role | refused |

"Stock role" is the rule the current stock screens already use (`access_control.STOCK_ROLES`: System Manager, Purchase Manager, Stock Manager,
Stock User, Item Manager, Agriculture Manager, and the Administrator). A Sales User (cashier) holds none of them, so is refused with
"Your role cannot change stock...". The first check in each write is `require_stock_role()`.

Cost: the stock value (tile, per-row `value`, expiry `value`) is returned only when `role_tiers.can("cost_of_goods")`. For anyone else the key is absent, not null.
`unit_cost` on `stock_in` is also only used for such a person.

## get_stock_levels

`GET murzak_pos.api.stock_levels_api.get_stock_levels`

| Parameter | Meaning |
|---|---|
| `tab` | `overview` (default), `low`, `movements`, `expiry` |
| `search` | text in the product name or code (movements: also the document number; expiry: also the batch) |
| `category` | an item group name (overview and low only) |
| `warehouse` | one store; omitted means every store of the business |
| `status` | overview and low: `ok`, `low`, `out`. movements: `draft`, `submitted`, `cancelled`. expiry: `expired`, `critical` (within 7 days), `soon` |
| `from_date`, `to_date` | movements only, `YYYY-MM-DD`; none means the last 30 days; one end alone gives 30 days from or to it; at most 366 days |
| `page`, `page_size` | page 1, 20 rows by default, at most 100 |
| `company` | defaults to the person's business |
| `kind` (extra) | movements only: `ledger`, `entry`, `receipt`, `issue` (the `?type=` of the old addresses) |
| `days` (extra) | expiry only: the "expiring within" window, 1 to 365, default 30 |

### Answer (all tabs)

```json
{
  "tab": "overview", "rows": [ ... ],
  "total": 45, "page": 1, "page_size": 20, "pages": 3,
  "features": { "expiry": false, "locations": 1 },
  "can": { "change_stock": true },
  "role": "owner", "cached": false
}
```

* `total` is exact for the same filters. A page past the last returns `rows: []` with the true `total` (the page moves back to the last page).
* `features.locations` counts real selling locations (not group warehouses, disabled ones, "All Warehouses", "Work In Progress", "Finished Goods", "Goods In Transit"), the same rule as the till. The Locations and Transfers tabs need 2 or more.
* `features.expiry` is true when any enabled product has a batch with an expiry date, or any Inventory Item Details record of the business has one. The Expiry tab is shown only then.
* `can.change_stock` says whether to offer Stock in, Stock out or write-off and Count stock.

### Overview and Low stock rows

```json
{ "item_code": "MLK", "item_name": "Maziwa 500ml", "category": "Maziwa", "unit": "Nos",
  "qty": 4.0, "value": 240.0, "level": "low", "warning_level": 10.0, "stores": 1 }
```

`level` is `ok`, `low` or `out`. `warning_level` is set only for `low`. `unit` is the stock unit code ("Nos", "Kg"); the page writes it in plain words. `value` is absent for a cashier. Rows are in name order (overview) or out first, then the least stock first (low).

The overview answer adds:

```json
"tiles": { "items": 5, "low": 2, "out_of_stock": 1, "stock_value": 7480.0 },
"categories": ["Maziwa", "Nafaka"]
```

**The tiles are counted from the rows of the list** (after `search`, `category` and `warehouse`, before `status`). So with no filter `tiles.items == total`; `tiles.low` equals the total of `status=low`; `tiles.out_of_stock` equals the total of `status=out`; `stock_value` is the sum of the rows' `value`. `stock_value` is absent for a cashier.
The Low stock tab lists low and out together, so its total is `tiles.low + tiles.out_of_stock`.

### The rules (the same as Home)

* **Low**: some store holds stock (more than 0) at or below that store's warning level (`Item Reorder.warehouse_reorder_level`, above 0). This is `overview_calc.running_low_items` over `alerts.low_stock_rows`, the very rows Home's Low on stock tile starts from.
* **Out**: the quantity in all stores (or the chosen store) is 0 or less.
* **Listed**: a product is listed when it holds stock, or sold in the last 30 days (`dashboard_overview._sold_recently`). So the Out count is Home's out-of-stock count, plus any product with negative stock. A product with no stock that has not sold in 30 days is not listed.
* **Stock value**: sum of `Bin.stock_value` (quantity times valuation rate).

### Movements rows

One list of: Stock Ledger Entry lines (everything that moved stock: sales, purchases, stock entries, counts; submitted or cancelled) and **draft** stock entries (not yet in the ledger).

```json
{ "id": "ledger:MAT-SLE-1", "date": "2026-10-12", "time": "09:10", "item_code": "MLK", "item_name": "Maziwa 500ml",
  "warehouse": "Duka Kuu - SA", "kind": "stock_in", "source": "ledger", "qty_change": 12.0, "qty_after": 16.0,
  "status": "submitted", "reference": "MAT-STE-2026-00004", "voucher_type": "Stock Entry",
  "entry": "MAT-STE-2026-00004", "reason": null }
```

* `kind`: `stock_in`, `stock_out`, `transfer`, `made`, `entry`, `sale`, `sale_return`, `purchase`, `purchase_return`, `count`, `other`.
* `status`: `draft` (no ledger yet, `qty_after` null), `submitted`, `cancelled`.
* `entry` is the Stock Entry number to open (current screen `/inventory/stock-entries/:name`), else null.
* `reason`: `damaged`, `expired`, `lost`, `used_in_shop`, `other` for a write-off made with `stock_out`, else null.
* Newest first. For a draft entry the sign of `qty_change` follows its purpose (issue negative, receipt positive).
* The answer also has `from_date` and `to_date` (the window used).

### Expiry rows

From the existing `expiry_api.get_expiry_alerts`: `{ item_code, item_name, warehouse, batch_no, qty, unit, expiry_date, days_left, state, value? }`, soonest first. `state` is `expired`, `critical` or `soon`. The answer also has `days`.

### Cache

Overview, Low stock and Expiry hold the whole list for **30 seconds** per business, store and tab (movements per its filters), by `_cache_get` and `_cache_set`. The kept list contains costs; costs are removed after it is read, per person, so an owner's answer is never served to a cashier. Every write below clears the business's kept answers (`_cache_clear`), so a person sees their own change at once.

## find_products

`find_products(search=None, warehouse=None, company=None, limit=10)` returns `{ rows: [{ item_code, item_name, category, unit, qty }] }`, enabled stock products by name or code, `qty` in the store (or all stores). At most 20. Stock roles only. For the product pickers of the three forms (a product with no stock yet must be findable).

## stock_in

`POST stock_in(item_code, qty, warehouse=None, note=None, company=None, unit_cost=None)` makes a submitted **Material Receipt** through `inventory_api.create_material_receipt`.

* `warehouse` may be omitted only when the business has exactly one selling location; with two or more: "Please choose which store this is for."
* `note` (up to 140 characters) is written as `Stock in: <note>` in the stock entry's `remarks`.
* `unit_cost` (optional, above 0) becomes the line's `basic_rate` for an owner or manager only.
* Answer: `{ entry, item_code, qty, warehouse, in_stock }`.

## stock_out

`POST stock_out(item_code, qty, reason, warehouse=None, note=None, company=None)` makes a submitted **Material Issue** through `inventory_api.create_material_issue`.

* `reason` is required: `damaged`, `expired`, `lost`, `used_in_shop` or `other` (`other` also needs a `note`). It is checked **before anything is read or written** (after the role check): blank gives "Please say why the stock is leaving: ..."; an unknown one gives "That reason is not one of the choices: ..."; Other without a note gives "Please add a short note when the reason is Other." (acceptance check AC-27).
* The reason is written on the stock entry's `remarks` as `Write-off (Damaged): <note>`, and shows in Movements.
* More than is in stock is refused: "There is only 4.0 in stock, so 5.0 cannot be taken out."
* Answer: `{ entry, item_code, qty, reason, warehouse, in_stock }`.

## get_count_sheet

`get_count_sheet(scope="all", category=None, warehouse=None, company=None)` returns `{ warehouse, rows: [{ item_code, item_name, category, unit, expected }], total, truncated }`: the enabled stock products that have a Bin in the store, in category then name order. `scope="category"` needs a `category`. At most 1000 rows (`truncated` says if cut). No cost figures. A count is for one store: the only selling location, or the `warehouse` given.

## submit_count

`POST submit_count(counts, warehouse=None, company=None)`, `counts = [{ item_code, counted }]` (a list or JSON text; `counted` is 0 or more; each product once; at most 1000).

* The expected quantity is read again from the Bin at the moment of saving. Counted minus expected is the difference.
* Only the lines that differ go to `inventory_api.create_stock_reconciliation` (one reconciliation, submitted). If every line matches, nothing is created and `reconciliation` is null.
* A count sets the stock to the counted quantity. Sales made between opening the sheet and saving are therefore overwritten; count when the shop is quiet.
* Answer: `{ warehouse, differences: [{ item_code, expected, counted, difference, state }], totals: { lines, short, over, matches, net_difference }, reconciliation }`. `state` is `short`, `over` or `matches`.
* If the helper fails: `{ success: false, message: "The count was not saved...", detail }`; the transaction is rolled back so no half-made draft is left.

## Errors (plain sentences)

Not signed in; "Choose a tab..."; "That status is not one of the choices for this list."; "Dates must look like 2026-10-13."; "The start date must not be after the end date."; "Please choose a period of one year or less."; "Enter a quantity above zero."; "Choose a product."; the three reason sentences above; "Please choose which store this is for."; "This business has no store to put stock in yet."; "Please count at least one product."; "Each counted quantity must be a number, zero or more."; "A product appears twice in the count."; "Please count no more than 1000 products at a time."; "You do not have access to this store." (another business's store, also `ensure_record_access`).

`EXPECTED_GUARDS` lists the six calls with `Warehouse warehouse` and, for `stock_in` and `stock_out`, `Item item_code`.

## Assumptions I could not verify (no database here)

* `Bin` has `item_code`, `warehouse`, `actual_qty`, `stock_value`; `Item` has `item_name`, `item_group`, `stock_uom`, `is_stock_item`, `disabled`, `custom_company`; `Warehouse` has `company`, `warehouse_name`, `disabled`, `is_group` (all used by existing code).
* `Stock Ledger Entry` has `company`, `posting_date`, `posting_time`, `voucher_type`, `voucher_no`, `actual_qty`, `qty_after_transaction`, `is_cancelled`; `Stock Entry` has `company`, `purpose`, `remarks`, `posting_date`, `posting_time`, `docstatus`; `Stock Entry Detail` has `parent`, `item_code`, `item_name`, `qty`, `s_warehouse`, `t_warehouse`.
* The UNION ALL of the two movement queries uses the same column list in the same order and plain text columns; a database with different collations between the two tables could need an explicit `COLLATE`.
* The `remarks` field of a submitted Stock Entry can be written with `frappe.db.set_value` (it is only a note; no ledger or account is touched).
* `frappe.cache().delete_keys(prefix)` deletes by prefix (Frappe 15). If it did not, the change would show after at most 30 seconds.
* The expiry flag looks at `Batch.expiry_date`, `Batch.disabled` and `Inventory Item Details.expiry_date`/`company`; a shop with items marked "has expiry date" but no dates entered yet does not count as tracking expiry.
* A Material Receipt with no cost for a product that has never had a valuation rate may be refused by ERPNext ("Valuation Rate is mandatory"); the owner can then give a `unit_cost`. The refusal is shown in plain words and nothing is left behind.
