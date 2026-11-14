# Industry profiles, pharmacy and restaurant (Phases 1 to 4)

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

## Phase 2

### Calls added

| Call | Who | What it does |
| --- | --- | --- |
| `batches.set_batch_tracking(item_code, on, company?)` | catalogue roles | Switches a product to tracking batches and expiry dates. Allowed only before its first sale or delivery (ERPNext's rule); a product with history is refused in plain words. |
| `menu_options_api.list_menu_options(company?)` | anyone in the business | Every dish with options: `{item_code: [groups]}`. |
| `menu_options_api.set_menu_options(item_code, groups, company?)` | catalogue roles | Keeps a dish's option groups (`{name, required, multiple, choices}`; a choice such as `"Extra cheese +50"` adds 50). |
| `levy_api.get_tourism_levy(company?, month?)` | owner, manager | A month's sales of each restaurant-and-bar store and 2% of them, **both before and after VAT**. Confirm with the Tourism Fund which base your business files on. |
| `pharmacy.get_controlled_register(company?, from_date?, to_date?, warehouse?)` | owner, manager | For each controlled medicine: opening balance, every movement with the balance after it (patient, prescriber and dispenser on a sale), closing balance. |

### Changed behaviour

- **Batches at receiving.** Receive goods (`receive_goods`), a bill already held (`create_direct_bill`) and
  `create_purchase_receipt` take `batch_no` (optional: made from the code and expiry when blank) and `expiry_date`
  (required) for each batch-tracked product. Stock that has already expired is refused before anything is saved.
  One batch per product per delivery.
- **New products** (`create_product_simple`) take `track_batches`, and with opening stock `opening_batch_no` and
  `opening_expiry`; also `controlled_drug` and `ppb_registration_no`. `update_product` takes the last two.
  A controlled medicine is always prescription-only too.
- **Selling batches.** ERPNext picks the batch for a sale (earliest expiry first, never expired). The new after-migrate
  step `batch_order.keep_auto_batch_pick_on` keeps that automatic pick switched on, because the till never names a batch.
- **Menu options** chosen at the till go on the invoice line's description (`options_text` on a sale line).
- **Product list rows** now carry `prescription_only`, `tracks_batches`, `controlled_drug` and `ppb_registration_no`;
  purchase lines carry `tracks_batches`.

### New fields

`Item`: `custom_controlled_drug` (Controlled Medicine), `custom_ppb_registration_no` (PPB Registration Number),
`custom_menu_options` (hidden, kept by the POS).

### Why the controlled register reads POS sales one by one

A POS Invoice moves no stock until the shift is closed and its sales are merged into one Sales Invoice. Reading only
the stock ledger would lose who each dose was for. The register therefore reads every POS sale itself and leaves out
the merged Sales Invoice, so each dispensing line is kept and nothing is counted twice.

## Phase 3

### Calls added

| Call | Who | What it does |
| --- | --- | --- |
| `restaurant_settings_api.get_restaurant_settings(company?)` | anyone in the business | Happy hours, service charge percent (and the business's Service charge item), delivery platforms with commission, each store's tables. |
| `restaurant_settings_api.save_restaurant_settings(settings, company?)` | owner, manager | Keeps them, checked by `restaurant_settings_rules`. A service charge above 0 makes the Service charge item (a service, no stock) on first use. |
| `restaurant_api.record_tip(amount, mode_of_payment, sales_person?, invoice?, warehouse?, company?)` | anyone in the business | Keeps a tip as a **Till Tip**, apart from the sale. |
| `restaurant_api.get_platform_sales(company?, month?)` | owner, manager | A month's orders, sales, commission and expected payout per delivery platform. |

### Changed behaviour

- **Sales** take `order_channel` and `channel_ref` (kept in `custom_order_channel` and `custom_channel_ref` on the
  invoice, under their own Order Channel section).
- **The waiter cash-up** shows each waiter's tips and how they were paid. Tips never count as takings or as cash to
  hand over.
- **Purchase lines** carry their product's barcodes, so a scanned GS1 pack code finds its line at Receive goods.
- The till (frontend) reads GS1 pack codes, applies happy hours, adds the service charge as its own sale line, splits
  bills (by item, with the rest held under the same table name; or equally, with one payment each) and shows the table
  map from the open bills.

### New records

**Till Tip** (doctype): company, store, sale, amount, how paid, waiter, recorded by, when. Written once, never changed.
POS Invoice and Sales Invoice: `custom_order_channel`, `custom_channel_ref`.

## Phase 4

### Pharmacy: a bill split between a medical insurer and the patient

A patient covered by a private insurer (AAR, Jubilee, CIC, Britam, Madison and so on) pays only a **co-payment** at the
counter; the insurer owes the rest and the pharmacy claims it later. How it works:

- **Insurers** are kept per business under Settings, Business, Medical insurers, each with its co-payment rule: a
  percentage, a fixed amount, or both (the patient pays the larger, never more than the bill). Saving an insurer makes
  a **Customer** for it (on first use), because the insurer is who the sale is billed to.
- **At the till** the cashier chooses Insurance, the insurer, and types the patient's name and member number (scheme and
  pre-authorisation number optional). The pay screen opens split in two: the co-payment by the patient's payment
  method, and the rest on **Credit** to the insurer. The till's credit limit check does not apply to an insurer.
- **The server** (`create_pos_invoice(..., insurance={insurer, patient, member_no, scheme?, preauth?})`) refuses an
  insurer that is not one of the business's, a sale billed to any customer other than that insurer, and a claim with
  no member number or patient name. The co-payment it keeps is what the patient actually paid (the payment rows that are
  not left owing), never a figure the till sends.
- **Claims** (Reports, Insurance claims) list every such sale with what the insurer owes. Choose claims to mark them as
  sent (with the claim or batch number), as rejected (with the reason), or to record the insurer's payment.
- **An insurer's payment** is shared over the chosen claims from the top, each taking what it is owed; a payment larger
  than they owe together is refused. Each part becomes a submitted **Payment Entry** on the sale's Sales Invoice (for a
  POS sale, the Sales Invoice its shift was merged into), so what the insurer owes goes down in the books. A sale whose
  till shift is still open cannot take a payment yet, and says so.

