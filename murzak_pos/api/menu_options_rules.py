"""
Menu options for restaurants and bars, the rules that need no database: how a dish's option groups are kept, and what
a waiter's choice adds to the bill and says on the kitchen ticket.

A dish can have a few groups of options:

- "How cooked" (choose one, required): Rare, Medium, Well done
- "Extras" (choose several, optional): Extra cheese +50, Bacon +80
- "Leave out" (choose several, optional): No onions, No chilli

A choice with a price adds to the dish's price. The chosen options are printed on the kitchen ticket and written on the
invoice line, so the kitchen, the guest's receipt and the tax invoice all say the same thing.

Kept free of Frappe imports so it can be tested on its own. The calls are in menu_options_api.py.
"""

from __future__ import annotations

import json
import re

MAX_GROUPS = 8
MAX_CHOICES = 15
MAX_NAME = 40
MAX_PRICE = 1_000_000

# "Extra cheese +50" or "Extra cheese + 50" or "Bacon +80.5"
_PRICED = re.compile(r"^(?P<name>.*?)\s*\+\s*(?P<price>\d+(?:\.\d{1,2})?)\s*$")


class OptionsError(ValueError):
	"""The options cannot be kept as given; the message says why in plain words."""


def _name(value) -> str:
	return " ".join(str(value or "").split())[:MAX_NAME]


def _price(value) -> float:
	try:
		price = round(float(value or 0), 2)
	except (TypeError, ValueError):
		raise OptionsError("A price must be a number.") from None
	if price < 0 or price > MAX_PRICE:
		raise OptionsError("A price must be between 0 and 1,000,000.")
	return price


def parse_choice(text) -> dict | None:
	"""One choice as an owner types it: "No onions" (free) or "Extra cheese +50". None for an empty entry."""
	raw = str(text or "").strip()
	if not raw:
		return None
	m = _PRICED.match(raw)
	if m:
		name, price = _name(m.group("name")), _price(m.group("price"))
	else:
		name, price = _name(raw), 0.0
	return {"name": name, "price": price} if name else None


def clean_groups(raw) -> list[dict]:
	"""
	A dish's option groups, checked and tidied. Each group is {name, required, multiple, choices: [{name, price}]}.
	May arrive as a list or as JSON text; an empty value means the dish has no options. Raises OptionsError in plain
	words when something cannot be kept.
	"""
	if raw in (None, "", "[]"):
		return []
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			raise OptionsError("The options could not be read.") from None
	if not isinstance(raw, list):
		raise OptionsError("The options could not be read.")
	groups, seen = [], set()
	for group in raw:
		if not isinstance(group, dict):
			raise OptionsError("The options could not be read.")
		name = _name(group.get("name"))
		if not name:
			raise OptionsError("Every group of options needs a name, such as How cooked or Extras.")
		if name.lower() in seen:
			raise OptionsError(f"There are two groups called {name}.")
		seen.add(name.lower())
		choices, names = [], set()
		for choice in group.get("choices") or []:
			parsed = parse_choice(choice) if isinstance(choice, str) else None
			if isinstance(choice, dict):
				parsed = {"name": _name(choice.get("name")), "price": _price(choice.get("price"))}
				parsed = parsed if parsed["name"] else None
			if not parsed:
				continue
			if parsed["name"].lower() in names:
				raise OptionsError(f"{parsed['name']} is listed twice in {name}.")
			names.add(parsed["name"].lower())
			choices.append(parsed)
		if not choices:
			raise OptionsError(f"Add at least one choice to {name}.")
		if len(choices) > MAX_CHOICES:
			raise OptionsError(f"{name} has too many choices. Keep it to {MAX_CHOICES}.")
		groups.append(
			{
				"name": name,
				"required": bool(group.get("required")),
				"multiple": bool(group.get("multiple")),
				"choices": choices,
			}
		)
	if len(groups) > MAX_GROUPS:
		raise OptionsError(f"A dish can have at most {MAX_GROUPS} groups of options.")
	return groups


def dump_groups(groups: list[dict]) -> str:
	"""The groups as kept on the product (JSON text), or empty for none."""
	return json.dumps(groups, separators=(",", ":")) if groups else ""


def load_groups(text) -> list[dict]:
	"""The groups kept on a product; anything unreadable is treated as no options (never an error at the till)."""
	try:
		return clean_groups(text)
	except OptionsError:
		return []


def choose(groups: list[dict], picked: dict) -> tuple[list[dict], float, str]:
	"""
	A waiter's choice checked against the dish's groups: (the options chosen, the price they add, the words for the
	ticket and invoice). `picked` is {group name: [choice names]}. Raises OptionsError when a required group has no
	choice, a choose-one group has several, or a choice is not on the menu.
	"""
	chosen, extra, words = [], 0.0, []
	picked = picked or {}
	for group in groups:
		names = [n for n in picked.get(group["name"], []) if n]
		if group["required"] and not names:
			raise OptionsError(f"Choose {group['name']}.")
		if not group["multiple"] and len(names) > 1:
			raise OptionsError(f"Choose only one for {group['name']}.")
		on_menu = {c["name"]: c for c in group["choices"]}
		for n in names:
			if n not in on_menu:
				raise OptionsError(f"{n} is not an option for {group['name']}.")
			c = on_menu[n]
			chosen.append({"group": group["name"], "name": c["name"], "price": c["price"]})
			extra += c["price"]
			words.append(f"{c['name']} (+{c['price']:g})" if c["price"] else c["name"])
	return chosen, round(extra, 2), "; ".join(words)
