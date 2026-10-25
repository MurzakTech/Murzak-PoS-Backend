# Shift management

How cash is controlled during a till shift, and how the count at the end of the shift stays honest.

A shift is one cashier's session on one till (an ERPNext POS Opening Entry). The shift opens with a counted float and closes with a count of what is in the drawer, compared with what the system expects (`till_api.close_till`). This page covers the cash that moves in and out during the shift.

## Part 1: cash movements (this release)

Three kinds of cash movement can be written down during an open shift:

| Kind | Meaning | Reasons offered |
|---|---|---|
| Paid In | Cash put into the drawer: a float top-up, or change brought from the safe | Float top-up, Change, Other |
| Paid Out | Cash taken from the drawer: a staff advance, or change fetched | Staff advance, Change, Other |
| Safe Drop | Notes moved from the drawer to the safe or taken to the bank | Bank deposit, Other |

"Other" needs a few words saying what the money was for.

### What does not need recording by hand

- **Change given to a customer.** A customer who pays 500 for a 450 sale puts 450 into the drawer, not 500: the shift count takes the change off the cash sales by itself.
- **Money spent on the business** (petty cash, supplies, a supplier paid from the drawer). That is an expense: add it in Expenses, paid from Cash. If the person who adds it has an open shift, the expense also comes out of that shift's drawer as a cash movement with the reason "Expense" (the Expenses call takes `from_till=false` to keep it out of the drawer, for example when it was paid from the safe, and `session` to name another open shift). If the drawer holds less than the expense, nothing is saved. With no open shift the expense is saved and no drawer is touched.
- **A cash refund to a customer.** See "Paying a refund back" below; it comes out of the drawer with the reason "Customer refund".

The reasons "Expense" and "Customer refund" are written by the system only; a request cannot pick them. Older movements recorded as "Petty cash" or "Supplier payment" stay valid.

### What the system does with them

- Every movement is saved in the **Till Cash Movement** table with the shift, the person, the time, the kind, the amount, the reason and the note.
- The shift-end count adds Paid In and takes off Paid Out and Safe Drop, so **expected cash = opening float + cash sales - refunds + paid in - paid out - safe drops**. A difference at the end is then a real difference.
- Money leaving the drawer cannot be more than the drawer is expected to hold.
- A movement is written once and **never changed or deleted** (the table refuses it). A mistake is put right with a movement the other way.
- A till that resends the same movement after a dropped connection (same `client_id`) records it once.

### Who may do what

| Person | Record cash | See a shift's movements |
|---|---|---|
| Cashier | In the shift they opened | In the shift they opened |
| Manager, Owner | In any open shift of the business | In any shift of the business |
| Anyone from another business | Refused | Refused |

### Calls

Both are reached as `/api/method/murzak_pos.api.shift_api.<name>` and answer `{ "message": { ... } }`.

`record_cash_movement(session, kind, amount, reason=None, note=None, client_id=None)`

`session` is the POS Opening Entry name. The answer is `{ success, message, data: { movement, duplicate, cash_in_till } }`, or `{ success: false, message, error_type }` with one of `not_found`, `not_open`, `bad_kind`, `bad_amount`, `too_large`, `bad_reason`, `note_required`, `bad_client_id`, `more_than_in_till`, `no_cash_line`.

`list_cash_movements(session)` answers the shift's movements oldest first, the total of each kind and the reasons offered for each kind.

### What this does not do (yet)

- A manual cash movement does not post to the accounts. Expenses and refunds do, and they are the way to record money spent or paid back.
- It has no approval step for large amounts.
- Only the cash line of the till moves. M-Pesa and card are not drawer money.

## Part 2: shift reports

`get_shift_report(session)` gives the report of a shift:

- **X report** while the shift is open: the figures so far. It changes and closes nothing, so it can be looked at as often as wanted.
- **Z report** once the shift is closed: the same figures plus what was counted and the difference.

The same people may read it as may record cash in the shift (the cashier who opened it, a manager or owner of the business).

