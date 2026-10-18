# P4-5 contract: the Purchases page and the guided Receive goods step

Six new server methods (ticket P4-5, acceptance check AC-29). No existing method, record, report or setting is
changed. They sit in new files and import existing helpers; nothing existing was edited except two additions of rows
(`translations/sw.csv`, `EXPECTED_GUARDS` in `test_record_access.py`).

- `murzak_pos.api.purchases_list_api.list_purchases` : the list (tabs All purchases, Orders, Returns) and the summary row
- `murzak_pos.api.purchases_list_api.get_purchase` : the panel for one order or one bill
- `murzak_pos.api.purchases_list_api.receive_goods` : the guided step. ONE transaction: goods received note, stock and supplier bill together
- `murzak_pos.api.purchases_list_api.create_direct_bill` : "I already have the bill": a bill that raises stock at once
- `murzak_pos.api.purchases_list_api.add_purchase_note` : a note on an order or a bill
- `murzak_pos.api.purchases_list_api.get_payment_modes` : the ways the shop pays a supplier, for the forms

Code: `murzak_pos/api/purchases_list_api.py` (database reads and writes, role checks) and
`murzak_pos/api/purchases_list_calc.py` (all rules, no database). Tests: `test_purchases_list_api.py` and
`test_purchases_list_calc.py` (274 tests, no database: every Frappe call is mocked).

Period rules come from `murzak_pos/api/overview_calc.py` (on main). Replies follow the usual shape
`{"message": {"success": true, "data": {...}}}`. An expected refusal is HTTP 200 with
`{"success": false, "message": "<plain sentence>", "error_type": "<code>"}`. A person who is not allowed raises a Frappe
error (`PermissionError`, `AuthenticationError`).

## Who may use these calls

