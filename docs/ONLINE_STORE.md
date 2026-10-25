# Selling online with Murzak POS

Murzak POS lets a shop sell on the internet from the same products and stock it already has at the till. There are two
ways to do it, and a business can use either one.

| | Track A: "I already have a website" | Track B: "I have no website" |
|---|---|---|
| Who it is for | A shop with a WooCommerce (WordPress) website | A shop or WhatsApp seller with no website |
| Where customers shop | The owner's own website | A Murzak-hosted store at `/shop/<name>` |
| Products | POS sends them to the website | Read straight from the POS |
| Stock | POS keeps the website's stock current | Always the POS's live stock |
| Orders | The website sends each order to the POS | The store sends each order to the POS |
| Payment | Whatever the website offers | Pay on delivery or pickup, and M-Pesa |

Both tracks end in the same place: an order becomes a **Sales Order** in the POS, with its stock reserved, and it
appears on the till as an **online order** that staff confirm, prepare and deliver.

```
 WooCommerce site ──┐                                   ┌─ Confirm
                    ├──► Online order (Sales Order) ───►├─ Ready
 Hosted store ──────┘    stock reserved, shown on till   ├─ Delivered ──► draft Sales Invoice
 WhatsApp order ───►                                     └─ Cancelled (stock released)
```

## Before anything works (one-time, by whoever runs the server)

1. Deploy this version and run `bench --site <site> migrate`. This adds the online fields (a **Show Online** tick on
   every product, and the online-order fields on Sales Orders) and lets M-Pesa payments be linked to an order.
2. For Track B, if the shop pages are served from a different address than the POS server, set `storefront_base_url`
   in the site's `site_config.json` (for example `"storefront_base_url": "https://shop.murzak.app"`). The share link is
   `<storefront_base_url>/shop/<name>`. If it is not set, the server's own address is used.
3. For Track A and for M-Pesa orders, make sure the server's public address is reachable from the internet. The
   website's orders and M-Pesa's replies are sent to it.

## Track A: the business already has a WooCommerce website

The owner does this once, in Settings > E-Commerce:

1. In WordPress open **WooCommerce > Settings > Advanced > REST API > Add key**. Give it **Read/Write** permission and
   copy the Consumer key and Consumer secret.
2. In Murzak POS enter the website address (`https://myshop.com`) and the two keys, choose the warehouse the online
   stock comes from, and press **Test connection**.
3. Press **Sync catalogue**. Every online product is sent. Products the website already has (matched by SKU, which is
   the POS item code) have their **price and stock** updated; their name, pictures and description stay as the owner
   edited them on the website. New products are created.
4. To receive orders, read the **webhook address and secret** (`get_order_webhook`). In WordPress open
   **WooCommerce > Settings > Advanced > Webhooks > Add webhook**, choose the topic **Order created**, status
   **Active**, paste the address as **Delivery URL** and the secret as **Secret**. When you save, WooCommerce sends one test
   message to check the address; a right address answers it, and WooCommerce shows an error if the address is wrong (a 401 means the
   address's code is wrong or incomplete). Add a second webhook with the topic
   **Order updated**, so an order cancelled on the website is cancelled in the POS too.

After that it runs by itself:

* **Stock.** Every sale, return and online order triggers a stock update to the website within moments, and an hourly
  job catches stock that moved without a sale (deliveries, stock counts, transfers).
* **Orders.** A website order that is paid or being processed becomes an online order. The price the customer paid is
  kept. If the stock was short, the order is still recorded (the customer has paid) and flagged. An order that cannot be
  matched (a website product with no SKU, or a SKU not in the POS) is not lost silently: the reason is shown in
  Settings > E-Commerce (`last_order_message`) and written to the Error Log.

Notes for the owner:

* Every product on the website must have a **SKU equal to the POS item code**. Products created by "Sync catalogue" do.
* Press **Regenerate** on the webhook (`regenerate_order_webhook`) if the secret may have leaked. The old address and
  secret stop working at once; paste the new ones into WooCommerce.

## Track B: the business has no website

The owner does this once, in Settings > Online Store:

1. Choose a **web address name**, such as `duka-ya-amina` (3 to 40 lowercase letters, numbers or hyphens). The screen
   can check availability while typing (`check_store_slug`).
2. Say how customers receive goods (**delivery**, **pickup**, or both) and how they pay (**pay on delivery or pickup**,
   **M-Pesa**, or both). M-Pesa needs M-Pesa to be set up already under Settings > Payment Gateways.
3. Add the shop's **WhatsApp number**, an optional **minimum order**, a pickup address and a delivery note.
   Then choose **what delivery costs** (see "Delivery fees" below).
4. Tick which products appear (**Show Online** on the product, or `set_items_online` for many at once).
5. Switch the store on. The screen then shows the **store link** and a **share on WhatsApp** link.

The store is only allowed to open when a customer could really order from it: it needs a name, a way to receive, and a
way to pay.

What the customer does: opens the link, browses products with live stock, adds to a basket, gives their name, Kenyan
phone number and (for delivery) address, and chooses how to pay. They get an order number and a private **tracking
code**. With M-Pesa they receive a payment prompt on their phone; a successful payment marks the order paid. If they
miss the prompt, they can ask for it again (`pay_for_order`). The reply also carries a **WhatsApp link** that opens a
chat with the shop and the order already written, so the customer can confirm in one tap.

## Delivery fees

The owner picks one of four ways to charge for delivery. The fee is always worked out on the server from these rules;
the customer sees it before ordering, but can never change it.

| Way | How it works | What the owner enters |
|---|---|---|
| Free | Delivery costs nothing | Nothing |
| Flat | One fee for every delivery | The fee |
| By area | A fee for each named area. An area not on the list is not delivered to | The areas and a fee for each (for example Westlands 200, Kasarani 300) |
| By distance | A starting fee that covers the first few kilometres, then a price for each extra kilometre, up to a longest distance | The shop's location, the included kilometres, the starting fee, the fee per extra kilometre, the longest distance |

For flat, area and distance, the owner can also set **free delivery above** an amount. It looks at the value of the
goods only, not the delivery.

**How distance is measured.** It is the straight line between the shop and the customer, multiplied by a **road
factor** (1.3 unless the owner changes it), because roads are longer than a straight line. This is an estimate that
needs no paid map service. The owner should try a few addresses they know and adjust the road factor until the fees feel
right. The customer gives their location with a map pin or their phone's location, which the store page sends as
`latitude` and `longitude`. If the shop later wants true road distance, a routing service can replace this one function.

Fees are rounded up to whole shillings. On the order the fee appears as a separate **Delivery** line, so it is in the
total, in the M-Pesa prompt and on the invoice. The "Delivery" item is made automatically for each business the first
time it is needed, and is kept off the online product list.

## Working the orders at the till

Staff holding the online store's `orders` permission (Business Owner, System Manager, Sales Manager or Online Orders
Clerk) can:

