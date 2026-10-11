"""
Profit arithmetic for the dashboard.

Kept free of Frappe imports so it can be tested on its own. The database
work (which invoice lines, which expense entries) lives in dashboard_api.py.

How profit is worked out, in the same way as ERPNext's Gross Profit report:
- Sales: the net amount (before tax) of every submitted Sales Invoice line in
  the period. Credit notes (returns) have negative amounts, so they reduce it.
- Cost of goods sold: for each stock item line, quantity x the cost ERPNext
  recorded when the stock left (Sales Invoice Item.incoming_rate). Returns
  have negative quantities, so the cost comes back. When no cost was recorded
  (for example stock delivered on a separate Delivery Note), the item's
  current valuation rate is used and the line is counted as estimated.
  Service and other non-stock items have no cost of goods.
- Gross profit = sales - cost of goods sold.
- Net profit = gross profit - operating expenses.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 2) if whole else 0.0


def summarize_profit(rows: Iterable[Dict], operating_expenses: Optional[float] = 0.0) -> Dict:
    """Return the dashboard profit figures.

    Each row totals the invoice lines for one item in one store:
      net_amount     sum of line net amounts (returns are negative)
      is_stock_item  1 for stock items; others have no cost of goods
      recorded_cost  sum of quantity x cost recorded when the stock left
      uncosted_qty   quantity on lines where no cost was recorded
      fallback_rate  the item's current valuation rate (may be 0)
    """
    sales = 0.0
    cost = 0.0
    estimated_items = 0
    missing_items = 0

    for row in rows:
        sales += _num(row.get("net_amount"))
        if not row.get("is_stock_item"):
            continue
        cost += _num(row.get("recorded_cost"))
        uncosted = _num(row.get("uncosted_qty"))
        if uncosted:
            rate = _num(row.get("fallback_rate"))
            if rate:
                cost += uncosted * rate
                estimated_items += 1
            else:
                missing_items += 1

    expenses = _num(operating_expenses)
    gross = sales - cost
    net = gross - expenses

    return {
        "profitSales": round(sales, 2),
        "costOfGoodsSold": round(cost, 2),
        "grossProfit": round(gross, 2),
        "grossMargin": _pct(gross, sales),
        "netProfit": round(net, 2),
        "profitMargin": _pct(net, sales),
        "costEstimatedItems": estimated_items,
        "costMissingItems": missing_items,
    }
