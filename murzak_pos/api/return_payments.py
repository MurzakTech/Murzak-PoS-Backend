"""
The payment line a POS Invoice return must carry.

ERPNext refuses a POS Invoice with no mode of payment ("At least one mode of payment is required for POS invoice"),
and a return is a POS Invoice when the sale was one. A return pays money back, so its line is the way the money goes
back, for a negative amount equal to the whole return. Without it every return failed, whichever refund the cashier
chose.

Which way: the one asked for (Cash, M-Pesa...), else the way the sale was mostly paid, else Cash.

Kept free of Frappe imports so it can be tested on its own.
"""

from __future__ import annotations

DEFAULT_MODE = "Cash"


def refund_mode(asked, sale_payments) -> str:
	"""The mode of payment the money goes back by."""
	if asked and str(asked).strip():
		return str(asked).strip()
	best, best_amount = None, 0.0
	for row in sale_payments or []:
		amount = float(row.get("amount") or 0)
		if row.get("mode_of_payment") and amount > best_amount:
			best, best_amount = row["mode_of_payment"], amount
	return best or DEFAULT_MODE


def refund_amount(total) -> float:
	"""The negative amount that pays back a return of this total (the total of a return is itself negative)."""
	return -abs(round(float(total or 0), 2))
