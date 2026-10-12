# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for business alerts: the wording and timing rules, and the once-a-day and one-business-at-a-time
behaviour. No database is needed; every Frappe call is replaced.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from murzak_pos.api import alert_rules as rules
from murzak_pos.api import alerts


class RecipientCase(unittest.TestCase):
	def test_addresses_are_cleaned_and_unique(self):
		text = "Owner@Acme.co.ke; owner@acme.co.ke, bad-address\nmanager@acme.co.ke"
		self.assertEqual(rules.parse_recipients(text), ["owner@acme.co.ke", "manager@acme.co.ke"])
		self.assertEqual(rules.invalid_recipients(text), ["bad-address"])

	def test_empty_values(self):
		self.assertEqual(rules.parse_recipients(None), [])
		self.assertEqual(rules.parse_numbers(""), [])

	def test_numbers_are_unique(self):
		self.assertEqual(
			rules.parse_numbers("0712345678, 0712345678 254700000000"), ["0712345678", "254700000000"]
		)


class TimingCase(unittest.TestCase):
	def test_good_settings(self):
		self.assertIsNone(rules.timing_problem(30, 7, 7))

	def test_bad_settings_are_named(self):
		self.assertIn("between 1 and 365", rules.timing_problem(0, 7, 7))
		self.assertIn("cannot be longer", rules.timing_problem(7, 30, 7))
		self.assertIn("0 and 23", rules.timing_problem(30, 7, 24))
		self.assertIn("whole numbers", rules.timing_problem("x", 7, 7))

	def test_daily_alerts_start_at_the_hour_and_still_go_out_late(self):
		self.assertFalse(rules.due_for_daily_alerts(6, 7))
		self.assertTrue(rules.due_for_daily_alerts(7, 7))
		self.assertTrue(rules.due_for_daily_alerts(15, 7))

	def test_key_names_business_alert_and_day(self):
		self.assertEqual(rules.dedupe_key("Acme", "Low stock", "2026-10-11"), "Acme|Low stock|2026-10-11")


class WordingCase(unittest.TestCase):
	def low(self, n):
		return [
			{
				"item_code": f"I{k}",
				"item_name": f"Item {k}",
				"warehouse": "W",
				"actual_qty": k,
				"reorder_level": 10,
			}
			for k in range(n)
		]

	def test_nothing_to_report_sends_nothing(self):
		self.assertIsNone(rules.low_stock_digest("Acme", []))
		self.assertIsNone(rules.expiry_digest("Acme", [], 7, 30))
		self.assertIsNone(rules.etims_digest("Acme", 0, None))

	def test_low_stock_lists_exactly_the_items(self):
		subject, html, _text = rules.low_stock_digest("Acme", self.low(3))
		self.assertIn("3 item(s)", subject)
		for k in range(3):
			self.assertIn(f"Item {k}", html)
		self.assertNotIn("Item 3", html)

	def test_long_lists_are_cut_and_counted(self):
		_s, html, _t = rules.low_stock_digest("Acme", self.low(rules.MAX_ROWS + 5))
		self.assertIn("and 5 more", html)

	def test_names_are_escaped(self):
		rows = [
			{
				"item_code": "X",
				"item_name": "<script>alert(1)</script>",
				"warehouse": "W",
				"actual_qty": 1,
				"reorder_level": 5,
			}
		]
		_s, html, _t = rules.low_stock_digest("Acme", rows)
		self.assertNotIn("<script>", html)

	def test_expiry_groups(self):
		rows = [
			{"item_name": "A", "days_left": -2, "stock_value": 10},
			{"item_name": "B", "days_left": 3, "stock_value": 10},
			{"item_name": "C", "days_left": 20, "stock_value": 10},
		]
		groups = rules.group_expiry_rows(rows, 7)
		self.assertEqual([len(groups[k]) for k in ("Expired", "Urgent", "Soon")], [1, 1, 1])
		subject, html, _t = rules.expiry_digest("Acme", rows, 7, 30)
		self.assertIn("Already expired", html)
		self.assertIn("3 stock line(s)", subject)

	def test_shift_summary_says_when_cash_was_not_counted(self):
		modes = [{"mode": "Cash", "sales": 10, "expected": 10, "counted": None, "difference": 0}]
		_s, html, _t = rules.shift_summary({"company": "Acme", "grand_total": 10, "modes": modes})
		self.assertIn("not counted", html)

	def test_shift_summary(self):
		data = {
			"company": "Acme",
			"grand_total": 1500,
			"pos_profile": "Till 1",
			"user": "cashier@acme.test",
			"modes": [{"mode": "Cash", "sales": 1000, "expected": 1000, "counted": 990, "difference": -10}],
			"refunds_count": 2,
			"refunds_total": 300,
			"voids_count": 1,
		}
		subject, html, _t = rules.shift_summary(data)
		self.assertIn("1,500.00", subject)
		for text in ("Cash", "990.00", "Refunds: 2", "Cancelled sales: 1"):
			self.assertIn(text, html)


