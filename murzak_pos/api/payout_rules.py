"""
Matching a delivery platform's payout against the orders it settles, the rules that need no database.

A platform (Glovo, Uber Eats, Bolt Food) pays the restaurant every week or so: the sales of the orders it carried, less
its commission. Its statement lists the order numbers. The cashier typed each order's number at the till
(custom_channel_ref), so the statement's numbers find the orders, and what the platform actually kept can be compared
with the commission agreed.

Kept free of Frappe imports so it can be tested on its own. The calls are in payout_api.py.
"""

from __future__ import annotations

import re

MAX_REFS = 2000
_SPLIT = re.compile(r"[\s,;]+")


class PayoutError(ValueError):
	"""Something about the payout cannot be accepted; the message says why in plain words."""


def norm_ref(value) -> str:
	"""An order number as compared: no spaces, no leading '#', any letter case."""
	return "".join(str(value or "").split()).lstrip("#").upper()


def parse_refs(raw) -> list[str]:
	"""The order numbers from a statement: a list, or text with one per line (commas and spaces also separate them).
	Duplicates are dropped, the order kept."""
	if isinstance(raw, list | tuple):
		parts = [str(x) for x in raw]
	else:
		parts = _SPLIT.split(str(raw or ""))
	out, seen = [], set()
	for part in parts:
		ref = norm_ref(part)
		if ref and ref not in seen:
			seen.add(ref)
			out.append(ref)
	if len(out) > MAX_REFS:
		raise PayoutError(f"Keep a payout to {MAX_REFS} orders or fewer.")
	return out


def match(refs: list[str], orders: list[dict]) -> dict:
	"""
	The statement's order numbers against the platform's orders in the POS ([{invoice, ref, total, payout}]).
	Returns the orders to settle, the ones an earlier payout already settled, and the numbers found nowhere.
	An order number used on two sales matches both (a sale and its correction, for example); the cashier can see them.
	"""
	by_ref: dict[str, list[dict]] = {}
	for order in orders or []:
		key = norm_ref(order.get("ref"))
		if key:
			by_ref.setdefault(key, []).append(order)
	matched, settled, missing = [], [], []
	for ref in refs:
		found = by_ref.get(ref)
		if not found:
			missing.append(ref)
			continue
		for order in found:
			(settled if order.get("payout") else matched).append(order)
	return {"matched": matched, "already_settled": settled, "not_found": missing}


def summary(matched: list[dict], received, commission_percent) -> dict:
	"""
	What the platform kept compared with what was agreed: the sales of the matched orders, the commission expected at
	the platform's percent, the commission actually taken (sales less the amount received) and the difference.
	"""
	try:
		received = round(float(received or 0), 2)
	except (TypeError, ValueError):
		raise PayoutError("Enter the amount the platform paid.") from None
	if received <= 0:
		raise PayoutError("Enter the amount the platform paid.")
	sales = round(sum(float(o.get("total") or 0) for o in matched), 2)
	expected = round(sales * float(commission_percent or 0) / 100, 2)
	actual = round(sales - received, 2)
	return {
		"orders": len(matched),
		"sales": sales,
		"received": received,
		"commission_percent": float(commission_percent or 0),
		"expected_commission": expected,
		"actual_commission": actual,
		"difference": round(actual - expected, 2),
	}
