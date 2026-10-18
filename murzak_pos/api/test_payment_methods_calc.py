# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the payment method rules. Plain Python: no database and no Frappe calls."""

import json
import unittest

from murzak_pos.api import payment_methods_calc as calc

SHOP = ["Cash", "Mpesa", "Credit Card", "Credit", "Bank Draft", "Cheque"]


def payload_for(names=SHOP, stored=None):
	return calc.build_payment_methods(names, stored)


def method(payload, key):
	return next(m for m in payload["methods"] if m["key"] == key)


class TestMapping(unittest.TestCase):
	def test_names_map_to_the_same_switch_as_the_front_end(self):
		cases = {
			"Cash": "cash",
			"cash": "cash",
			" CASH ": "cash",
			"Mpesa": "mpesa",
			"M-Pesa": "mpesa",
			"MPESA": "mpesa",
			"M-PESA Till": "mpesa",
			"Lipa na Mpesa": "mpesa",
			"Card": "card",
			"Credit Card": "card",
			"credit card": "card",
			"Credit": "credit",
			"On credit": "credit",
			"on credit": "credit",
			"Bank Draft": "other",
			"Cheque": "other",
			"Wire Transfer": "other",
			"Mobile Money": "other",
			"Debit Card": "other",
			"Cash on Delivery": "other",
		}
		for name, key in cases.items():
			with self.subTest(name=name):
				self.assertEqual(calc.payment_key(name), key)

	def test_blank_and_missing_names_are_other(self):
		for name in ("", "   ", None):
			with self.subTest(name=name):
				self.assertEqual(calc.payment_key(name), "other")

	def test_group_modes_sorts_and_drops_repeats_and_blanks(self):
		groups = calc.group_modes(["Mpesa", "M-Pesa", "Mpesa", "", None, 5, "Cash", "Cheque", "Bank Draft"])
		self.assertEqual(groups["mpesa"], ["M-Pesa", "Mpesa"])
		self.assertEqual(groups["cash"], ["Cash"])
		self.assertEqual(groups["other"], ["Bank Draft", "Cheque"])
		self.assertEqual(groups["card"], [])

	def test_group_modes_of_nothing_is_empty(self):
		self.assertEqual(set(calc.group_modes(None)), set(calc.ALL_KEYS))
		self.assertTrue(all(v == [] for v in calc.group_modes([]).values()))


class TestDefaults(unittest.TestCase):
	def test_the_four_fixed_methods_are_always_there_in_order(self):
		for names in ([], ["Cash"], SHOP):
			with self.subTest(names=names):
				keys = [m["key"] for m in payload_for(names)["methods"]]
				self.assertEqual(keys, ["cash", "mpesa", "card", "credit"])

	def test_labels(self):
		labels = [m["label"] for m in payload_for([])["methods"]]
		self.assertEqual(labels, ["Cash", "M-Pesa", "Card", "On credit"])

	def test_nothing_saved_means_every_available_method_is_on(self):
		payload = payload_for()
		self.assertTrue(all(m["enabled"] for m in payload["methods"]))
		self.assertTrue(all(o["enabled"] for o in payload["other"]))

	def test_a_method_the_shop_does_not_have_is_unavailable_and_not_on(self):
		payload = payload_for(["Cash"])
		self.assertTrue(method(payload, "cash")["available"])
		for key in ("mpesa", "card", "credit"):
			self.assertFalse(method(payload, key)["available"])
			self.assertFalse(method(payload, key)["enabled"])
			self.assertEqual(method(payload, key)["modes"], [])

	def test_modes_list_what_each_switch_controls(self):
		payload = payload_for(["Mpesa", "M-Pesa", "Cash"])
		self.assertEqual(method(payload, "mpesa")["modes"], ["M-Pesa", "Mpesa"])
		self.assertEqual(method(payload, "cash")["modes"], ["Cash"])

	def test_only_mpesa_needs_a_code(self):
		flags = {m["key"]: m["needs_code"] for m in payload_for()["methods"]}
		self.assertEqual(flags, {"cash": False, "mpesa": True, "card": False, "credit": False})

	def test_other_modes_are_grouped_apart_with_their_own_shape(self):
		other = payload_for()["other"]
		self.assertEqual(
			other,
			[
				{"mode": "Bank Draft", "label": "Bank Draft", "enabled": True, "available": True},
				{"mode": "Cheque", "label": "Cheque", "enabled": True, "available": True},
			],
		)

	def test_a_shop_with_no_modes_has_nothing_on(self):
		payload = payload_for([])
		self.assertEqual(calc.till_modes(payload), [])
		self.assertEqual(payload["other"], [])


