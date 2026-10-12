# Sales people and commission (WS8.4)

A business keeps its own sales people. Each is an ERPNext **Sales Person** tagged with the business it belongs to, a
commission rate in percent, and, optionally, the till login it belongs to. The calls are in
`murzak_pos/api/sales_person_api.py`.

## Calls

| Call | Who | What it does |
| --- | --- | --- |
| `create_sales_person(sales_person_name, commission_rate, user?, company)` | Sales Manager, owner | Adds a sales person. |
| `update_sales_person(name, commission_rate?, user?, enabled?)` | same | Changes the rate, the linked login, or switches the person off. Sales already made keep the rate they were made at. |
| `list_sales_persons(company, include_disabled?)` | any member of the business | The business's sales people. |
| `get_commission_report(company, from_date?, to_date?, sales_person?)` | Sales Manager, Accounts Manager, owner | Per person: sales, returns, net sales, commission, number of invoices, and totals. |
| `create_sales_invoice(..., sales_person?)` and `create_pos_invoice(..., sales_person?)` | cashier | Credit the sale to a named sales person. |

## How a sale is credited

* The till can name the sales person on the sale.
* If it does not, and the signed-in cashier's login is linked to a sales person of the business, the sale is credited to
  them automatically.
* If neither, the sale has no sales person and earns no commission.
* The whole sale is credited to one person at their commission rate (ERPNext's Sales Team, 100 percent). The invoice's
  own commission total is filled in by ERPNext.

## Returns

A return created with `create_sales_return` against a sale is credited to the same sales person, so their commission
falls by the same share. The commission is worked out on the amount before VAT (the net sale).

## Names

Sales person names are shared across the whole site by ERPNext. A name already used by another business is saved with the
business's abbreviation added ("Jane - ABC"). One login can be linked to one sales person per business.

## Limits

* One sales person per sale; splitting a sale between people is not offered by the till calls.
* Targets and incentives are left to ERPNext's own Sales Person screens.
