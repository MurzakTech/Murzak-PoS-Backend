# P4-1 and P4-2 contract: the Products page calls

Three new methods in `murzak_pos/api/products_list_api.py` (rules in `products_list_calc.py`). They are new: no existing
call, record, report or setting is changed, and the old `get_products` and `create_product` behave exactly as before.
The front end builds the Products page, the Categories tab and the New product form on these.

Replies follow the same shape as the Sales page calls: `{ "success": true, "data": {...} }`, or
`{ "success": false, "message": "..." }` with words a shop owner can act on (no codes, no traces). A call that is
refused for who is asking (not signed in, no permission) raises the normal Frappe error instead.

Python methods: `murzak_pos.api.products_list_api.list_products`, `.list_categories`, `.create_product_simple`.

## Why the old count was wrong

`product_api.get_products` made its `total` from different filters than its rows:

- the search text (`search_term`) was applied to the rows but not to the count, so a search for "tin" showed 3 rows
  beside the full catalogue count, and the page showed "Showing 3 of 135";
- when the signed-in person has a POS industry set, the count query dropped the category, brand, stock-item and
  sales-item filters as well;
- the stock figure was `get_stock_balance(item, None, today)`: no store is named, so it is not limited to the
  business's own stores and it is not a sum over stores (read from the ERPNext source, not run against a site);
- `page_size` 0 divided by zero, and a page past the end returned no rows.

`list_products` builds the count and the rows from one set of query parts (one FROM, one WHERE), so they cannot differ.
A test counts every combination of filters and then reads every page; the two must match.

## list_products

`GET /api/method/murzak_pos.api.products_list_api.list_products`

| Parameter | Meaning |
|---|---|
| `search` | Matches the product name, the product code or any barcode, containing the text, ignoring case. `%` and `_` are taken literally. At most 100 characters. |
| `category` | A category name (an Item Group). Exact match: products inside sub-categories are not included. |
| `status` | `active` (default and when empty): enabled products. `all`: enabled and disabled. `low`: enabled and low on stock. `out`: enabled and out of stock. `disabled`: disabled only. Anything else is refused with "Choose a product view: active, all, low, out or disabled." |
| `page` | From 1. Missing or invalid is 1. A page past the last page returns the last page (so the rows are never empty while the list has products). |
| `page_size` | Default 20, at most 100, never below 1. A missing or invalid value is 20. |
| `company` | Optional. Must be a company the caller belongs to; default is the caller's own. |
| `warehouse` | Optional store. When given, stock and the low-stock warning are for that store only. Must belong to the company, else refused. |

What is listed: the products of the company (`Item.custom_company`) that are for sale (`is_sales_item = 1`), including
services (not kept in stock). If the person has a POS industry, products of another industry are left out, as in the
old list. Order: newest first (`creation` descending, then code).

### Reply

```json
{
  "success": true,
  "data": {
    "rows": [
      {
        "item_code": "PRD-00007",
        "item_name": "Soap bar",
        "category": "Toiletries",
        "price": 120.0,
        "price_source": "price_list",
        "stock": 10,
        "unit": "Nos",
        "barcode": "6001234567890",
        "barcodes": ["6001234567890"],
        "disabled": 0,
        "is_stock_item": 1,
        "low_stock_level": 5,
        "status": "ok",
        "cost_price": 80.0,
        "profit_per_item": 40.0,
        "margin_percent": 33.3
      }
    ],
    "total": 11,
    "page": 1,
    "page_size": 20,
    "pages": 1,
    "summary": { "total": 11, "low_stock": 2, "out_of_stock": 2 },
    "status": "active",
    "stock_scope": { "kind": "all_stores", "warehouse": null },
    "price_list": "Standard Selling",
    "can_see_cost": true
  }
}
```

- `total` is the exact number of products matching the search, category and status filters (and the store scope for
  `low` and `out`). `pages` is never below 1 (an empty list has one empty page).
- `price` is the product's price in the selling price list (the list `create_product` saves into: the Selling Settings
  list, else "Standard Selling"), in the product's own unit; if the list has none, the product's standard rate
  (`price_source` says which). The unit is the product's stock unit as stored (`Nos`, `Kg`); the front end writes it in
  plain words.
- `stock` is the quantity summed over the company's stores (non-group warehouses), or the one store named. `null` for a
  service (a product that is not kept in stock). `stock_scope` says which. Fractions are kept (2.5 kg); a whole number
  has no decimals.
- `low_stock_level` is the low-stock warning (the Item Reorder levels summed over the same stores), or `null` when none
  is set. It is the same field the low-stock email alert and the Home "running low" card read.
