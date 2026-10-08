# Plain unit tests for the profit arithmetic. They need no Frappe site:
#   python -m unittest techsavanna_pos.api.test_profit_calc
import unittest

from techsavanna_pos.api.profit_calc import summarize_profit


def line(net, qty, incoming=0, fallback=0, stock=1):
    """One invoice line expressed as the per-item totals the query returns."""
    return {
        "net_amount": net,
        "is_stock_item": stock,
        "recorded_cost": qty * incoming,
        "uncosted_qty": 0 if incoming else qty,
        "fallback_rate": fallback,
    }


class TestSummarizeProfit(unittest.TestCase):
    def test_uses_recorded_cost(self):
        r = summarize_profit([line(1000, 10, incoming=70)], operating_expenses=100)
        self.assertEqual(r["profitSales"], 1000)
        self.assertEqual(r["costOfGoodsSold"], 700)
        self.assertEqual(r["grossProfit"], 300)
        self.assertEqual(r["grossMargin"], 30.0)
        self.assertEqual(r["netProfit"], 200)
        self.assertEqual(r["profitMargin"], 20.0)
        self.assertEqual(r["costEstimatedItems"], 0)

    def test_returns_reduce_sales_and_cost(self):
        r = summarize_profit([line(1000, 10, incoming=70), line(-200, -2, incoming=70)])
        self.assertEqual(r["profitSales"], 800)
        self.assertEqual(r["costOfGoodsSold"], 560)
        self.assertEqual(r["grossProfit"], 240)

    def test_falls_back_to_valuation_and_flags_it(self):
        r = summarize_profit([line(500, 5, incoming=0, fallback=60)])
        self.assertEqual(r["costOfGoodsSold"], 300)
        self.assertEqual(r["costEstimatedItems"], 1)

    def test_service_items_have_no_cost(self):
        r = summarize_profit([line(400, 1, incoming=0, fallback=0, stock=0)])
        self.assertEqual(r["costOfGoodsSold"], 0)
        self.assertEqual(r["costMissingItems"], 0)
        self.assertEqual(r["grossMargin"], 100.0)

    def test_stock_item_with_no_cost_at_all_is_reported(self):
        r = summarize_profit([line(400, 2)])
        self.assertEqual(r["costMissingItems"], 1)

    def test_no_sales(self):
        r = summarize_profit([], operating_expenses=50)
        self.assertEqual(r["profitSales"], 0)
        self.assertEqual(r["netProfit"], -50)
        self.assertEqual(r["profitMargin"], 0.0)

    def test_tolerates_missing_and_text_values(self):
        r = summarize_profit([{"net_amount": "100.5", "recorded_cost": None, "uncosted_qty": "", "is_stock_item": 1}])
        self.assertEqual(r["profitSales"], 100.5)
        self.assertEqual(r["costOfGoodsSold"], 0)


if __name__ == "__main__":
    unittest.main()
