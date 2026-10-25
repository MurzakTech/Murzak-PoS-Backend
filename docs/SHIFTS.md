# Shift management

How cash is controlled during a till shift, and how the count at the end of the shift stays honest.

A shift is one cashier's session on one till (an ERPNext POS Opening Entry). The shift opens with a counted float and closes with a count of what is in the drawer, compared with what the system expects (`till_api.close_till`). This page covers the cash that moves in and out during the shift.

## Part 1: cash movements (this release)

Three kinds of cash movement can be written down during an open shift:

| Kind | Meaning | Reasons offered |
|---|---|---|
| Paid In | Cash put into the drawer: a float top-up, or change brought from the safe | Float top-up, Change, Other |
| Paid Out | Cash taken from the drawer: petty cash, a supplier paid, a staff advance | Petty cash, Supplier payment, Staff advance, Change, Other |
| Safe Drop | Notes moved from the drawer to the safe or taken to the bank | Bank deposit, Other |

"Other" needs a few words saying what the money was for.

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

- It does not post to the accounts. Cash paid out for an expense should also be recorded in Expenses so the books show it; the shift record is for the drawer count and the shift report.
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

### Known gap: cash handed back for a return is not recorded

The sales return call makes a credit note; it does not record paying cash to the customer. A cash refund handed over the counter therefore does not lower the cash the shift expects, and the count then reads over by the refund. Recording the payout of a refund (a Payment Entry against the credit note, in the method used) is the next piece of work.

## Planned next

1. Record the payout of a refund so cash refunds lower the expected cash.
2. Handover between cashiers: close one shift and open the next with the counted cash as the new float.
3. Till screens for all of the above.
4. Staff rota and clock-in.
