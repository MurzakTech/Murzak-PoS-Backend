"""
A restaurant or bar's own settings, the rules that need no database: happy hours, the service charge, the delivery
platforms it sells through and the tables of each store.

- Happy hour: on chosen days between two times, a percentage off chosen menu sections or items (for example 20% off
  Beer, Monday to Friday, 17:00 to 19:00). A window may run past midnight (22:00 to 02:00).
- Service charge: a percentage of the bill, added as its own line, so the receipt and the tax invoice show it.
- Delivery platforms: the apps a restaurant takes orders through (Glovo, Uber Eats, Bolt Food) and the commission each
  keeps, so the platform report can say what the restaurant should expect to be paid.
- Tables: the table names of each store, for the table map.

Kept free of Frappe imports so it can be tested on its own. The calls are in restaurant_settings_api.py.
"""

from __future__ import annotations

import json
import re

MAX_RULES = 20
MAX_TABLES = 200
MAX_NAME = 40
MAX_SERVICE_CHARGE = 25.0
MAX_PERCENT = 100.0
DEFAULT_PLATFORMS = ("Glovo", "Uber Eats", "Bolt Food")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SettingsError(ValueError):
	"""The settings cannot be kept as given; the message says why in plain words."""


def _name(value, limit=MAX_NAME) -> str:
	return " ".join(str(value or "").split())[:limit]


def _percent(value, ceiling, what) -> float:
	try:
		number = round(float(value or 0), 2)
	except (TypeError, ValueError):
		raise SettingsError(f"{what} must be a number.") from None
	if number < 0 or number > ceiling:
		raise SettingsError(f"{what} must be between 0 and {ceiling:g} percent.")
	return number


def _names(values, limit) -> list[str]:
	seen, out = set(), []
	for value in values or []:
		name = _name(value, 140)
		if name and name.lower() not in seen:
			seen.add(name.lower())
			out.append(name)
	return out[:limit]


def clean_happy_hours(raw) -> list[dict]:
	rules = []
	for rule in raw or []:
		if not isinstance(rule, dict):
			raise SettingsError("A happy hour could not be read.")
		name = _name(rule.get("name")) or "Happy hour"
		days = [d for d in DAYS if d in {str(x).strip().lower()[:3] for x in rule.get("days") or []}]
		if not days:
			raise SettingsError(f"Choose at least one day for {name}.")
		start, end = str(rule.get("start") or "").strip(), str(rule.get("end") or "").strip()
		if not _TIME.match(start) or not _TIME.match(end) or start == end:
			raise SettingsError(f"Give {name} a start and an end time, such as 17:00 and 19:00.")
		percent = _percent(rule.get("percent"), MAX_PERCENT, f"The discount of {name}")
		if percent <= 0:
			raise SettingsError(f"Give {name} a discount above 0 percent.")
		groups, items = _names(rule.get("groups"), 50), _names(rule.get("items"), 200)
		if not groups and not items:
			raise SettingsError(f"Choose the menu sections or items {name} applies to.")
		rules.append(
			{
				"name": name,
				"days": days,
				"start": start,
				"end": end,
				"percent": percent,
				"groups": groups,
				"items": items,
			}
		)
	if len(rules) > MAX_RULES:
		raise SettingsError(f"Keep happy hours to {MAX_RULES} or fewer.")
	return rules


def clean_platforms(raw) -> list[dict]:
	platforms, seen = [], set()
	for p in raw or []:
		if not isinstance(p, dict):
			raise SettingsError("A delivery platform could not be read.")
		name = _name(p.get("name"))
		if not name or name.lower() in seen:
			continue
		seen.add(name.lower())
		platforms.append(
			{
				"name": name,
				"commission_percent": _percent(
					p.get("commission_percent"), MAX_PERCENT, f"The commission of {name}"
				),
			}
		)
	return platforms[:10]


def clean_tables(raw) -> dict:
	if not isinstance(raw, dict):
		return {}
	out = {}
	for store, names in raw.items():
		store = str(store or "").strip()
		if not store:
			continue
		tables = _names(names, MAX_TABLES)
		if tables:
			out[store] = tables
	return out


def clean_settings(raw) -> dict:
	"""The whole settings, checked and tidied. May arrive as a dict or JSON text. Raises SettingsError in plain words."""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw or "{}")
		except ValueError:
			raise SettingsError("The settings could not be read.") from None
	if not isinstance(raw, dict):
		raise SettingsError("The settings could not be read.")
	return {
		"happy_hours": clean_happy_hours(raw.get("happy_hours")),
		"service_charge_percent": _percent(
			raw.get("service_charge_percent"), MAX_SERVICE_CHARGE, "The service charge"
		),
		"platforms": clean_platforms(raw.get("platforms")),
		"tables": clean_tables(raw.get("tables")),
	}


def default_settings() -> dict:
	"""A restaurant that has set nothing: no happy hour, no service charge, the three Kenyan platforms at 0% commission."""
	return {
		"happy_hours": [],
		"service_charge_percent": 0.0,
		"platforms": [{"name": n, "commission_percent": 0.0} for n in DEFAULT_PLATFORMS],
		"tables": {},
	}


def load_settings(text) -> dict:
	"""The kept settings; anything unreadable falls back to the defaults (never an error at the till)."""
	if not text:
		return default_settings()
	try:
		return clean_settings(text)
	except SettingsError:
		return default_settings()


def _minutes(hhmm: str) -> int:
	h, m = hhmm.split(":")
	return int(h) * 60 + int(m)


def happy_hour_for(
	rules: list[dict], item_code: str, item_group: str, weekday: int, hhmm: str
) -> dict | None:
	"""
	The happy hour that applies to an item now (`weekday` 0 is Monday, `hhmm` the time of day), or None. When several
	apply, the largest discount wins. A window past midnight belongs to the day it started.
	"""
	now = _minutes(hhmm)
	best = None
	for rule in rules or []:
		if item_code not in rule["items"] and item_group not in rule["groups"]:
			continue
		start, end = _minutes(rule["start"]), _minutes(rule["end"])
		today, yesterday = DAYS[weekday], DAYS[(weekday - 1) % 7]
		if start < end:
			on = today in rule["days"] and start <= now < end
		else:  # past midnight: the evening part today, or the early part from yesterday's start
			on = (today in rule["days"] and now >= start) or (yesterday in rule["days"] and now < end)
		if on and (best is None or rule["percent"] > best["percent"]):
			best = rule
	return best
