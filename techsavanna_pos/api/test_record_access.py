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

from techsavanna_pos.api import access_control

API = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(API))

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager", "Stock Manager")

# Every call that opens a record by its number, and the kinds of record it checks.
# "variable" means the kind of record is decided while the call runs (an invoice or a POS invoice).
EXPECTED_GUARDS = {
	"inventory_api": {
		"delete_inventory_discount_rule": ("Inventory Discount Rule",),
		"get_inventory_discount_rule": ("Inventory Discount Rule",),
		"get_stock_entry_details": ("Stock Entry",),
		"update_stock_entry": ("Stock Entry",),
		"cancel_stock_entry": ("Stock Entry",),
		"submit_stock_entry": ("Stock Entry",),
		"add_sales_person_stock_take": ("Stock Reconciliation",),
		"add_stock_controller_stock_take": ("Stock Reconciliation",),
		"add_stock_manager_stock_take_and_submit": ("Stock Reconciliation",),
		"get_multi_level_stock_reconciliation": ("Stock Reconciliation",),
	},
	"invoices": {
		"validate_cart_items": ("POS Profile",),
		"update_invoice": ("POS Profile",),
		"submit_invoice": ("POS Profile", "variable"),
		"get_invoice_for_return": ("Sales Invoice",),
	},
	"items": {
		"search_by_barcode": ("POS Profile",),
		"get_item_stock": ("Warehouse",),
		"get_batch_serial_details": ("Item",),
		"get_item_variants": ("POS Profile",),
		"get_items": ("POS Profile",),
		"get_item_details": ("Item", "POS Profile"),
		"get_stock_quantities": ("Warehouse",),
		"get_product_bundle_availability": ("Warehouse",),
		"return_items": ("Stock Entry",),
	},
	"onboarding_api": {
		"update_pos_profile": ("POS Profile",),
	},
	"product_api": {
		"delete_product": ("Item",),
		"enable_product": ("Item",),
		"add_barcode": ("Item",),
		"remove_barcode": ("Item",),
		"create_product_variant": ("Item",),
		"get_product_variants": ("Item",),
		"set_product_warranty": ("Item",),
		"get_product_warranty": ("Item",),
	},
	"purchase": {
		"create_purchase_order": ("Supplier",),
		"create_grn": ("Purchase Order",),
		"create_stock_receipt": ("Purchase Receipt",),
		"get_grn_details": ("Purchase Receipt",),
		"create_purchase_invoice": ("variable",),
		"create_purchase_invoice_from_grn": ("Purchase Receipt",),
		"create_delivery_note_from_po": ("Purchase Order",),
		"pay_purchase_invoice": ("Purchase Invoice",),
	},
	"purchase_receipt": {
		"reject_return_items": ("Purchase Receipt",),
	},
	"reports": {
		"grn_detail_report": ("Purchase Receipt",),
	},
	"sales_api": {
		"get_sales_invoice": ("Sales Invoice",),
		"cancel_sales_invoice": ("Sales Invoice",),
		"get_pos_invoice": ("POS Invoice",),
		"cancel_pos_invoice": ("POS Invoice",),
		"cancel_sales_return": ("Sales Invoice",),
		"submit_invoice": ("variable",),
		"get_pos_opening_entry": ("POS Opening Entry",),
		"create_payment_entry_for_invoice": ("Sales Invoice",),
		"get_invoice_payment_status": ("Sales Invoice",),
	},
	"stock": {
		"get_low_stock_items": ("Warehouse",),
		"start_stock_count": ("Warehouse",),
		"add_stock_count_items": ("Stock Reconciliation",),
		"complete_stock_count": ("Stock Reconciliation",),
		"create_pr_from_po": ("Purchase Order",),
		"cancel_purchase_order": ("Purchase Order",),
		"approve_stock_transfer_workflow": ("Material Request",),
		"dispatch_stock": ("Material Request",),
		"receive_stock_destination": ("Material Request",),
		"get_stock_transfer_request": ("Material Request",),
		"confirm_receive_transfer": ("Stock Entry",),
	},
	"warehouse_api": {
		"get_warehouse_details": ("Warehouse",),
		"update_warehouse": ("Warehouse",),
		"assign_warehouses_to_staff": ("Warehouse",),
		"get_warehouse_staff": ("Warehouse",),
	},
}

# Calls that must be told which kind of record the number is, before they look at it
CALL_NEEDS = {("purchase", "create_purchase_invoice"): {"reference_type": "Purchase Order"}}


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
			patch("techsavanna_pos.api.access_control.get_user_companies", return_value=set(companies)),
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
			kinds = sorted({kind for kind, _arg in _checks_in(node)})
			if kinds:
				actual.setdefault(module, {})[name] = tuple(kinds)
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
				module = importlib.import_module(f"techsavanna_pos.api.{module_name}")
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
		self.assertGreater(tested, 30, "expected many calls to be exercised")
		if skipped:
			self.skipTest(f"ran {tested}; not importable without ERPNext: {sorted(set(skipped))}")


def _doctype_files():
	"""Folder name -> doctype JSON path, for ERPNext (when installed) and this app."""
	roots = [os.path.join(APP, "techsavanna_pos")]
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
	path = os.path.join(APP, "techsavanna_pos", "techsavanna_pos", "custom", frappe.scrub(doctype) + ".json")
	if not os.path.exists(path):
		return set()
	with open(path) as handle:
		return {f.get("fieldname") for f in json.load(handle).get("custom_fields", [])}


class TestTheOwnerFieldExists(unittest.TestCase):
	"""A check on a field the document type does not have would silently let every record through."""

	def kinds(self):
		kinds = {k for calls in EXPECTED_GUARDS.values() for kinds in calls.values() for k in kinds}
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
