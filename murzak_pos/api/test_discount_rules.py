# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for item and item group discounts kept as Pricing Rules. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import discount_rules as rules
from murzak_pos.api import inventory_api
from murzak_pos.murzak_pos.doctype.inventory_discount_rule.inventory_discount_rule import (
	InventoryDiscountRule,
)


def _rule(**kwargs) -> dict:
	"""A rule in the screen's shape."""
	base = {
		"name": "PRLE-1",
		"rule_type": "Item",
		"item_code": "A",
		"item_group": None,
		"warehouse": None,
		"company": "Acme",
		"discount_type": "Percentage",
		"discount_value": 10,
		"priority": 10,
		"is_active": 1,
		"valid_from": None,
		"valid_upto": None,
		"description": "d",
		"modified": "2026-01-01 00:00:00",
	}
	base.update(kwargs)
	return base


class PriorityCase(unittest.TestCase):
	def test_an_item_discount_takes_the_upper_half_and_a_group_discount_the_lower(self):
		self.assertEqual(rules.pricing_priority(1, "Item"), "20")
		self.assertEqual(rules.pricing_priority(10, "Item"), "11")
		self.assertEqual(rules.pricing_priority(1, "Item Group"), "10")
		self.assertEqual(rules.pricing_priority(10, "Item Group"), "1")

	def test_an_item_discount_always_beats_a_group_discount(self):
		weakest_item = int(rules.pricing_priority(10, "Item"))
		strongest_group = int(rules.pricing_priority(1, "Item Group"))
		self.assertGreater(weakest_item, strongest_group)

	def test_a_blank_or_out_of_range_priority_is_held_to_the_screen_range(self):
		self.assertEqual(rules.clamp_priority(None), 10)
		self.assertEqual(rules.clamp_priority(""), 10)
		self.assertEqual(rules.clamp_priority(0), 1)
		self.assertEqual(rules.clamp_priority(99), 10)

	def test_the_screen_priority_round_trips(self):
		for kind in ("Item", "Item Group"):
			for number in range(1, 11):
				self.assertEqual(rules.screen_priority(rules.pricing_priority(number, kind), kind), number)

	def test_a_rule_with_no_priority_goes_last(self):
		self.assertEqual(rules.screen_priority(0, "Item"), 10)
		self.assertEqual(rules.screen_priority(None, "Item Group"), 10)


class CheckValuesCase(unittest.TestCase):
	def check(self, **kwargs):
		values = dict(
			rule_type="Item",
			item_code="A",
			item_group=None,
			discount_type="Percentage",
			discount_value=10,
			valid_from=None,
			valid_upto=None,
		)
		values.update(kwargs)
		rules.check_values(**values)

	def test_a_good_rule_passes(self):
		self.check()
		self.check(
			rule_type="Item Group", item_code=None, item_group="G", discount_type="Amount", discount_value=500
		)

	def test_the_target_value_and_dates_are_checked(self):
		for kwargs in (
			{"item_code": None},
			{"rule_type": "Item Group", "item_group": None},
			{"discount_value": 0},
			{"discount_value": -3},
			{"discount_value": 101},
			{"discount_type": "Free"},
			{"valid_from": "2026-05-01", "valid_upto": "2026-04-01"},
		):
			with self.subTest(kwargs=kwargs), self.assertRaises(frappe.ValidationError):
				self.check(**kwargs)

	def test_an_amount_may_exceed_a_hundred(self):
		self.check(discount_type="Amount", discount_value=250)


