import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import payout_api as api
from murzak_pos.api import payout_rules as rules

ORDERS = [
	{"invoice": "POS-1", "doctype": "POS Invoice", "ref": "GL-1001", "total": 1200.0, "payout": None},
	{"invoice": "POS-2", "doctype": "POS Invoice", "ref": "#gl-1002", "total": 800.0, "payout": None},
	{"invoice": "POS-3", "doctype": "POS Invoice", "ref": "GL-0999", "total": 500.0, "payout": "PAY-OLD"},
	{"invoice": "POS-4", "doctype": "POS Invoice", "ref": "", "total": 300.0, "payout": None},
]


def plain(fn):
	return getattr(fn, "__wrapped__", fn)


class TestPayoutRules(unittest.TestCase):
	def test_order_numbers_are_read_from_a_pasted_statement(self):
		self.assertEqual(
			rules.parse_refs("GL-1001\n #gl-1002, gl-1001 ;GL 0999"), ["GL-1001", "GL-1002", "GL", "0999"]
		)
		self.assertEqual(rules.parse_refs(["gl-1001", " GL-1001 ", "", "#x 1"]), ["GL-1001", "X1"])

	def test_the_statement_finds_its_orders_and_says_what_it_could_not(self):
		found = rules.match(["GL-1001", "GL-1002", "GL-0999", "GL-7777"], ORDERS)
		self.assertEqual([o["invoice"] for o in found["matched"]], ["POS-1", "POS-2"])
		self.assertEqual([o["invoice"] for o in found["already_settled"]], ["POS-3"])
		self.assertEqual(found["not_found"], ["GL-7777"])

	def test_the_commission_taken_is_compared_with_the_commission_agreed(self):
		figures = rules.summary(ORDERS[:2], 1450, 25)
		self.assertEqual(
			(
				figures["sales"],
				figures["expected_commission"],
				figures["actual_commission"],
				figures["difference"],
			),
			(2000.0, 500.0, 550.0, 50.0),
		)

	def test_a_payout_of_nothing_is_refused(self):
		for amount in (0, "", "ten", -1):
			with self.assertRaises(rules.PayoutError, msg=amount):
				rules.summary(ORDERS[:1], amount, 25)


class TestRecordPayout(unittest.TestCase):
	def record(self, refs="GL-1001\nGL-1002\nGL-7777", amount="1450", **kw):
		db = MagicMock()
		db.table_exists.return_value = True
		db.exists.return_value = True
		doc = MagicMock()
		doc.name = "PAY-1"
		with (
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "session", frappe._dict(user="boss@cafe.test"), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api.role_tiers, "require_access"),
			patch.object(api, "resolve_company", return_value="Cafe Ltd"),
			patch.object(api, "_orders", return_value=ORDERS),
			patch.object(api, "_commission", return_value=25.0),
			patch.object(api, "now_datetime", return_value="2026-11-13 10:00:00"),
			patch.object(api.frappe, "get_doc", return_value=doc) as get_doc,
		):
			result = plain(api.record_payout)("Glovo", refs, amount, statement_ref="WK-45", **kw)
		return result, get_doc, db

	def test_the_payout_is_kept_and_its_orders_marked_settled(self):
		result, get_doc, db = self.record()
		kept = get_doc.call_args.args[0]
		self.assertEqual(
			(kept["orders_matched"], kept["difference"], kept["unmatched_refs"]), (2, 50.0, "GL-7777")
		)
		marked = [c.args[:3] for c in db.set_value.call_args_list]
		self.assertEqual(
			marked,
			[
				("POS Invoice", "POS-1", "custom_channel_payout"),
				("POS Invoice", "POS-2", "custom_channel_payout"),
			],
		)
		self.assertEqual(result["data"]["payout"], "PAY-1")

	def test_a_statement_matching_nothing_new_or_a_future_day_is_refused(self):
		for kw in (
			{"refs": "GL-0999\nGL-7777"},
			{"refs": ""},
			{"payout_date": "2999-01-01"},
			{"amount": "0"},
		):
			with self.assertRaises(frappe.ValidationError, msg=kw):
				self.record(**kw)


if __name__ == "__main__":
	unittest.main()
