"""
Rules for starter products that do not need the database: which starter items are services, which units
they are sold in, and what the till is told about each one.

Kept free of Frappe imports so it can be tested on its own (see test_seed_rules.py).
"""

from __future__ import annotations

SERVICES_GROUP = "services"

# Units that can be sold in parts: half a kilo, 2.5 litres. Anything else is sold in whole pieces.
MEASURE_UNITS = {
	"kg",
	"g",
	"gram",
	"l",
	"ltr",
	"litre",
	"liter",
	"ml",
	"millilitre",
	"milliliter",
	"m",
	"meter",
	"metre",
	"cm",
}

# A starter list says "L" or "ml"; a site may already hold the same unit under another name
UOM_ALIASES = {
	"l": ["L", "Litre", "Liter", "Ltr"],
	"ltr": ["Ltr", "Litre", "Liter", "L"],
	"litre": ["Litre", "Liter", "Ltr", "L"],
	"ml": ["ml", "Millilitre", "Milliliter", "Ml"],
	"g": ["g", "Gram", "Grams"],
	"gram": ["Gram", "Grams", "g"],
	"kg": ["Kg", "Kilogram", "KG"],
	"m": ["Meter", "Metre", "m"],
}


def is_service_group(item_group) -> bool:
	"""Starter items in the "Services" group are things done for the customer (a haircut, a car wash)."""
	return str(item_group or "").strip().lower() == SERVICES_GROUP


def starter_product(template) -> dict:
	"""One starter product as the till reads it. The unit and the service flag are what it needs to price and stock it."""
	group = template.get("item_group") or ""
	return {
		"sku": template.get("item_code"),
		"name": template.get("item_name"),
		"status": "available",
		"item_group": group,
		"uom": (template.get("uom") or "").strip() or "Nos",
		"is_service": is_service_group(group),
	}


def stock_flag(requested, template_group=None) -> int:
	"""
	Whether a new starter item keeps stock: 1 or 0.

	What the till asked for wins. When it said nothing (an older till), a starter item that is a service
	keeps no stock, and everything else does.
	"""
	if requested is not None and str(requested).strip() != "":
		return 0 if str(requested).strip().lower() in ("0", "false", "no") else 1
	return 0 if is_service_group(template_group) else 1


def unit_candidates(requested) -> list[str]:
	"""The unit names to look for on the site, best match first."""
	name = str(requested or "").strip()
	if not name:
		return ["Nos"]
	names = [name]
	for alias in UOM_ALIASES.get(name.lower(), []):
		if alias not in names:
			names.append(alias)
	return names


def must_be_whole_number(uom_name) -> int:
	"""1 when the unit is sold in whole pieces only, 0 when it can be sold in parts."""
	return 0 if str(uom_name or "").strip().lower() in MEASURE_UNITS else 1


def resolve_uom(requested, exists, create) -> str:
	"""
	The name of a unit that exists on the site for what a starter list asks for.

	`exists(name)` says whether the site has a unit of that name. When none of the likely names is there,
	`create(name, whole_numbers_only)` makes it and returns its name, so the product is sold in the unit
	it should be rather than in pieces.
	"""
	for name in unit_candidates(requested):
		if exists(name):
			return name
	name = unit_candidates(requested)[0]
	return create(name, must_be_whole_number(name))
