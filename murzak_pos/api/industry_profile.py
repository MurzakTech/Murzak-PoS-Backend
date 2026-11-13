"""
The industry profile: how the till behaves and speaks for each kind of shop (the "personality" of the POS).

A profile says three things about an outlet (a store, which ERPNext calls a warehouse):

- which industry features are switched on (prescription checks and the dispensing register for a pharmacy; tables,
  kitchen tickets, approval of voids and the waiter cash-up for a restaurant or bar);
- which words the till uses for its usual things (a pharmacy dispenses to a patient, a restaurant settles a bill);
- the look it starts with (an accent colour and a light or dark theme). The owner's own brand colour always wins.

Each outlet can have its own industry, so one business can run a restaurant till and a pharmacy counter side by side.
An outlet with no industry of its own takes the business's: the shop type chosen at sign-up, else the answer to
"What do you sell?". Anything else is a general shop, which behaves exactly as the till does today.

Kept free of Frappe imports so it can be tested on its own. The calls that read and write are in industry_profile_api.py.
"""

from __future__ import annotations

import copy

PHARMACY = "pharmacy"
RESTAURANT = "restaurant"
GENERAL = "general"
KEYS = (PHARMACY, RESTAURANT, GENERAL)

# Where the industry of an outlet came from, most specific first
FROM_OUTLET = "outlet"
FROM_SHOP_TYPE = "shop_type"
FROM_BUSINESS_KIND = "business_kind"
FROM_DEFAULT = "default"

# The features, by their stable names. The browser shows or hides a thing by one of these names.
FEATURE_NAMES = (
	# Pharmacy
	"prescription_only",  # the Rx flag on products, and the patient and prescriber asked at the sale
	"expiry",  # expiry dates and the Expiry tab
	"dispensing_register",  # the list of every prescription sale, for inspections
	"antibiotics_prescription_only",  # a new product named after a common antibiotic starts as prescription-only
	"controlled_register",  # the controlled medicines register, with a running balance
	# Restaurant and bar
	"tables",  # bills held by table or tab name
	"kitchen_tickets",  # orders sent to the kitchen and bar
	"void_approval",  # removing an item the kitchen already has needs a manager and a reason
	"waiter_on_sale",  # each bill is credited to a waiter
	"waiter_cashup",  # the end-of-shift cash-up by waiter
	"tourism_levy",  # the monthly 2% tourism levy report
)

_FEATURES_ON = {
	PHARMACY: (
		"prescription_only",
		"expiry",
		"dispensing_register",
		"antibiotics_prescription_only",
		"controlled_register",
	),
	RESTAURANT: (
		"tables",
		"kitchen_tickets",
		"void_approval",
		"waiter_on_sale",
		"waiter_cashup",
		"tourism_levy",
	),
	GENERAL: (),
}

# The till's usual words, by stable key. A general shop keeps the words it uses today.
WORD_KEYS = (
	"sale",
	"sales",
	"customer",
	"product",
	"products",
	"held_sale",
	"held_sales",
	"charge",
	"notes",
	"cashier",
	"shift_report",
	"write_off",
)

_GENERAL_WORDS = {
	"sale": "Sale",
	"sales": "Sales",
	"customer": "Customer",
	"product": "Product",
	"products": "Products",
	"held_sale": "Held sale",
	"held_sales": "Held sales",
	"charge": "Charge",
	"notes": "Notes",
	"cashier": "Cashier",
	"shift_report": "Shift report",
	"write_off": "Stock adjustment",
}