class TestStoredText(unittest.TestCase):
	def test_nothing_or_nonsense_means_nothing_off(self):
		for raw in (None, "", "not json", "[]", "5", '{"off": "mpesa"}', b"{", 7):
			with self.subTest(raw=raw):
				self.assertEqual(calc.parse_stored(raw), {"off": [], "off_other": []})

	def test_unknown_keys_in_the_text_are_ignored(self):
		state = calc.parse_stored('{"off": ["mpesa", "bitcoin", 3], "off_other": ["Cheque", "", 4]}')
		self.assertEqual(state, {"off": ["mpesa"], "off_other": ["Cheque"]})

	def test_a_dict_is_accepted_as_well_as_text(self):
		self.assertEqual(calc.parse_stored({"off": ["card"]})["off"], ["card"])

	def test_round_trip(self):
		state = {"off": ["card", "mpesa"], "off_other": ["Cheque"]}
		self.assertEqual(calc.parse_stored(calc.dump_stored(state)), state)

	def test_the_kept_text_is_small_and_stable(self):
		text = calc.dump_stored({"off_other": ["Cheque"], "off": ["card", "mpesa"]})
		self.assertEqual(text, '{"off":["card","mpesa"],"off_other":["Cheque"]}')
		self.assertEqual(json.loads(text)["off"], ["card", "mpesa"])

	def test_switching_off_in_the_text_turns_it_off(self):
		payload = payload_for(stored='{"off":["mpesa"],"off_other":["Cheque"]}')
		self.assertFalse(method(payload, "mpesa")["enabled"])
		self.assertTrue(method(payload, "mpesa")["available"])
		self.assertTrue(method(payload, "cash")["enabled"])
		self.assertEqual([o["enabled"] for o in payload["other"]], [True, False])

	def test_a_mode_added_later_starts_on(self):
		payload = payload_for([*SHOP, "Gift Voucher"], '{"off":[],"off_other":["Cheque"]}')
		flags = {o["mode"]: o["enabled"] for o in payload["other"]}
		self.assertEqual(flags, {"Bank Draft": True, "Cheque": False, "Gift Voucher": True})


class TestTillModes(unittest.TestCase):
	def test_default_order_is_cash_mpesa_card_credit_then_others(self):
		self.assertEqual(
			calc.till_modes(payload_for(list(reversed(SHOP)))),
			["Cash", "Mpesa", "Credit Card", "Credit", "Bank Draft", "Cheque"],
		)

	def test_switching_mpesa_off_removes_it(self):
		payload = payload_for(stored='{"off":["mpesa"]}')
		modes = calc.till_modes(payload)
		self.assertNotIn("Mpesa", modes)
		self.assertEqual(modes[0], "Cash")

	def test_every_mode_under_one_switch_goes_together(self):
		payload = payload_for(["Cash", "Mpesa", "M-Pesa"], '{"off":["mpesa"]}')
		self.assertEqual(calc.till_modes(payload), ["Cash"])
		payload = payload_for(["Cash", "Mpesa", "M-Pesa"])
		self.assertEqual(calc.till_modes(payload), ["Cash", "M-Pesa", "Mpesa"])

	def test_cash_and_mpesa_come_first_whatever_the_name_order(self):
		modes = calc.till_modes(payload_for(["Zebra Pay", "Credit", "Card", "Mpesa", "Cash"]))
		self.assertEqual(modes[:4], ["Cash", "Mpesa", "Card", "Credit"])
		self.assertEqual(modes[-1], "Zebra Pay")

	def test_other_modes_that_are_off_are_left_out(self):
		payload = payload_for(stored='{"off_other":["Bank Draft"]}')
		self.assertEqual(calc.till_modes(payload)[-1], "Cheque")
		self.assertNotIn("Bank Draft", calc.till_modes(payload))

	def test_a_stored_off_for_a_method_the_shop_lacks_changes_nothing(self):
		payload = payload_for(["Cash"], '{"off":["card"]}')
		self.assertEqual(calc.till_modes(payload), ["Cash"])


class TestFlag(unittest.TestCase):
	def test_the_browsers_spellings(self):
		for sent, expected in ((True, True), ("true", True), ("1", True), (1, True), ("ON", True),
			(False, False), ("false", False), ("0", False), (0, False), ("off", False)):  # fmt: skip
			with self.subTest(sent=sent):
				self.assertIs(calc.parse_flag(sent), expected)

	def test_nonsense_is_none(self):
		for sent in ("maybe", None, "2", 2, [], ""):
			with self.subTest(sent=sent):
				self.assertIsNone(calc.parse_flag(sent))