* list online orders by status or channel (`list_online_orders`) and open one (`get_online_order`);
* move an order along: **New > Confirmed > Ready > Delivered**, or **Cancelled** (`set_online_order_status`).
  Cancelling frees the reserved stock, and is refused once the order has been invoiced or delivered;
* mark it paid, for cash received on delivery (`mark_online_order_paid`);
* make a **draft Sales Invoice** from it, which takes the stock out when the owner submits it
  (`create_invoice_for_online_order`).

The till is told the moment a new order arrives (a `murzak_pos_event` screen message of kind `online_order`). Only users
holding the `orders` permission receive it.

### Selling an online order at the till

`create_pos_invoice` and `create_sales_invoice` accept an extra `online_order` (the Sales Order name). The caller needs
the `orders` permission as well as the right to sell. When it is given:

1. The order must exist, belong to the same business, not be cancelled or closed, and not already be sold. The order is
   locked while the sale is made, so two tills cannot sell it at once. A sale saved as a draft is refused.
2. The sale takes the stock out once. After it is saved the order is **closed**, which releases the stock the order was
   holding, so the stock is not counted twice.
3. The two are linked both ways: the sale holds `custom_online_order` and `custom_sales_channel = Online` (reports can
   tell Online from Till; empty means Till), and the order holds `custom_till_sale_type` and `custom_till_sale`.
4. The order is marked paid unless the sale left money owing (a sale on credit). `mark_online_order_paid` can be called
   again afterwards and does nothing.
5. A sold order cannot get a second invoice (`create_invoice_for_online_order`) and cannot be cancelled; return the sale
   instead. Staff still mark it Delivered.
6. A retry with the same `client_reference` returns the first sale, and finishes settling the order if the first try
   was cut off before it was settled.

Run `bench --site <site> migrate` to add the link fields and create the **Online Orders Clerk** role.

## For the frontend developer

All calls are `/api/method/murzak_pos.api.<module>.<function>`. Replies are `{"success": true, "data": ...}` unless noted.

### Public calls (no sign-in): `storefront_api`

