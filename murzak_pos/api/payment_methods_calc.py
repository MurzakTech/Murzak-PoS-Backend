"""
Payment methods: the plain rules behind the switches in Settings. No database and no Frappe in here.

A shop takes money in one of four ways that always have a switch (Cash, M-Pesa, Card, On credit), plus any
other way it has set up (a bank draft, a cheque), grouped under "Other". Each switch is on or off. Nothing has
to be saved for a shop to work: until an owner switches something off, every payment method the shop has is on.

How a mode of payment name becomes a switch (this is the same rule as `paymentKey` in the front end's
`src/newlook/components/Chip.jsx`, so the till and the reports group the names the same way):

- Mpesa, M-Pesa, MPESA, M-PESA Till and so on: M-Pesa
- Cash: Cash
- Card or Credit Card: Card
- Credit or On credit: On credit
- anything else: Other

What is kept: only the choices to switch OFF, as a small piece of text, for example
`{"off": ["mpesa"], "off_other": ["Bank Draft"]}`. A method with no entry is on, so a payment method the shop adds
later starts on, and the shop looks exactly as it does today until an owner changes something.
"""

from __future__ import annotations

import json
import re

CASH = "cash"
MPESA = "mpesa"
CARD = "card"
CREDIT = "credit"
OTHER = "other"

# The four switches that always exist, in the order the till shows them: Cash and M-Pesa first
FIXED = (
	(CASH, "Cash"),
	(MPESA, "M-Pesa"),
	(CARD, "Card"),
	(CREDIT, "On credit"),
)
FIXED_KEYS = tuple(key for key, _label in FIXED)
LABELS = dict(FIXED)
ALL_KEYS = (*FIXED_KEYS, OTHER)

# Same patterns, in the same order, as METHODS in src/newlook/components/Chip.jsx
_PATTERNS = (
	(MPESA, re.compile(r"m-?pesa", re.IGNORECASE)),
	(CASH, re.compile(r"^cash$", re.IGNORECASE)),
	(CARD, re.compile(r"^(credit )?card$", re.IGNORECASE)),
	(CREDIT, re.compile(r"^(on )?credit$", re.IGNORECASE)),
)

# Reasons a change is refused. The server turns each into a plain sentence.
UNKNOWN_KEY = "unknown_key"
MODE_NEEDED = "mode_needed"
HAS_OWN_SWITCH = "has_own_switch"
NOT_AVAILABLE = "not_available"
LAST_METHOD = "last_method"
BAD_FLAG = "bad_flag"

_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off")


def payment_key(name) -> str:
	"""Which switch a mode of payment name belongs to: cash, mpesa, card, credit or other."""
	text = str(name if name is not None else "").strip()
	for key, pattern in _PATTERNS:
		if pattern.search(text):
			return key
	return OTHER


def _sort_key(name: str):
	return (name.lower(), name)


def group_modes(names) -> dict:
	"""The mode of payment names of a shop, grouped by switch. Each group is in alphabetical order, with no repeats
	and no blanks."""
	groups = {key: [] for key in ALL_KEYS}
	seen = set()
	for raw in names or []:
		if not isinstance(raw, str):
			continue
		name = raw.strip()
		if not name or name in seen:
			continue
		seen.add(name)
		groups[payment_key(name)].append(name)
	for key in groups:
		groups[key].sort(key=_sort_key)
	return groups


def parse_flag(value):
	"""True or False for however the browser sent an on or off value (true, "true", 1, "1"); None when it is
	anything else."""
	if isinstance(value, bool):
		return value
	if isinstance(value, int) and value in (0, 1):
		return bool(value)
	text = str(value if value is not None else "").strip().lower()
	if text in _ON:
		return True
	if text in _OFF:
		return False
	return None


def empty_state() -> dict:
	return {"off": [], "off_other": []}


def parse_stored(raw) -> dict:
	"""Read what was kept. Nothing kept, or text that cannot be understood, means nothing is switched off."""
	state = empty_state()
	if not raw:
		return state
	try:
		data = json.loads(raw) if isinstance(raw, str | bytes) else raw
	except (TypeError, ValueError):
		return state
	if not isinstance(data, dict):
		return state
	off = data.get("off")
	if isinstance(off, list):
		state["off"] = sorted({k for k in off if k in FIXED_KEYS})
	off_other = data.get("off_other")
	if isinstance(off_other, list):
		state["off_other"] = sorted({m for m in off_other if isinstance(m, str) and m.strip()}, key=_sort_key)
	return state


