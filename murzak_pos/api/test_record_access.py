"""
Tests for ensure_record_access: records opened by their number must belong to the caller's business.

No database is used; every Frappe call is mocked. Three things are checked:

1. the helper itself (own business passes, another business is refused, unowned records are left alone);
2. every call listed below really refuses a record owned by another business, and the list is kept in step
   with the source, so a new lookup that skips the check, or a check that is removed, fails here;
3. the field the check reads (`company`, or `custom_company` for items, customers and suppliers) exists on
   each document type it is used for.
"""

import ast
import glob
import importlib
import inspect
import json
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import access_control

API = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(API))

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager", "Stock Manager")

# Every call that opens a record by its number, and the kinds of record it checks.
# Each entry reads "kind of record, then the value checked". "variable" means the kind of record is decided
# while the call runs (an invoice or a POS invoice).
EXPECTED_GUARDS = {
	"print_api": {"get_print": ("variable name",)},
	"profit_api": {"get_profit_summary": ("Warehouse warehouse",)},
	"sales_person_api": {
		"update_sales_person": ("variable name",),
		"get_commission_report": ("variable sales_person",),
	},
	"recipes_api": {
		"clean_ingredients": ("Item code",),
		"_dish_for": ("Item dish",),
		"get_recipe": ("Warehouse warehouse",),
		"expand_wastage": ("Item code",),
		"record_wastage": ("Warehouse warehouse",),
		"get_wastage_report": ("Warehouse warehouse",),
	},
	"purchase_orders_api": {
		"_order_lines": ("Item code",),
		"upsert_purchase_order": ("Purchase Order name", "Supplier supplier"),
		"cancel_purchase_invoice": ("Purchase Invoice name",),
	},
	"purchase_receipts_api": {
		"create_purchase_receipt": ("Item code", "Supplier supplier"),
		"get_purchase_receipt": ("Purchase Receipt name",),
		"create_purchase_return": ("Purchase Invoice return_against",),
	},
	"dashboard_api": {
		"get_dashboard_metrics": ("Warehouse warehouse",),
	},
	"expiry_api": {
		"get_expiry_alerts": ("Warehouse warehouse",),
	},
	"held_sales_api": {
		"list_held_sales": ("Warehouse warehouse",),
		"save_held_sale": ("Warehouse warehouse",),
	},
	"inventory_api": {
		"get_stock_balance_api": ("Warehouse warehouse",),
		"get_stock_balance_multiple": ("Warehouse warehouse",),
		"create_stock_entry": ("Warehouse from_warehouse", "Warehouse to_warehouse"),
		"create_material_receipt": ("Warehouse target_warehouse",),
		"create_material_issue": ("Warehouse source_warehouse",),
		"create_material_transfer": ("Warehouse source_warehouse", "Warehouse target_warehouse"),
		"create_stock_reconciliation": ("Warehouse warehouse",),
		"check_stock_availability": ("Warehouse warehouse",),
		"get_stock_ledger_entries": ("Warehouse warehouse",),
		"get_stock_summary": ("Warehouse warehouse",),
		"repost_stock": ("Warehouse warehouse",),
		"get_low_stock_items": ("Warehouse warehouse",),
		"get_inventory_item_details": ("Warehouse warehouse",),
		"list_inventory_items": ("Warehouse warehouse",),
		"update_inventory_item_details": ("Warehouse warehouse",),
		"create_inventory_discount_rule": ("Item item_code", "Warehouse warehouse"),
		"update_inventory_discount_rule": (
			"Inventory Discount Rule name",
			"Item item_code",
			"Pricing Rule name",
			"Warehouse warehouse",
		),
		"delete_inventory_discount_rule": ("Inventory Discount Rule name", "Pricing Rule name"),
		"get_inventory_discount_rule": ("Inventory Discount Rule name", "Pricing Rule name"),
		"list_inventory_discount_rules": ("Warehouse warehouse",),
		"get_inventory_discount_for_item": ("Warehouse warehouse",),
		"bulk_get_inventory_discounts": ("Warehouse warehouse",),
		"list_stock_entries": ("Warehouse warehouse",),
		"list_material_receipts": ("Warehouse warehouse",),
		"list_material_issues": ("Warehouse warehouse",),
		"list_material_transfers": ("Warehouse warehouse",),
		"get_stock_entry_details": ("Stock Entry stock_entry_name",),
		"update_stock_entry": (
			"Stock Entry stock_entry_name",
			"Warehouse from_warehouse",
			"Warehouse to_warehouse",
		),
		"cancel_stock_entry": ("Stock Entry stock_entry_name",),
		"submit_stock_entry": ("Stock Entry stock_entry_name",),
		"create_multi_level_stock_reconciliation": ("Warehouse warehouse",),
		"add_sales_person_stock_take": ("Stock Reconciliation reconciliation_name",),
		"add_stock_controller_stock_take": ("Stock Reconciliation reconciliation_name",),
		"add_stock_manager_stock_take_and_submit": ("Stock Reconciliation reconciliation_name",),
		"get_multi_level_stock_reconciliation": ("Stock Reconciliation reconciliation_name",),
		"list_multi_level_stock_reconciliations": ("Warehouse warehouse",),
	},
	"items": {
		"search_by_barcode": ("POS Profile pos_profile",),
		"get_item_stock": ("Warehouse warehouse",),
		"get_batch_serial_details": ("Item item_code", "Warehouse warehouse"),
		"get_item_variants": ("POS Profile pos_profile",),
		"get_items": ("POS Profile pos_profile",),
		"get_item_details": ("Item item_code", "POS Profile pos_profile"),
		"get_stock_quantities": ("Warehouse warehouse",),
		"get_product_bundle_availability": ("Warehouse warehouse",),
		"return_items": ("Stock Entry grn_name",),
	},
	"kitchen_api": {
		"send_tickets": ("Warehouse warehouse",),
		"list_tickets": ("Warehouse warehouse",),
	},
	"onboarding_api": {
		"update_company": ("Warehouse default_warehouse",),
		"create_pos_profile": ("Price List selling_price_list", "Warehouse warehouse"),
		"update_pos_profile": (
			"POS Profile name",
			"POS Profile profile_name",
			"Price List selling_price_list",
			"Warehouse warehouse",
		),
	},
	"product_api": {
		"get_products": ("Price List price_list", "Warehouse warehouse"),
		"update_product": ("Warehouse warehouse",),
		"delete_product": ("Item item_code",),
		"enable_product": ("Item item_code",),
		"add_barcode": ("Item item_code",),
		"remove_barcode": ("Item item_code",),
		"get_product_price": ("Price List price_list",),
		"set_product_price": ("Price List price_list",),
		"get_stock_quantity": ("Warehouse warehouse",),
		"bulk_update_prices": ("Price List price_list",),
		"create_product_variant": ("Item template_item_code",),
		"get_product_variants": ("Item template_item_code",),
		"bulk_import_opening_stock": ("Warehouse warehouse",),
		"update_price_list": ("Price List price_list_doc.name",),
		"delete_price_list": ("Price List price_list_name",),
		"set_product_warranty": ("Item item_code",),
		"get_product_warranty": ("Item item_code",),
	},
	"purchase": {
		"create_purchase_order": ("Supplier supplier",),
		"create_grn": ("Purchase Order data.get('lpo_no')", "Warehouse data.get('warehouse')"),
		"create_stock_receipt": ("Purchase Receipt grn_no",),
		"get_grn_details": ("Purchase Receipt grn_no",),
		"create_purchase_invoice": ("variable reference_name",),
		"create_purchase_invoice_from_grn": ("Purchase Receipt grn_no",),
		"create_delivery_note_from_po": ("Purchase Order purchase_order_name",),
		"pay_purchase_invoice": ("Purchase Invoice invoice_no",),
	},
	"purchase_receipt": {
		"reject_return_items": ("Purchase Receipt purchase_receipt",),
	},
	"reports": {
		"inventory_summary_report": ("Warehouse warehouse",),
		"inventory_movement_report": ("Warehouse warehouse",),
		"stock_aging_report": ("Warehouse warehouse",),
		"inventory_value_report": ("Warehouse warehouse",),
		"sales_analytics_report": ("Warehouse warehouse",),
		"export_sales_analytics_report": ("Warehouse warehouse",),
		"inventory_value_by_category_report": ("Warehouse warehouse",),
		"inventory_cost_method_comparison_report": ("Warehouse warehouse",),
		"inventory_value_trends_report": ("Warehouse warehouse",),
		"inventory_turnover_report": ("Warehouse warehouse",),
		"inventory_days_on_hand_report": ("Warehouse warehouse",),
		"inventory_movement_patterns_report": ("Warehouse warehouse",),
		"inventory_obsolescence_risk_report": ("Warehouse warehouse",),
		"inventory_aging_recommendations_report": ("Warehouse warehouse",),
		"inventory_accuracy_report": ("Warehouse warehouse",),
		"inventory_variance_report": ("Warehouse warehouse",),
		"inventory_adjustment_trends_report": ("Warehouse warehouse",),
		"inventory_transfer_efficiency_report": ("Warehouse from_warehouse", "Warehouse to_warehouse"),
		"grn_list_report": ("Warehouse warehouse",),
		"grn_detail_report": ("Purchase Receipt grn_no",),
	},
	"sales_api": {
		"create_sales_invoice": ("Warehouse warehouse",),
		"create_pos_invoice": ("Warehouse warehouse",),
		"get_sales_invoice": ("Sales Invoice name",),
		"cancel_sales_invoice": ("Sales Invoice name",),
		"get_pos_invoice": ("POS Invoice name",),
		"cancel_pos_invoice": ("POS Invoice name",),
		"cancel_sales_return": ("Sales Invoice name",),
		"submit_invoice": ("variable name",),
		"get_pos_opening_entry": ("POS Opening Entry name",),
		"create_payment_entry_for_invoice": ("Sales Invoice sales_invoice",),
		"get_invoice_payment_status": ("Sales Invoice sales_invoice",),
	},
	"stock": {
		"create_stock_transfer": (
			"Warehouse data.get('from_warehouse')",
			"Warehouse data.get('to_warehouse')",
		),
		"create_stock_adjustment": ("Warehouse data.get('warehouse')",),
		"create_pos_delivery_note": ("Warehouse data.get('warehouse')",),
		"create_stock_receipt": ("Warehouse data.get('to_warehouse')",),
		"get_low_stock_items": ("Warehouse warehouse",),
		"start_stock_count": ("Warehouse data['warehouse']",),
		"add_stock_count_items": ("Stock Reconciliation count_id",),
		"complete_stock_count": ("Stock Reconciliation count_id",),
		"create_pr_from_po": ("Purchase Order purchase_order",),
		"cancel_purchase_order": ("Purchase Order purchase_order",),
		"approve_stock_transfer_workflow": ("Material Request request_id",),
		"dispatch_stock": ("Material Request request_id", "Warehouse data.get('origin_warehouse')"),
		"receive_stock_destination": (
			"Material Request request_id",
			"Warehouse data.get('destination_warehouse')",
		),
		"create_stock_transfer_request": (
			"Warehouse data.get('from_warehouse')",
			"Warehouse data.get('to_warehouse')",
		),
		"get_stock_transfer_request": ("Material Request request_id",),
		"create_stock_entry_old": ("Warehouse from_warehouse", "Warehouse to_warehouse"),
		"create_stock_entry": ("Warehouse from_warehouse", "Warehouse to_warehouse"),
		"confirm_receive_transfer": ("Stock Entry stock_entry_name",),
	},
	"warehouse_api": {
		"create_warehouse": ("Warehouse parent_warehouse",),
		"list_warehouses": ("Warehouse parent_warehouse",),
		"get_warehouse_details": ("Warehouse name",),
		"update_warehouse": ("Warehouse name", "Warehouse parent_warehouse"),
		"assign_warehouses_to_staff": ("Warehouse warehouse",),
		"get_warehouse_staff": ("Warehouse warehouse",),
		"remove_warehouse_from_staff": ("Warehouse warehouse",),
		"set_default_warehouse": ("Warehouse warehouse",),
	},
}