def settings(**changes):
	values = dict(
		company="Acme",
		enabled=1,
		email_recipients="owner@acme.test",
		sms_numbers="",
		alert_expiry=1,
		alert_low_stock=1,
		alert_shift_summary=1,
		alert_unsent_etims=1,
		alert_payment_matching=1,
		expiry_window_long=30,
		expiry_window_short=7,
		send_hour=7,
	)
	values.update(changes)
	return SimpleNamespace(**values)


LOW = [{"item_code": "I1", "item_name": "One", "warehouse": "W", "actual_qty": 1, "reorder_level": 5}]


class RunCase(unittest.TestCase):
	def run_business(
		self, config, hour=8, claimed=True, low=None, expiring=None, unsent=(0, None), today="2026-10-11", counts=None
	):
		with (
			patch.object(alerts, "low_stock_rows", return_value=low or []),
			patch.object(alerts, "expiring_stock", return_value=expiring or []),
			patch.object(alerts, "unsent_etims", return_value=unsent),
			patch.object(alerts, "_claim", return_value="LOG-1" if claimed else None) as claim,
			patch.object(alerts, "_send_sms"),
			patch.object(alerts.frappe, "sendmail") as sendmail,
			patch.object(alerts.frappe, "db", MagicMock()),
			patch("murzak_pos.api.payment_matching_api.summary_counts", return_value=counts or {}),
		):
			result = alerts.run_for_business(config, today=today, hour=hour)
		return result, sendmail, claim

	def test_low_stock_sends_one_email_to_the_recipients(self):
		result, sendmail, _claim = self.run_business(settings(), low=LOW)
		self.assertTrue(result["Low stock"])
		sendmail.assert_called_once()
		self.assertEqual(sendmail.call_args.kwargs["recipients"], ["owner@acme.test"])

	def test_a_second_run_the_same_day_sends_nothing(self):
		result, sendmail, _claim = self.run_business(settings(), low=LOW, claimed=False)
		self.assertFalse(result["Low stock"])
		sendmail.assert_not_called()

	def test_nothing_low_sends_nothing_and_claims_nothing(self):
		result, sendmail, claim = self.run_business(settings(alert_expiry=0))
		self.assertFalse(result["Low stock"])
		claim.assert_not_called()
		sendmail.assert_not_called()

	def test_before_the_chosen_hour_only_the_hourly_check_runs(self):
		result, _sendmail, _claim = self.run_business(settings(), hour=5, low=LOW)
		self.assertNotIn("Low stock", result)
		self.assertNotIn("Expiry", result)

	def test_turning_an_alert_off_stops_it(self):
		result, sendmail, _claim = self.run_business(settings(alert_low_stock=0), low=LOW)
		self.assertNotIn("Low stock", result)
		sendmail.assert_not_called()

	def test_switching_the_business_off_stops_everything(self):
		result, sendmail, _claim = self.run_business(settings(enabled=0), low=LOW, unsent=(3, "x"))
		self.assertEqual(result, {})
		sendmail.assert_not_called()

	def test_unsent_invoices_are_reported_hourly_even_before_the_daily_hour(self):
		result, _sendmail, _claim = self.run_business(settings(), hour=3, unsent=(2, "2026-10-11 01:00:00"))
		self.assertTrue(result["Unsent eTIMS"])

	def test_payment_matching_goes_out_on_mondays_only(self):
		counts = {"Amount differs": 2}
		monday, sendmail, _claim = self.run_business(settings(), today="2026-10-12", counts=counts)
		self.assertTrue(monday["Payment matching"])
		sunday, _sendmail, _claim = self.run_business(settings(), today="2026-10-11", counts=counts)
		self.assertNotIn("Payment matching", sunday)

	def test_payment_matching_can_be_switched_off(self):
		result, _sendmail, _claim = self.run_business(
			settings(alert_payment_matching=0), today="2026-10-12", counts={"Amount differs": 2}
		)
		self.assertNotIn("Payment matching", result)

	def test_payment_matching_is_keyed_by_the_week(self):
		_result, _sendmail, claim = self.run_business(settings(), today="2026-10-12", counts={"Amount differs": 1})
		self.assertEqual(claim.call_args.args[:3], ("Acme", "Payment matching", "2026-W42"))

	def test_failed_send_forgets_the_claim_so_it_is_tried_again(self):
		with (
			patch.object(alerts, "_claim", return_value="LOG-1"),
			patch.object(alerts, "_send_sms"),
			patch.object(alerts.frappe, "sendmail", side_effect=RuntimeError("mail down")),
			patch.object(alerts.frappe, "delete_doc") as delete,
			patch.object(alerts.frappe, "log_error"),
			patch.object(alerts.frappe, "db", MagicMock()),
		):
			sent = alerts._deliver(settings(), "Low stock", "2026-10-11", ("s", "<p>h</p>", "t"))
		self.assertFalse(sent)
		delete.assert_called_once()


