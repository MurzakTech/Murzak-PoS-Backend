# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for restaurant recipes and wastage. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import recipes_api as api


def _db(items=None, exists=None, bundles=(), values=None):
	"""A fake database: `items` maps item code -> its fields; `bundles` are the dishes that have a recipe."""
	items = items or {}
	values = values or {}
	db = MagicMock()

	def exist(doctype, name=None):
		if doctype == "Product Bundle":
			return name in bundles
		if doctype == "Item":
			return name in items
		if exists is not None and doctype in exists:
			return exists[doctype]
		return False

	def get_value(doctype, name, fieldname=None, **kwargs):
		if doctype == "Item" and isinstance(name, str):
			row = items.get(name)
			if row is None:
				return None
			if isinstance(fieldname, list):
				return frappe._dict({f: row.get(f) for f in fieldname})
			return row.get(fieldname)
		return values.get((doctype, fieldname if isinstance(fieldname, str) else None))

	db.exists.side_effect = exist
	db.get_value.side_effect = get_value
	return db


def _flt(value, precision=None):
	"""Frappe's flt rounds through the database settings, which a test with no database does not have."""
	number = float(value or 0)
	return round(number, precision) if precision is not None else number


FLOUR = {"item_name": "Flour", "stock_uom": "Kg", "is_stock_item": 1, "disabled": 0}
EGG = {"item_name": "Egg", "stock_uom": "Nos", "is_stock_item": 1, "disabled": 0}
SERVICE = {"item_name": "Delivery", "stock_uom": "Nos", "is_stock_item": 0, "disabled": 0}


@patch.object(api, "flt", _flt)
class UnitsCase(unittest.TestCase):
	def convert(self, qty, uom, detail=None, general=0.0, stock_uom="Kg"):
		db = MagicMock()
		db.get_value.return_value = detail
		with (
			patch.object(api.frappe, "db", db),
			patch("erpnext.stock.doctype.item.item.get_uom_conv_factor", return_value=general),
		):
			return api.to_stock_quantity("FLR", stock_uom, qty, uom)

	def test_the_stock_unit_needs_no_conversion(self):
		self.assertEqual(self.convert(2, None), 2)
		self.assertEqual(self.convert(2, "Kg"), 2)

	def test_the_items_own_unit_table_is_used_first(self):
		self.assertAlmostEqual(self.convert(250, "Gram", detail=0.001, general=99), 0.25)

	def test_the_general_conversions_are_the_fallback(self):
		self.assertAlmostEqual(self.convert(500, "Gram", detail=None, general=0.001), 0.5)

	def test_an_unknown_pair_is_refused_not_treated_as_equal(self):
		with self.assertRaises(frappe.ValidationError) as error:
			self.convert(250, "Litre", detail=None, general=0.0)
		self.assertIn("Litre", str(error.exception))

	def test_a_quantity_that_would_round_away_is_refused(self):
		for quantity in (0.0001, 0.0004):
			with self.assertRaises(frappe.ValidationError):
				api._checked_quantity("FLR", quantity)

	def test_a_quantity_is_kept_to_three_decimals(self):
		self.assertEqual(api._checked_quantity("FLR", 0.25), 0.25)
		self.assertEqual(api._checked_quantity("FLR", 0.3501), 0.35)


@patch.object(api, "flt", _flt)
class IngredientsCase(unittest.TestCase):
	def clean(self, ingredients, items=None, dish="DISH"):
		items = (
			{"FLR": FLOUR, "EGG": EGG, "SVC": SERVICE, "OFF": dict(FLOUR, disabled=1)}
			if items is None
			else items
		)
		with (
			patch.object(api.frappe, "db", _db(items)),
			patch.object(api, "ensure_record_access"),
			patch.object(
				api,
				"to_stock_quantity",
				side_effect=lambda code, stock_uom, qty, uom: qty * (0.001 if uom == "Gram" else 1),
			),
		):
			return api.clean_ingredients(ingredients, "Acme", dish)

	def test_lines_are_converted_and_a_repeated_ingredient_is_added_together(self):
		rows = self.clean(
			[
				{"item_code": "FLR", "qty": 250, "uom": "Gram"},
				{"item_code": "EGG", "qty": 2},
				{"item_code": "FLR", "qty": 100, "uom": "Gram"},
			]
		)
		self.assertEqual(
			[(r["item_code"], r["qty"], r["uom"]) for r in rows], [("FLR", 0.35, "Kg"), ("EGG", 2.0, "Nos")]
		)

	def test_the_ingredients_may_arrive_as_text(self):
		rows = self.clean('[{"item_code": "EGG", "qty": 3}]')
		self.assertEqual(rows[0]["qty"], 3.0)

	def test_bad_lines_are_refused(self):
		for ingredients in (
			[],
			[{"item_code": "NOPE", "qty": 1}],
			[{"item_code": "SVC", "qty": 1}],
			[{"item_code": "OFF", "qty": 1}],
			[{"item_code": "DISH", "qty": 1}],
			[{"item_code": "EGG", "qty": 0}],
			[{"item_code": "EGG", "qty": -1}],
			[{"qty": 1}],
		):
			with self.subTest(ingredients=ingredients), self.assertRaises(frappe.ValidationError):
				self.clean(
					ingredients,
					items={
						"FLR": FLOUR,
						"EGG": EGG,
						"SVC": SERVICE,
						"OFF": dict(FLOUR, disabled=1),
						"DISH": EGG,
					},
				)

	def test_too_many_ingredients_are_refused(self):
		lines = [{"item_code": "EGG", "qty": 1}] * (api.MAX_INGREDIENTS + 1)
		with self.assertRaises(frappe.ValidationError):
			self.clean(lines)


