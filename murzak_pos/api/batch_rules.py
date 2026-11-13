"""
Batches and expiry dates for medicines (and any product a shop chooses to track this way), the rules that need no
database.

A product that tracks batches is received batch by batch: each delivery line says its batch number (printed on the pack)
and its expiry date. A batch number left blank is made from the product code and the expiry date, so a pharmacist who
only types the expiry still gets a batch. ERPNext then sells the batch that expires first and refuses an expired one.

Kept free of Frappe imports so it can be tested on its own (see test_batch_rules.py). The database work is in batches.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime

MAX_BATCH = 60
# Letters, digits and the separators printed on packs. Anything else is dropped.
_BATCH_CHARS = re.compile(r"[^A-Za-z0-9\-/._ ]")

# Why an expiry date cannot be used, by stable code
MISSING = "missing"
BAD = "bad"
PAST = "past"


def clean_batch_no(value) -> str:
	"""The batch number as printed, tidied: no odd characters, single spaces, upper case, at most 60 characters."""
	text = _BATCH_CHARS.sub("", str(value or ""))
	return " ".join(text.split()).upper()[:MAX_BATCH]


def parse_expiry(value) -> date | None:
	"""An expiry date from yyyy-mm-dd, dd/mm/yyyy, or a date; None when it is not a real date."""
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = str(value or "").strip()
	for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
		try:
			return datetime.strptime(text, pattern).date()
		except ValueError:
			continue
	return None


def check_expiry(value, today: date) -> tuple[date | None, str | None]:
	"""
	(the expiry date, None) when it can be used, else (None, why): missing, bad, or past. Stock that has already expired
	is refused at receiving, so it can never be put on the shelf by mistake.
	"""
	if value in (None, ""):
		return None, MISSING
	expiry = parse_expiry(value)
	if expiry is None:
		return None, BAD
	if expiry < today:
		return None, PAST
	return expiry, None


def auto_batch_no(item_code, expiry: date) -> str:
	"""The batch number made when none is typed: the product code and the expiry, such as AMOX-500-EXP-2027-03-31."""
	code = clean_batch_no(item_code) or "ITEM"
	return f"{code[: MAX_BATCH - 15]}-EXP-{expiry.isoformat()}"


def batch_lines(items) -> dict[str, dict]:
	"""
	The batch number and expiry typed for each product on a delivery: {item_code: {"batch_no", "expiry_date"}}. A product
	is received as one batch per delivery; the first line that names one is used.
	"""
	found: dict[str, dict] = {}
	for line in items or []:
		if not isinstance(line, dict):
			continue
		code = str(line.get("item_code") or "").strip()
		if not code or code in found:
			continue
		batch = clean_batch_no(line.get("batch_no"))
		expiry = line.get("expiry_date")
		if batch or expiry:
			found[code] = {"batch_no": batch, "expiry_date": expiry}
	return found
