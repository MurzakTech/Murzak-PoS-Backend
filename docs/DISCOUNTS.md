# Discounts: one engine

Item and item group discounts are ERPNext **Pricing Rules**. The Discounts screen in the web app and the
**Pricing Rule** list in the Desk are two views of the same rules, and ERPNext applies them on every sale, whether
the sale is made at the till or in the Desk.

The only discount this app keeps in its own record (**Inventory Discount Rule**) is the discount on a **batch**,
because ERPNext has no rule for a batch.

Code: `murzak_pos/api/discount_rules.py`. The screen's calls are unchanged (`inventory_api.py`).

## What the Discounts screen manages

A Pricing Rule appears on the screen when it is plain: selling, one item or one item group, a percentage or an
amount off, and no other conditions (no customer group, price list, minimum quantity, coupon or free item).
Anything more involved is left to the Desk. The screen neither lists nor changes it, but ERPNext still applies it
on sales and the till's preview shows its discount.

## Priority

The screen goes lowest number first (1 beats 10). ERPNext goes highest number first. The two are mirrored:

| Kind | Screen 1 to 10 | ERPNext |
| --- | --- | --- |
| Item discount | 1 ... 10 | 20 ... 11 |
| Item group discount | 1 ... 10 | 10 ... 1 |

So an item discount always beats a group discount, as it always has, and an item rule and a group rule can never tie.

**Ties.** ERPNext refuses to complete a sale when two matching rules tie, so the screen stops a tie when a rule is
saved: two switched-on discounts on the same item (or on groups inside one another) with the same priority, where
the warehouses and dates can overlap, are refused with a message naming the other rule. Rules made directly in the
Desk are not checked this way; give them different priorities.

## What the till shows

`bulk_get_inventory_discounts` and `get_inventory_discount_for_item` give a batch discount first, then ask ERPNext's
own pricing engine for one unit of the item in that warehouse on that date. The sale is then priced by the same
engine, so what the till shows is what the sale charges. Rules that need a larger quantity or a customer group do not
show in the preview and still apply on the sale when their conditions are met.

## Behaviour to know

* A Pricing Rule beats a discount typed by hand on the same line. Extra discount can be given as a discount on the
  whole invoice.
* An amount discount is per unit, as in ERPNext.
* **Offline sales** are never discounted again: the till already charged them, so those invoices ignore pricing rules.
* Item discounts are checked against the businesses the caller belongs to; the list shows only the caller's
  businesses when no company is given.

## Moving the old rules

The patch `convert_discount_rules_to_pricing_rules` runs once on `bench migrate`. For every old item or item group
discount it makes a Pricing Rule with the same percentage or amount, warehouse, dates, priority and on/off state.
The old record is switched off and its description ends with `[Moved to Pricing Rule PRLE-xxxx]`. Where two old rules
for the same item shared a priority, the one changed later wins, as it did before. A rule that cannot be moved (for
example its item was deleted) is written to the Error Log, left as it was, and the rest carry on; the patch prints
how many moved and which were left.

After the migrate, check **Pricing Rule** in the Desk and the Discounts screen, and look in the Error Log for
titles starting "Discount rule".

## Later

ERPNext's **Promotional Scheme** and **Coupon Code** build on the same Pricing Rules, so promotions such as "buy 3
get 1 free" and coupon codes need no new engine.