class ShapeCase(unittest.TestCase):
	def row(self, **kwargs):
		base = dict(
			name="PRLE-1",
			title="Ten off",
			apply_on="Item Code",
			disable=0,
			rate_or_discount="Discount Percentage",
			discount_percentage=10,
			discount_amount=0,
			warehouse="Stores",
			company="Acme",
			valid_from=None,
			valid_upto=None,
			priority="11",
			min_qty=0,
			applicable_for="",
			for_price_list=None,
			condition=None,
			modified="2026-01-01",
		)
		base.update(kwargs)
		return frappe._dict(base)

	def test_an_item_rule_reads_in_the_screens_shape(self):
		shape = rules.serialize(self.row(), {"items": ["A"], "groups": []})
		self.assertEqual(
			(shape["rule_type"], shape["item_code"], shape["discount_type"], shape["discount_value"]),
			("Item", "A", "Percentage", 10),
		)
		self.assertEqual((shape["priority"], shape["is_active"], shape["description"]), (10, 1, "Ten off"))
		self.assertIsNone(shape["batch_no"])

	def test_a_group_amount_rule_reads_in_the_screens_shape(self):
		row = self.row(
			apply_on="Item Group",
			rate_or_discount="Discount Amount",
			discount_amount=5,
			priority="1",
			disable=1,
		)
		shape = rules.serialize(row, {"items": [], "groups": ["G"]})
		self.assertEqual(
			(shape["rule_type"], shape["item_group"], shape["discount_type"], shape["discount_value"]),
			("Item Group", "G", "Amount", 5),
		)
		self.assertEqual((shape["priority"], shape["is_active"]), (10, 0))

	def test_only_a_plain_single_target_rule_is_the_screens_to_manage(self):
		one = {"items": ["A"], "groups": []}
		self.assertTrue(rules._is_simple(self.row(), one))
		for row, children in (
			(self.row(), {"items": ["A", "B"], "groups": []}),
			(self.row(), {"items": [], "groups": []}),
			(self.row(applicable_for="Customer"), one),
			(self.row(for_price_list="Retail"), one),
			(self.row(condition="doc.x == 1"), one),
			(self.row(min_qty=5), one),
			(self.row(rate_or_discount="Rate"), one),
			(self.row(apply_on="Brand"), one),
		):
			with self.subTest(row=dict(row), children=children):
				self.assertFalse(rules._is_simple(row, children))

	def test_filters_for_the_list_screen(self):
		rule = _rule(warehouse="Stores")
		self.assertTrue(rules.matches(rule))
		self.assertTrue(rules.matches(rule, item_code="A", warehouse="Stores", is_active=1))
		self.assertFalse(rules.matches(rule, item_code="B"))
		self.assertFalse(rules.matches(rule, item_group="G"))
		self.assertFalse(rules.matches(rule, warehouse="Other"))
		self.assertFalse(rules.matches(rule, is_active=0))


