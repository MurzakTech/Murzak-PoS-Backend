"""
Sales rung up while the till was offline and uploaded later.

Kept free of Frappe imports so it can be tested on its own. The till stores an
offline sale on the device and sends it when the connection returns, with:
- client_reference: the sale's own id, so sending it twice never records it twice;
- offline_sold_at: when the customer actually paid.

The invoice is posted when it arrives (so stock and accounts are never back-dated)
and keeps the real sale time in its own field and in its remarks.
"""

from __future__ import annotations

from datetime import datetime

CLIENT_REFERENCE_FIELD = "pos_client_reference"
OFFLINE_SOLD_AT_FIELD = "pos_offline_sold_at"

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M")


def parse_sold_at(value) -> str | None:
	"""Normalise the till's sale time to "YYYY-MM-DD HH:MM:SS", or None when not given or unreadable."""
	if not value:
		return None
	text = str(value).strip().replace("Z", "")
	text = text.split(".")[0]  # drop fractions of a second
	if "+" in text[10:]:
		text = text[: 10 + text[10:].index("+")]  # drop a time-zone offset; tills send local time
	for fmt in _FORMATS:
		try:
			return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
		except ValueError:
			continue
	return None


def offline_remark(sold_at: str, existing: str | None = None) -> str:
	"""Remarks line that tells anyone reading the invoice it was sold offline."""
	note = f"Sold offline at {sold_at}; uploaded when the till reconnected."
	if existing and note not in existing:
		return f"{existing}\n{note}"
	return existing or note