Owner and manager only (`role_tiers.get_tier`). A cashier is refused with `PermissionError`
("Only an owner or a manager can use Purchases.") as the very first statement, before any record is read. A guest gets
`AuthenticationError`. Every record opened by its number is also checked with `ensure_record_access` (another
business's supplier, order, bill or store is refused); `EXPECTED_GUARDS` lists them.

## What a purchase is

| Kind | Document | Short number | In the list |
|---|---|---|---|
| Order | Purchase Order (any docstatus) | `#O0012` | tab All and Orders |
| Bill | Purchase Invoice that is NOT tied to an order (a direct bill) | `#B0007` | tab All |
| Return | Purchase Invoice with `is_return = 1` (a debit note) | `#R0009` | tab All and Returns |
| Goods received note | Purchase Receipt | `#G0004` | never a row; shown inside the order panel |

A bill made from an order (through Receive goods or the current GRN screens) is not a second row: it is shown inside
the order's panel (`bills`), so one purchase is one row. "Not tied to an order" means no line of the bill carries a
`purchase_order`. A stock-only receipt (no supplier bill) is not a row either; it stays reachable in the current
Purchase Receipts and GRN screens (the page links to them).

The number is the digits at the end of the system name, padded to four, with a letter for the kind. The system name
(`PUR-ORD-2026-00012`) is returned only as `reference` in the panel (shown under Details), never as a list number.

### Status

One of `draft`, `ordered`, `received`, `billed`, `paid`, `returned`, `cancelled`:

- docstatus 2: `cancelled`; docstatus 0: `draft`
- bill: `returned` when it is a return; `paid` when `outstanding_amount` is 0; otherwise `billed`
- order: `ordered` while `per_received` is below 100 (an order part received is still `ordered`, with `part_received: true`);
  `received` once everything arrived; `billed` once also `per_billed` is 100; an order with status Closed counts as `received`

## `list_purchases`

### Request (GET)

| Field | Meaning |
|---|---|
| `tab` | `all` (default), `orders` or `returns` |
| `search` | up to 60 characters. Looks in supplier name, supplier id, the supplier's bill number and the system name. A short number (`12`, `0012`, `#B0012`, `O12`, `R9`) matches the end of the name instead (not the year) and, with a letter, only that kind |
| `supplier` | a Supplier id; limits the list and the summary |
| `period` | `today`, `week`, `7d`, `30d` (default), `month` or `custom` (rules of `overview_calc.resolve_period`) |
| `from_date`, `to_date` | `YYYY-MM-DD`, for `custom` |
| `page`, `page_size` | from 1; size 20 by default, at most 100; a page past the end is the last page |
| `company` | optional; a company the caller does not belong to is refused |
| `kind` | `receipts` lists goods received notes (Purchase Receipts, with or without an order, so stock-only receipts too; short number `#G0004`); `bills` lists every supplier bill (those made from an order too, returns left out). Both ignore the tab. This is how the goods received notes and the bills the All list folds into their order stay reachable in the new look. A rows `doctype` is then `Purchase Receipt` or `Purchase Invoice`; the panel opens them (`get_purchase`) |
| `open_only` | `1` lists only the orders still waiting for goods (submitted, not Closed, Completed, Cancelled or On Hold, `per_received` below 100), whatever their date; it forces the Orders tab. This is what Receive goods offers to choose from |

Dates filter on the order date (orders) or the posting date (bills and returns). Newest first.

### Response

```json
{ "success": true, "data": {
  "rows": [ { "id": "PUR-ORD-2026-00012", "doctype": "Purchase Order", "number": "#O0012",
              "supplier": "Bidco", "supplier_id": "SUP-0001", "date": "2026-10-12", "total": 2320.0,
              "status": "ordered", "outstanding": 0.0, "due_date": "", "bill_no": "",
              "is_return": false, "part_received": false } ],
  "total": 45, "page": 1, "page_size": 20, "pages": 3,
  "summary": { "orders_open": 3, "bills_unpaid": 12500.5, "returned": 800.0 } } }
```

- `total` is the exact number of rows matching the filters of the tab (a `COUNT(*)` over the same query as the rows).
- `total` on a row is positive (a return's total is shown as a positive number; the status says it is a return).
- `outstanding` and `due_date` are filled for a bill only.
- `summary.orders_open`: submitted orders with goods still to arrive (not Closed, Completed or Cancelled). `bills_unpaid`:
  the sum still owed on submitted bills. Both are what is open NOW, so they ignore the period. `returned`: the value of
  submitted returns in the period. All three follow the supplier choice and ignore the tab and the search.
- Failure of a read: `{ "success": false, "message": "Could not load the purchases. Please try again." }`.

## `get_purchase`

### Request (GET)

`id` (required) and `doctype` (`Purchase Order`, `Purchase Invoice` or `Purchase Receipt`; optional, found from the number when
missing). A goods received note opens too (the redirect table sends `/purchases/grns/:id` and `/purchases/receipts/:id` to
`?open=:id`): number `#G0004`, status `received` (`billed` once fully billed, `returned` for a return), `orders` naming the order(s)
it belongs to, `bills` made from it, and `can.pay_bill` when one is unpaid; it offers no receive, cancel or edit, and `can.make_bill` is true while it is not yet fully billed (status To Bill or Partly Billed): the page then offers the existing `murzak_pos.api.purchase.create_purchase_invoice_from_grn` (`grn_no`, `bill_no`, `bill_date`).

### Response

```json
{ "success": true, "data": {
  "id": "PUR-ORD-2026-00012", "doctype": "Purchase Order", "number": "#O0012", "status": "ordered",
  "part_received": true, "reference": "PUR-ORD-2026-00012", "orders": [],
  "supplier": { "id": "SUP-0001", "name": "Bidco" }, "date": "2026-10-01",
  "bill_no": "", "bill_date": "", "due_date": "", "is_return": false, "return_against": null,
  "lines": [ { "item_code": "A", "item_name": "Sugar", "unit": "Nos", "ordered": 10.0, "received": 4.0,
               "to_arrive": 6.0, "rate": 100.0, "amount": 1000.0, "warehouse": "Stores - A" } ],
  "totals": { "subtotal": 2000.0, "tax": 320.0, "total": 2320.0 },
  "bills":    [ { "id": "ACC-PINV-2026-00020", "number": "#B0020", "bill_no": "INV-1", "bill_date": "2026-10-03",
                  "date": "2026-10-03", "due_date": "2026-11-02", "total": 1160.0, "outstanding": 1160.0,
                  "is_return": false, "docstatus": 1 } ],
  "returns":  [ ],
  "receipts": [ { "id": "MAT-PRE-2026-00004", "number": "#G0004", "date": "2026-10-03", "total": 1160.0, "is_return": false } ],
  "pay": { "invoice": "ACC-PINV-2026-00020", "number": "#B0020", "bill_no": "INV-1", "outstanding": 1160.0,
           "due_date": "2026-11-02", "modes": [ { "name": "M-Pesa", "kind": "mpesa" } ] },
  "can": { "receive_goods": true, "pay_bill": true, "cancel": false, "return": true, "edit_draft": false, "print": true },
  "return_target": "ACC-PINV-2026-00020",
  "notes": [ { "text": "Call before delivery", "date": "2026-10-02" } ] } }
```

- A bill's lines have `ordered = received = qty` and `to_arrive = 0`. `totals.outstanding` is present for a bill only.
- `can.receive_goods`: submitted order, status not On Hold, Closed, Completed or Cancelled, with something still to arrive.
- `can.pay_bill` and `pay`: the unpaid bill due soonest. `pay.modes` lists the ways the shop pays, `kind` is `cash`,
  `mpesa`, `card` or `other` (so the page asks for the M-Pesa code when `kind` is `mpesa`). Payment is made with the
  EXISTING `murzak_pos.api.purchase.pay_purchase_invoice` (`invoice_no`, `paid_amount`, `mode_of_payment`, `reference_no`).
- `can.cancel`: an order with no receipt and no bill (cancel with the existing `murzak_pos.api.stock.cancel_purchase_order`);
  a bill that is not a return, when the caller holds one of the roles `cancel_purchase_invoice` already requires (cancel
  with the existing `murzak_pos.api.purchase.cancel_purchase_invoice`).
- `can.return` and `return_target`: the submitted bill a return would be made against (the bill itself, or the order's first bill).
- `can.edit_draft`: docstatus 0.
- Not found: `{ "success": false, "message": "Purchase X was not found.", "error_type": "not_found" }`.

## `receive_goods` (POST): the guided step, ONE transaction

### Request

| Field | Meaning |
|---|---|
| `order` | the Purchase Order (required; must be submitted) |
| `items` | `[{ "item_code": "A", "qty_received": 6 }]` (a list or JSON text). Only items that arrived |
| `bill_no`, `bill_date` | the supplier's bill number and date (optional) |
| `due_date` | optional; if empty the supplier's payment terms decide. Never before `bill_date` |
| `warehouse` | optional store to receive into; default is each line's own store |
| `company` | optional |
| `request_id` | optional but the page always sends one: 8 to 64 of letters, digits, `.`, `:`, `-` |

### Rules (all checked before anything is written)

1. Quantity above 0, not above what is still to arrive on that line (`qty - received_qty`, counting earlier deliveries). Over-receipt is NOT allowed.
2. Every item must be on the order and named once. Nothing ticked is refused.
3. The order must be submitted and not Closed, Completed, Cancelled or On Hold, with something left to arrive.
4. An order that lists the same item on two lines cannot be received here (the existing GRN helper keys by item code and would
   put the quantity on both lines). The message sends the person to the earlier Receive goods screen (the current Create GRN screen).
5. A store of another business is refused. A due date before the bill date is refused.

### What it does

The existing GRN call `murzak_pos.api.stock.create_pr_from_po` (it builds the receipt with ERPNext's `make_purchase_receipt`,
handles partial quantities, submits it, and does NOT commit) makes the goods received note; submitting it raises the stock.
ERPNext's own `make_purchase_invoice` from that receipt (the way `create_purchase_invoice_from_grn` does it) makes the
supplier bill, which is saved and submitted. Then a single `frappe.db.commit()`.

**Why the existing calls are not simply chained.** `create_grn` and `create_purchase_invoice_from_grn` each commit by
themselves, so if the second one fails the first one is already permanent: stock raised with no bill. This call uses the
no-commit GRN helper and the shared ERPNext bill mapping inside one transaction instead. The existing calls are untouched.

**Nothing half done.** If any step fails, `frappe.db.rollback()` is called and no commit happens: no receipt, no stock
change, no bill. The answer is `{ "success": false, "error_type": "receipt_failed" | "server_error", "message": "The goods
were not received and no bill was made, so nothing was changed. <reason>" }`. The reason is shown only when it came from a
validation (tags removed, cut at 240 characters); an unexpected error is logged (`Receive goods error`) and the person sees
"Please try again."

**Idempotent.** The order row is locked (`for update`) and the receipt and bill carry `[recv-req:<request_id>:<order>]` in
their remarks. A repeat with the same `request_id` finds them and returns the FIRST result with `repeat: true` (empty
`stock`), without creating anything and without touching the stock. If the receipt is found but its bill is gone (cancelled
later) the answer is `error_type: "already_received"`. A new `request_id` is a new delivery.

**Partly delivered.** Receiving less than is left leaves the order open: `order.open` is true and `order.still_to_arrive`
lists what remains. The page offers Receive goods again on the same order.

### Response

```json
{ "success": true, "data": {
  "receipt": "MAT-PRE-2026-00004", "receipt_number": "#G0004",
  "invoice": "ACC-PINV-2026-00030", "invoice_number": "#B0030",
  "bill": { "bill_no": "INV-55", "due_date": "2026-11-13", "total": 1740.0 },
  "stock": [ { "item_code": "A", "received": 6.0, "before": 10.0, "after": 16.0 } ],
  "stock_ok": true,
  "order": { "id": "PUR-ORD-2026-00012", "open": true, "still_to_arrive": [ { "item_code": "B", "to_arrive": 5.0 } ] },
  "repeat": false } }
```

`stock` has one entry per stock item received (a service has none); `before` is read before the receipt and `after` after the
bill, from the Bin of the store the goods go into, summed over stores for an item. `stock_ok` is true when
`after = before + received` for every entry (AC-29). `before` and `after` are quantities in the stock unit.

### Refusals (`error_type`)

`bad_request_id`, `bad_bill_date`, `bad_due_date`, `due_before_bill`, `not_found`, `bad_warehouse`, `not_submitted`,
`nothing_to_receive`, `no_items`, `no_item_code`, `not_on_order`, `named_twice`, `bad_qty`, `too_much` (the sentence
names the quantity left), `duplicate_lines`, `receipt_failed`, `server_error`, `already_received`.

## `create_direct_bill` (POST): "I already have the bill"

The current New Purchase screen only ever saved a draft purchase order (its "update stock", "mark as paid" and "bill number"
fields were not sent to the server), so there was no server path for a bill that raises stock at once. This call is that path.

| Field | Meaning |
|---|---|
| `supplier` (required), `items` | `[{ item_code, qty, rate }]`; qty above 0, rate 0 or more; an item named twice is added up |
| `bill_no`, `bill_date`, `due_date` | optional, as above |
| `warehouse` | store; lines without one use the item's or the business's default (the same rules as `upsert_purchase_order`) |
| `paid_amount`, `mode_of_payment`, `reference_no` | optional. With `paid_amount` the bill is marked as paid in the SAME document (`is_paid`, `cash_bank_account` from the way of paying), above 0 and not above the total; the M-Pesa code goes into the remarks |
| `company`, `request_id` | as above; a repeat returns the first bill with `repeat: true` |
| `notes` | optional text (500 characters, tags removed), saved as a comment on the bill in the same transaction |

One Purchase Invoice with `update_stock = 1` is inserted and submitted (stock rises with it), then committed; on any failure
`frappe.db.rollback()` is called and "The bill was not saved and no stock was added, so nothing was changed." comes back.
Reply: `{ "invoice", "invoice_number", "total", "outstanding", "repeat" }`. Refusal codes: `no_supplier`, `no_items`,
`no_item_code`, `bad_qty`, `bad_rate`, `bad_paid`, `paid_too_much`, `no_mode`, `no_account`, plus the date and request codes above.

A purchase ORDER (stock raised later by Receive goods) is saved with the EXISTING `murzak_pos.api.purchase.upsert_purchase_order`
with `submit = 1` (the order must be submitted before goods can be received). An order that needs approval stays a draft and the
reply carries `approval`.

## `get_payment_modes` (GET)

`{ "success": true, "data": { "modes": [ { "name": "M-Pesa", "kind": "mpesa" } ] } }`: the enabled ways of paying that the business may use
(`business_lists.modes_of_payment_for`), `kind` being `cash`, `mpesa`, `card` or `other`. Owner and manager only. The New purchase form
uses it for "mark as paid".

## `add_purchase_note` (POST)

`id`, `note`, optional `doctype`. Adds a comment (`Comment` type) to the order or bill and commits; the panel's `notes` reads these
back (oldest first, at most 20). The note is cleaned (tags removed, 500 characters). Refusals: `no_note`, `not_found`,
`server_error` (rolled back). It exists because `upsert_purchase_order` (existing, not edited) has no place for a note.

## Assumptions about ERPNext that could not be verified here (no database)

- `Purchase Order` has `status`, `per_received`, `per_billed`, `transaction_date`, `supplier_name`, `grand_total`; `Purchase Invoice` has
  `status`, `is_return`, `return_against`, `outstanding_amount`, `due_date`, `bill_no`, `bill_date`, `supplier_name`, `remarks`, `set_warehouse`,
  `update_stock`, `is_paid`, `paid_amount`, `mode_of_payment`, `cash_bank_account`; `Purchase Receipt` has `remarks` (all standard fields).
- A Purchase Invoice Item made from a receipt of an order carries `purchase_order` (ERPNext's mapping does; the bill rule depends on it).
- Submitting a Purchase Receipt updates `Bin.actual_qty` in the same transaction for a current-dated receipt (a back-dated one may be
  re-posted later by ERPNext; then `stock_ok` could read false even though the stock is right).
- `LIKE` on `remarks` and `REGEXP` on `name` work as on MariaDB.
- `get_default_bank_cash_account(company, mode_of_payment=...)` returns the account of a way of paying.
- Rows in the list that were saved with a missing `supplier_name` fall back to the supplier id.

## Departures from the proposed contract

- `receive_goods` also takes `due_date` (the page shows a due date field) and returns extra fields (`receipt_number`, `invoice_number`,
  `bill`, `stock_ok`, `order`, `repeat`) besides the proposed `{receipt, invoice, stock}`; `stock` entries also carry `received`.
- A fourth call, `create_direct_bill`, was needed because no server path existed for the direct bill (see above), and a fifth,
  `add_purchase_note`, because the New purchase form has a Notes field and no existing order call stores one.
- Pay bill, Cancel and Edit draft reuse existing calls and screens; there is no new payment or cancel method.
