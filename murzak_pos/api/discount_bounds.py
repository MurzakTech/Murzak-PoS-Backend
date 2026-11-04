"""
A discount can never be more than the sale itself.

The till accepted a discount of 150 percent, which made the sale total KES 0 and left "Pay KES 0" enabled. Whatever a
person's role, a sale discount is from 0 to 100 percent, and a flat amount is not below zero or above what is being
sold. Kept free of Frappe imports so it can be tested on its own.
"""

from __future__ import annotations


def problem(percent, amount, items_total) -> str | None:
	"""'percent_range', 'amount_negative', 'amount_too_big', or None when the discount is within bounds."""
	percent, amount, total = _num(percent), _num(amount), _num(items_total)
	if percent < 0 or percent > 100:
		return "percent_range"
	if amount < 0:
		return "amount_negative"
	if total > 0 and amount > total:
		return "amount_too_big"
	return None


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
