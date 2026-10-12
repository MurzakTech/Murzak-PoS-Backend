# Print formats (WS8.3)

Four print formats ship with the app, and each business has its own letter head. The calls are in
`murzak_pos/api/print_api.py`; the templates are in `murzak_pos/templates/print_formats/`.

## The formats

| Alias | Print format | For | Notes |
| --- | --- | --- | --- |
| `invoice` | Murzak Tax Invoice | Sales Invoice | A4. Shows the customer, VAT, totals and, when the sale went to KRA, the eTIMS block with its QR code. A return prints as a **Credit Note**. |
| `receipt` | Murzak Receipt 80mm | Sales Invoice, POS Invoice | 72 mm wide, for the till printer. Prints its own compact header, the payments, change and, for an eTIMS sale, the SCU ID, receipt number, signature and QR code. |
| `delivery_note` | Murzak Delivery Note | Delivery Note | A4, no prices, with signature lines for the driver and the person receiving. |
| `purchase_order` | Murzak Purchase Order | Purchase Order | A4, with totals and signature lines. |

The eTIMS QR code is drawn from the receipt link that the Kenya tax app stores on the invoice, so it is only on a sale
that was accepted by KRA.

## Calls

| Call | Who | What it does |
| --- | --- | --- |
| `get_print(doctype, name, format?, as_pdf?)` | any member of the business | The document as HTML (`data.html`) or, with `as_pdf=1`, a PDF as base64 text (`data.pdf_base64`). With no `format` an invoice is a tax invoice, a POS invoice a receipt, and the other documents their own format. `format` may also be the name of any other print format on the site. |
| `save_letterhead(company?, header_html?, footer_html?, logo_url?)` | System Manager, Accounts Manager | Sets the business's letter head. With no `header_html` one is built from the business's name, PIN and contact details. |

## Letter head per business

Each business has its own Letter Head record, "<Company> Letter Head", set as the company's default. It is used on the
A4 documents. A business without one prints with none. The till receipt never uses it.

Frappe makes the first letter head on a site the default for everyone. To stop one business's header appearing on
another's documents, the app first creates an empty letter head, "No Letter Head", as the site default.

## Installing and changing a format

The formats are written to the site's Print Format records after every update (`after_migrate`), so a change to a
template reaches every site when it is next updated. Edit the template files, not the records on the site: an edit made
in the Frappe desk is replaced at the next update. To make a different layout, copy a format in the desk and give it
another name.

## Known limits

* The PDF is made by the server's wkhtmltopdf. If it cannot be made, the call says so; ask for the HTML and print from the
  browser. (The test server used for this work has an unpatched wkhtmltopdf, which fails on Frappe's own formats too, so
  the PDF itself should be checked once on the live server.)
* Printing straight to a thermal printer is the frontend's job; this gives it the 72 mm HTML.