- `status` is one of `ok`, `low`, `out`, `not_tracked` (a service) or `disabled`.
  - low: some left (more than 0) but no more than the warning;
  - out: none left (0 or less) for a product kept in stock;
  - a warning of 0 or none means no warning: the product can be `out` but never `low`.
  - Low and out never overlap. (The Spec says "at or below the warning"; here a product at 0 is `out`, so that
    low + out + ok adds up to the products kept in stock.)
- `barcode` is the first barcode ("" when none); `barcodes` is all of them.
- **Cost, for owners and managers only** (`role_tiers.can("cost_of_goods")`): `cost_price`, `profit_per_item` (price minus
  cost) and `margin_percent` (profit as a percentage of the price, one decimal). For a **cashier** these three keys are
  **absent** (not null) and the cost tables are not read. `cost_price` is `null` (never 0) when no cost is known;
  `profit_per_item` is then `null` too. The cost is the buying price recorded for the store (the Inventory Item Details
  "buying price" the old product screen keeps), else the product's valuation rate, else the highest valuation rate of
  any of the company's stores. `can_see_cost` says which kind of reply this is.
- `summary` counts the company's enabled products and does **not** change with the search, category or status filters
  (it follows the store scope): `total` (equals `total` of the default list with no filters), `low_stock`,
  `out_of_stock`. If the summary cannot be worked out it is `null` and the list still draws.

### Errors

| When | Result |
|---|---|
| Not signed in | `AuthenticationError`, "Please sign in first." |
| Unknown `status` | `ValidationError`, "Choose a product view: active, all, low, out or disabled." |
| `warehouse` of another business | `PermissionError`, from `ensure_record_access`, or "That store belongs to another business." |
| Anything unexpected | `{ "success": false, "message": "Could not load the products. Please try again." }` (the detail goes to the Error Log) |

Role rule: any signed-in person may read the list (as the old list). Only cost and profit are role-limited.

## list_categories

`GET /api/method/murzak_pos.api.products_list_api.list_categories?company=`

```json
{
  "success": true,
  "data": {
    "categories": [
      { "name": "Food", "parent": "All Item Groups", "is_group": 0, "is_default": false, "product_count": 5 },
      { "name": "All Item Groups", "parent": null, "is_group": 1, "is_default": true, "product_count": 2 }
    ],
    "total": 2,
    "product_total": 7
  }
}
```

- Every Item Group is listed with how many **enabled, for-sale** products of the company sit directly in it (the same
  catalogue as `summary.total`, so the counts add up to it). Sub-categories are not added into their parent.
- Sorted A to Z, ignoring case. The top group `All Item Groups` (`is_default: true`) is where a product lands when no
  category is chosen; it is listed last, and only when products sit in it. The front end shows it as "No category".
- Item Groups are shared by the whole site in ERPNext (they have no owner field), so the list is the same for every
  business; only the counts are the company's.
- Adding, renaming and deleting a category use the existing `product_api.create_item_group`, `update_item_group` and
  `delete_item_group` unchanged.
- Errors: not signed in raises `AuthenticationError`; anything unexpected gives `{ "success": false, "message": "Could not load the categories. Please try again." }`.

## create_product_simple

`POST /api/method/murzak_pos.api.products_list_api.create_product_simple` (POST only)

| Parameter | Meaning |
|---|---|
| `item_name` | **Required.** At most 140 characters. |
| `price` | **Required.** Selling price in KES, 0 or more (digits, an optional decimal point; commas and spaces are ignored). Saved as the product's standard rate and its price in the selling price list. |
| `item_code` | Optional. Left out or empty: made automatically (`PRD-00001`, `PRD-00002` ...; a code already used is skipped). Letters, numbers, spaces, dashes, dots and underscores; must start with a letter or number. |
| `category` | Optional category name. Default `All Item Groups`. Must exist. |
| `unit` | Optional unit name (`Nos`, `Kg`, `Ltr`, `Box` ...). Default `Nos`. Must exist. |
| `barcode` | Optional. No spaces or hidden characters. Must not be used by any other product. |
| `cost_price` | Optional, owners and managers only (`cost_of_goods`); anyone else who sends it is refused with `PermissionError`. |
| `opening_stock` | Optional quantity (fractions allowed). 0 or empty means none. |
| `low_stock_warning` | Optional quantity. 0 or empty means none. |
| `warehouse` | Optional store for opening stock, the warning and the cost. Default: the business's default store (`warehouse_api.get_default_warehouse_for_company`), else a store whose name starts with "Stores", else the oldest. |
| `company` | Optional, as above. |

