# Recipes and wastage (WS6)

A restaurant dish is an ERPNext **Product Bundle**: the dish is a parent item that is not kept in stock, and its
ingredients are the bundle's components. ERPNext takes the ingredients out of stock on every sale, on both kinds of
invoice, and puts them back when the sale is cancelled or returned. The calls are in `murzak_pos/api/recipes_api.py`.

## Calls

| Call | What it does |
| --- | --- |
| `create_recipe(dish, ingredients, company)` | Gives a menu item a recipe. The item stops being kept in stock. |
| `update_recipe(dish, ingredients, company)` | Replaces the ingredients. |
| `get_recipe(dish, company, warehouse?)` | Ingredients with their cost, cost per portion, selling price, margin; with a warehouse, also how many portions the stock allows and which ingredient limits it. |
| `list_recipes(company, search?, page, page_size)` | A page of recipes, with ingredient count and cost per portion. |
| `delete_recipe(dish, company)` | Removes the recipe; the item stays, still not kept in stock. |
| `replace_dish_item(item_code, new_item_code?, company)` | See "A dish that already has stock history". |
| `record_wastage(items, warehouse, reason, company, posting_date?, ticket?)` | Takes wasted or spoiled stock out, with the reason. A dish is taken out as its ingredients. |
| `get_wastage_report(company, from_date?, to_date?, warehouse?)` | Wastage by item and the latest entries with their reasons. |

An ingredient is `{item_code, qty, uom?}`. Setting up a recipe needs a catalogue role (the same roles that may change
products); wastage needs a stock role. Reading a recipe needs only membership of the business.

## Units

ERPNext fixes a bundle row to the ingredient's **stock unit**, so a recipe is saved in it. An ingredient given in
another unit is converted when the recipe is saved: 250 grams of flour kept in kilograms is saved as 0.25. The item's own
unit table is used first, then ERPNext's general conversions. If neither knows the pair the call is refused, because
ERPNext would otherwise treat the units as equal and 250 grams would become 250 kilograms. A quantity too small to keep
in the stock unit (under 0.001, or losing more than 5 percent when rounded) is refused too; use a smaller stock unit for
that ingredient. The same ingredient on two lines is added together.

## A dish that already has stock history

ERPNext will not turn an item with stock history into a bundle parent. `create_recipe` says so and points to
`replace_dish_item`, which makes a copy of the item that is not kept in stock, moves the barcodes and prices across,
and switches the old item off so the till stops offering it. The old item keeps its stock history. Then call
`create_recipe` on the new item.

## Cost and margin

Cost per portion is each ingredient's quantity times its cost: the store's valuation when a warehouse is given;
otherwise the average cost of the business's stock on hand; failing that the item's own valuation, then its last
purchase price. `costs_known` is false when any ingredient has no cost.

The dashboard's profit figures now include the ingredients: a dish's own sale line carries no cost, so the cost of
the ingredients on the invoice (its packed items) is added, and comes back on a return.

## Wastage

Food cooked and thrown away should not go back into stock the way a refund does. `record_wastage` makes a submitted
Stock Entry of the type **Wastage** (a Material Issue, made on first use) with the reason in its remarks, and the
kitchen ticket if one is given. On a business on eTIMS the entry carries the business's branch, which its books
require on every profit and loss posting.

The existing inventory variance report is built from stock counts, so a wastage entry does not appear in it;
`get_wastage_report` lists wastage on its own.

## Tested on staging

On staging, with two businesses (one on eTIMS, so it sells through Sales Invoices, and one not, so it sells through POS
Invoices): selling 3 portions of a dish took exactly 3 times each ingredient's recipe quantity on both paths and
posted the ingredients' cost; cancelling the invoice put the stock back; the dashboard's cost of goods rose by the
ingredients' cost; wastage of a dish took out its ingredients; a second business was refused on every read and change.
