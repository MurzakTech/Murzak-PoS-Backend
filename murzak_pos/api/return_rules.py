"""
How much of a sale can still be returned. Pure rules, no database (the numbers are gathered in sales_api).

A sale of 3 items can be returned for 3 items in total, in one go or over several returns. Without this check the
till could return the same sale again and again, refunding money that was never taken and adding stock that does
not exist.
"""

from __future__ import annotations

# Quantities are compared to this many decimals, so 0.1 + 0.2 sold and returned as 0.3 is not an overage
PLACES = 6


def overages(requested: dict[str, float], sold: dict[str, float], returned: dict[str, float]) -> list[dict]:
	"""
	The items asked back in greater quantity than the sale still has, in the order they were asked.

	All three are totals by item code, in the item's stock unit. An item that was not on the sale has 0 left.
	"""
	found = []
	for item, wanted in requested.items():
		sold_qty = round(float(sold.get(item) or 0), PLACES)
		returned_qty = round(float(returned.get(item) or 0), PLACES)
		left = round(max(sold_qty - returned_qty, 0), PLACES)
		if round(float(wanted), PLACES) > left:
			found.append(
				{
					"item_code": item,
					"asked": round(float(wanted), PLACES),
					"left": left,
					"sold": sold_qty,
					"returned": returned_qty,
				}
			)
	return found
