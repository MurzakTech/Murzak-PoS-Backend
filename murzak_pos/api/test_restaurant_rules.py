import unittest

from murzak_pos.api import restaurant_rules as rules


class TestVoidRules(unittest.TestCase):
	def test_a_reason_is_tidied_and_must_say_something(self):
		self.assertEqual(rules.clean_reason("  Wrong   item "), "Wrong item")
		self.assertEqual(len(rules.clean_reason("x" * 500)), rules.MAX_REASON)
		for bad in (None, "", "  ", "ok"):
			self.assertIsNone(rules.clean_reason(bad), bad)

	def test_a_quantity_must_be_above_nothing_and_sane(self):
		self.assertEqual(rules.clean_qty("2"), 2.0)
		self.assertEqual(rules.clean_qty(0.5), 0.5)
		for bad in (None, "", "x", 0, -1, 10**9):
			self.assertIsNone(rules.clean_qty(bad), bad)

	def test_a_cashier_needs_an_approver_but_a_manager_or_owner_does_not(self):
		self.assertTrue(rules.approver_needed("cashier"))
		self.assertTrue(rules.approver_needed(None))
		self.assertFalse(rules.approver_needed("manager"))
		self.assertFalse(rules.approver_needed("owner"))

	def test_only_a_manager_or_owner_can_approve(self):
		self.assertTrue(rules.can_approve("manager") and rules.can_approve("owner"))
		self.assertFalse(rules.can_approve("cashier") or rules.can_approve(None))


class TestCashup(unittest.TestCase):
	def run_cashup(self, **over):
		args = {
			"invoices": [
				{"name": "S1", "grand_total": 1000, "change_amount": 200, "owner": "amina@bar.test"},
				{"name": "S2", "grand_total": 500, "change_amount": 0, "owner": "amina@bar.test"},
				{"name": "S3", "grand_total": 300, "change_amount": 0, "owner": "otieno@bar.test"},
				{"name": "S4", "grand_total": 50, "change_amount": 0, "owner": "stranger@bar.test"},
			],
			"waiter_of": {"S3": "Brian"},
			"payments": [
				{"parent": "S1", "mode_of_payment": "Cash", "amount": 1200},
				{"parent": "S2", "mode_of_payment": "M-Pesa", "amount": 500},
				{"parent": "S3", "mode_of_payment": "Cash", "amount": 300},
				{"parent": "S4", "mode_of_payment": "Card", "amount": 50},
				{"parent": "NOT-LISTED", "mode_of_payment": "Cash", "amount": 9999},
			],
			"cash_modes": {"Cash"},
			"voids": [
				{"requested_by": "amina@bar.test", "amount": 450},
				{"requested_by": "amina@bar.test", "amount": 50.5},
			],
			"waiter_of_user": {"amina@bar.test": "Amina", "otieno@bar.test": "Otieno"},
		}
		args.update(over)
		return rules.cashup(**args)

	def by_name(self, result):
		return {w["waiter"]: w for w in result["waiters"]}

	def test_a_sale_named_to_a_waiter_is_theirs_else_it_belongs_to_who_rang_it_up(self):
		w = self.by_name(self.run_cashup())
		self.assertEqual(w["Brian"]["bills"], 1)  # rung up by Otieno, credited to Brian
		self.assertNotIn("Otieno", w)
		self.assertEqual(w["Amina"]["bills"], 2)

	def test_cash_to_hand_over_is_cash_taken_less_change_given(self):
		w = self.by_name(self.run_cashup())
		self.assertEqual(w["Amina"]["cash_to_hand_over"], 1000.0)  # 1200 cash taken, 200 change back
		self.assertEqual(w["Amina"]["by_mode"], {"Cash": 1200.0, "M-Pesa": 500.0})
		self.assertEqual(w["Brian"]["cash_to_hand_over"], 300.0)

	def test_a_person_with_no_waiter_record_is_listed_by_their_login(self):
		w = self.by_name(self.run_cashup())
		self.assertEqual(w[rules.NO_WAITER]["bills"], 1)
		w = self.by_name(self.run_cashup(waiter_of_user={"stranger@bar.test": "Stranger"}))
		self.assertEqual(w["Stranger"]["sales"], 50.0)

	def test_voids_are_counted_against_the_person_who_asked(self):
		amina = self.by_name(self.run_cashup())["Amina"]
		self.assertEqual((amina["voids"], amina["voids_amount"]), (2, 500.5))

	def test_payments_of_sales_not_listed_are_ignored_and_totals_add_up(self):
		totals = self.run_cashup()["totals"]
		self.assertEqual(totals["bills"], 4)
		self.assertEqual(totals["sales"], 1850.0)
		self.assertEqual(totals["cash_to_hand_over"], 1300.0)
		self.assertEqual(totals["voids"], 2)

	def test_waiters_are_listed_by_sales_largest_first(self):
		self.assertEqual(
			[w["waiter"] for w in self.run_cashup()["waiters"]], ["Amina", "Brian", rules.NO_WAITER]
		)

	def test_a_quiet_day_is_empty_not_an_error(self):
		result = rules.cashup([], {}, [], {"Cash"}, [], {})
		self.assertEqual(result["waiters"], [])
		self.assertEqual(result["totals"]["sales"], 0)

	def test_a_return_reduces_the_waiters_takings(self):
		result = self.run_cashup(
			invoices=[
				{"name": "S1", "grand_total": 1000, "change_amount": 0, "owner": "amina@bar.test"},
				{"name": "R1", "grand_total": -400, "change_amount": 0, "owner": "amina@bar.test"},
			],
			payments=[
				{"parent": "S1", "mode_of_payment": "Cash", "amount": 1000},
				{"parent": "R1", "mode_of_payment": "Cash", "amount": -400},
			],
			voids=[],
		)
		amina = self.by_name(result)["Amina"]
		self.assertEqual((amina["sales"], amina["cash_to_hand_over"]), (600.0, 600.0))