class IsolationCase(unittest.TestCase):
	def test_one_business_failing_does_not_stop_the_next(self):
		seen = []

		def fake_run(config):
			seen.append(config.company)
			if config.company == "Bad":
				raise RuntimeError("boom")

		with (
			patch.object(alerts.frappe, "get_all", return_value=["Bad", "Good"]),
			patch.object(alerts.frappe, "get_doc", side_effect=lambda _d, name: settings(company=name)),
			patch.object(alerts, "run_for_business", side_effect=fake_run),
			patch.object(alerts.frappe, "db", MagicMock()),
			patch.object(alerts.frappe, "log_error") as log_error,
		):
			alerts.run_hourly_alerts()
		self.assertEqual(seen, ["Bad", "Good"])
		log_error.assert_called_once()

	def test_shift_summary_is_not_queued_when_the_alert_is_off(self):
		closing = SimpleNamespace(company="Acme", name="POS-CLO-1")
		with (
			patch.object(alerts, "_settings_for", return_value=settings(alert_shift_summary=0)),
			patch.object(alerts.frappe, "enqueue") as enqueue,
		):
			alerts.queue_shift_summary(closing)
		enqueue.assert_not_called()

	def test_shift_summary_is_queued_after_commit_when_on(self):
		closing = SimpleNamespace(company="Acme", name="POS-CLO-1")
		with (
			patch.object(alerts, "_settings_for", return_value=settings()),
			patch.object(alerts.frappe, "enqueue") as enqueue,
		):
			alerts.queue_shift_summary(closing)
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
		self.assertEqual(enqueue.call_args.kwargs["closing_entry"], "POS-CLO-1")


if __name__ == "__main__":
	unittest.main()