| Call | Who | What it does |
| --- | --- | --- |
| `insurance_api.get_insurers(company?)` | anyone in the business | The insurers, their co-payment rules and customer records. |
| `insurance_api.save_insurers(insurers, company?)` | owner, manager | Keeps them; makes each insurer's Customer on first use. |
| `insurance_api.get_claims(company?, insurer?, status?)` | owner, manager | Claims, newest first, with the total owed by status. |
| `insurance_api.mark_claimed(invoices, claim_ref?, company?)` | owner, manager | Marks claims as sent. |
| `insurance_api.set_claim_rejected(invoice, reason?, company?)` | owner, manager | Marks a claim as rejected. |
| `insurance_api.record_insurer_payment(invoices, amount, mode_of_payment, reference?, company?)` | owner, manager | Shares the payment over the claims and records a Payment Entry for each. |

A claim's status is one of To claim, Claimed, Part paid, Paid, Rejected.

### Restaurant: matching a delivery platform's payout against its orders

Glovo, Uber Eats and Bolt Food pay the restaurant every week or so: the sales of the orders they carried less their
commission. On the Delivery platforms report the owner pastes the order numbers from the platform's statement and the
amount paid. **Check** shows, without saving, the orders matched, the numbers found nowhere in the POS, the orders an
earlier payout already settled, and the commission the platform actually kept against the commission agreed in the
settings. **Record payout** keeps a **Platform Payout** and marks each matched order (`custom_channel_payout`), so no
order is paid out twice.

This is a check, not bookkeeping: no journal entry is made. The money received is recorded in the books the way the
business already records it. Orders match on the order number the cashier typed at the till, so an order without one
cannot be matched; the report says how many there are.

| Call | Who | What it does |
| --- | --- | --- |
| `payout_api.list_unsettled_orders(platform, company?, from_date?, to_date?)` | owner, manager | The platform's orders no payout has settled, and how many have no order number. |
| `payout_api.check_payout(platform, order_refs, amount, company?)` | owner, manager | What recording would do, without saving. |
| `payout_api.record_payout(platform, order_refs, amount, payout_date?, statement_ref?, mode_of_payment?, company?)` | owner, manager | Keeps the payout and marks its orders. |
| `payout_api.list_payouts(company?, platform?)` | owner, manager | The last 200 payouts recorded. |

### New features in the profiles

`insurance` (pharmacy) and `delivery_platforms` (restaurant and bar). A store whose server has not been updated simply
does not show the new screens.

### New records and fields

- **Platform Payout** (doctype): company, platform, payout day, statement reference, amount received, orders matched,
  their sales, commission expected and taken, the difference, the order numbers not found, who recorded it and when.
  Written once, never changed or deleted.
- **POS Invoice** and **Sales Invoice**: `custom_channel_payout` (Platform Payout), and an Insurance Claim section:
  `custom_insurer`, `custom_insured_patient`, `custom_member_no`, `custom_scheme`, `custom_preauth_no`,
  `custom_copay`, `custom_claim_status`, `custom_claim_ref`. None is copied when a shift's sales are merged, and merged
  invoices are left out of the claims and payout lists, so nothing is counted twice.

### Points to confirm before a pilot

- **Credit sales must be on.** The insurer's share is a Credit payment line, so the Credit payment method must be
  switched on in Settings. The till says so if it is not.
- **Co-payment rules differ by scheme.** The rule kept per insurer is a simple percent or amount; check each contract.
  Insurers that need claims sent through their own portal or a switch (such as Smart or Slade360) still need that done
  outside the POS; the claim list gives the details to send.
- **Rejected claims stay owed in the books** until the business collects them from the patient or writes them off.
- **The till's "Sales Invoice" mode** (POS Settings set to create Sales Invoices) does not take receivable payment
  lines, so insurance sales need the default POS Invoice mode.

## Not done yet

- Moving the stock of a medicine that already has sales or deliveries to a batch-tracked copy (it is refused, with a
  message to ask for help).
- Sending claims electronically to insurers or to Smart/Slade360; posting a platform payout's commission to the books.
- A happy hour is decided when the item is added; a bill kept open past the end of happy hour keeps those prices.
- Two of the same dish with different options on one bill: the options apply to the whole line, as notes already do.