| Function | Purpose |
|---|---|
| `get_store(slug)` | Store name, tagline, WhatsApp number, delivery and pickup options, payment options, minimum order, currency, categories, and `delivery`: the fee rules a customer needs (`mode`, `flat_fee`, `areas`, `needs_location`, `longest_distance_km`, `free_above`) |
| `list_products(slug, search, category, page, page_size)` | One page of products: code, name, category, unit, price, description, image, `in_stock`, `low_stock` (a number only when 5 or fewer are left) |
| `get_product(slug, item_code)` | One product |
| `quote_delivery(slug, lines, delivery_area, latitude, longitude)` | The delivery fee for this basket to this area or location. Returns `deliverable`, `fee`, `distance_km` and a `message` to show when the shop will not deliver there. Call it when the customer picks an area or shares their location |
| `checkout(slug, customer_name, phone, lines, delivery_method, payment, address, notes, delivery_area, latitude, longitude)` | Place the order. `lines` is `[{"item_code": "RICE", "qty": 2}]`. `delivery_method` is `Delivery` or `Pickup`. `payment` is `cash` or `mpesa`. Returns `order`, `token`, `total`, `delivery_fee`, `lines`, `payment_prompt` (`sent` and `message`, for M-Pesa) and `whatsapp_link` |
| `pay_for_order(token)` | Send the M-Pesa prompt again for an unpaid order |
| `online_orders.track_online_order(token)` | Order status, delivery method, paid, total, date and shop name. Nothing personal |

A closed or unknown store answers "Store not found" for every call. Prices are always the POS's; any price sent by the
browser is ignored. The store route the frontend needs to build is `/shop/<slug>`, plus an order-tracking page that
takes the tracking code.

### Owner and staff calls (signed in)

| Module | Function | Who |
|---|---|---|
| `online_store_api` | `set_items_online` | `catalogue`: owners, Sales Manager, Item Manager |
| `online_store_api` | `get_online_store`, `check_store_slug`, `save_online_store` | `settings`: owners, Sales Manager |
| `ecommerce_api` | `get_ecommerce_products` | `catalogue` |
| `ecommerce_api` | `get_ecommerce_settings`, `save_ecommerce_settings`, `test_woocommerce_connection`, `sync_woocommerce_catalog` (`sync_type="stock"` for stock only), `get_order_webhook`, `regenerate_order_webhook` | `integrations`: owners only |
| `online_orders` | `list_online_orders`, `get_online_order`, `set_online_order_status`, `mark_online_order_paid`, `create_invoice_for_online_order` | `orders`: owners, Sales Manager, Online Orders Clerk |

The owners are Business Owner and System Manager. The four permissions are defined in `api/access_control.py` and match the
frontend's `onlineStoreAccess`.

Changes to existing replies (the Settings > E-Commerce screen should read them):

* `sync_woocommerce_catalog` keeps its old fields and adds `status` (`success`, `partial` or `failed`), `created_count`,
  `updated_count`, `error_count` and `errors`. It now reports a real failure as a failure.
* `get_ecommerce_settings` adds `webhook_ready`, `last_order_at` and `last_order_message`, and `catalog_count` now counts
  only this business's online products.
* `get_ecommerce_products` returns `stock: 0` for a product with no stock count, as before.

## How it is kept safe

* **One business never sees another's products or orders.** Every product list is filtered to the business, in the
  shared catalogue code that both tracks use. (The previous sync did not filter, and counted other businesses' items.)
* **A stranger cannot set a price or order what is not there.** Prices come from the POS. An order for more than is in
  stock is refused. Quantities and basket sizes are limited.
* **A stranger cannot flood a shop.** Every public call is limited per visitor; an order is limited per phone number
  (5 an hour); M-Pesa prompts are limited to one at a time per order. A prompt can still reach a phone whose owner did
  not place the order (the store cannot prove who owns a number), but it can do nothing unless that person enters their
  own M-Pesa PIN, and the limits keep it rare. Staff should still treat an unpaid M-Pesa order as unpaid.
* **Only the owner's own website can create orders.** Each call is signed by WooCommerce with the shop's secret, and a
  call with a wrong or missing signature is refused, with the same answer for an unknown address.
* **The same order is never recorded twice**, however many times the website repeats it.
* **A customer's tracking code is long and private**, and the tracking reply holds no names, phone numbers or addresses.
* The WooCommerce keys and the webhook secret are stored encrypted, as before.

## What this version does not do yet

These are deliberate gaps, listed so nobody assumes otherwise:

* **Delivery distance is an estimate, not road distance** (see "Delivery fees"). Delivery fees apply to the hosted store;
  a WooCommerce shop sets its own shipping on the website.
* **Product categories and photos are not mapped to WooCommerce categories.** Products are sent without categories;
  pictures are sent only when a product is first created.
* **WhatsApp is a link, not a robot.** The customer is sent into a WhatsApp chat with the order written for them.
  Automatic WhatsApp messages (order confirmations, status updates) need a WhatsApp Business API account with Meta and
  are a separate piece of work.
* **The storefront pages themselves belong to the frontend** (`Murzak-PoS-Frontend`). This version provides everything
  they call.
* **Order sync is one way for stock and prices** (POS to website). The POS stays the source of truth.
* **No automated test touches a real WooCommerce site, Safaricom or a database.** The unit tests replace all three.
  Before customers rely on it, run one real test order through each path on the staging server
  (`docs/STAGING_SERVER.md`): a website order, a hosted-store order paid by M-Pesa, and a cancelled order.
