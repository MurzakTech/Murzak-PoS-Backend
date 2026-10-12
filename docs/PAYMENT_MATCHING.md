# Payment matching (WS7)

Every till payment and every M-Pesa prompt already stores the M-Pesa receipt number. This work brings the M-Pesa
statement in and checks each paid-in line against those receipts, using ERPNext's own Bank Transaction and Bank
Reconciliation. The calls are in `murzak_pos/api/payment_matching_api.py`; reading the file is in
`murzak_pos/api/statement_rules.py`. All calls need a payment administrator (the same people who may change the
payment settings) and work on the signed-in business only.

## Calls

| Call | What it does |
| --- | --- |
| `setup_payment_clearing(company)` | Creates the clearing accounts and the bank account for the statement. Safe to run again. |
| `import_mpesa_statement(csv_text, company)` | Reads the statement into bank lines, then matches. Importing the same file twice adds nothing. |
| `match_mpesa_statement(company)` | Matches the lines still waiting. Use after a sale was corrected. |
| `get_payment_exceptions(company, days)` | What does not match, with the reason for each. |

The same list is the desk report **Murzak Payment Matching Exceptions** (System Manager and Accounts Manager).

## Set-up (once per business)

1. Run `setup_payment_clearing`. Each business gets three clearing accounts, "M-Pesa Clearing", "Card Clearing" and
   "PayPal Clearing". Money sits in a clearing account from the moment of the sale until the provider pays it into the
   bank. Any payment setting that had no account is pointed at its clearing account. New settings saved after this
   release get the clearing account on their own.
2. The same call creates the bank account for the M-Pesa statement, on the M-Pesa clearing account, using the bank
   "M-Pesa" with a saved column mapping, so ERPNext's own statement import understands the M-Pesa file as well.
   Onboarding of a new business does all of this by itself.

## Each week: bringing in the statement

1. Sign in to the M-Pesa organisation portal and download the statement as CSV for the period.
2. Send the text of the file to `import_mpesa_statement`.
3. Read the answer: how many lines were added, how many were already there, how many payments did not complete and were
   left out, any lines that could not be read (with the line number), and how many were matched.

Columns are found by their headings, in any order: receipt number, completion time, details, status, paid in,
withdrawn. Lines above the heading row are ignored. Dates may be written `2026-10-05 10:00:00`, `05/10/2026 10:00` and
a few other common ways. A payment whose status is not "Completed" is left out. A receipt repeated inside one file is
kept once.

## How matching works

A paid-in line is matched on its **receipt number** to the payment that holds it: the payment row of a till sale
(Sales Invoice), or the Payment Entry made when a payment is collected by prompt. One payment with the same amount means
the line is reconciled, which is the same result as the ERPNext Bank Reconciliation Tool gives. ERPNext's own matching of
a till sale looks only at the amount, so many sales of the same amount cannot be told apart; matching on the receipt is
exact.

## What the exceptions list means

| Reason | What to do |
| --- | --- |
| No statement line | The prompt succeeded but the payment is not on the statement. Check it with Safaricom, or that the statement covers that day. Only payments at least `days` old (default 3) and inside the period of the statements imported are listed. |
| No receipt on record | Money arrived on the till number that no sale or prompt explains. Find the customer, or record the receipt on the sale. |
| Receipt found, but no sale or payment holds it | The prompt was recorded but the sale was never completed. Complete or refund it. |
| Amount differs from the sale or payment | The customer paid a different amount than the sale. Correct the sale or the refund. |
| More than one sale or payment holds this receipt | The receipt was used twice. Remove the duplicate. |

## Limits

* A business whose sales are POS Invoices (the ones not on eTIMS) has no payment row in the bank account until the shift
  is merged, so its lines are matched to the receipt only.
* Only M-Pesa has a statement import. Card and PayPal have clearing accounts, ready for the same treatment.
* Withdrawal lines are stored but not matched.

## Weekly summary

On Mondays, once the business's chosen hour has passed, an email (and SMS, if set) says how many payments of each kind
do not match. Nothing is sent when everything matches. It is switched off with the **Payment matching** box in the
business's alert settings.