Who may call it: the same roles as `create_product` (`require_catalogue_role`: System Manager, Sales Manager, Stock
Manager, Item Manager, Agriculture Manager). A cashier is refused with `PermissionError`.

What it does, in this order, and nothing happens if a check in step 1 fails:

1. Checks the values (and the database for a used code or barcode, a missing category or unit, and a missing store when
   stock or a warning is asked for).
2. Calls the existing `product_api.create_product(item_code, item_name, item_group, stock_uom, standard_rate, barcode, company)`.
3. Low-stock warning: adds a row to the product's Reorder table (`warehouse`, `warehouse_reorder_level`, quantity 0,
   `Purchase`) and saves the product.
4. Cost price: sets the product's `valuation_rate`, and keeps the buying price for the store with the existing
   `inventory_api._create_or_update_inventory_item_details` (the helper the old product screen uses).
5. Opening stock: a Material Receipt through the existing `inventory_api.create_stock_entry` (the same call as Stock in),
   with the cost as `basic_rate` when known. This call also needs the stock role (`require_stock_role`).

Steps 3 to 5 are independent: if one fails, the product stays saved, the others still run, and `notes` names what to do
again.

### Reply on success

```json
{
  "success": true,
  "message": "Product saved.",
  "data": {
    "product": { "item_code": "PRD-00007", "item_name": "Soap bar", "category": "All Item Groups", "unit": "Nos", "price": 120.0, "barcode": "" },
    "code_made_automatically": true,
    "low_stock_warning": { "saved": true, "level": 5 },
    "opening_stock": { "saved": true, "qty": 12, "store": "Stores - DS", "stock_entry": "MAT-STE-2026-00001" },
    "store": "Stores - DS",
    "notes": []
  }
}
```

`low_stock_warning` and `opening_stock` are `null` when not asked for. `notes` is
`[{ "step": "opening_stock", "message": "The product was saved, but the opening stock was not added. Use Stock in to add it." }]`
(steps `low_stock_warning`, `cost_price`, `opening_stock`) when a later step failed.

### Reply on a refusal

```json
{
  "success": false,
  "message": "Enter a name for the product.",
  "errors": [
    { "field": "item_name", "message": "Enter a name for the product." },
    { "field": "price", "message": "Enter a price." }
  ]
}
```

`field` is one of `item_name`, `price`, `item_code`, `category`, `unit`, `barcode`, `cost_price`, `opening_stock`,
`low_stock_warning`. A refusal that comes from the existing `create_product` carries its own words in `message`; its
field is read from the words (a barcode, a code, a category or a unit) and `errors` is `[]` when it cannot be told, so
the front end shows it in the summary only. Messages exist in English and Swahili (`sw.csv`).

Refusals for who is asking (not signed in, no permission, a store of another business, cost by someone who may not
set it) are raised as Frappe errors.

## Assumptions about ERPNext that could not be checked without a database

- `Item` has `custom_company`, `custom_pos_industry` (optional), `is_sales_item`, `is_stock_item`, `valuation_rate`,
  `disabled`, `item_group`, `stock_uom`, `standard_rate`; child tables `Item Barcode` (`parent`, `barcode`) and
  `Item Reorder` (`parent`, `parenttype`, `warehouse`, `warehouse_reorder_level`); `Bin` (`item_code`, `warehouse`,
  `actual_qty`, `valuation_rate`); `Warehouse` (`company`, `is_group`). The same names the dashboard overview and the
  low-stock alert already use.
- The list SQL is plain (derived-table joins, `CASE`, `IFNULL`, `LIKE ... ESCAPE '|'`). It is tested on SQLite with the
  same table names; MariaDB reads it the same way. LIKE case-insensitivity depends on the column collation (the Frappe
  default is case-insensitive).
- `make_autoname("PRD-.#####")` gives the next code from the site's naming counter.
- A Material Receipt with no valuation rate may be refused by ERPNext when perpetual inventory is on ("Valuation Rate
  ... is required"). The product is still saved and `notes` says to use Stock in; the front end advises entering a cost
  price with opening stock.
- Setting `Item.valuation_rate` on a product with no stock yet is allowed; saving the product again passes the
  eTIMS-optional relaxation (`relax_etims_mandatory`) the way `create_product` does.
- The Standard Selling price list exists, or Selling Settings names one. If neither exists the price is the product's
  standard rate.

## Tests

`murzak_pos/api/test_products_list_api.py`: no database server. The list SQL runs on an in-memory SQLite database with
the same names, so the count and the rows are proved to agree for every filter combination (720 combinations), and the
stock words in Python and in SQL are proved to say the same thing for every case. `EXPECTED_GUARDS` in
`test_record_access.py` lists `list_products` and `create_product_simple` (store check).