class ConflictCase(unittest.TestCase):
	def clashes(self, others, rule_type="Item", item_code="A", item_group=None, **kwargs):
		values = dict(warehouse=None, valid_from=None, valid_upto=None, priority=10)
		values.update(kwargs)
		with (
			patch.object(rules, "simple_rules", return_value=others),
			patch.object(rules, "_related_groups", return_value={"G", "Parent", "Child"}),
		):
			return rules.conflicting_rules(
				"Acme", rule_type, item_code, item_group, exclude=kwargs.pop("exclude", None), **values
			)

	def test_the_same_item_at_the_same_priority_clashes(self):
		self.assertEqual(self.clashes([_rule()]), ["PRLE-1"])

	def test_a_different_priority_or_item_does_not(self):
		self.assertEqual(self.clashes([_rule(priority=3)]), [])
		self.assertEqual(self.clashes([_rule(item_code="B")]), [])

	def test_a_switched_off_rule_and_the_rule_being_changed_do_not_clash(self):
		self.assertEqual(self.clashes([_rule(is_active=0)]), [])
		with (
			patch.object(rules, "simple_rules", return_value=[_rule()]),
		):
			self.assertEqual(
				rules.conflicting_rules("Acme", "Item", "A", None, None, None, None, 10, exclude="PRLE-1"), []
			)

	def test_warehouses_that_cannot_meet_do_not_clash_but_a_blank_one_does(self):
		self.assertEqual(self.clashes([_rule(warehouse="S1")], warehouse="S2"), [])
		self.assertEqual(self.clashes([_rule(warehouse="S1")], warehouse="S1"), ["PRLE-1"])
		self.assertEqual(self.clashes([_rule(warehouse=None)], warehouse="S2"), ["PRLE-1"])

	def test_dates_that_cannot_meet_do_not_clash(self):
		other = _rule(valid_from="2026-01-01", valid_upto="2026-01-31")
		self.assertEqual(self.clashes([other], valid_from="2026-02-01", valid_upto="2026-02-28"), [])
		self.assertEqual(self.clashes([other], valid_from="2026-01-15", valid_upto=None), ["PRLE-1"])

	def test_groups_that_contain_each_other_clash(self):
		other = _rule(rule_type="Item Group", item_code=None, item_group="Parent")
		self.assertEqual(
			self.clashes([other], rule_type="Item Group", item_code=None, item_group="Child"), ["PRLE-1"]
		)
		unrelated = _rule(rule_type="Item Group", item_code=None, item_group="Elsewhere")
		self.assertEqual(
			self.clashes([unrelated], rule_type="Item Group", item_code=None, item_group="Child"), []
		)

	def test_an_item_rule_and_a_group_rule_never_clash(self):
		group_rule = _rule(rule_type="Item Group", item_code=None, item_group="G")
		self.assertEqual(self.clashes([group_rule]), [])

	def test_the_nearest_free_priority_is_stronger_first(self):
		taken = [_rule(priority=10), _rule(name="PRLE-2", priority=9)]
		with (
			patch.object(rules, "simple_rules", return_value=taken),
			patch.object(rules, "_related_groups", return_value={"G"}),
		):
			self.assertEqual(rules.free_priority("Acme", "Item", "A", None, None, None, None, 10), 8)

	def test_no_free_priority_gives_none(self):
		taken = [_rule(name=f"P{n}", priority=n) for n in range(1, 11)]
		with patch.object(rules, "simple_rules", return_value=taken):
			self.assertIsNone(rules.free_priority("Acme", "Item", "A", None, None, None, None, 5))

	def test_saving_a_clashing_rule_is_refused_and_names_the_other(self):
		with (
			patch.object(rules, "conflicting_rules", return_value=["PRLE-9"]),
			self.assertRaises(frappe.ValidationError) as error,
		):
			rules._refuse_clash("Acme", "Item", "A", None, None, None, None, 10)
		self.assertIn("PRLE-9", str(error.exception))