_WORDS = {
	GENERAL: _GENERAL_WORDS,
	PHARMACY: {
		**_GENERAL_WORDS,
		"customer": "Patient",
		"product": "Medicine",
		"products": "Medicines",
		"held_sale": "Prescription waiting",
		"held_sales": "Prescriptions waiting",
		"charge": "Dispense and pay",
		"notes": "Directions",
		"cashier": "Dispenser",
		"shift_report": "End-of-day dispensing summary",
		"write_off": "Expired or damaged",
	},
	RESTAURANT: {
		**_GENERAL_WORDS,
		"sale": "Bill",
		"sales": "Bills",
		"customer": "Guest",
		"product": "Menu item",
		"products": "Menu",
		"held_sale": "Open table",
		"held_sales": "Open tables and tabs",
		"charge": "Settle bill",
		"notes": "Special requests",
		"cashier": "Waiter",
		"shift_report": "Waiter cash-up",
		"write_off": "Wastage or breakage",
	},
}

# The look an outlet starts with. The accent is one of the brand accents (brand_api.ACCENTS).
_LOOK = {
	GENERAL: {"accent": "indigo", "theme": "light"},
	PHARMACY: {"accent": "green", "theme": "light"},
	RESTAURANT: {"accent": "amber", "theme": "dark"},
}

# What the home screen leads with, in order. Card names the browser knows; one it does not know is skipped.
_HOME_CARDS = {
	GENERAL: (),
	PHARMACY: ("expiring_soon", "prescriptions_today", "reorder"),
	RESTAURANT: ("open_tables", "sales_by_waiter", "voids_today"),
}

_LABELS = {GENERAL: "General shop", PHARMACY: "Pharmacy", RESTAURANT: "Restaurant and bar"}

# What a POS Industry code (the sign-up list and the outlet setting) means here. Anything else is a general shop.
_KEY_BY_INDUSTRY = {
	"PHARMACY": PHARMACY,
	"REST": RESTAURANT,
}

# What the answer to "What do you sell?" (setup_calc.KINDS) means here
_KEY_BY_KIND = {
	"pharmacy": PHARMACY,
}


def key_for_industry(code) -> str | None:
	"""The profile key a POS Industry code stands for, or None when it has no profile of its own."""
	return _KEY_BY_INDUSTRY.get(str(code or "").strip().upper())


def key_for_kind(kind) -> str | None:
	"""The profile key a business kind stands for, or None."""
	return _KEY_BY_KIND.get(str(kind or "").strip().lower())


def resolve(outlet_industry=None, shop_type=None, business_kind=None) -> tuple[str, str]:
	"""
	The profile key for an outlet and where it came from: the outlet's own industry, else the business's shop type,
	else its answer to "What do you sell?", else a general shop.

	An outlet set to an industry with no profile of its own (say Retail) is a general shop on purpose: the owner chose
	it, so the business's pharmacy shop type does not leak into it.
	"""
	if str(outlet_industry or "").strip():
		return key_for_industry(outlet_industry) or GENERAL, FROM_OUTLET
	from_shop_type = key_for_industry(shop_type)
	if from_shop_type:
		return from_shop_type, FROM_SHOP_TYPE
	from_kind = key_for_kind(business_kind)
	if from_kind:
		return from_kind, FROM_BUSINESS_KIND
	return GENERAL, FROM_DEFAULT


def clean_key(value) -> str | None:
	"""One of the profile keys however it was typed; None for anything else."""
	text = str(value if value is not None else "").strip().lower()
	return text if text in KEYS else None


def features_for(key) -> dict:
	"""Every feature name mapped to on or off. An unknown key is a general shop: everything off."""
	on = _FEATURES_ON.get(clean_key(key) or GENERAL, ())
	return {name: name in on for name in FEATURE_NAMES}


def profile(key) -> dict:
	"""The whole profile of a key, as the browser receives it. An unknown key is a general shop."""
	clean = clean_key(key) or GENERAL
	return {
		"key": clean,
		"label": _LABELS[clean],
		"features": features_for(clean),
		"words": dict(_WORDS[clean]),
		"look": copy.deepcopy(_LOOK[clean]),
		"home_cards": list(_HOME_CARDS[clean]),
	}


def any_has(keys, feature: str) -> bool:
	"""Whether any of these profile keys switches the feature on (a business whose outlets differ)."""
	return any(features_for(key).get(feature) for key in keys)