# Calls that must be told which kind of record the number is, before they look at it
CALL_NEEDS = {
	("purchase", "create_purchase_invoice"): {"reference_type": "Purchase Order"},
	("print_api", "get_print"): {"doctype": "Sales Invoice"},
}


class RecordAccessCase(unittest.TestCase):
	def as_user(self, user="owner@shop-a.test", companies=("Shop A",), owner="Shop B", has_column=True):
		"""Sign in as `user` of `companies`; every record looked up is owned by `owner`."""
		db = MagicMock()
		db.exists.return_value = True
		db.has_column.return_value = has_column
		db.get_value.return_value = owner
		self.db = db
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(OWNER_ROLES)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "log_error", create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("murzak_pos.api.access_control.get_user_companies", return_value=set(companies)),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestEnsureRecordAccess(RecordAccessCase):
	def test_a_record_of_another_business_is_refused(self):
		self.as_user(companies=("Shop A",), owner="Shop B")
		with self.assertRaises(frappe.PermissionError):
			access_control.ensure_record_access("Sales Invoice", "SINV-0001")

	def test_a_record_of_your_own_business_is_allowed(self):
		self.as_user(companies=("Shop A",), owner="Shop A")
		access_control.ensure_record_access("Sales Invoice", "SINV-0001")

	def test_a_user_of_several_businesses_may_open_any_of_them(self):
		self.as_user(companies=("Shop A", "Shop B"), owner="Shop B")
		access_control.ensure_record_access("Stock Entry", "STE-0001")

	def test_a_record_with_no_owner_is_left_to_the_call(self):
		self.as_user(owner=None)
		access_control.ensure_record_access("Item", "OLD-ITEM")

	def test_a_record_that_does_not_exist_is_left_to_the_call(self):
		self.as_user(owner=None)  # a missing record has no owner to compare
		access_control.ensure_record_access("Sales Invoice", "NO-SUCH")

	def test_the_administrator_may_open_anything(self):
		self.as_user(user="Administrator", companies=(), owner="Shop B")
		access_control.ensure_record_access("Sales Invoice", "SINV-0001")
		self.db.get_value.assert_not_called()

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user(user="Guest")
		with self.assertRaises(frappe.AuthenticationError):
			access_control.ensure_record_access("Sales Invoice", "SINV-0001")

	def test_nothing_is_looked_up_when_no_number_was_given(self):
		self.as_user()
		for empty in (None, "", 0):
			access_control.ensure_record_access("Sales Invoice", empty)
		self.db.get_value.assert_not_called()

	def test_a_value_that_is_not_a_record_number_is_ignored(self):
		self.as_user()
		access_control.ensure_record_access("Sales Invoice", {"name": "SINV-0001"})
		access_control.ensure_record_access("Sales Invoice", ["SINV-0001"])
		self.db.get_value.assert_not_called()

	def test_a_document_type_without_the_owner_field_is_left_alone(self):
		self.as_user(has_column=False)
		access_control.ensure_record_access("Sales Invoice", "SINV-0001")
		self.db.get_value.assert_not_called()

	def test_items_customers_and_suppliers_are_read_from_custom_company(self):
		self.as_user()
		for doctype in ("Item", "Customer", "Supplier"):
			self.db.has_column.reset_mock()
			with self.assertRaises(frappe.PermissionError):
				access_control.ensure_record_access(doctype, "X")
			self.db.has_column.assert_called_with(doctype, "custom_company")

	def test_other_records_are_read_from_company(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			access_control.ensure_record_access("Warehouse", "Stores - B")
		self.db.has_column.assert_called_with("Warehouse", "company")

	def test_the_message_names_the_kind_of_record(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError) as raised:
			access_control.ensure_record_access("Warehouse", "Stores - B")
		self.assertIn("Warehouse", str(raised.exception))


def _function_nodes():
	"""(module, name) -> function node, for every function in the api folder."""
	found = {}
	for path in sorted(glob.glob(os.path.join(API, "*.py"))):
		name = os.path.basename(path)[:-3]
		if name.startswith(("test_", "__")) or name == "access_control":
			continue
		with open(path) as handle:
			tree = ast.parse(handle.read())
		for node in tree.body:
			if isinstance(node, ast.FunctionDef):
				found[(name, node.name)] = node  # the last definition wins, as in Python
	return found


def _checks_in(node):
	"""The (kind of record, argument) pairs a function passes to ensure_record_access."""
	pairs = []
	for call in ast.walk(node):
		if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "ensure_record_access":
			kind = call.args[0].value if isinstance(call.args[0], ast.Constant) else "variable"
			pairs.append((kind, call.args[1]))
	return pairs


class TestTheListMatchesTheSource(unittest.TestCase):
	def test_every_listed_call_makes_its_checks_and_no_unlisted_call_does(self):
		actual = {}
		for (module, name), node in _function_nodes().items():
			checks = sorted({f"{kind} {ast.unparse(arg)}" for kind, arg in _checks_in(node)})
			if checks:
				actual.setdefault(module, {})[name] = tuple(checks)
		expected = {
			m: {n: tuple(sorted(k)) for n, k in calls.items()} for m, calls in EXPECTED_GUARDS.items()
		}
		self.assertEqual(
			actual,
			expected,
			"a call that opens a record by its number lost its check, or a new one has it; update EXPECTED_GUARDS",
		)

	def test_the_check_runs_before_anything_is_changed(self):
		"""The check is a plain statement near the top, not buried after the work has begun."""
		for (module, name), node in _function_nodes().items():
			pairs = _checks_in(node)
			if not pairs:
				continue
			statements = [s for s in ast.walk(node) if isinstance(s, ast.stmt)]
			first_check = min(
				s.lineno
				for s in statements
				if isinstance(s, ast.Expr)
				and isinstance(s.value, ast.Call)
				and getattr(s.value.func, "id", "") == "ensure_record_access"
			)
			writes = [
				c.lineno
				for c in ast.walk(node)
				if isinstance(c, ast.Call)
				and isinstance(c.func, ast.Attribute)
				and c.func.attr in ("save", "submit", "cancel", "insert", "delete", "delete_doc", "set_value")
				and c.lineno < first_check
			]
			with self.subTest(call=f"{module}.{name}"):
				self.assertEqual(writes, [], "this call changes something before it checks the record")


class TestEachCallRefusesAnotherBusinessesRecord(RecordAccessCase):
	"""For every call whose record number is one of its own parameters: a record of Shop B is refused for Shop A."""

	def test_each_one_refuses_a_record_of_shop_b(self):
		self.as_user(companies=("Shop A",), owner="Shop B")
		nodes = _function_nodes()
		tested, skipped = 0, []
		for module_name, calls in EXPECTED_GUARDS.items():
			try:
				module = importlib.import_module(f"murzak_pos.api.{module_name}")
			except ImportError:  # needs ERPNext, which only a full bench has
				skipped.append(module_name)
				continue
			for name in calls:
				node = nodes[(module_name, name)]
				parameters = {a.arg for a in node.args.args + node.args.kwonlyargs}
				argument_names = [
					arg.id
					for _kind, arg in _checks_in(node)
					if isinstance(arg, ast.Name) and arg.id in parameters
				]
				if not argument_names:
					continue  # the number arrives in the request body: covered by the list test above
				function = inspect.unwrap(getattr(module, name))
				arguments = {
					p.name: "x"
					for p in inspect.signature(function).parameters.values()
					if p.default is inspect.Parameter.empty
					and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
				}
				for argument in argument_names:
					arguments[argument] = "x"
				arguments.update(CALL_NEEDS.get((module_name, name), {}))
				with self.subTest(call=f"{module_name}.{name}"), self.assertRaises(frappe.PermissionError):
					function(**arguments)
				tested += 1
		self.assertGreater(tested, 100, "expected many calls to be exercised")
		if skipped:
			self.skipTest(f"ran {tested}; not importable without ERPNext: {sorted(set(skipped))}")


def _doctype_files():
	"""Folder name -> doctype JSON path, for ERPNext (when installed) and this app."""
	roots = [os.path.join(APP, "murzak_pos")]
	try:
		import erpnext

		roots.append(os.path.dirname(erpnext.__file__))
	except ImportError:
		pass
	found = {}
	for root in roots:
		for path in glob.glob(os.path.join(root, "**", "doctype", "*", "*.json"), recursive=True):
			folder = os.path.basename(os.path.dirname(path))
			if os.path.basename(path)[:-5] == folder:
				found.setdefault(folder, path)
	return found


def _custom_fields(doctype):
	"""Field names this app adds to a standard document type."""
	path = os.path.join(APP, "murzak_pos", "murzak_pos", "custom", frappe.scrub(doctype) + ".json")
	if not os.path.exists(path):
		return set()
	with open(path) as handle:
		return {f.get("fieldname") for f in json.load(handle).get("custom_fields", [])}


class TestTheOwnerFieldExists(unittest.TestCase):
	"""A check on a field the document type does not have would silently let every record through."""

	def kinds(self):
		kinds = {kind for node in _function_nodes().values() for kind, _arg in _checks_in(node)}
		return sorted(kinds - {"variable"} | {"Sales Invoice", "POS Invoice"})

	def test_each_kind_of_record_has_the_field_the_check_reads(self):
		files = _doctype_files()
		missing_files = []
		for kind in self.kinds():
			field = access_control.OWNER_FIELD.get(kind, "company")
			path = files.get(frappe.scrub(kind))
			if path is None:
				missing_files.append(kind)
				continue
			with open(path) as handle:
				fields = {f.get("fieldname") for f in json.load(handle).get("fields", [])}
			with self.subTest(kind=kind):
				self.assertTrue(
					field in fields or field in _custom_fields(kind),
					f"{kind} has no {field} field, so the check on it would let every record through",
				)
		if missing_files:
			self.skipTest(
				f"ran the rest; document type files not found (ERPNext not installed): {missing_files}"
			)

	def test_the_owner_field_map_only_names_fields_this_app_adds(self):
		for kind, field in access_control.OWNER_FIELD.items():
			with self.subTest(kind=kind):
				self.assertIn(field, _custom_fields(kind))


# Calls that read the company from the request body rather than from a parameter
BODY_COMPANY_CALLS = {
	"stock": [
		"create_stock_transfer",
		"create_stock_adjustment",
		"create_pos_delivery_note",
		"create_stock_receipt",
		"start_stock_count",
		"create_stock_transfer_request",
		"list_stock_transfer_requests",
	],
	"purchase": ["create_purchase_order"],
	"reports": ["get_profit_and_loss"],
}


class TestCompanyInTheRequestBodyIsChecked(unittest.TestCase):
	def test_each_call_checks_the_company_it_is_given(self):
		nodes = _function_nodes()
		for module, names in BODY_COMPANY_CALLS.items():
			for name in names:
				with self.subTest(call=f"{module}.{name}"):
					self.assertIn("resolve_company(", ast.unparse(nodes[(module, name)]))


if __name__ == "__main__":
	unittest.main()
