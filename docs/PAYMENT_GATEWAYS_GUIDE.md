# Payment Gateways: Setup and Operations Guide

This guide covers M-Pesa (Safaricom Daraja), Pesapal, PayPal and bank payments in Murzak POS.
It is written for the person onboarding a new client business, and assumes no coding knowledge.

## 1. How it works in one paragraph

Every client business enters its **own** keys on the screen **Settings > Payment Gateways** in the POS.
The keys are stored encrypted on the server, separately for each company, and are never sent back to the
browser. The addresses that Safaricom, Pesapal and PayPal use to confirm payments ("callback URLs") are
built automatically from the server's address, with a secret token unique to each business. Onboarding a
new client is therefore: create their account, enter their keys, press **Test connection**. No code
changes and no server access are needed.

## 2. One-time server setup (done once for the whole platform)

1. The site must be reachable on a **public HTTPS address** (for example `https://api.pos.murzaktech.tech`).
   Payment providers cannot call `http://` or `localhost` addresses.
2. Frappe builds callback URLs from the site's `host_name`. Check `sites/<site>/site_config.json` contains:
   ```json
   "host_name": "https://api.pos.murzaktech.tech"
   ```
   If the server sits behind a proxy with a different public address, add
   `"payment_callback_base_url": "https://public.address"` instead. The settings screen shows a yellow
   warning when the address cannot be reached by payment providers.
3. Deploy the app and run `bench --site <site> migrate`. Migration adds a unique index on the M-Pesa
   receipt number; it would only fail if the log already held two records with the same receipt, which the
   earlier (non-working) version could not create.
   This also adds the new fields and the two new record types (POS Gateway Settings, POS Gateway
   Transaction).

## 3. Onboarding a client: M-Pesa

### What the client must obtain from Safaricom

| Item | Where it comes from | Notes |
| --- | --- | --- |
| Till number (Buy Goods) **or** Paybill number | Safaricom, when the business registered for Lipa na M-Pesa | Printed on the till sticker |
| Store number (head office shortcode) | Safaricom, for Buy Goods tills | Often different from the till number. Ask Safaricom if unsure |
| Consumer Key and Consumer Secret | developer.safaricom.co.ke > My Apps | Create an app with "Lipa na M-Pesa Online" (and "M-Pesa C2B" if direct till payments are wanted) |
| Lipa na M-Pesa Online passkey | Safaricom, by email, after the "Go Live" process | Not shown on the portal for production |

### Steps

1. Sign in to the POS as the business owner (or an Accounts Manager).
2. Go to **Settings > Payment Gateways > M-Pesa**.
3. Start with **Testing (sandbox)**. Press **Use Safaricom test shortcode**, then paste the Consumer Key and
   Consumer Secret of a sandbox app. Choose the receiving account (for example "M-Pesa - ABC").
4. Press **Save**, then **Test connection**. Then enter a phone number and press **Send KES 1 test prompt**.
   In sandbox no real money moves.
5. When the client has gone live with Safaricom, switch to **Live (real money)**, choose **A till** or
   **A paybill**, enter the live numbers, keys and passkey, and save again. Repeat the KES 1 test with a real phone.

### Options

* **Let cashiers record an M-Pesa code when the phone prompt fails** (on by default). If the prompt
  cannot be delivered, the customer pays the till directly and the cashier records the code from their
  SMS. Turn this off for stricter control.
* **Show payments customers make straight to the till** (off by default). Registers the till with
  Safaricom (the "C2B" product) so direct payments appear at the till and are matched by code, with
  no trust in the cashier needed. Requires the C2B product on the Daraja app.

## 4. Onboarding a client: Pesapal (cards, M-Pesa, Airtel Money)

1. The client opens a Pesapal merchant account at pesapal.com and gets a **Consumer Key** and
   **Consumer Secret** (sandbox keys are on developer.pesapal.com).
2. In **Settings > Payment Gateways > Pesapal**, enter the keys and the receiving account (usually the bank
   account Pesapal settles into), then **Save** and **Test connection**. Testing also registers the
   notification address with Pesapal automatically.
3. At the till the cashier enters the customer's phone or email, presses **Show Pesapal QR code**, and the
   customer scans it and pays on their own phone.

## 5. Onboarding a client: PayPal

1. The client creates a PayPal Business account and a REST app on developer.paypal.com to get the
   **Client ID** and **Secret**.
