"""
Units by industry: the curated lists, and the rules that need no database (ticket P7-2).

A unit is an ERPNext "UOM" record. Each kind of shop has a short list of the units it really counts in, in plain
words ("tablet", "strip", "tonne"), with a note where a conversion matters. This file holds those lists and the
pure rules around them: which kind of shop a business is, which units already exist on the site (so none is added
twice), what a custom unit may be called, and how the shop's own units are kept as text.

Kept free of Frappe imports so it can be tested on its own (see test_unit_sets.py).
"""

from __future__ import annotations

import json

DEFAULT_INDUSTRY = "retail"

# A unit as the till reads it: the stored UOM name, the plain word for one and for many, whether it may only be sold
# whole (a tablet, a piece) or in parts (a kilogram), an optional conversion note, and other names the same unit
# often goes by on a site (so "Piece" is never added beside an existing "Nos").


def _unit(name, singular, plural, whole=True, note="", aliases=()):
	return {
		"name": name,
		"singular": singular,
		"plural": plural,
		"whole": bool(whole),
		"note": note,
		"aliases": tuple(aliases),
	}


PIECE = _unit("Nos", "piece", "pieces", aliases=("Piece", "Pieces", "Pcs", "Pc", "Each"))
KILOGRAM = _unit("Kg", "kilogram", "kilograms", whole=False, aliases=("Kilogram", "Kilograms", "KG", "Kgs"))
GRAM = _unit("Gram", "gram", "grams", whole=False, aliases=("g", "Grams", "Gm"))
LITRE = _unit("Litre", "litre", "litres", whole=False, aliases=("Ltr", "Liter", "L", "Litres", "Liters"))
MILLILITRE = _unit("Millilitre", "millilitre", "millilitres", whole=False, aliases=("ml", "Milliliter", "Ml"))
METRE = _unit("Meter", "metre", "metres", whole=False, aliases=("Metre", "m", "Meters", "Metres"))
TONNE = _unit(
	"Tonne",
	"tonne",
	"tonnes",
	whole=False,
	note="One tonne is 1,000 kilograms.",
	aliases=("Ton", "Tonnes", "Tons", "Metric Ton"),
)
BOX = _unit("Box", "box", "boxes", aliases=("Boxes",))
PACK = _unit("Pack", "pack", "packs", aliases=("Packs",))
PACKET = _unit("Packet", "packet", "packets", aliases=("Pkt", "Packets"))
BOTTLE = _unit("Bottle", "bottle", "bottles", aliases=("Bottles",))
CAN = _unit("Can", "can", "cans", aliases=("Cans",))
TUBE = _unit("Tube", "tube", "tubes", aliases=("Tubes",))
SACHET = _unit("Sachet", "sachet", "sachets", aliases=("Sachets",))
BAG = _unit(
	"Bag", "bag", "bags", note="Bag sizes differ: keep the weight in the product name.", aliases=("Bags",)
)
SACK = _unit(
	"Sack",
	"sack",
	"sacks",
	note="Sack sizes differ: keep the weight in the product name.",
	aliases=("Sacks",),
)
CRATE = _unit(
	"Crate",
	"crate",
	"crates",
	note="A crate usually holds 12 or 24 bottles: check yours.",
	aliases=("Crates",),
)
CARTON = _unit(
	"Carton",
	"carton",
	"cartons",
	note="Pieces per carton differ: keep the count in the product name.",
	aliases=("Ctn", "Cartons"),
)
DOZEN = _unit("Dozen", "dozen", "dozens", note="One dozen is 12 pieces.", aliases=("Dozens", "Doz"))
PAIR = _unit("Pair", "pair", "pairs", aliases=("Pairs",))
SET = _unit("Set", "set", "sets", aliases=("Sets",))
ROLL = _unit("Roll", "roll", "rolls", aliases=("Rolls",))
SHEET = _unit("Sheet", "sheet", "sheets", aliases=("Sheets",))
BUNDLE = _unit("Bundle", "bundle", "bundles", aliases=("Bundles",))
BUNCH = _unit("Bunch", "bunch", "bunches", aliases=("Bunches",))
TRAY = _unit("Tray", "tray", "trays", aliases=("Trays",))
BALE = _unit("Bale", "bale", "bales", aliases=("Bales",))
PALLET = _unit("Pallet", "pallet", "pallets", aliases=("Pallets",))
PLATE = _unit("Plate", "plate", "plates", aliases=("Plates",))
PORTION = _unit("Portion", "portion", "portions", aliases=("Portions", "Serving", "Servings"))
CUP = _unit("Cup", "cup", "cups", aliases=("Cups",))
GLASS = _unit("Glass", "glass", "glasses", aliases=("Glasses",))
TOT = _unit(
	"Tot",
	"tot",
	"tots",
	note="A tot is a small bar measure, often 25 or 50 millilitres: check yours.",
	aliases=("Tots",),
)
TABLET = _unit("Tablet", "tablet", "tablets", aliases=("Tablets", "Tab", "Tabs"))
CAPSULE = _unit("Capsule", "capsule", "capsules", aliases=("Capsules", "Cap", "Caps"))
STRIP = _unit(
	"Strip",
	"strip",
	"strips",
	note="A strip usually holds 10 tablets or capsules: check the pack.",
	aliases=("Strips",),
)
VIAL = _unit("Vial", "vial", "vials", aliases=("Vials",))
CARCASS = _unit(
	"Carcass",
	"carcass",
	"carcasses",
	note="A whole animal. Sell it by the carcass, or weigh it and sell by the kilogram.",
	aliases=("Carcasses",),
)
SESSION = _unit("Session", "session", "sessions", aliases=("Sessions",))
HOUR = _unit("Hour", "hour", "hours", whole=False, aliases=("Hours", "Hr", "Hrs"))
DAY = _unit("Day", "day", "days", aliases=("Days",))
NIGHT = _unit(
	"Night",
	"night",
	"nights",
	note="One night is one stay from check-in to the next day.",
	aliases=("Nights",),
)
PERSON = _unit("Person", "person", "people", aliases=("People", "Persons"))

