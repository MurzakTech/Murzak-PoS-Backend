"""
Alert rules: who is told, when, and what each message says.

Kept free of Frappe imports so it can be tested on its own. The database work (which stock, which
business, sending the email) lives in alerts.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html import escape

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
SEPARATORS = re.compile(r"[,;\s]+")

EXPIRY = "Expiry"
LOW_STOCK = "Low stock"
SHIFT_SUMMARY = "Shift summary"
UNSENT_ETIMS = "Unsent eTIMS"

MAX_ROWS = 50  # rows listed in one message; the rest are counted


def _tokens(text) -> list[str]:
	return [token for token in SEPARATORS.split(str(text or "")) if token]


def parse_recipients(text) -> list[str]:
	"""The valid email addresses in a list typed by a person, each once, in lower case."""
	seen, result = set(), []
	for token in _tokens(text):
		address = token.lower()
		if EMAIL_PATTERN.match(address) and address not in seen:
			seen.add(address)
			result.append(address)
	return result


def invalid_recipients(text) -> list[str]:
	return [token for token in _tokens(text) if not EMAIL_PATTERN.match(token)]


def parse_numbers(text) -> list[str]:
	"""Phone numbers as typed, each once. Putting them in 254 form is left to the sender."""
	seen, result = set(), []
	for token in _tokens(text):
		if token not in seen:
			seen.add(token)
			result.append(token)
	return result


def timing_problem(long_days, short_days, hour) -> str | None:
	"""Plain-words reason the timing settings are unusable, or None."""
	try:
		long_days, short_days, hour = int(long_days), int(short_days), int(hour)
	except (TypeError, ValueError):
		return "The expiry windows and the hour must be whole numbers."
	if not 1 <= short_days <= 365 or not 1 <= long_days <= 365:
		return "The expiry windows must be between 1 and 365 days."
	if short_days > long_days:
		return "The urgent expiry window cannot be longer than the expiry window."
	if not 0 <= hour <= 23:
		return "The hour must be between 0 and 23."
	return None


def due_for_daily_alerts(current_hour: int, send_hour: int) -> bool:
	"""True from the chosen hour onwards; a once-a-day record stops repeats, and a late start still sends."""
	return int(current_hour) >= int(send_hour)


def dedupe_key(company: str, alert_type: str, reference: str) -> str:
	return f"{company}|{alert_type}|{reference}"[:140]


# --- wording -----------------------------------------------------------------------------------------


def _cell(value) -> str:
	return escape("" if value is None else str(value))


def _table(headings: list[str], rows: list[list]) -> str:
	head = "".join(f"<th align='left'>{escape(h)}</th>" for h in headings)
	body = "".join("<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in row) + "</tr>" for row in rows)
	return f"<table cellpadding='4' cellspacing='0' border='1'><tr>{head}</tr>{body}</table>"


def _more(total: int) -> str:
	return f"<p>and {total - MAX_ROWS} more.</p>" if total > MAX_ROWS else ""


def _money(value) -> str:
	return f"{float(value or 0):,.2f}"


def group_expiry_rows(rows: Iterable[dict], short_days: int) -> dict[str, list[dict]]:
	groups = {"Expired": [], "Urgent": [], "Soon": []}
	for row in rows:
		days = row.get("days_left")
		if days is None:
			continue
		if days < 0:
			groups["Expired"].append(row)
		elif days <= short_days:
			groups["Urgent"].append(row)
		else:
			groups["Soon"].append(row)
	return groups


def expiry_digest(company: str, rows: list[dict], short_days: int, long_days: int):
	"""(subject, html, short text) for a business's expiring stock, or None when there is none."""
	if not rows:
		return None
	groups = group_expiry_rows(rows, short_days)
	value = sum(float(r.get("stock_value") or 0) for r in rows)
	subject = f"{company}: {len(rows)} stock line(s) expired or expiring within {long_days} days"
	parts = [f"<p>{escape(subject)}. Stock value at risk: {_money(value)}.</p>"]
	titles = {
		"Expired": "Already expired",
		"Urgent": f"Expires within {short_days} days",
		"Soon": f"Expires within {long_days} days",
	}
	for key in ("Expired", "Urgent", "Soon"):
		if not groups[key]:
			continue
		table_rows = [
			[
				r.get("item_name") or r.get("item_code"),
				r.get("warehouse"),
				r.get("batch_no"),
				r.get("qty"),
				r.get("expiry_date"),
			]
			for r in groups[key][:MAX_ROWS]
		]
		parts.append(f"<h4>{escape(titles[key])} ({len(groups[key])})</h4>")
		parts.append(_table(["Item", "Warehouse", "Batch", "Quantity", "Expiry date"], table_rows))
		parts.append(_more(len(groups[key])))
	text = f"{company}: {len(groups['Expired'])} expired, {len(groups['Urgent'])} urgent, {len(groups['Soon'])} soon."
	return subject, "".join(parts), text


def low_stock_digest(company: str, rows: list[dict]):
	if not rows:
		return None
	subject = f"{company}: {len(rows)} item(s) at or below reorder level"
	table_rows = [
		[
			r.get("item_name") or r.get("item_code"),
			r.get("warehouse"),
			r.get("actual_qty"),
			r.get("reorder_level"),
		]
		for r in rows[:MAX_ROWS]
	]
	html = (
		f"<p>{escape(subject)}.</p>"
		+ _table(["Item", "Warehouse", "In stock", "Reorder level"], table_rows)
		+ _more(len(rows))
	)
	return subject, html, subject


def etims_digest(company: str, count: int, oldest: str | None):
	if not count:
		return None
	subject = f"{company}: {count} invoice(s) not yet sent to KRA"
	html = (
		f"<p>{escape(subject)}. The oldest was made at {escape(str(oldest or ''))}. "
		"They are retried every hour; if this continues, check the eTIMS settings and the connection.</p>"
	)
	return subject, html, subject


def shift_summary(data: dict):
	"""(subject, html, short text) for a closed shift. `data` holds plain values from the closing entry."""
	company = data["company"]
	subject = f"{company}: shift closed, sales {_money(data.get('grand_total'))}"
	modes = data.get("modes") or []
	table = _table(
		["Payment method", "Sales", "Expected", "Counted", "Difference"],
		[
			[
				m["mode"],
				_money(m.get("sales")),
				_money(m.get("expected")),
				"not counted" if m.get("counted") is None else _money(m.get("counted")),
				"" if m.get("counted") is None else _money(m.get("difference")),
			]
			for m in modes
		],
	)
	html = (
		f"<p>{escape(subject)}.</p>"
		f"<p>Till: {escape(str(data.get('pos_profile') or ''))}. Cashier: {escape(str(data.get('user') or ''))}. "
		f"Period: {escape(str(data.get('period_start') or ''))} to {escape(str(data.get('period_end') or ''))}.</p>"
		f"{table}"
		f"<p>Refunds: {int(data.get('refunds_count') or 0)} ({_money(data.get('refunds_total'))}). "
		f"Cancelled sales: {int(data.get('voids_count') or 0)}.</p>"
	)
	text = f"{subject}. Refunds {int(data.get('refunds_count') or 0)}, cancelled {int(data.get('voids_count') or 0)}."
	return subject, html, text
