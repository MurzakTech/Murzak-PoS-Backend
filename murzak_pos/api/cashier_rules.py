"""
How far a cashier may discount a sale, and how far back a sale may be dated. Kept free of Frappe so it can be tested
on its own; cashier_limits.py applies these rules when a sale is saved.

Discounts: a cashier may take at most `limit` percent off the price-list price, on any line and on the sale as a
whole, however it is done (a lower price, a percentage, an amount off, or a discount on the whole sale). Discounts the
shop set up itself (ERPNext pricing rules and batch discounts) do not count against the cashier.

Dates: never in the future, and at most DAYS_BACK days ago (a till offline for up to a week uploads its sales late).
Owners and managers may go beyond both.
"""

from __future__ import annotations

from datetime import date

DEFAULT_DISCOUNT_LIMIT = 10.0  # percent
DAYS_BACK = 7
# Rounding: a few cents either way is not a discount
TOLERANCE = 0.01


def line_reduction(list_price: float, rate: float, qty: float, shop_discount_percent: float = 0.0) -> float:
	"""
	The money the cashier took off one line: what it costs at the price-list price, less the shop's own discount, less
	what it is sold for. Never below zero; zero when the line has no price-list price to compare with.
	"""
	if list_price <= 0 or qty <= 0:
		return 0.0
	allowed_price = list_price * (1 - max(shop_discount_percent, 0.0) / 100)
	return max(allowed_price - rate, 0.0) * qty


def discount_problems(lines: list[dict], sale_discount: float, limit: float) -> list[str]:
	"""
	What is over the cashier's limit: the item codes of lines more than `limit` percent below their price-list price
	(the shop's own discounts aside), and "sale" when the sale as a whole is. Each line is a dict with item_code, qty,
	list_price, rate (the price it is sold at before any discount on the whole sale) and shop_discount_percent.
	`sale_discount` is the money taken off the whole sale.
	"""
	problems = []
	list_total = 0.0
	taken_off = max(sale_discount or 0.0, 0.0)
	for line in lines:
		list_price = float(line.get("list_price") or 0)
		qty = float(line.get("qty") or 0)
		if list_price <= 0 or qty <= 0:
			continue
		line_list = list_price * qty
		reduced = line_reduction(
			list_price, float(line.get("rate") or 0), qty, line.get("shop_discount_percent") or 0
		)
		if reduced > line_list * limit / 100 + TOLERANCE:
			problems.append(line.get("item_code"))
		list_total += line_list
		taken_off += reduced
	if list_total > 0 and taken_off > list_total * limit / 100 + TOLERANCE and "sale" not in problems:
		problems.append("sale")
	return problems


def date_problem(posting_date: date, today: date, days_back: int = DAYS_BACK) -> str | None:
	"""None when a sale may carry this date; "future" or "too_old" otherwise."""
	if posting_date > today:
		return "future"
	if (today - posting_date).days > days_back:
		return "too_old"
	return None