def dump_stored(state: dict) -> str:
	"""The text to keep for a state."""
	clean = parse_stored(state)
	return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def build_payment_methods(mode_names, stored=None) -> dict:
	"""
	The answer to "what payment methods does this shop have, and which are on".

	`mode_names` are the modes of payment the shop can use; `stored` is what was kept (text, a dict, or nothing).

	A method is `available` when the shop has at least one mode of payment of that kind. It is `enabled` only when
	it is available and has not been switched off, so a method the shop does not have never counts as on.
	"""
	groups = group_modes(mode_names)
	state = parse_stored(stored)
	methods = []
	for key, label in FIXED:
		modes = groups[key]
		available = bool(modes)
		item = {
			"key": key,
			"label": label,
			"enabled": available and key not in state["off"],
			"available": available,
			"modes": modes,
			"needs_code": key == MPESA,
		}
		methods.append(item)
	other = [
		{"mode": mode, "label": mode, "enabled": mode not in state["off_other"], "available": True}
		for mode in groups[OTHER]
	]
	return {"methods": methods, "other": other}


def till_modes(payload: dict) -> list:
	"""The flat, ordered list of mode of payment names the till should offer: Cash, M-Pesa, Card and On credit
	first (in that order), then the other modes that are on."""
	names = []
	for item in payload["methods"]:
		if item["enabled"]:
			names.extend(item["modes"])
	names.extend(item["mode"] for item in payload["other"] if item["enabled"])
	return names


def enabled_count(payload: dict) -> int:
	"""How many ways of taking money are on. Every mode counts: Mpesa and M-Pesa are two ways under one switch."""
	return len(till_modes(payload))


def _is_enabled(payload: dict, key: str, mode) -> bool:
	if key == OTHER:
		return any(item["mode"] == mode and item["enabled"] for item in payload["other"])
	return any(item["key"] == key and item["enabled"] for item in payload["methods"])


def plan_change(payload: dict, key, enabled, mode=None) -> dict:
	"""
	Decide whether a switch may be changed, before anything is written.

	Returns {"error": None or a reason, "key", "mode", "enabled", "changed"}. `changed` is False when the switch
	is already as asked (so asking twice does nothing the second time) and also when switching off a method the
	shop does not have.

	Refused: a key that is not one of the five; Other without saying which; a mode that has its own switch;
	switching on a method the shop does not have; switching off the last way of taking money.
	"""
	plan = {"error": None, "key": None, "mode": None, "enabled": None, "changed": False}
	flag = parse_flag(enabled)
	if flag is None:
		plan["error"] = BAD_FLAG
		return plan
	plan["enabled"] = flag
	clean_key = str(key if key is not None else "").strip().lower()
	if clean_key not in ALL_KEYS:
		plan["error"] = UNKNOWN_KEY
		return plan
	plan["key"] = clean_key

	if clean_key == OTHER:
		name = str(mode if mode is not None else "").strip()
		if not name:
			plan["error"] = MODE_NEEDED
			return plan
		plan["mode"] = name
		if payment_key(name) != OTHER:
			plan["error"] = HAS_OWN_SWITCH
			return plan
		available = any(item["mode"] == name for item in payload["other"])
	else:
		available = any(item["key"] == clean_key and item["available"] for item in payload["methods"])

	if not available:
		if flag:
			plan["error"] = NOT_AVAILABLE
		return plan  # switching off something the shop does not have changes nothing

	current = _is_enabled(payload, clean_key, plan["mode"])
	if current == flag:
		return plan
	if not flag and enabled_count(payload) - _count_of(payload, clean_key, plan["mode"]) < 1:
		plan["error"] = LAST_METHOD
		return plan
	plan["changed"] = True
	return plan


def _count_of(payload: dict, key: str, mode) -> int:
	"""How many ways of taking money one switch accounts for."""
	if key == OTHER:
		return 1
	for item in payload["methods"]:
		if item["key"] == key:
			return len(item["modes"]) if item["enabled"] else 0
	return 0


def apply_change(stored, key: str, enabled: bool, mode=None) -> dict:
	"""The state after one switch is changed. Switching on removes the entry, so the method goes back to the
	default (on)."""
	state = parse_stored(stored)
	if key == OTHER:
		off = set(state["off_other"])
		(off.discard if enabled else off.add)(mode)
		state["off_other"] = sorted(off, key=_sort_key)
	else:
		off = set(state["off"])
		(off.discard if enabled else off.add)(key)
		state["off"] = sorted(off)
	return state