class CreateRecipeCase(unittest.TestCase):
	def create(self, dish_item, history=False, bundles=(), company="Acme"):
		item = frappe._dict(name="DISH", item_name="Dish", custom_company=company, **dish_item)
		bundle = MagicMock()
		db = _db({"DISH": dish_item}, bundles=bundles)
		with (
			patch.object(api, "require_catalogue_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_dish_for", return_value=item),
			patch.object(api, "_has_stock_history", return_value=history),
			patch.object(
				api,
				"clean_ingredients",
				return_value=[{"item_code": "EGG", "item_name": "Egg", "qty": 2.0, "uom": "Nos"}],
			),
			patch.object(api, "get_recipe", return_value={"success": True}),
			patch.object(api.frappe, "db", db),
			patch.object(api.frappe, "get_doc", return_value=bundle) as get_doc,
			patch.object(api.frappe, "clear_document_cache"),
		):
			result = api.create_recipe("DISH", [{"item_code": "EGG", "qty": 2}], "Acme")
		return result, db, bundle, get_doc

	def test_a_dish_with_no_stock_history_becomes_a_non_stock_item_with_a_bundle(self):
		_result, db, bundle, get_doc = self.create({"is_stock_item": 1})
		db.set_value.assert_called_once_with("Item", "DISH", "is_stock_item", 0)
		bundle.insert.assert_called_once()
		row = get_doc.call_args.args[0]["items"][0]
		self.assertEqual((row["item_code"], row["qty"], row["uom"]), ("EGG", 2.0, "Nos"))

	def test_a_dish_that_is_already_not_stocked_is_left_as_it_is(self):
		_result, db, _bundle, _get = self.create({"is_stock_item": 0})
		db.set_value.assert_not_called()

	def test_a_dish_with_stock_history_points_to_the_helper(self):
		with self.assertRaises(frappe.ValidationError) as error:
			self.create({"is_stock_item": 1}, history=True)
		self.assertIn("replace_dish_item", str(error.exception))

	def test_a_dish_that_has_a_recipe_is_not_made_again(self):
		with self.assertRaises(frappe.ValidationError):
			self.create({"is_stock_item": 0}, bundles=("DISH",))

	def test_another_businesss_dish_is_not_found(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.create({"is_stock_item": 0}, company="Other")


class ReplaceDishCase(unittest.TestCase):
	def test_an_item_with_no_stock_history_needs_no_replacing(self):
		item = frappe._dict(name="OLD", custom_company="Acme", is_stock_item=1)
		with (
			patch.object(api, "require_catalogue_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_dish_for", return_value=item),
			patch.object(api, "_has_stock_history", return_value=False),
			self.assertRaises(frappe.ValidationError),
		):
			api.replace_dish_item("OLD", company="Acme")

	def test_a_taken_code_is_refused(self):
		item = frappe._dict(name="OLD", custom_company="Acme", is_stock_item=1)
		with (
			patch.object(api, "require_catalogue_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_dish_for", return_value=item),
			patch.object(api, "_has_stock_history", return_value=True),
			patch.object(api.frappe, "db", _db({"OLD-RECIPE": EGG})),
			self.assertRaises(frappe.ValidationError),
		):
			api.replace_dish_item("OLD", company="Acme")

	def test_the_new_item_takes_the_barcodes_prices_and_the_old_one_is_switched_off(self):
		item = frappe._dict(name="OLD", custom_company="Acme", is_stock_item=1)
		source = MagicMock()
		source.get.return_value = [MagicMock(barcode="123")]
		new = MagicMock(standard_rate=90)
		new.name = "OLD-RECIPE"
		price = MagicMock()
		with (
			patch.object(api, "require_catalogue_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_dish_for", return_value=item),
			patch.object(api, "_has_stock_history", return_value=True),
			patch.object(api, "relax_etims_mandatory"),
			patch.object(api.frappe, "db", _db({})),
			patch.object(api.frappe, "get_doc", return_value=source),
			patch.object(api.frappe, "copy_doc", return_value=new),
			patch.object(
				api.frappe, "get_all", return_value=[{"price_list": "Retail", "price_list_rate": 90}]
			),
			patch.object(api.frappe, "new_doc", return_value=price),
		):
			result = api.replace_dish_item("OLD", company="Acme")
		self.assertEqual(result["data"], {"old_item": "OLD", "new_item": "OLD-RECIPE", "barcodes_moved": 1})
		self.assertEqual(new.is_stock_item, 0)
		self.assertEqual(source.disabled, 1)
		new.db_set.assert_called_once_with("standard_rate", 90)
		new.append.assert_called_once_with("barcodes", {"barcode": "123"})
		price.insert.assert_called_once()


@patch.object(api, "flt", _flt)
class WastageCase(unittest.TestCase):
	def expand(self, items, bundles=(), parts=()):
		with (
			patch.object(
				api.frappe, "db", _db({"EGG": EGG, "DISH": SERVICE, "SVC": SERVICE}, bundles=bundles)
			),
			patch.object(api, "ensure_record_access"),
			patch.object(api.frappe, "get_all", return_value=list(parts)),
		):
			return api.expand_wastage(items)

	def test_a_stock_item_is_taken_out_as_itself(self):
		self.assertEqual(
			self.expand([{"item_code": "EGG", "qty": 3}, {"item_code": "EGG", "qty": 2}]), {"EGG": 5}
		)

	def test_a_dish_is_taken_out_as_its_ingredients(self):
		parts = [frappe._dict(item_code="FLR", qty=0.25), frappe._dict(item_code="EGG", qty=2)]
		wanted = self.expand(
			[{"item_code": "DISH", "qty": 3}, {"item_code": "EGG", "qty": 1}], bundles=("DISH",), parts=parts
		)
		self.assertEqual(wanted, {"FLR": 0.75, "EGG": 7})

	def test_an_item_with_no_stock_and_no_recipe_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.expand([{"item_code": "SVC", "qty": 1}])

	def test_bad_rows_are_refused(self):
		for items in ([], [{"item_code": "NOPE", "qty": 1}], [{"item_code": "EGG", "qty": 0}]):
			with self.subTest(items=items), self.assertRaises(frappe.ValidationError):
				self.expand(items)

	def record(self, reason="Dropped tray", warehouse_company="Acme", branch=None, **kwargs):
		entry = MagicMock(total_amount=100)
		entry.name = "MAT-1"
		entry.meta.has_field.return_value = True
		entry.items = [MagicMock()]
		entry.items[0].meta.has_field.return_value = True
		with (
			patch.object(api, "require_stock_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "expand_wastage", return_value={"EGG": 5.0}),
			patch.object(api, "_wastage_type", return_value="Wastage"),
			patch.object(api, "_default_branch", return_value=branch),
			patch.object(api, "nowdate", return_value="2026-10-12"),
			patch.object(
				api.frappe,
				"db",
				_db(
					{"EGG": EGG},
					exists={"Warehouse": True},
					values={("Warehouse", "company"): warehouse_company},
				),
			),
			patch.object(api.frappe, "get_doc", return_value=entry) as get_doc,
		):
			result = api.record_wastage([{"item_code": "EGG", "qty": 5}], "Stores", reason, "Acme", **kwargs)
		return result, entry, get_doc

	def test_a_wastage_entry_is_a_submitted_material_issue_with_the_reason(self):
		result, entry, get_doc = self.record(ticket="K-12")
		values = get_doc.call_args.args[0]
		self.assertEqual(
			(values["stock_entry_type"], values["purpose"], values["from_warehouse"]),
			("Wastage", "Material Issue", "Stores"),
		)
		self.assertEqual(values["remarks"], "Wastage: Dropped tray (kitchen ticket K-12)")
		entry.insert.assert_called_once()
		entry.submit.assert_called_once()
		self.assertEqual(result["data"]["items"], [{"item_code": "EGG", "qty": 5.0}])

	def test_the_branch_of_an_etims_business_is_put_on_the_entry(self):
		_result, entry, _get = self.record(branch="00")
		self.assertEqual(entry.branch, "00")
		self.assertEqual(entry.items[0].branch, "00")

	def test_a_reason_is_required_and_kept_short(self):
		for reason in ("", "   ", "x" * (api.MAX_REASON + 1)):
			with self.subTest(reason=reason[:5]), self.assertRaises(frappe.ValidationError):
				self.record(reason=reason)

	def test_a_warehouse_of_another_business_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.record(warehouse_company="Other")

	def test_the_branch_comes_from_the_active_etims_settings_then_the_dimension_default(self):
		def lookup(doctype, filters, fieldname=None, **kwargs):
			return {
				"Navari KRA eTims Settings": "01",
				"Accounting Dimension": "Branch",
				"Accounting Dimension Detail": "00",
			}.get(doctype)

		db = MagicMock()
		db.exists.return_value = True
		db.get_value.side_effect = lookup
		with patch.object(api.frappe, "db", db):
			self.assertEqual(api._default_branch("Acme"), "01")
		db.get_value.side_effect = lambda doctype, *a, **k: (
			None if doctype == "Navari KRA eTims Settings" else lookup(doctype, a[0])
		)
		with patch.object(api.frappe, "db", db):
			self.assertEqual(api._default_branch("Acme"), "00")

	def test_a_business_with_no_branch_dimension_has_no_branch(self):
		db = MagicMock()
		db.exists.return_value = False
		db.get_value.return_value = None
		with patch.object(api.frappe, "db", db):
			self.assertIsNone(api._default_branch("Acme"))

	def test_the_report_adds_up_by_item_and_lists_the_entries(self):
		entries = [
			frappe._dict(
				name="E1",
				posting_date="2026-10-10",
				remarks="Wastage: Dropped tray",
				total_amount=60,
				from_warehouse="S",
			),
			frappe._dict(
				name="E2",
				posting_date="2026-10-09",
				remarks="Wastage: Burnt",
				total_amount=40,
				from_warehouse="S",
			),
		]
		lines = [
			frappe._dict(item_code="EGG", item_name="Egg", qty=5, uom="Nos", basic_amount=50),
			frappe._dict(item_code="EGG", item_name="Egg", qty=3, uom="Nos", basic_amount=30),
			frappe._dict(item_code="FLR", item_name="Flour", qty=0.5, uom="Kg", basic_amount=20),
		]
		with (
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "get_all", side_effect=[entries, lines]),
		):
			data = api.get_wastage_report("Acme", "2026-10-01", "2026-10-31")["data"]
		self.assertEqual((data["total_value"], data["entries_count"]), (100.0, 2))
		self.assertEqual(
			[(i["item_code"], i["qty"], i["value"]) for i in data["items"]],
			[("EGG", 8.0, 80.0), ("FLR", 0.5, 20.0)],
		)
		self.assertEqual(data["entries"][0]["reason"], "Dropped tray")


@patch.object(api, "flt", _flt)
class ReadCase(unittest.TestCase):
	def test_a_recipe_adds_up_the_cost_and_the_margin(self):
		parts = [
			frappe._dict(item_code="FLR", description="", qty=0.25, uom="Kg"),
			frappe._dict(item_code="EGG", description="", qty=2, uom="Nos"),
		]
		with (
			patch.object(api.frappe, "get_all", return_value=parts),
			patch.object(api.frappe, "db", _db({"FLR": FLOUR, "EGG": EGG})),
			patch.object(
				api,
				"_valuation",
				side_effect=lambda code, warehouse, company: {"FLR": 100.0, "EGG": 10.0}[code],
			),
		):
			rows, cost, known = api._recipe_rows("DISH", None, "Acme")
		self.assertEqual((cost, known), (45.0, True))
		self.assertEqual([(r["item_code"], r["cost"]) for r in rows], [("FLR", 25.0), ("EGG", 20.0)])

	def test_a_missing_cost_is_flagged(self):
		parts = [frappe._dict(item_code="FLR", description="", qty=1, uom="Kg")]
		with (
			patch.object(api.frappe, "get_all", return_value=parts),
			patch.object(api.frappe, "db", _db({"FLR": FLOUR})),
			patch.object(api, "_valuation", return_value=0.0),
		):
			_rows, cost, known = api._recipe_rows("DISH", None, "Acme")
		self.assertEqual((cost, known), (0.0, False))

	def test_the_cost_of_an_ingredient_is_the_stores_then_the_average_then_the_items_own(self):
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, *a, **k: (
			80.0 if doctype == "Bin" else frappe._dict(valuation_rate=5, last_purchase_rate=6)
		)
		with patch.object(api.frappe, "db", db):
			self.assertEqual(api._valuation("FLR", "Stores"), 80.0)
		db.get_value.side_effect = lambda doctype, *a, **k: (
			None if doctype == "Bin" else frappe._dict(valuation_rate=0, last_purchase_rate=6)
		)
		bins = [frappe._dict(actual_qty=10, stock_value=1000), frappe._dict(actual_qty=10, stock_value=800)]
		with (
			patch.object(api.frappe, "db", db),
			patch.object(api.frappe, "get_all", side_effect=[["S1", "S2"], bins]),
		):
			self.assertEqual(api._valuation("FLR", None, "Acme"), 90.0)
		with patch.object(api.frappe, "db", db):
			self.assertEqual(api._valuation("FLR", "Stores"), 6.0)


if __name__ == "__main__":
	unittest.main()
