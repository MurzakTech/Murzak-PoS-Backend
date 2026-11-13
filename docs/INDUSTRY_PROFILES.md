# Industry profiles, pharmacy and restaurant (Phase 1)

## In plain words

Murzak POS now has two specialised "personalities": a **pharmacy** and a **restaurant and bar**. Each store (an
*outlet*, which ERPNext calls a warehouse) can have its own kind of shop, so one business can run a restaurant till
and a pharmacy counter side by side. The kind of shop decides three things, kept in `api/industry_profile.py`:

- **Features** switched on (prescription checks and the dispensing register for a pharmacy; tables, kitchen tickets,
  void approval and the waiter cash-up for a restaurant).
- **Words** the till uses (a pharmacy *dispenses* to a *patient*; a restaurant *settles a bill* for a *guest*).
- **Look** it starts with (green and light for a pharmacy, amber and dark for a bar). The owner's brand colour wins.

A store with no kind of its own follows the business: the shop type chosen at sign-up, else the answer to "What do you
sell?". Anything else is a general shop, which behaves exactly as before.

## Calls

| Call | Who | What it does |
| --- | --- | --- |
| `industry_profile_api.get_industry_profile(warehouse?, company?)` | anyone in the business | The store's profile: `key`, `label`, `features`, `words`, `look`, `home_cards`, and `source` (outlet, shop_type, business_kind or default). |
| `industry_profile_api.list_outlet_industries(company?)` | owner, manager | Every store with its own kind of shop (empty follows the business) and the choices. |
| `industry_profile_api.set_outlet_industry(warehouse, industry, company?)` | owner | Sets a store's kind of shop (a POS Industry such as `PHARMACY` or `REST`); empty follows the business again. Written to the store's record. |
| `pharmacy.get_dispensing_register(company?, from_date?, to_date?, warehouse?)` | owner, manager | Every prescription-only medicine sold in the period (30 days by default, a year at most): date, medicine, quantity, batch, patient, prescriber, reference, dispenser. |
| `restaurant_api.list_void_approvers(company?)` | anyone in the business | Who may approve a void, whether the signed-in person needs one, and the quick reasons. |
| `restaurant_api.approve_void(item_code, qty, reason, rate?, item_name?, bill_label?, warehouse?, approver?, password?, company?)` | anyone in the business | Approves taking an item the kitchen already has off a bill, and keeps a **Till Void**. A cashier or waiter needs a manager or the owner to type their own password; a manager approves their own. |
| `restaurant_api.get_waiter_cashup(company?, date?, warehouse?)` | owner, manager | The day's takings by waiter: bills, sales, amount by payment mode, cash to hand over (cash taken less change), voids. |

## What changed in existing behaviour

- **Online store.** A prescription-only medicine is never shown online or accepted in an online order, whatever its
  "Show Online" setting. Kenya's Ministry of Health has banned online and boda boda antibiotic sales.
- **Antibiotics.** In a business with a pharmacy (the business itself or any of its stores), a new product named after
  a common antibiotic (amoxicillin, ciprofloxacin, co-trimoxazole and about 50 others, see `pharmacy_rules.py`) starts
  as prescription-only unless the person creating it says otherwise. The pharmacy starter list does the same.
- **Dispensing record.** A sale of prescription-only medicines still writes its line in the remarks, and now also fills
  separate fields on the invoice (`custom_rx_status`, `custom_rx_items`, `custom_rx_patient`, `custom_rx_prescriber`,
  `custom_rx_reference`, `custom_rx_dispensed_by`). The register reads those fields, so **sales made before this update
  are not in the register**; they can still be found by their remarks. The fields are not copied when a shift's POS
  sales are merged, and merged invoices are left out of the register, so nothing is counted twice.
- **Expired batches.** ERPNext 15 already refuses a sale from an expired batch and picks the batch that expires first
  (with `batch_order.pick_earliest_expiry_first`). Switching batch tracking on for products is the next slice.

## New records and fields

- **Till Void** (doctype): company, store, table or tab, item, quantity, amount, reason, requested by, approved by,
  when. Written once, never changed or deleted.
- **Warehouse** `custom_pos_industry` (Kind of Shop).
- **POS Invoice** and **Sales Invoice**: the dispensing record fields above, read only.

All are created by `bench migrate` (and on a new site by `install-app`) from `murzak_pos/murzak_pos/custom/*.json` and
the doctype folder. No patch is needed.

## Not in this slice

Batch and expiry switches on the product form and at goods receiving; menu modifiers on kitchen tickets; split bills;
service charge and tips; the tourism levy report.