class TestPlanChange(unittest.TestCase):
	def test_switching_a_method_off_is_allowed_when_another_stays_on(self):
		plan = calc.plan_change(payload_for(), "mpesa", False)
		self.assertEqual((plan["error"], plan["changed"], plan["key"]), (None, True, "mpesa"))

	def test_asking_for_what_is_already_so_changes_nothing(self):
		on = calc.plan_change(payload_for(), "cash", True)
		self.assertEqual((on["error"], on["changed"]), (None, False))
		off = calc.plan_change(payload_for(stored='{"off":["card"]}'), "card", False)
		self.assertEqual((off["error"], off["changed"]), (None, False))

	def test_the_key_is_forgiving_about_case_and_spaces(self):
		plan = calc.plan_change(payload_for(), " MPesa ", False)
		self.assertEqual((plan["error"], plan["key"]), (None, "mpesa"))

	def test_an_unknown_key_is_refused(self):
		for key in ("bitcoin", "", None, "cashh", "credit card"):
			with self.subTest(key=key):
				self.assertEqual(calc.plan_change(payload_for(), key, True)["error"], calc.UNKNOWN_KEY)

	def test_nonsense_on_or_off_is_refused(self):
		self.assertEqual(calc.plan_change(payload_for(), "cash", "maybe")["error"], calc.BAD_FLAG)

	def test_switching_on_a_method_the_shop_does_not_have_is_refused(self):
		plan = calc.plan_change(payload_for(["Cash"]), "mpesa", True)
		self.assertEqual(plan["error"], calc.NOT_AVAILABLE)
		self.assertFalse(plan["changed"])

	def test_switching_off_a_method_the_shop_does_not_have_changes_nothing(self):
		plan = calc.plan_change(payload_for(["Cash"]), "mpesa", False)
		self.assertEqual((plan["error"], plan["changed"]), (None, False))

	def test_the_last_method_cannot_be_switched_off(self):
		plan = calc.plan_change(payload_for(["Cash"]), "cash", False)
		self.assertEqual(plan["error"], calc.LAST_METHOD)
		self.assertFalse(plan["changed"])

	def test_the_last_method_rule_counts_what_is_on_not_what_exists(self):
		payload = payload_for(["Cash", "Mpesa"], '{"off":["mpesa"]}')
		self.assertEqual(calc.plan_change(payload, "cash", False)["error"], calc.LAST_METHOD)

	def test_the_last_method_can_be_an_other_mode(self):
		payload = payload_for(["Cheque", "Cash"], '{"off":["cash"]}')
		plan = calc.plan_change(payload, "other", False, "Cheque")
		self.assertEqual(plan["error"], calc.LAST_METHOD)

	def test_one_switch_with_two_modes_is_not_the_last_while_the_other_stays_on(self):
		payload = payload_for(["Mpesa", "M-Pesa", "Cash"])
		self.assertEqual(calc.plan_change(payload, "mpesa", False)["error"], None)

	def test_one_switch_holding_all_the_modes_is_the_last(self):
		payload = payload_for(["Mpesa", "M-Pesa"])
		self.assertEqual(calc.plan_change(payload, "mpesa", False)["error"], calc.LAST_METHOD)

	def test_switching_a_method_on_is_always_fine_when_it_exists(self):
		payload = payload_for(["Cash", "Card"], '{"off":["card"]}')
		plan = calc.plan_change(payload, "card", True)
		self.assertEqual((plan["error"], plan["changed"]), (None, True))

	def test_other_needs_a_mode(self):
		for mode in (None, "", "  "):
			with self.subTest(mode=mode):
				self.assertEqual(
					calc.plan_change(payload_for(), "other", False, mode)["error"], calc.MODE_NEEDED
				)

	def test_a_mode_with_its_own_switch_cannot_be_changed_under_other(self):
		for mode in ("Cash", "Mpesa", "Credit Card", "On credit"):
			with self.subTest(mode=mode):
				plan = calc.plan_change(payload_for(), "other", False, mode)
				self.assertEqual(plan["error"], calc.HAS_OWN_SWITCH)

	def test_an_other_mode_the_shop_lacks_cannot_be_switched_on(self):
		plan = calc.plan_change(payload_for(), "other", True, "Gift Voucher")
		self.assertEqual(plan["error"], calc.NOT_AVAILABLE)

	def test_switching_off_an_other_mode_works(self):
		plan = calc.plan_change(payload_for(), "other", False, "Cheque")
		self.assertEqual((plan["error"], plan["changed"], plan["mode"]), (None, True, "Cheque"))


class TestApplyChange(unittest.TestCase):
	def test_switching_off_then_on_returns_to_the_default(self):
		state = calc.apply_change(None, "mpesa", False)
		self.assertEqual(state, {"off": ["mpesa"], "off_other": []})
		self.assertEqual(calc.apply_change(state, "mpesa", True), {"off": [], "off_other": []})

	def test_other_modes_are_kept_by_name(self):
		state = calc.apply_change(None, "other", False, "Cheque")
		state = calc.apply_change(state, "other", False, "Bank Draft")
		self.assertEqual(state["off_other"], ["Bank Draft", "Cheque"])
		self.assertEqual(calc.apply_change(state, "other", True, "Cheque")["off_other"], ["Bank Draft"])

	def test_the_same_change_twice_is_the_same_state(self):
		once = calc.apply_change(None, "card", False)
		self.assertEqual(calc.apply_change(once, "card", False), once)

	def test_switching_mpesa_off_end_to_end_removes_it_from_the_till(self):
		payload = payload_for()
		self.assertIn("Mpesa", calc.till_modes(payload))
		plan = calc.plan_change(payload, "mpesa", False)
		state = calc.apply_change(None, plan["key"], plan["enabled"], plan["mode"])
		after = payload_for(stored=calc.dump_stored(state))
		self.assertNotIn("Mpesa", calc.till_modes(after))
		self.assertEqual(calc.till_modes(after)[0], "Cash")


if __name__ == "__main__":
	unittest.main()
