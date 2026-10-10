"""
Expiry alert rules: which stock counts as expired or expiring, and how urgent it is.

Kept free of Frappe imports so it can be tested on its own. The database work
(which batches and stock lines, for which business) lives in expiry_api.py.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

EXPIRED = "Expired"
CRITICAL = "Expires within 7 days"
SOON = "Expiring soon"

# Most urgent first; also the order the screen lists them in.
STATUS_ORDER = (EXPIRED, CRITICAL, SOON)

CRITICAL_DAYS = 7
DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365


def clamp_window(days) -> int:
	"""Accept 1 to 365 days; anything else falls back to the 30-day default."""
	try:
		value = int(days)
	except (TypeError, ValueError):
		return DEFAULT_WINDOW_DAYS
	if value < 1:
		return DEFAULT_WINDOW_DAYS
	return min(value, MAX_WINDOW_DAYS)


def classify(expiry_date: date | None, today: date, window_days: int) -> dict | None:
	"""
	Status and days left for one expiry date, or None when it needs no alert:
	no date set, or further away than the window.
	"""
	if not expiry_date:
		return None
	days_left = (expiry_date - today).days
	if days_left > window_days:
		return None
	if days_left < 0:
		status = EXPIRED
	elif days_left <= CRITICAL_DAYS:
		status = CRITICAL
	else:
		status = SOON
	return {"status": status, "days_left": days_left}


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def sort_alerts(rows: Iterable[dict]) -> list[dict]:
	"""Soonest expiry first, then item and warehouse so the order is stable."""
	return sorted(
		rows,
		key=lambda r: (str(r.get("expiry_date") or ""), r.get("item_code") or "", r.get("warehouse") or ""),
	)


def summarize(rows: Iterable[dict]) -> dict:
	"""Counts and stock value per status, plus totals."""
	summary = {status: {"lines": 0, "value": 0.0} for status in STATUS_ORDER}
	for row in rows:
		bucket = summary.get(row.get("status"))
		if bucket is None:
			continue
		bucket["lines"] += 1
		bucket["value"] += _num(row.get("stock_value"))
	return {
		"by_status": [
			{"status": s, "lines": summary[s]["lines"], "value": round(summary[s]["value"], 2)}
			for s in STATUS_ORDER
		],
		"total_lines": sum(b["lines"] for b in summary.values()),
		"value_at_risk": round(sum(b["value"] for b in summary.values()), 2),
	}
