"""
First-time set-up: the plain rules, with no database and no Frappe import so they can be tested on their own
(ticket P5-4). The calls that read and write are in setup_api.py.

What lives here
- the four answers to "What do you sell?" and the features each one turns on
- the six steps of the wizard, and how progress through them is read and written
- the one rule for when a business is offered the wizard
- tidying the "usual opening cash" amount
"""

from __future__ import annotations

import json
import math

PHARMACY = "pharmacy"
RETAIL = "retail"
SERVICES = "services"
OTHER = "other"
KINDS = (PHARMACY, RETAIL, SERVICES, OTHER)

# The wizard's steps in the order they are shown
STEPS = ("business", "shop", "cash", "payments", "products", "staff")

# A business with this many products or more is not new
PRODUCT_LIMIT = 5

MAX_OPENING_CASH = 1_000_000_000

# The features, by their stable names. The browser shows or hides a thing by one of these names.
FEATURE_NAMES = ("expiry", "prescription_only", "barcodes_first", "units_first", "services_first")

_FEATURES_BY_KIND = {
	# Expiry dates and the Expiry tab, and prescription-only products
	PHARMACY: ("expiry", "prescription_only"),
	# Barcodes and units come first when adding a product
	RETAIL: ("barcodes_first", "units_first"),
	# Services are listed before goods
	SERVICES: ("services_first",),
	OTHER: (),
}

# What the sign-up screen's "Business industry" list (POS Industry codes) says about the kind, as a suggestion only
_KIND_BY_INDUSTRY = {
	"PHARMACY": PHARMACY,
	"RETAIL": RETAIL,
	"GROCERY": RETAIL,
	"FASHION": RETAIL,
	"ELECTRONICS": RETAIL,
	"BOOKSTORE": RETAIL,
	"SPORTS": RETAIL,
	"JEWELRY": RETAIL,
	"FURNITURE": RETAIL,
	"ONLINE": RETAIL,
	"SALON": SERVICES,
	"AUTO": SERVICES,
	"HOTEL": SERVICES,
}


def clean_kind(value) -> str | None:
	"""One of the four kinds however it was typed (case and spaces ignored); None for anything else."""
	text = str(value if value is not None else "").strip().lower()
	return text if text in KINDS else None


def features_for(kind) -> dict:
	"""Every feature name mapped to on or off for this kind. No kind (or an unknown one) turns nothing on."""
	on = _FEATURES_BY_KIND.get(clean_kind(kind) or "", ())
	return {name: name in on for name in FEATURE_NAMES}


def kind_from_industry(code) -> str | None:
	"""The kind that a sign-up industry code suggests, or None when it says nothing useful."""
	return _KIND_BY_INDUSTRY.get(str(code or "").strip().upper())


def clean_step(value) -> str | None:
	"""One of the six step names; None for anything else."""
	text = str(value if value is not None else "").strip().lower()
	return text if text in STEPS else None


# --- progress ---------------------------------------------------------------------------------------------


def empty_progress() -> dict:
	return {"done": [], "skipped": [], "finished": False}


def parse_progress(text) -> dict:
	"""
	Progress as it was saved: which steps were completed, which were skipped, and whether the wizard is finished.
	Anything unreadable is the same as nothing saved yet.
	"""
	progress = empty_progress()
	if not text:
		return progress
	try:
		raw = json.loads(text) if isinstance(text, str) else text
	except ValueError:
		return progress
	if not isinstance(raw, dict):
		return progress
	progress["done"] = _in_order(raw.get("done"))
	progress["skipped"] = [s for s in _in_order(raw.get("skipped")) if s not in progress["done"]]
	progress["finished"] = raw.get("finished") is True
	return progress


def _in_order(steps) -> list[str]:
	"""The valid step names in the list, each once, in wizard order."""
	if not isinstance(steps, list | tuple | set | frozenset):
		return []
	named = {clean_step(s) for s in steps}
	return [s for s in STEPS if s in named]


def dump_progress(progress: dict) -> str:
	return json.dumps(
		{
			"done": _in_order(progress.get("done")),
			"skipped": _in_order(progress.get("skipped")),
			"finished": progress.get("finished") is True,
		},
		separators=(",", ":"),
	)


def record_step(progress: dict, step: str, skipped: bool = False) -> dict:
	"""
	A new progress with `step` passed: completed, or skipped. A step completed later is no longer skipped, and a step
	that was completed stays completed if it is skipped again (nothing already saved is undone).
	"""
	done = set(progress.get("done") or [])
	skip = set(progress.get("skipped") or [])
	if skipped:
		if step not in done:
			skip.add(step)
	else:
		done.add(step)
		skip.discard(step)
	return {
		"done": _in_order(done),
		"skipped": _in_order(skip),
		"finished": progress.get("finished") is True,
	}


def finish(progress: dict) -> dict:
	"""Progress marked finished. "Skip for now" and "Finish" both end here."""
	return {**progress, "finished": True}


def passed_steps(progress: dict) -> list[str]:
	"""Every step the person has moved past, completed or skipped, in wizard order."""
	return _in_order(list(progress.get("done") or []) + list(progress.get("skipped") or []))


def next_step(progress: dict) -> str | None:
	"""The first step not passed yet, which is where a refresh continues. None when all six were passed."""
	passed = set(passed_steps(progress))
	for step in STEPS:
		if step not in passed:
			return step
	return None


# --- when the wizard is offered -----------------------------------------------------------------------------


def needs_setup(kind_recorded: bool, sales: int, products: int, finished: bool) -> bool:
	"""
	A business is offered the wizard only when it is new: no business kind recorded (by the wizard or at sign-up),
	no submitted sale, fewer than PRODUCT_LIMIT products, and the wizard not already finished or skipped.
	A business with any data of its own never sees it.
	"""
	if finished or kind_recorded:
		return False
	return _count(sales) == 0 and _count(products) < PRODUCT_LIMIT


def should_resume(progress: dict, sales: int, products: int) -> bool:
	"""
	The wizard was started (a step was passed) but not finished, and the business is still new: offer to carry on.
	A business that has sold something, or has grown past the product limit, is not nagged.
	"""
	if progress.get("finished"):
		return False
	if not passed_steps(progress):
		return False
	return _count(sales) == 0 and _count(products) < PRODUCT_LIMIT


def _count(value) -> int:
	try:
		return max(0, int(value or 0))
	except (TypeError, ValueError):
		return 0


# --- the usual opening cash -------------------------------------------------------------------------------


def clean_amount(value) -> float | None:
	"""
	An amount of money: zero or more, at most two decimals. Blank means "no suggestion" and gives 0.0.
	None when the value is not a usable number (text, negative, not finite, or absurdly large).
	"""
	if value is None or (isinstance(value, str) and not value.strip()):
		return 0.0
	if isinstance(value, bool):
		return None
	try:
		number = float(str(value).replace(",", "").strip())
	except (TypeError, ValueError):
		return None
	if not math.isfinite(number) or number < 0 or number > MAX_OPENING_CASH:
		return None
	return round(number, 2)
