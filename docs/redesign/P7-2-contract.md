# P7-2 contract: units by industry

New module `murzak_pos/api/units_api.py` (rules and lists in `unit_sets.py`, no database needed). No existing call, record or setting changes.
All calls answer `{ "success": true, "data": {...} }`. Methods are `murzak_pos.api.units_api.<name>`.

## Industries

Keys: `pharmacy`, `restaurant`, `hotel`, `butchery_groceries`, `hardware`, `services`, `wholesale`, `retail`, `agriculture`.
The shop's industry for units is, in order: the owner's choice (`set_units_industry`), the sign-up industry code (REST to restaurant, HOTEL to hotel, GROCERY to butchery_groceries, PHARMACY to pharmacy, SALON to services, AUTO and FURNITURE to hardware), the answer to "What do you sell?" (pharmacy, services, retail, other to retail), else `retail`.

## get_industry_units (GET)

Request: `industry` (optional key, else the shop's), `company` (optional).
Response data: `company`, `industry`, `industries` (all keys), `units` (in order: `name` the stored UOM name actually on the site, or the name to be created; `singular`, `plural`, `whole` true when it cannot be fractional, `note` conversion note or empty, `exists`), `missing` (count not yet on the site), `custom` (the shop's own units, `{name, plural}`), `created` (empty here), `can_manage`.
Anyone signed in. Changes nothing. Errors: not signed in; unknown industry (ValidationError).

## ensure_industry_units (POST)

Request: `industry` (optional), `company` (optional). Owners and managers only (PermissionError for a cashier).
Creates only the UOMs the site lacks. Matching ignores case and counts the usual other names (Piece is Nos, Metre is Meter, Ltr is Litre), so nothing is added twice. Existing UOMs are never renamed, changed or removed. `must_be_whole_number` is set on created UOMs only (0 for kilogram, gram, litre, millilitre, metre, tonne, hour; 1 for the rest). Idempotent: a second call returns `created: []`. A duplicate raised by a concurrent call is treated as done.
Response: same as get, with `created` the names added.

## set_units_industry (POST)

`industry` (required key). Owners and managers. Kept in DefaultValue key `murzak_units_industry:<company>`. Adds and removes no unit. Response as get.

## add_custom_unit (POST)

`name`, `plural`, `allow_parts` (0 or 1, default 0 means whole numbers only), `company`. Owners and managers.
Validation (ValidationError, plain message): 2 to 30 characters after tidying; letters, numbers, spaces, hyphens, full stops only; at least one letter; the name may not repeat (ignoring case) any UOM on the site, any curated unit name or plain word, or any custom unit name or plural; a plural that differs from the name is checked the same way; at most 50 custom units per shop.
Creates a UOM (first letter capitalised) and keeps `{name, plural}` in DefaultValue key `murzak_units_custom:<company>`. Response as get plus `added` (the stored name).

## Assumptions not verifiable here

UOM fields `uom_name` and `must_be_whole_number` (already used by `product_seeding._make_uom`); `frappe.get_all("UOM", pluck="name")` returns every UOM; the setup kind is read through `setup_api._read_kind`, and a failure there is treated as no kind. Unit words (singular and plural) come from the curated list and are English only; the stored UOM names are not translated.
