"""
The figures of a shift report (X report while the shift is open, Z report once it is closed). Plain functions: no
database, no Frappe. `shift_api.py` reads the rows and joins these.

Words used below
- X report: the figures so far, printed or looked at any number of times; it does not close or change anything
- Z report: the figures of a shift that has been closed, with what was counted and any difference
- a "line" is one mode of payment of the shift, as `till_calc.build_expected` makes it
"""

from __future__ import annotations

from murzak_pos.api import till_calc as calc

X, Z = "X", "Z"


def sales_block(count, gross, net, tax, discounts) -> dict:
	"""The shift's sales: how many, the total, before tax, tax, discounts given and the average sale."""
	count = int(calc.num(count))
	gross = calc.money(gross)
	return {
		"count": count,
		"gross": gross,
		"net": calc.money(net),
		"tax": calc.money(tax),
		"discounts": calc.money(discounts),
		"average": calc.money(gross / count) if count else 0.0,
	}


def returns_block(count, total) -> dict:
	"""Refunds and returns in the shift. The total is a positive amount of money handed back."""
	return {"count": int(calc.num(count)), "total": calc.money(abs(calc.num(total)))}


def method_rows(lines, counted_by_mode=None) -> list[dict]:
	"""
	One row per way of being paid (cash, M-Pesa, card and so on), with the counted amount and the difference when
	the shift was counted. On credit is not money in the till and is left to `credit_total`.
	"""
	counted_by_mode = counted_by_mode or {}
	rows = []
	for line in lines or []:
		if not line.get("counts_cash"):
			continue
		counted = counted_by_mode.get(line["mode"])
		rows.append(
			{
				"mode": line["mode"],
				"label": line.get("label") or line["mode"],
				"key": line["key"],
				"sales": calc.money(line["sales"]),
				"refunds": calc.money(line["refunds"]),
				"expected": calc.money(line["expected"]),
				"counted": None if counted is None else calc.money(counted),
				"difference": None if counted is None else calc.money(counted - line["expected"]),
			}
		)
	return rows


def credit_total(lines) -> float:
	"""What was sold on credit in the shift (not money in the till)."""
	return calc.money(sum(line["sales"] for line in lines or [] if not line.get("counts_cash")))


def cash_block(lines, movement_totals, counted=None) -> dict | None:
	"""
	The cash drawer, line by line: opening float, cash sales, money paid in, money paid out, safe drops, refunds,
	then what should be there and, when counted, what was and the difference. None when the shift has no cash line.

	`movement_totals` is the total of each kind of cash movement ({"Paid In": 1000.0, ...}). Safe drops are part of
	what the count calls paid out, so they are shown on their own and taken out of the other paid-out amount.
	"""
	cash = [line for line in lines or [] if line["key"] == calc.CASH]
	if not cash:
		return None
	movement_totals = movement_totals or {}
	drops = calc.money(movement_totals.get("Safe Drop"))
	paid_out_all = sum(line["paid_out"] for line in cash)
	expected = calc.money(sum(line["expected"] for line in cash))
	block = {
		"opening": calc.money(sum(line["opening"] for line in cash)),
		"sales": calc.money(sum(line["sales"] for line in cash)),
		"paid_in": calc.money(sum(line["paid_in"] for line in cash)),
		"paid_out": calc.money(max(paid_out_all - drops, 0.0)),
		"safe_drops": drops,
		"refunds": calc.money(sum(line["refunds"] for line in cash)),
		"expected": expected,
		"counted": None,
		"difference": None,
		"word": None,
	}
	if counted is not None:
		block["counted"] = calc.money(counted)
		block["difference"] = calc.money(counted - expected)
		block["word"] = calc.difference_word(block["difference"])
	return block


def totals(rows) -> dict:
	"""Expected, counted and difference over every counted way of being paid (counted is None until all are)."""
	rows = rows or []
	expected = calc.money(sum(r["expected"] for r in rows))
	if not rows or any(r["counted"] is None for r in rows):
		return {"expected": expected, "counted": None, "difference": None, "word": None}
	counted = calc.money(sum(r["counted"] for r in rows))
	difference = calc.money(counted - expected)
	return {
		"expected": expected,
		"counted": counted,
		"difference": difference,
		"word": calc.difference_word(difference),
	}
