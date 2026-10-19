# P7-3 contract: the shop's brand (`murzak_pos.api.brand_api`)

Two new calls. Nothing existing is changed. The brand is kept per business, as one JSON value in Frappe's `DefaultValue` table under the key `murzak_brand:<company>` (a long company name is shortened to a SHA-1 fingerprint so the key fits 140 characters), the same way the New look switch is kept. No new document type, no custom field, no migration. No secret is stored: the logo is only the address of a picture uploaded through the standard public file upload.

## `get_brand(company=None)` (GET)

Anyone signed in may read the brand of their own business (every cashier prints receipts). A business that never chose has the defaults.

Reply:

```
{ "success": true, "data": {
    "company": "Demo Shop",
    "logo": "",            // "" or "/files/<name>.jpg|jpeg|png|webp"
    "accent": "indigo",    // one of: indigo blue teal green amber red pink purple slate
    "tagline": "",         // up to 80 characters
    "footer": "",          // up to 160 characters, printed under the totals
    "style": "thermal80",  // thermal80, thermal58 or a4
    "social": "",          // "" or "@handle" (letters, numbers, dot, underscore, up to 30)
    "website": ""          // "" or "www.example.co.ke" (no http://)
} }
```

A stored value that is damaged, or that no longer passes the checks, falls back to the default for that field only.

Errors: signed out gives `AuthenticationError`; a business the caller does not belong to gives `PermissionError`.

## `set_brand(brand, company=None)` (POST)

Owners only (`require_manager`, checked before anything else): a System Manager, or the Administrator. `brand` is a JSON object (or a JSON string). Only the fields sent are changed; the others keep their saved value, so saving the same body twice gives the same result. Reply is the same shape as `get_brand`, with the saved values.

Checks, each refused with `ValidationError` and a plain sentence (all with Swahili rows in `sw.csv`):

- `accent` must be one of the listed names (case does not matter). The colours themselves are not stored; they live in the front end's design tokens, so a colour outside the palette can never be saved.
- `style` must be one of `thermal80`, `thermal58`, `a4`.
- `logo` must be `""` (clears it) or a public upload `/files/<name>` ending in jpg, jpeg, png or webp, at most 200 characters, with no `..`. It must exist as a `File` record with that `file_url` (assumption: `File.file_url` holds `/files/<name>` for a public upload, as the standard upload returns). The 1 MB limit is enforced where the file is uploaded (the browser checks before sending); the server does not read the picture.
- `tagline` up to 80 and `footer` up to 160 characters, control characters removed, spaces tidied, no `<` or `>`.
- `social` is made `@handle`; `website` has `http://` or `https://` and a closing `/` removed and must look like a domain with an optional path.
- A field that is not in the list above is refused.

Errors: signed out `AuthenticationError`; not an owner `PermissionError`; another business `PermissionError`.

## Assumptions not verified (no database here)

- `frappe.db.get_default` / `set_default` behave as they do for the New look switch (`DefaultValue`, `defvalue` is a Text field, so the JSON of about 700 characters fits).
- The receipt also needs the business name, address, phone and KRA PIN. The front end reads those from the existing `onboarding_api.get_company`, which any signed-in user may call today; this contract adds nothing for them.
- There is no tax breakdown or eTIMS reference in `sales_list_api.get_sale` today, so the receipt shows the single tax total and no eTIMS line. Nothing was added to that call.
