# P7-1 contract: product spreadsheet import

Two new whitelisted calls in `murzak_pos/api/product_import_api.py`. The old `bulk_import_products`, `bulk_create_products`
and `create_product` are unchanged (the current Bulk import screen keeps using them). Rules are in
`product_import_calc.py`, picture handling in `product_import_images.py`; both have no database.

Roles: `require_catalogue_role()` (the same roles as the old import), plus the cost-of-goods capability (owner or manager)
when any row carries a cost price. A store named in the request goes through `ensure_record_access("Warehouse", warehouse)`
(listed in `EXPECTED_GUARDS`).

## Request (both calls, POST)

| Field | Meaning |
| --- | --- |
| `rows` | list of objects (or its JSON text), at most 2,000 |
| `company` | optional; refused when the caller does not belong to it |
| `warehouse` | optional store that opening stock and low stock levels go to when a row names none; default: the business's default store |
| `photos_file` | optional `file_url` of a ZIP the caller uploaded with `upload_file` (private); only the caller's own upload is read |

Each row has these keys (all optional except the first two; text or numbers; numbers may be written `1,200`):
`item_code`, `item_name`, `category`, `unit`, `price`, `cost_price`, `description`, `brand`, `barcode`, `image`,
`low_stock_level`, `is_service` (yes/no), `vat_class` (standard, zero_rated, exempt), `opening_stock`, `store`, and
`row_number` (the line in the file, used in messages; default position + 2).

## Decisions

- An item code that exists is UPDATED, never created twice. Only filled cells change anything; blank cells keep what the
  product has. Sending the same sheet again reports every row "unchanged" and writes nothing.
- The unit, tax class and "is a service" of an existing product are never changed (noted per row).
- Unknown categories are created under "All Item Groups" (not folders); unknown brands are created. Names are matched
  ignoring capitals and spaces; the spelling already in the shop wins. Rows left out do not create anything.
- Unknown units and stores are NOT created: the row is left out and says why.
- Opening stock goes through the existing `bulk_import_opening_stock` (one Stock Reconciliation), only for a product with
  no stock ledger entries, and only with a cost price (the sheet's, or the product's own valuation rate). Otherwise the
  product is saved and the stock is left out, with a note.
- A cost price of an existing product is applied only while the product has no stock.
- A picture is added only to a product with none. A bad picture or link is a note; the product is still saved.
- Pictures: `image` is an http(s) link, a `/files/...` path already on the server, or a file name found in the ZIP
  (matched ignoring case, spaces and folders). jpg, png, webp by file content, at most 2 MB each.
- ZIP: read in memory; at most 60 MB, 1,000 entries, 250 MB unpacked; names with `..`, absolute paths, drive letters,
  hidden files, `__MACOSX` and symbolic links are ignored; nothing is written under a name from the ZIP.
- Links: http and https on ports 80 and 443 only, no password in the address, and every address the name resolves to must
  be public (not private, loopback, link-local, multicast, reserved, or the cloud metadata address). The connection is made
  to the checked address with the name as Host and SNI, redirects are followed by hand (at most 3) and re-checked.

## `murzak_pos.api.product_import_api.preview_product_import`

Changes nothing. Answer `{ success, data }`:

- `rows[]`: `{ row, item_code, item_name, action, will, understood, changes[], problems[], notes[] }`
  - `action`: `create`, `update`, `unchanged` or `invalid`
  - `will`: one sentence ("Will be added as a new product.", "Will be updated: price, brand.", ...)
  - `understood`: `{ category, unit, price, cost_price, brand, barcode, image{kind,value}, low_stock_level, is_service, vat_class, opening_stock, store, description }` with blanks filled by what will really happen (category "All Item Groups", unit "Nos" for a new product)
  - `changes[]`: `{ field, from, to }` for an update
  - `problems[]`: `{ field, message }` (the row is left out); `notes[]`: sentences (the row still imports)
- `counts`: `{ total, create, update, unchanged, invalid }`; `summary`: one sentence
- `new_categories[]`, `new_brands[]`, and ready sentences `categories_line` ("3 new categories will be created: A, B, C."), `brands_line`
- `stock`: `{ rows, units, stores[] }`; `store`: the default store used
- `photos`: `{ zip_used, zip_files, zip_ignored, problem }`; `limits`: `{ max_rows }`

Errors: `ValidationError` for no rows, more than 2,000 rows, unreadable rows; `PermissionError` for another business's store
or a role without access. A failure while checking answers `{ success: false, message }`.

## `murzak_pos.api.product_import_api.import_products`

Plans again on the server (the preview is never trusted), then carries the plan out, committing after each row so a retry is
safe. Answer `{ success, data }`:

- `rows[]`: `{ row, item_code, item_name, status, message, notes[], problems[], understood, stock? }` with `status` one of
  `created`, `updated`, `unchanged`, `failed`, `left_out`
- `counts`: `{ created, updated, unchanged, failed, left_out }`, `total`
- `categories_created[]`, `brands_created[]`
- `stock`: `{ rows, reference, problem }` (`reference` is the Stock Reconciliation; when it fails the products stay saved and
  `problem` says to import the same file again, which adds the stock because those products still have no stock history)
- `pictures_added`, `photos_problem`, `changed_something`

## Assumptions not verified against a real site (no database was reachable)

- Item Group, Brand and UOM names are global; Item names compare ignoring case (MariaDB collation).
- `Stock Ledger Entry` has `is_cancelled`; `Item Reorder` has `warehouse` and `warehouse_reorder_level`.
- A Stock Reconciliation for Opening Stock needs a valuation rate for items with no stock (hence the cost price rule).
- `File` insert with `content` bytes, `attached_to_*` and `is_private = 0` creates a public file and returns `file_url`.
- urllib3 `HTTPSConnectionPool(ip, server_hostname=..., assert_hostname=...)` verifies the certificate against the name.
- The uploaded ZIP stays on the server as a private file after the import (so a retry can read it); it is not deleted.
