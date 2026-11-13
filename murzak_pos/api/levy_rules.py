"""
The tourism levy (restaurants and bars), the rules that need no database.

Regulated hotels, restaurants and bars remit 2% of their gross receipts from food, drinks and services to the Tourism
Fund, monthly. Whether "gross receipts" is counted before or after VAT is not settled in what we could confirm, so the
report shows the levy on both bases and the owner confirms with the Tourism Fund which one they file on.

Kept free of Frappe imports so it can be tested on its own (see test_levy_rules.py). The call is in levy_api.py.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

RATE = 0.02
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


def month_range(value, today: date) -> tuple[date, date, str]:
	"""(first day, last day, "yyyy-mm") of the month asked for, or of this month. Raises ValueError for a bad month."""
	text = str(value or "").strip() or today.strftime("%Y-%m")
	m = _MONTH.match(text)
	if not m:
		raise ValueError("bad month")
	year, month = int(m.group(1)), int(m.group(2))
	if not 1 <= month <= 12 or year < 2000:
		raise ValueError("bad month")
	first = date(year, month, 1)
	if first > today:
		raise ValueError("future month")
	last = date(year, month, calendar.monthrange(year, month)[1])
	return first, last, text


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def levy_report(rows, outlet_names: dict, levied: set | None = None, rate: float = RATE) -> dict:
	"""
	The month's levy by store. `rows` are [{warehouse, net, gross}] sales of the month (returns already negative),
	possibly several per store; `outlet_names` maps a store to its name; `levied` is the stores the levy applies to
	(None means every store). Stores that are not levied (say, a pharmacy counter) are listed apart, with no levy.
	"""
	by_store: dict[str, dict] = {}
	for row in rows or []:
		store = row.get("warehouse") or ""
		entry = by_store.setdefault(store, {"net": 0.0, "gross": 0.0})
		entry["net"] += _num(row.get("net"))
		entry["gross"] += _num(row.get("gross"))

	stores, others = [], []
	for store, amounts in by_store.items():
		item = {
			"warehouse": store,
			"name": outlet_names.get(store) or store or "No store recorded",
			"sales_before_vat": round(amounts["net"], 2),
			"sales_with_vat": round(amounts["gross"], 2),
		}
		if levied is None or store in levied:
			item["levy_before_vat"] = round(amounts["net"] * rate, 2)
			item["levy_with_vat"] = round(amounts["gross"] * rate, 2)
			stores.append(item)
		else:
			others.append(item)
	stores.sort(key=lambda s: s["name"].lower())
	others.sort(key=lambda s: s["name"].lower())
	totals = {
		key: round(sum(s[key] for s in stores), 2)
		for key in ("sales_before_vat", "sales_with_vat", "levy_before_vat", "levy_with_vat")
	}
	return {"rate_percent": round(rate * 100, 2), "stores": stores, "not_levied": others, "totals": totals}