class WriteCase(unittest.TestCase):
	def test_creating_an_item_discount_builds_a_selling_pricing_rule(self):
		doc = MagicMock()
		doc.get.side_effect = lambda table: {
			"items": [MagicMock(item_code="A")],
			"item_groups": [],
		}[table]
		doc.name = "PRLE-1"
		doc.title = "Ten off A"
		doc.apply_on, doc.rate_or_discount = "Item Code", "Discount Percentage"
		with (
			patch.object(rules.frappe, "new_doc", return_value=doc),
			patch.object(rules, "_company_currency", return_value="KES"),
			patch.object(rules, "conflicting_rules", return_value=[]),
		):
			rules.create_rule("Item", "Acme", "Percentage", 10, item_code="A", priority=3)
		self.assertEqual((doc.selling, doc.buying, doc.company, doc.currency), (1, 0, "Acme", "KES"))
		self.assertEqual((doc.price_or_product_discount, doc.has_priority, doc.priority), ("Price", 1, "18"))
		self.assertEqual(doc.rate_or_discount, "Discount Percentage")
		doc.append.assert_called_once_with("items", {"item_code": "A"})
		doc.insert.assert_called_once_with(ignore_permissions=True)

	def test_creating_an_amount_discount_for_a_group(self):
		doc = MagicMock()
		doc.get.side_effect = lambda table: {"items": [], "item_groups": [MagicMock(item_group="G")]}[table]
		with (
			patch.object(rules.frappe, "new_doc", return_value=doc),
			patch.object(rules, "_company_currency", return_value="KES"),
			patch.object(rules, "conflicting_rules", return_value=[]),
		):
			rules.create_rule("Item Group", "Acme", "Amount", 50, item_group="G", is_active=0)
		doc.append.assert_called_once_with("item_groups", {"item_group": "G"})
		self.assertEqual(
			(doc.rate_or_discount, doc.discount_amount, doc.discount_percentage), ("Discount Amount", 50, 0)
		)
		self.assertEqual(doc.disable, 1)

	def test_a_bad_value_is_refused_before_anything_is_made(self):
		with patch.object(rules.frappe, "new_doc") as new_doc, self.assertRaises(frappe.ValidationError):
			rules.create_rule("Item", "Acme", "Percentage", 150, item_code="A")
		new_doc.assert_not_called()

	def test_a_switched_off_rule_may_share_a_priority(self):
		doc = MagicMock()
		doc.get.return_value = []
		with (
			patch.object(rules.frappe, "new_doc", return_value=doc),
			patch.object(rules, "_company_currency", return_value="KES"),
			patch.object(rules, "conflicting_rules", return_value=["PRLE-9"]) as clash,
		):
			rules.create_rule("Item", "Acme", "Percentage", 10, item_code="A", is_active=0)
		clash.assert_not_called()

	def test_a_rule_the_screen_does_not_manage_cannot_be_changed_or_deleted_here(self):
		with patch.object(rules, "find_simple_rule", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				rules.update_rule("PRLE-1", discount_value=5)
			with self.assertRaises(frappe.ValidationError):
				rules.delete_rule("PRLE-1")

	def test_an_item_discount_cannot_become_a_batch_discount(self):
		with patch.object(rules, "find_simple_rule", return_value=_rule()):
			with self.assertRaises(frappe.ValidationError):
				rules.update_rule("PRLE-1", rule_type="Batch")

	def test_a_change_is_checked_against_the_values_it_would_leave(self):
		with patch.object(rules, "find_simple_rule", return_value=_rule()):
			with self.assertRaises(frappe.ValidationError):
				rules.update_rule("PRLE-1", discount_value=500)

	def test_delete_removes_the_pricing_rule(self):
		with (
			patch.object(rules, "find_simple_rule", return_value=_rule()),
			patch.object(rules.frappe, "delete_doc") as delete,
		):
			rules.delete_rule("PRLE-1")
		delete.assert_called_once_with("Pricing Rule", "PRLE-1", ignore_permissions=True)


class PreviewCase(unittest.TestCase):
	def answers(self, rows, pricing):
		batch = MagicMock(
			side_effect=lambda **kw: (
				{"name": "INV-1", "rule_type": "Batch"} if kw["batch_no"] == "B1" else None
			)
		)
		with (
			patch(
				"murzak_pos.murzak_pos.doctype.inventory_discount_rule.inventory_discount_rule.get_applicable_inventory_discount",
				batch,
			),
			patch.object(rules, "pricing_rule_discounts", return_value=pricing) as engine,
		):
			return rules.discounts_for(rows, "Acme"), engine

	def test_a_batch_discount_wins_and_other_rows_go_to_erpnext(self):
		rows = [
			{"item_code": "A", "item_group": "G", "batch_no": "B1"},
			{"item_code": "B", "item_group": "G"},
		]
		found, engine = self.answers(rows, [{"name": "PRLE-1"}])
		self.assertEqual(found[0]["name"], "INV-1")
		self.assertEqual(found[1], {"name": "PRLE-1"})
		self.assertEqual([r["item_code"] for r in engine.call_args.args[0]], ["B"])

	def test_rows_with_no_discount_stay_empty(self):
		found, _engine = self.answers([{"item_code": "B", "item_group": "G"}], [None])
		self.assertEqual(found, [None])

	def test_nothing_to_ask_gives_nothing(self):
		self.assertEqual(rules.pricing_rule_discounts([], "Acme"), [])


class AskErpnextCase(unittest.TestCase):
	def ask(self, answer, managed=None):
		rows = [{"item_code": "A", "item_group": "G", "warehouse": "Stores"}]
		answers = [dict(answer, name="preview-0")]
		with (
			patch.object(rules, "_company_currency", return_value="KES"),
			patch.object(rules, "_selling_price_list", return_value="Retail"),
			patch.object(rules.frappe, "db", MagicMock(get_value=MagicMock(return_value="Nos"))),
			patch(
				"erpnext.accounts.doctype.pricing_rule.pricing_rule.apply_pricing_rule", return_value=answers
			) as engine,
			patch.object(rules, "find_simple_rule", return_value=managed),
			patch.object(
				rules.frappe.db,
				"get_value",
				return_value=frappe._dict(
					title="Promo",
					apply_on="Brand",
					warehouse=None,
					company="Acme",
					valid_from=None,
					valid_upto=None,
					priority="5",
				),
			),
		):
			return rules.pricing_rule_discounts(rows, "Acme", "2026-03-01"), engine

	def test_the_engine_is_asked_for_one_unit_on_the_posting_date_in_the_warehouse(self):
		_found, engine = self.ask({"pricing_rules": "[]"})
		args = engine.call_args.args[0]
		self.assertEqual(
			(args["transaction_date"], args["company"], args["price_list"]), ("2026-03-01", "Acme", "Retail")
		)
		self.assertEqual((args["items"][0]["qty"], args["items"][0]["warehouse"]), (1, "Stores"))

	def test_no_rule_applied_means_no_discount(self):
		found, _engine = self.ask({"pricing_rules": "[]", "discount_percentage": 0})
		self.assertEqual(found, [None])

	def test_a_rate_rule_with_no_discount_is_not_shown_as_one(self):
		found, _engine = self.ask(
			{"pricing_rules": '["PRLE-3"]', "discount_percentage": 0, "discount_amount": 0}
		)
		self.assertEqual(found, [None])

	def test_a_rule_the_screen_manages_is_shown_as_it_reads_there(self):
		found, _engine = self.ask({"pricing_rules": '["PRLE-1"]', "discount_percentage": 10}, managed=_rule())
		self.assertEqual(found[0]["name"], "PRLE-1")

	def test_a_desk_rule_is_shown_with_what_it_gives(self):
		found, _engine = self.ask({"pricing_rules": '["PRLE-7"]', "discount_amount": 25})
		self.assertEqual(
			(found[0]["name"], found[0]["discount_type"], found[0]["discount_value"]),
			("PRLE-7", "Amount", 25),
		)


class ListCase(unittest.TestCase):
	def listed(self, pricing=(), batches=(), **kwargs):
		with (
			patch.object(inventory_api, "resolve_company", side_effect=lambda c: c),
			patch.object(inventory_api, "ensure_record_access"),
			patch.object(rules, "simple_rules", return_value=list(pricing)),
			patch.object(rules, "companies_in_scope", return_value=["Acme"]),
			patch.object(inventory_api.frappe, "get_all", return_value=list(batches)) as get_all,
		):
			result = inventory_api.list_inventory_discount_rules(company="Acme", **kwargs)
		return result, get_all

	def test_pricing_rules_and_batch_discounts_come_back_together_lowest_priority_first(self):
		batch = _rule(name="INV-1", rule_type="Batch", item_code=None, batch_no="B", priority=1)
		result, _get_all = self.listed(pricing=[_rule(priority=5)], batches=[batch])
		self.assertEqual([r["name"] for r in result["data"]["rules"]], ["INV-1", "PRLE-1"])
		self.assertEqual(result["data"]["pagination"]["total"], 2)

	def test_an_item_group_filter_leaves_out_batch_discounts(self):
		_result, get_all = self.listed(rule_type="Item Group")
		get_all.assert_not_called()

	def test_a_batch_filter_leaves_out_pricing_rules(self):
		result, get_all = self.listed(pricing=[_rule()], rule_type="Batch")
		self.assertEqual(result["data"]["rules"], [])
		self.assertEqual(get_all.call_args.kwargs["filters"]["rule_type"], "Batch")

	def test_pages_are_cut_from_the_combined_list(self):
		pricing = [_rule(name=f"P{n}", priority=n) for n in range(1, 6)]
		result, _get_all = self.listed(pricing=pricing, page=2, page_size=2)
		self.assertEqual([r["name"] for r in result["data"]["rules"]], ["P3", "P4"])
		self.assertEqual(result["data"]["pagination"]["total_pages"], 3)

	def test_an_item_filter_applies_to_pricing_rules(self):
		result, _get_all = self.listed(
			pricing=[_rule(), _rule(name="P2", item_code="B")], item_code="B", rule_type="Item"
		)
		self.assertEqual([r["name"] for r in result["data"]["rules"]], ["P2"])


class BatchRuleCase(unittest.TestCase):
	def test_a_new_item_or_group_rule_in_the_old_record_is_refused(self):
		for kind in ("Item", "Item Group"):
			doc = InventoryDiscountRule.__new__(InventoryDiscountRule)
			doc.rule_type, doc.batch_no = kind, None
			with self.subTest(kind=kind), self.assertRaises(frappe.ValidationError):
				doc._validate_rule_target()

	def test_a_batch_rule_needs_a_batch(self):
		doc = InventoryDiscountRule.__new__(InventoryDiscountRule)
		doc.rule_type, doc.batch_no = "Batch", None
		with self.assertRaises(frappe.ValidationError):
			doc._validate_rule_target()
		doc.batch_no = "B1"
		doc._validate_rule_target()

	def test_only_a_batch_discount_is_looked_up_in_the_old_record(self):
		from murzak_pos.murzak_pos.doctype.inventory_discount_rule import inventory_discount_rule as module

		with patch.object(module.frappe, "get_all") as get_all:
			self.assertIsNone(module.get_applicable_inventory_discount("A", "Acme", item_group="G"))
		get_all.assert_not_called()


class PatchCase(unittest.TestCase):
	def run_patch(self, old_rules, create=None, free=lambda *a: 10):
		from murzak_pos.patches import convert_discount_rules_to_pricing_rules as patch_module

		create = create or MagicMock(return_value={"name": "PRLE-9"})
		with (
			patch.object(patch_module.frappe, "db", MagicMock(exists=MagicMock(return_value=True))) as db,
			patch.object(patch_module.frappe, "get_all", return_value=old_rules),
			patch.object(patch_module.frappe, "log_error") as log,
			patch.object(rules, "create_rule", create),
			patch.object(rules, "free_priority", side_effect=free),
		):
			patch_module.execute()
		return db, create, log

	def old(self, **kwargs):
		base = frappe._dict(
			name="INV-1",
			rule_type="Item",
			item_code="A",
			item_group=None,
			company="Acme",
			warehouse="Stores",
			discount_type="Percentage",
			discount_value=10,
			priority=4,
			is_active=1,
			valid_from=None,
			valid_upto=None,
			description="Ten off",
		)
		base.update(kwargs)
		return base

	def test_a_rule_is_copied_and_the_old_one_switched_off_and_marked(self):
		db, create, _log = self.run_patch([self.old()])
		self.assertEqual(create.call_args.kwargs["discount_value"], 10)
		self.assertEqual(create.call_args.kwargs["warehouse"], "Stores")
		args = db.set_value.call_args.args
		self.assertEqual(args[:2], ("Inventory Discount Rule", "INV-1"))
		self.assertEqual(args[2]["is_active"], 0)
		self.assertIn("[Moved to Pricing Rule PRLE-9]", args[2]["description"])

	def test_a_switched_off_rule_is_copied_switched_off(self):
		_db, create, _log = self.run_patch([self.old(is_active=0)])
		self.assertEqual(create.call_args.kwargs["is_active"], 0)

	def test_a_rule_already_moved_is_left_alone(self):
		db, create, _log = self.run_patch([self.old(description="x [Moved to Pricing Rule PRLE-3]")])
		create.assert_not_called()
		db.set_value.assert_not_called()

	def test_a_rule_that_cannot_be_copied_is_logged_and_left_as_it_was(self):
		create = MagicMock(side_effect=frappe.ValidationError("bad"))
		db, _create, log = self.run_patch([self.old()], create=create)
		log.assert_called_once()
		db.set_value.assert_not_called()

	def test_a_tie_moves_the_later_rule_to_the_free_priority(self):
		_db, create, _log = self.run_patch([self.old()], free=lambda *a: 3)
		self.assertEqual(create.call_args.kwargs["priority"], 3)

	def test_a_rule_with_no_free_priority_is_logged_not_copied(self):
		_db, create, log = self.run_patch([self.old()], free=lambda *a: None)
		create.assert_not_called()
		log.assert_called_once()

	def test_a_switched_off_rule_does_not_look_for_a_free_priority(self):
		with patch.object(rules, "free_priority") as free:
			self.run_patch([self.old(is_active=0)])
		free.assert_not_called()


if __name__ == "__main__":
	unittest.main()