# Industry key -> the units to put first, in this order. Plain words below are the picker's labels in the web app.
INDUSTRY_UNITS = {
	"pharmacy": (PIECE, TABLET, CAPSULE, STRIP, BOTTLE, VIAL, SACHET, TUBE, BOX, MILLILITRE),
	"restaurant": (PLATE, PORTION, CUP, GLASS, TOT, BOTTLE, CAN, CRATE, LITRE, KILOGRAM, PIECE),
	"hotel": (NIGHT, DAY, HOUR, PERSON, PLATE, BOTTLE, SET, PIECE),
	"butchery_groceries": (KILOGRAM, GRAM, PORTION, CARCASS, PIECE, PACKET, TRAY, DOZEN, LITRE, BUNCH, SACK),
	"hardware": (PIECE, METRE, BAG, TONNE, ROLL, LITRE, KILOGRAM, SHEET, BUNDLE, BOX, PAIR),
	"services": (SESSION, HOUR, PIECE, SET, BOTTLE, SACHET, TUBE, DAY),
	"wholesale": (CARTON, BOX, BAG, SACK, DOZEN, CRATE, PALLET, TONNE, KILOGRAM, BUNDLE, PIECE, PACK),
	"retail": (PIECE, PACK, PACKET, BOX, BOTTLE, PAIR, SET, DOZEN, KILOGRAM, LITRE, METRE),
	"agriculture": (KILOGRAM, BAG, SACK, TONNE, LITRE, BUNCH, CRATE, TRAY, PACKET, BALE, PIECE),
}

INDUSTRIES = tuple(INDUSTRY_UNITS)

# What the sign-up list (POS Industry codes) says about the kind of shop. Anything not here is a general shop.
_INDUSTRY_BY_SIGNUP = {
	"PHARMACY": "pharmacy",
	"REST": "restaurant",
	"HOTEL": "hotel",
	"GROCERY": "butchery_groceries",
	"SALON": "services",
	"AUTO": "hardware",
	"FURNITURE": "hardware",
}

# What "What do you sell?" says (pharmacy, retail, services, other)
_INDUSTRY_BY_KIND = {
	"pharmacy": "pharmacy",
	"services": "services",
	"retail": "retail",
	"other": "retail",
}


def clean_industry(value) -> str | None:
	"""One of the industry keys however it was typed (case and spaces ignored); None for anything else."""
	text = str(value if value is not None else "").strip().lower()
	return text if text in INDUSTRY_UNITS else None


def industry_for(chosen=None, signup_code=None, kind=None) -> str:
	"""The kind of shop to use for units: what the owner chose, else the sign-up industry, else the business kind."""
	return (
		clean_industry(chosen)
		or _INDUSTRY_BY_SIGNUP.get(str(signup_code or "").strip().upper())
		or _INDUSTRY_BY_KIND.get(str(kind or "").strip().lower())
		or DEFAULT_INDUSTRY
	)


