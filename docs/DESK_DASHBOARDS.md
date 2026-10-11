# Dashboards and reports inside Frappe

A manager who signs in to the Frappe desk (the back-office screens, not the till) finds a **Murzak POS** workspace with figures for their own business only. It is visible to the System Manager, Accounts Manager and Sales Manager roles.

## What is in it

| Part | Contents |
|---|---|
| Number cards (7) | Sales today, sales this month, open shifts, unpaid purchase invoices, items below reorder level, batches expiring within 30 days, invoices not yet sent to KRA |
| Charts (5) | Sales per day, sales by payment method, top 10 items, sales against purchases by month, stock value by warehouse |
| Shortcuts (8) | POS Profile, POS Opening and Closing Entry, Item, Stock Ledger, Kitchen Ticket, payment settings, alert settings |
| Reports (11) | Sales analytics, inventory valuation, stock ageing (runs in the background), expiry, low stock, unsent eTIMS invoices, and one for each chart. Each has the usual filters and Excel and PDF export |
| Financial statements | Profit and Loss, Balance Sheet, Trial Balance, General Ledger, Receivables, Payables, linked from the workspace |

## Whose figures a person sees

`api/desk_scope.py` decides, and every card, chart and report asks it first:

- Administrator sees every business.
- A person bound to a business (company on their profile, a company permission, or a default company) sees only that business, and a request for another business is refused rather than quietly ignored.
- A System Manager bound to no business is the platform operator and sees every business. Anyone else bound to nothing sees nothing.

The migration binds every user who has a company on their profile to it with a company permission (staff created through the app already had one; owners who registered themselves did not). New businesses bind their creator automatically.

## One definition of sales

Everywhere (cards, charts, reports and the till's own dashboard) a sale is a submitted Sales Invoice or a submitted POS Invoice, not a return, and a POS Invoice that was merged into a Sales Invoice at shift close is left out because that Sales Invoice already counts it. **The till's dashboard used to read Sales Invoices only, so a business selling through POS Invoices saw none of its sales there; it now counts both**, so those businesses' dashboard totals will rise to the true figure.

## For the till frontend

`murzak_pos.api.standard_reports.run_standard_report(name, filters)` runs one of the six standard financial reports. Any other report name is refused, and the company filter is forced to a business the caller may see.

## Tested on staging

Two businesses, a manager of each (with the roles an owner gets):

| Check | Result |
|---|---|
| Every card, chart and report, as manager of A and again as manager of B | Each showed only its own business's figures; asking for the other business was refused |
| Today's sales card against the till dashboard's total for the same day | Equal for both businesses (3,316.00 and 77.00) |
| Excel export of every report | Worked, including the background stock ageing report |
| The workspace in the sidebar | Shown to the manager, not to a cashier |

A leak was found and fixed during this testing: the expiry report briefly showed another business's batches because the business list was worked out after switching to a higher-privilege user. It is now worked out first, and a test covers it.