2. **PayPal does not accept Kenyan Shillings.** Choose the currency to charge (default USD) and make sure an
   exchange rate exists: **Accounting > Currency Exchange**, from KES to USD. Sales are converted at that
   rate and both amounts are recorded.
3. Enter the keys and the receiving account, **Save** and **Test connection**.

## 6. Bank transfers and deposits

Any payment method of type **Bank** that has an account for the company (see **Settings > Bank
Accounts** and **Settings > Payment Methods**) asks the cashier for a reference (transfer, EFT, RTGS,
PesaLink or slip number) before the sale can be completed. The reference is saved on the sale's payment
line and printed on the receipt.

Direct bank APIs (for example Equity Jenga or KCB Buni) are not connected yet. Each needs bank-specific
onboarding and credentials; the gateway design allows them to be added later in the same way as Pesapal.

## 7. What happens at the till

| Method | What the cashier does | When "Complete sale" unlocks |
| --- | --- | --- |
| M-Pesa prompt | Confirms the customer's number, presses **Send prompt** | When Safaricom confirms the payment |
| M-Pesa code | Picks the payment from the recent list or types the code | When the code matches an unused payment (or is recorded manually, if allowed) |
| Pesapal / PayPal | Presses **Show QR code**; customer pays on their phone | When the gateway confirms the payment |
| Bank | Types the bank reference | When a reference is entered |

Amounts sent to M-Pesa are rounded **up** to whole shillings, because M-Pesa does not accept cents.

## 8. Safeguards built in

* **Tenant isolation.** Every request is limited to the signed-in user's own company. Only Accounts Managers
  and System Managers can view or change keys.
* **No fake payments.** When a sale is saved, the server checks every M-Pesa, Pesapal and PayPal line against
  a payment that really succeeded, belongs to the same business, covers the amount and has not been used on
  another sale. The payment is then linked to the sale.
* **Callbacks are verified.** Each callback URL carries the business's secret token. Pesapal and PayPal results
  are always re-checked directly with the provider instead of trusting the incoming call.
* **Safe to repeat (idempotent).** Repeated messages and retries never double-count money:
  * A callback delivered twice changes nothing the second time.
  * **One live prompt per payment line.** Pressing "Send prompt" again, or retrying, returns the prompt
    already waiting on the customer's phone (or the payment already received) instead of sending a second
    prompt the customer could also approve. A prompt that expired or failed can be resent.
  * **One record per M-Pesa receipt.** The database refuses a second record with the same receipt number.
    When Safaricom reports a prompt payment also as a direct till payment, the two are merged.
  * **A payment is locked while a sale saves.** Two tills cannot use the same payment at the same instant;
    the second is refused once the first is saved.
  * **A retried sale returns the original.** If the till's connection drops after a sale is saved, pressing
    Complete sale again shows the receipt of the sale already recorded instead of creating a second one
    (remembered for 24 hours).
* **Full audit trail.** Every request and response is kept in *MPESA Transaction Log* and *POS Gateway
  Transaction* (passwords are masked).

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "Safaricom refused the Consumer Key or Consumer Secret" | Wrong keys, or sandbox keys with Live selected | Re-enter keys; check the environment toggle |
| Prompt arrives but the till keeps waiting | Callback cannot reach the server | Check the HTTPS address in section 2. The till also asks Safaricom directly after 15 seconds, so it will still resolve |
| "The customer has another M-Pesa payment in progress" | Customer had a prompt open | Wait a minute and resend |
| PayPal: "No exchange rate from KES to USD" | Missing Currency Exchange record | Add one under Accounting > Currency Exchange |
| Pesapal: "needs the customer's phone number or email" | Neither entered | Enter one at the till |

## 10. Technical reference (for developers)

| File | Purpose |
| --- | --- |
| `api/payment_gateway_common.py` | Tenant checks, phone normalisation, callback URL building |
| `api/mpesa_client.py`, `api/mpesa_api.py` | Daraja client and M-Pesa endpoints |
| `api/pesapal_client.py`, `api/paypal_client.py` | Pesapal v3 and PayPal Orders v2 clients |
| `api/payment_gateway_api.py` | One API for the settings screen and the till; sale validation |
| `api/payment_callbacks.py` | Public callback endpoints (names avoid words Daraja rejects) |
| `api/test_payment_gateways.py` | Database-free tests (run with `bench run-tests` or plain `unittest`) |