def units_for(industry) -> tuple:
	"""The curated units of an industry (an unknown industry gets the general shop list)."""
	return INDUSTRY_UNITS[clean_industry(industry) or DEFAULT_INDUSTRY]


def all_known_words() -> set:
	"""Every name and plain word of every curated unit, lower case: a custom unit may not repeat any of them."""
	words = set()
	for units in INDUSTRY_UNITS.values():
		for unit in units:
			words.update(
				w.lower() for w in (unit["name"], unit["singular"], unit["plural"], *unit["aliases"])
			)
	return words


# --- matching what the site already has ---------------------------------------------------------------------


def find_existing(unit, existing_lower: dict) -> str | None:
	"""
	The name the site already uses for this unit, or None. `existing_lower` maps a lower case UOM name to its real
	name. The stored name is tried first, then the plain words and the usual other names; case is ignored.
	"""
	for candidate in (unit["name"], unit["singular"], unit["plural"], *unit["aliases"]):
		found = existing_lower.get(str(candidate).strip().lower())
		if found:
			return found
	return None


def plan_units(industry, existing_names) -> list[dict]:
	"""
	What to do for each unit of an industry: `use` the one the site has, or `create` it. Two units of the list that
	resolve to the same existing name are both `use`; nothing is ever renamed, changed or removed.
	"""
	existing_lower = {str(n).strip().lower(): n for n in existing_names or [] if str(n or "").strip()}
	plan = []
	for unit in units_for(industry):
		found = find_existing(unit, existing_lower)
		plan.append({"unit": unit, "stored": found or unit["name"], "create": found is None})
	return plan


# --- the shop's own units ---------------------------------------------------------------------------------

MIN_LENGTH = 2
MAX_LENGTH = 30
MAX_CUSTOM_UNITS = 50
_EXTRA_CHARACTERS = " -."


def tidy(text) -> str:
	"""Spaces at the ends removed and runs of spaces made one."""
	return " ".join(str(text if text is not None else "").split())


def check_word(text) -> str | None:
	"""Why a unit name or plural word is not allowed, as a short code, or None when it is fine."""
	text = tidy(text)
	if not text:
		return "empty"
	if len(text) < MIN_LENGTH:
		return "short"
	if len(text) > MAX_LENGTH:
		return "long"
	if not all(ch.isalnum() or ch in _EXTRA_CHARACTERS for ch in text):
		return "characters"
	if not any(ch.isalpha() for ch in text):
		return "characters"
	return None


def stored_name(text) -> str:
	"""How a custom unit is stored: tidied, first letter a capital, the rest as typed."""
	text = tidy(text)
	return text[:1].upper() + text[1:]


def is_duplicate(name, existing_names, custom=(), extra_words=()) -> bool:
	"""Whether a unit name repeats one already there, ignoring case and spaces at the ends."""
	key = tidy(name).lower()
	taken = {tidy(n).lower() for n in existing_names or []}
	taken.update(all_known_words())
	taken.update(tidy(c.get("name")).lower() for c in custom or [])
	taken.update(tidy(c.get("plural")).lower() for c in custom or [])
	taken.update(tidy(w).lower() for w in extra_words or [])
	return key in taken


def parse_custom(text) -> list[dict]:
	"""The shop's own units as they were saved: a list of { name, plural }. Anything unreadable is an empty list."""
	if not text:
		return []
	try:
		data = json.loads(text)
	except (TypeError, ValueError):
		return []
	if not isinstance(data, list):
		return []
	out = []
	seen = set()
	for row in data:
		if not isinstance(row, dict):
			continue
		name = tidy(row.get("name"))
		if check_word(name) or name.lower() in seen:
			continue
		plural = tidy(row.get("plural"))
		seen.add(name.lower())
		out.append({"name": name, "plural": plural if not check_word(plural) else name})
	return out[:MAX_CUSTOM_UNITS]


def dump_custom(rows) -> str:
	return json.dumps(
		[{"name": r["name"], "plural": r["plural"]} for r in rows], separators=(",", ":"), ensure_ascii=False
	)


def unit_row(plan_item: dict, exists: bool) -> dict:
	"""One curated unit as the web app reads it."""
	unit = plan_item["unit"]
	return {
		"name": plan_item["stored"],
		"singular": unit["singular"],
		"plural": unit["plural"],
		"whole": unit["whole"],
		"note": unit["note"],
		"exists": exists,
	}