if __name__ == "__main__":
	unittest.main()


class TestTipsAndPlatforms(unittest.TestCase):
	def test_tips_go_to_the_waiter_named_else_who_recorded_them_and_are_kept_apart_from_sales(self):
		result = rules.cashup(
			[{"name": "S1", "grand_total": 1000, "change_amount": 0, "owner": "amina@bar.test"}],
			{},
			[{"parent": "S1", "mode_of_payment": "Cash", "amount": 1000}],
			{"Cash"},
			[],
			{"amina@bar.test": "Amina"},
			[
				{"waiter": "Brian", "recorded_by": "amina@bar.test", "amount": 100, "mode": "M-Pesa"},
				{"waiter": None, "recorded_by": "amina@bar.test", "amount": 50, "mode": "Cash"},
			],
		)
		by = {w["waiter"]: w for w in result["waiters"]}
		self.assertEqual((by["Amina"]["tips"], by["Amina"]["sales"]), (50.0, 1000.0))
		self.assertEqual(by["Amina"]["cash_to_hand_over"], 1000.0)  # the tip is the staff's, not the takings
		self.assertEqual(by["Brian"]["tips_by_mode"], {"M-Pesa": 100.0})
		self.assertEqual(result["totals"]["tips"], 150.0)

	def test_the_platform_report_estimates_commission_and_payout(self):
		report = rules.platform_report(
			[
				{"channel": "Glovo", "orders": 3, "sales": 6000},
				{"channel": "Bolt Food", "orders": 1, "sales": 1000},
			],
			[{"name": "glovo", "commission_percent": 25}, {"name": "Uber Eats", "commission_percent": 30}],
		)
		glovo, bolt = report["platforms"]
		self.assertEqual((glovo["commission"], glovo["expected_payout"]), (1500.0, 4500.0))
		self.assertEqual((bolt["commission_percent"], bolt["expected_payout"]), (0.0, 1000.0))
		self.assertEqual(
			report["totals"], {"sales": 7000.0, "commission": 1500.0, "expected_payout": 5500.0, "orders": 4}
		)