| Part of the answer | What it holds |
|---|---|
| `kind` | `X` or `Z` |
| `sales` | how many sales, gross total, net and tax (both after returns), discounts given, average sale |
| `returns` | how many returns and the amount handed back |
| `credit_sales` | sold on credit (not money in the till) |
| `by_method` | each way of being paid: sales, refunds, expected, counted, difference |
| `cash` | the drawer line by line: opening float, cash sales, paid in, paid out, safe drops, refunds, expected, and when counted the count, the difference and the word (short, over, matches) |
| `totals` | expected, counted and difference over every counted method |
| `movements`, `movement_totals` | the cash movements of part 1 |

Sales while open are the till's own sales since the shift opened. Once closed they are everything its cashier sold on its till between its start and its end (receipts merged at closing are still counted once). A return made through the sales return call has no till of its own; it is counted in the shift of the cashier who made it when it is against a sale of that till.

## Part 3: paying a refund back

A sales return (`sales_api.create_sales_return`) makes a credit note: the shop owes the customer that money. Paying it back is a separate act, and until now nothing recorded it, so cash handed over the counter did not lower the cash the shift expects and the count at the end read over by the refund.

`refund_api.pay_refund(credit_note, mode_of_payment="Cash", amount=None, session=None)` pays a credit note back. The same payout can be done in the return itself: `create_sales_return(..., refund_mode="Cash")`.

- **Who:** the people who may record a return (a Sales Manager, Accounts Manager or the owner).
- **Cash:** needs an open shift (the caller's own, or the one named). The refund comes out of the drawer as a cash movement of kind Paid Out with the reason "Customer refund" and the credit note's number as the note, so the shift count and the Z report agree. It cannot be more than the drawer holds; if it is, nothing is paid.
- **Another way of paying (M-Pesa, bank):** the credit note is settled with a Payment Entry in that method and the drawer is not touched.
- **Always:** a Payment Entry settles the credit note in the books (its remarks start "Till refund", which tells the shift count not to count it a second time).
- A credit note paid in full cannot be paid again. A part payment leaves the rest owed. A return waiting for a manager's approval (a draft) cannot be paid until it is approved.
- If the payout fails in the same call as the return, the return stands and the answer says the refund still has to be paid.

## Part 4: handing a till over to the next cashier

When one cashier leaves and another takes the same till, the first shift is counted and closed and the second opens with what was counted. Nothing is guessed: the next shift starts from the cash in the drawer, not from a round number.

`handover_api.hand_over_till(session, to_user, counted=None, confirm=False, note=None)`

- **Who:** the cashier who opened the shift, or a manager or owner of the business (the same people who may close it).
- **Who can take over:** another enabled person of the same business. Not the same cashier, not someone from another business, not a switched-off account. `list_handover_people(session)` gives the choices.
- **Without confirm** nothing changes. The answer is the count against what should be there (as `close_till` shows it) and who would take over.
- **With confirm** (needs the cash counted) the shift is closed through the existing closing path, exactly as `close_till` does it, and the next shift is opened for `to_user` on the same till with the **counted cash** as its float. M-Pesa, card and the other methods start at 0, because they are not drawer money.
- **A difference stays with the cashier who counted out.** If the drawer was 50 short, that shows on their Z report; the next cashier starts from the 50-short amount, not from what the books expected.
- **The record:** both shifts get a comment saying who handed to whom, how much cash was counted, whether the count was short or over, and the optional note (up to 140 characters).
- **If the old shift is still closing in the background**, the next shift is not opened yet; the answer says so (`next_shift` is empty) and the new cashier opens theirs when it has closed.
- **If the old shift closes but the next cannot open**, the close stands (it is never undone). The answer says so and gives the float (`data.float`) to open the next shift with.

Answers use the same `{ success, message, data }` form as the other till calls. `data.next_shift` is `{ name, user, person, float }`. A refusal has `error_type`: `same_person`, `no_such_person`, `person_disabled`, `other_business`, `cash_count_required`, `already_closed`, `not_open`, `not_found`, `bad_counted`, `negative_count`, `unknown_mode`, `duplicate_mode`.

## Planned next

1. Till screens for the handover.
2. Staff rota and clock-in.
