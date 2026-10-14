# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Purchases page calls: the list, the panel, Receive goods and the direct bill. No database is needed:
every Frappe call is mocked, and the order of the steps (what is checked first, what is rolled back, what is
committed) is checked through the mocks.
"""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import purchases_list_api as api

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


class Doc:
	"""A document the way the code reads one: attributes, and get() for a field that may be missing.

	(frappe._dict cannot stand in: its own `items` method hides a document's `items` table.)
	"""

	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def row(**extra):
	base = {
		"item_code": "A",
		"item_name": "Sugar",
		"qty": 10,
		"received_qty": 0,
		"rate": 100,
		"amount": 1000,
		"warehouse": "Stores - A",
		"uom": "Nos",
	}
	base.update(extra)
	return frappe._dict(base)


def order_doc(**extra):
	values = {
		"add_comment": MagicMock(),
		"doctype": "Purchase Order",
		"name": "PUR-ORD-2026-00012",
		"docstatus": 1,
		"status": "To Receive and Bill",
		"company": "Acme",
		"supplier": "SUP-1",
		"supplier_name": "Bidco",
		"transaction_date": date(2026, 10, 1),
		"per_received": 0,
		"per_billed": 0,
		"total": 2000,
		"total_taxes_and_charges": 320,
		"grand_total": 2320,
		"items": [row(), row(item_code="B", item_name="Rice", qty=5, rate=200, amount=1000)],
	}
	values.update(extra)
	return Doc(**values)


def bill_doc(**extra):
	values = {
		"doctype": "Purchase Invoice",
		"name": "ACC-PINV-2026-00007",
		"docstatus": 1,
		"status": "Unpaid",
		"company": "Acme",
		"supplier": "SUP-1",
		"supplier_name": "Bidco",
		"posting_date": date(2026, 10, 2),
		"bill_no": "INV-9",
		"bill_date": date(2026, 10, 1),
		"due_date": date(2026, 11, 1),
		"is_return": 0,
		"total": 1000,
		"total_taxes_and_charges": 0,
		"grand_total": 1000,
		"outstanding_amount": 1000,
		"items": [row(qty=10, purchase_receipt=None)],
	}
	values.update(extra)
	return Doc(**values)


class ApiCase(unittest.TestCase):
	tier = "owner"
	roles = ("System Manager",)

	def setUp(self):
		self.db = MagicMock()
		self.db.exists.return_value = True
		self.db.get_value.side_effect = self.get_value
		self.db.sql.side_effect = self.sql
		self.queries = []
		self.access = MagicMock()
		self.logged = MagicMock()
		self.docs = {}
		self.sql_answers = {}
		stack = [
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "session", frappe._dict(user="owner@acme.test"), create=True),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(self.roles)),
			patch.object(frappe, "log_error", self.logged, create=True),
			patch.object(frappe, "get_doc", create=True, side_effect=self.get_doc),
			patch.object(frappe, "get_all", create=True, return_value=[]),
			patch.object(api.role_tiers, "get_tier", side_effect=lambda: self.tier),
			patch.object(api, "ensure_record_access", self.access),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_now_utc", return_value=NOW),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)

	def get_value(self, doctype, name=None, fieldname=None, *args, **kwargs):
		if doctype == "Purchase Order" and kwargs.get("as_dict"):
			order = self.docs.get(name)
			if order is None:
				return None
			return frappe._dict(
				docstatus=order.docstatus, company=order.company, status=order.status, supplier=order.supplier
			)
		if doctype == "Warehouse":
			return "Acme"
		return None

	def get_doc(self, *args, **kwargs):
		if len(args) == 2 and isinstance(args[1], str):
			doc = self.docs.get(args[1])
			if doc is None:
				raise frappe.DoesNotExistError(args[1])
			return doc
		return MagicMock(
			name="new doc",
			**{"get.side_effect": lambda key, default=None: (args[0] if args else kwargs).get(key, default)},
		)

	def sql(self, query, values=None, as_dict=False):
		self.queries.append((query, dict(values or {})))
		for key, answer in self.sql_answers.items():
			if key in query:
				return answer
		return []


class TestWhoMayUseThePurchasesCalls(ApiCase):
	def test_a_cashier_is_refused_before_anything_is_read(self):
		self.tier = "cashier"
		for call, args in (
			(api.list_purchases, {}),
			(api.get_purchase, {"id": "X"}),
			(api.receive_goods, {"order": "X", "items": []}),
			(api.create_direct_bill, {"supplier": "S", "items": []}),
		):
			with self.subTest(call=call.__name__), self.assertRaises(frappe.PermissionError):
				call(**args)
		self.assertEqual(self.db.method_calls, [])
		self.access.assert_not_called()

	def test_a_guest_is_asked_to_sign_in(self):
		self.tier = None
		with self.assertRaises(frappe.AuthenticationError):
			api.list_purchases()

	def test_a_manager_may_use_the_list(self):
		self.tier = "manager"
		self.assertTrue(api.list_purchases()["success"])

	def test_an_owner_may_use_the_list(self):
		self.assertTrue(api.list_purchases()["success"])

	def test_the_supplier_is_checked_before_the_list_is_read(self):
		api.list_purchases(supplier="SUP-9")
		self.access.assert_any_call("Supplier", "SUP-9")


class ListCase(ApiCase):
	def setUp(self):
		super().setUp()
		self.sql_answers = {
			"COUNT(*) AS n FROM (": [{"n": 45}],
			"ORDER BY p.posting_date": [
				{
					"doctype": "Purchase Order",
					"name": "PUR-ORD-2026-00012",
					"supplier": "SUP-1",
					"supplier_name": "Bidco",
					"posting_date": date(2026, 10, 12),
					"grand_total": 2320,
					"docstatus": 1,
					"status": "To Receive and Bill",
					"is_return": 0,
					"per_received": 0,
					"per_billed": 0,
					"outstanding_amount": 0,
					"due_date": None,
					"bill_no": "",
				},
				{
					"doctype": "Purchase Invoice",
					"name": "ACC-PINV-2026-00007",
					"supplier": "SUP-2",
					"supplier_name": "Unga Ltd",
					"posting_date": date(2026, 10, 11),
					"grand_total": 500,
					"docstatus": 1,
					"status": "Unpaid",
					"is_return": 0,
					"per_received": 0,
					"per_billed": 0,
					"outstanding_amount": 500,
					"due_date": date(2026, 11, 10),
					"bill_no": "INV-9",
				},
			],
			"FROM `tabPurchase Order` po WHERE po.company = %(company)s AND po.docstatus = 1": [{"n": 3}],
			"SUM(pi.outstanding_amount)": [{"total": 12500.5}],
			"SUM(ABS(pi.grand_total))": [{"total": 800}],
		}

	def union_sql(self):
		return next(q for q, _v in self.queries if "SELECT * FROM (" in q)

	def union_values(self):
		return next(v for q, v in self.queries if "SELECT * FROM (" in q)


class TestTheList(ListCase):
	def test_it_returns_rows_total_and_page_information(self):
		data = api.list_purchases()["data"]
		self.assertEqual((data["total"], data["page"], data["page_size"], data["pages"]), (45, 1, 20, 3))
		self.assertEqual([r["number"] for r in data["rows"]], ["#O0012", "#B0007"])

	def test_a_row_carries_supplier_date_total_status_and_what_is_owed(self):
		bill = api.list_purchases()["data"]["rows"][1]
		self.assertEqual(
			(
				bill["supplier"],
				bill["date"],
				bill["total"],
				bill["status"],
				bill["outstanding"],
				bill["due_date"],
			),
			("Unga Ltd", "2026-10-11", 500.0, "billed", 500.0, "2026-11-10"),
		)

	def test_the_summary_has_the_three_numbers(self):
		self.assertEqual(
			api.list_purchases()["data"]["summary"],
			{"orders_open": 3, "bills_unpaid": 12500.5, "returned": 800.0},
		)

	def test_the_all_tab_reads_orders_and_bills_and_returns(self):
		api.list_purchases(tab="all")
		sql = self.union_sql()
		self.assertIn("`tabPurchase Order`", sql)
		self.assertIn("`tabPurchase Invoice`", sql)
		self.assertIn("pi.is_return = 1 OR NOT EXISTS", sql)

	def test_a_bill_made_from_an_order_is_not_a_second_row(self):
		api.list_purchases(tab="all")
		self.assertIn("IFNULL(pii.purchase_order, '') != ''", self.union_sql())

	def test_the_orders_tab_reads_only_orders(self):
		api.list_purchases(tab="orders")
		self.assertNotIn("`tabPurchase Invoice`", self.union_sql())

	def test_the_returns_tab_reads_only_returns(self):
		api.list_purchases(tab="returns")
		sql = self.union_sql()
		self.assertNotIn("`tabPurchase Order`", sql)
		self.assertIn("pi.is_return = 1", sql)

	def test_a_bad_tab_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_purchases(tab="sessions")

	def test_the_period_becomes_dates(self):
		api.list_purchases(period="7d")
		values = self.union_values()
		self.assertEqual((values["from"], values["to"]), (date(2026, 10, 8), date(2026, 10, 14)))

	def test_a_custom_period_needs_both_dates(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_purchases(period="custom", from_date="2026-10-01")

	def test_a_bad_period_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_purchases(period="forever")

	def test_a_supplier_filter_is_applied_to_every_leg(self):
		api.list_purchases(supplier="SUP-1")
		sql = self.union_sql()
		self.assertIn("po.supplier = %(supplier)s", sql)
		self.assertIn("pi.supplier = %(supplier)s", sql)
		self.assertEqual(self.union_values()["supplier"], "SUP-1")

	def test_the_supplier_filter_also_limits_the_summary(self):
		api.list_purchases(supplier="SUP-1")
		summary_queries = [
			q for q, _v in self.queries if "SUM(" in q or "COUNT(*) AS n FROM `tabPurchase Order`" in q
		]
		self.assertTrue(summary_queries)
		self.assertTrue(all("supplier = %(supplier)s" in q for q in summary_queries))

	def test_a_text_search_looks_at_supplier_bill_number_and_name(self):
		api.list_purchases(search="bidco")
		sql = self.union_sql()
		self.assertIn("pi.bill_no LIKE %(like)s", sql)
		self.assertIn("po.supplier_name LIKE %(like)s", sql)
		self.assertEqual(self.union_values()["like"], "%bidco%")

	def test_a_short_number_search_matches_the_end_of_the_name(self):
		api.list_purchases(search="#B0007")
		sql = self.union_sql()
		self.assertIn("REGEXP %(regex)s", sql)
		self.assertNotIn("`tabPurchase Order`", sql)  # a B number cannot be an order

	def test_a_page_past_the_end_is_the_last_page(self):
		self.assertEqual(api.list_purchases(page=99)["data"]["page"], 3)

	def test_the_rows_per_page_are_read_from_the_request(self):
		api.list_purchases(page_size=50)
		self.assertEqual(self.union_values()["limit"], 50)

	def test_an_empty_list_has_no_rows_and_one_page(self):
		self.sql_answers["COUNT(*) AS n FROM ("] = [{"n": 0}]
		data = api.list_purchases()["data"]
		self.assertEqual((data["rows"], data["total"], data["pages"]), ([], 0, 1))

	def test_a_failure_in_the_database_is_a_plain_sentence(self):
		self.db.sql.side_effect = RuntimeError("boom")
		reply = api.list_purchases()
		self.assertFalse(reply["success"])
		self.assertIn("Could not load", reply["message"])
		self.logged.assert_called()

	def test_the_summary_ignores_the_tab(self):
		api.list_purchases(tab="orders")
		self.assertTrue(any("SUM(pi.outstanding_amount)" in q for q, _v in self.queries))


class PanelCase(ApiCase):
	def setUp(self):
		super().setUp()
		self.docs["PUR-ORD-2026-00012"] = order_doc(
			items=[row(received_qty=4), row(item_code="B", item_name="Rice", qty=5, rate=200, amount=1000)],
			per_received=40,
		)
		self.bill = {
			"id": "ACC-PINV-2026-00020",
			"number": "#B0020",
			"bill_no": "INV-1",
			"bill_date": "2026-10-03",
			"date": "2026-10-03",
			"due_date": "2026-11-02",
			"total": 1160.0,
			"outstanding": 1160.0,
			"is_return": False,
			"docstatus": 1,
		}
		stack = [
			patch.object(api, "_linked_parents", return_value=[]),
			patch.object(api, "_bill_rows", return_value=[]),
			patch.object(api, "_receipt_views", return_value=[]),
			patch.object(
				api,
				"_payment_modes",
				return_value=[{"name": "Cash", "kind": "cash"}, {"name": "M-Pesa", "kind": "mpesa"}],
			),
		]
		self.patched = [p.start() for p in stack]
		for p in stack:
			self.addCleanup(p.stop)

	def panel(self, id="PUR-ORD-2026-00012", doctype="Purchase Order"):
		self.db.exists.side_effect = lambda kind, name=None: kind == doctype
		return api.get_purchase(id, doctype)["data"]


class TestThePanel(PanelCase):
	def test_each_line_shows_ordered_received_and_still_to_arrive(self):
		lines = self.panel()["lines"]
		self.assertEqual(
			[(l["item_code"], l["ordered"], l["received"], l["to_arrive"]) for l in lines],
			[("A", 10.0, 4.0, 6.0), ("B", 5.0, 0.0, 5.0)],
		)

	def test_the_header_has_short_number_supplier_status_and_date(self):
		data = self.panel()
		self.assertEqual(
			(data["number"], data["supplier"]["name"], data["status"], data["date"], data["part_received"]),
			("#O0012", "Bidco", "ordered", "2026-10-01", True),
		)

	def test_the_totals_are_subtotal_tax_and_total(self):
		self.assertEqual(self.panel()["totals"], {"subtotal": 2000.0, "tax": 320.0, "total": 2320.0})

	def test_receive_goods_is_offered_when_something_is_still_to_arrive(self):
		self.assertTrue(self.panel()["can"]["receive_goods"])

	def test_receive_goods_is_not_offered_when_everything_arrived(self):
		self.docs["PUR-ORD-2026-00012"] = order_doc(items=[row(received_qty=10)], per_received=100)
		self.assertFalse(self.panel()["can"]["receive_goods"])

	def test_receive_goods_is_not_offered_on_a_draft(self):
		self.docs["PUR-ORD-2026-00012"] = order_doc(docstatus=0, status="Draft")
		data = self.panel()
		self.assertFalse(data["can"]["receive_goods"])
		self.assertTrue(data["can"]["edit_draft"])
		self.assertEqual(data["status"], "draft")

	def test_pay_bill_is_offered_when_a_bill_has_an_outstanding_amount(self):
		self.patched[1].return_value = []
		with patch.object(api, "_bill_view", return_value=self.bill):
			self.patched[0].return_value = ["ACC-PINV-2026-00020"]
			self.patched[1].return_value = [{"name": "ACC-PINV-2026-00020"}]
			data = self.panel()
		self.assertTrue(data["can"]["pay_bill"])
		self.assertEqual(
			(data["pay"]["invoice"], data["pay"]["outstanding"]), ("ACC-PINV-2026-00020", 1160.0)
		)
		self.assertEqual([m["kind"] for m in data["pay"]["modes"]], ["cash", "mpesa"])

	def test_pay_bill_is_not_offered_when_the_bill_is_paid(self):
		paid = dict(self.bill, outstanding=0.0)
		with patch.object(api, "_bill_view", return_value=paid):
			self.patched[1].return_value = [{"name": "X"}]
			data = self.panel()
		self.assertFalse(data["can"]["pay_bill"])
		self.assertIsNone(data["pay"])

	def test_an_order_with_no_goods_or_bills_can_be_cancelled(self):
		self.assertTrue(self.panel()["can"]["cancel"])

	def test_an_order_with_a_receipt_cannot_be_cancelled(self):
		self.patched[2].return_value = [{"id": "MAT-PRE-1"}]
		self.assertFalse(self.panel()["can"]["cancel"])

	def test_an_order_with_a_bill_cannot_be_cancelled_and_can_be_returned_against_it(self):
		with patch.object(api, "_bill_view", return_value=self.bill):
			self.patched[1].return_value = [{"name": "X"}]
			data = self.panel()
		self.assertFalse(data["can"]["cancel"])
		self.assertTrue(data["can"]["return"])
		self.assertEqual(data["return_target"], "ACC-PINV-2026-00020")

	def test_an_order_with_no_bill_cannot_have_a_return(self):
		self.assertFalse(self.panel()["can"]["return"])

	def test_a_bill_panel_has_the_bill_number_due_date_and_what_is_owed(self):
		self.docs["ACC-PINV-2026-00007"] = bill_doc()
		data = self.panel("ACC-PINV-2026-00007", "Purchase Invoice")
		self.assertEqual(
			(
				data["number"],
				data["bill_no"],
				data["due_date"],
				data["status"],
				data["totals"]["outstanding"],
			),
			("#B0007", "INV-9", "2026-11-01", "billed", 1000.0),
		)
		self.assertTrue(data["can"]["pay_bill"])
		self.assertTrue(data["can"]["return"])

	def test_a_bill_can_be_cancelled_only_by_a_role_that_may(self):
		self.docs["ACC-PINV-2026-00007"] = bill_doc()
		self.assertTrue(self.panel("ACC-PINV-2026-00007", "Purchase Invoice")["can"]["cancel"])
		self.roles = ("Stock Manager",)
		self.tier = "manager"
		self.assertFalse(self.panel("ACC-PINV-2026-00007", "Purchase Invoice")["can"]["cancel"])

	def test_a_return_has_no_payment_cancel_or_further_return(self):
		self.docs["ACC-PINV-2026-00009"] = bill_doc(
			name="ACC-PINV-2026-00009",
			is_return=1,
			grand_total=-300,
			outstanding_amount=0,
			return_against="ACC-PINV-2026-00007",
		)
		data = self.panel("ACC-PINV-2026-00009", "Purchase Invoice")
		self.assertEqual((data["status"], data["number"]), ("returned", "#R0009"))
		self.assertFalse(data["can"]["pay_bill"] or data["can"]["cancel"] or data["can"]["return"])

	def test_a_cancelled_purchase_offers_nothing(self):
		self.docs["PUR-ORD-2026-00012"] = order_doc(docstatus=2, status="Cancelled")
		data = self.panel()
		self.assertEqual(data["status"], "cancelled")
		self.assertFalse(any(data["can"][k] for k in ("receive_goods", "pay_bill", "cancel", "return")))

	def test_the_system_reference_is_kept_apart_under_reference(self):
		data = self.panel()
		self.assertEqual(data["reference"], "PUR-ORD-2026-00012")
		self.assertNotIn("PUR-ORD", data["number"])

	def test_an_unknown_purchase_is_not_found(self):
		self.db.exists.side_effect = lambda *a, **k: False
		reply = api.get_purchase("NOPE")
		self.assertFalse(reply["success"])
		self.assertEqual(reply["error_type"], "not_found")

	def test_a_bad_doctype_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.get_purchase("X", "Sales Invoice")

	def test_both_kinds_of_record_are_checked_for_another_business(self):
		self.panel()
		self.access.assert_any_call("Purchase Order", "PUR-ORD-2026-00012")
		self.access.assert_any_call("Purchase Invoice", "PUR-ORD-2026-00012")

	def test_the_kind_is_found_from_the_number_when_not_given(self):
		self.db.exists.side_effect = lambda kind, name=None: kind == "Purchase Order"
		self.assertEqual(api.get_purchase("PUR-ORD-2026-00012")["data"]["doctype"], "Purchase Order")


class TestOpenOrdersOnly(ListCase):
	def test_open_only_reads_orders_still_waiting_for_goods_whatever_their_date(self):
		api.list_purchases(open_only=1)
		sql = self.union_sql()
		self.assertIn("po.per_received < 100", sql)
		self.assertIn("po.docstatus = 1", sql)
		self.assertNotIn("po.transaction_date BETWEEN", sql)
		self.assertNotIn("`tabPurchase Invoice`", sql)

	def test_open_only_leaves_out_held_closed_and_cancelled_orders(self):
		api.list_purchases(open_only=1)
		self.assertIn("NOT IN ('Closed', 'Completed', 'Cancelled', 'On Hold')", self.union_sql())

	def test_without_open_only_the_period_applies_to_orders(self):
		api.list_purchases(tab="orders")
		self.assertIn("po.transaction_date BETWEEN", self.union_sql())

	def test_open_only_still_follows_the_supplier(self):
		api.list_purchases(open_only=1, supplier="SUP-1")
		self.assertIn("po.supplier = %(supplier)s", self.union_sql())


class TestGoodsReceivedAndBillsViews(ListCase):
	def test_goods_received_notes_are_listed_from_their_own_table(self):
		api.list_purchases(kind="receipts")
		sql = self.union_sql()
		self.assertIn("`tabPurchase Receipt` pr", sql)
		self.assertNotIn("`tabPurchase Order`", sql)
		self.assertNotIn("`tabPurchase Invoice`", sql)

	def test_the_period_and_the_supplier_apply_to_goods_received_notes(self):
		api.list_purchases(kind="receipts", supplier="SUP-1", period="7d")
		sql = self.union_sql()
		self.assertIn("pr.posting_date BETWEEN %(from)s AND %(to)s", sql)
		self.assertIn("pr.supplier = %(supplier)s", sql)

	def test_a_short_number_with_g_finds_a_goods_received_note(self):
		api.list_purchases(kind="receipts", search="G12")
		self.assertIn("pr.name REGEXP %(regex)s", self.union_sql())

	def test_a_short_number_for_another_kind_finds_no_goods_received_note(self):
		api.list_purchases(kind="receipts", search="B12")
		self.assertNotIn("`tabPurchase Receipt` pr", self.union_sql())

	def test_every_supplier_bill_is_listed_including_those_made_from_an_order(self):
		api.list_purchases(kind="bills")
		sql = self.union_sql()
		self.assertIn("pi.is_return = 0", sql)
		self.assertNotIn("NOT EXISTS", sql)
		self.assertNotIn("`tabPurchase Order`", sql)

	def test_a_view_of_its_own_ignores_the_tab(self):
		api.list_purchases(kind="bills", tab="returns")
		self.assertIn("pi.is_return = 0", self.union_sql())

	def test_open_only_wins_over_a_view(self):
		api.list_purchases(kind="receipts", open_only=1)
		self.assertIn("`tabPurchase Order`", self.union_sql())

	def test_an_unknown_view_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_purchases(kind="invoices")

	def test_the_default_is_the_tabs(self):
		api.list_purchases(kind="")
		self.assertIn("`tabPurchase Order`", self.union_sql())


class TestPaymentModes(ApiCase):
	def test_the_ways_of_paying_come_back_with_their_kind(self):
		with patch.object(api, "_payment_modes", return_value=[{"name": "M-Pesa", "kind": "mpesa"}]):
			self.assertEqual(api.get_payment_modes()["data"]["modes"], [{"name": "M-Pesa", "kind": "mpesa"}])

	def test_a_cashier_is_refused(self):
		self.tier = "cashier"
		with self.assertRaises(frappe.PermissionError):
			api.get_payment_modes()

	def test_modes_are_classified(self):
		with (
			patch.object(
				api.business_lists,
				"modes_of_payment_for",
				return_value=["Cash", "MPESA", "Credit Card", "Cheque"],
			),
			patch.object(frappe, "get_all", create=True, return_value=[]),
		):
			kinds = {m["name"]: m["kind"] for m in api._payment_modes("Acme")}
		self.assertEqual(kinds, {"Cash": "cash", "MPESA": "mpesa", "Credit Card": "card", "Cheque": "other"})

	def test_a_business_with_no_modes_gets_an_empty_list(self):
		with patch.object(api.business_lists, "modes_of_payment_for", return_value=None):
			self.assertEqual(api._payment_modes("Acme"), [])


class TestGoodsReceivedPanel(PanelCase):
	def setUp(self):
		super().setUp()
		self.docs["MAT-PRE-2026-00004"] = Doc(
			doctype="Purchase Receipt",
			name="MAT-PRE-2026-00004",
			docstatus=1,
			status="To Bill",
			company="Acme",
			supplier="SUP-1",
			supplier_name="Bidco",
			posting_date=date(2026, 10, 3),
			is_return=0,
			per_billed=0,
			total=1000,
			total_taxes_and_charges=160,
			grand_total=1160,
			items=[row(qty=6, purchase_order="PUR-ORD-2026-00012")],
		)

	def receipt_panel(self):
		self.db.exists.side_effect = lambda kind, name=None: kind == "Purchase Receipt"
		return api.get_purchase("MAT-PRE-2026-00004")["data"]

	def test_a_goods_received_note_opens_in_the_panel(self):
		data = self.receipt_panel()
		self.assertEqual(
			(data["number"], data["doctype"], data["status"], data["date"]),
			("#G0004", "Purchase Receipt", "received", "2026-10-03"),
		)

	def test_it_names_the_order_it_belongs_to(self):
		self.assertEqual(self.receipt_panel()["orders"], [{"id": "PUR-ORD-2026-00012", "number": "#O0012"}])

	def test_its_lines_are_what_arrived(self):
		line = self.receipt_panel()["lines"][0]
		self.assertEqual((line["received"], line["to_arrive"]), (6.0, 0.0))

	def test_it_offers_no_receive_cancel_or_edit(self):
		can = self.receipt_panel()["can"]
		self.assertFalse(can["receive_goods"] or can["cancel"] or can["edit_draft"])

	def test_it_offers_to_pay_the_bill_made_from_it(self):
		bill = {
			"id": "ACC-PINV-2026-00020",
			"number": "#B0020",
			"bill_no": "",
			"bill_date": "",
			"date": "2026-10-03",
			"due_date": "2026-11-02",
			"total": 1160.0,
			"outstanding": 1160.0,
			"is_return": False,
			"docstatus": 1,
		}
		with (
			patch.object(api, "_linked_bill_names", return_value=["ACC-PINV-2026-00020"]),
			patch.object(api, "_bill_rows", return_value=[{"name": "ACC-PINV-2026-00020"}]),
			patch.object(api, "_bill_view", return_value=bill),
		):
			data = self.receipt_panel()
		self.assertTrue(data["can"]["pay_bill"])
		self.assertEqual(data["pay"]["invoice"], "ACC-PINV-2026-00020")

	def test_the_receipt_is_checked_for_another_business_too(self):
		self.receipt_panel()
		self.access.assert_any_call("Purchase Receipt", "MAT-PRE-2026-00004")


class ReceiveCase(ApiCase):
	def setUp(self):
		super().setUp()
		self.order = order_doc(
			items=[row(received_qty=4), row(item_code="B", item_name="Rice", qty=5, rate=200, amount=1000)],
			per_received=40,
		)
		self.docs["PUR-ORD-2026-00012"] = self.order
		self.invoice = frappe._dict(
			name="ACC-PINV-2026-00030",
			bill_no="INV-55",
			due_date=date(2026, 11, 13),
			grand_total=1740,
			outstanding_amount=1740,
		)
		self.docs["ACC-PINV-2026-00030"] = self.invoice
		self.stock = {"A": [10.0, 16.0], "B": [3.0, 5.0]}
		self.reads = []
		stack = [
			patch.object(api, "_make_receipt", return_value=("MAT-PRE-2026-00004", None)),
			patch.object(api, "_make_invoice", return_value=self.invoice),
			patch.object(api, "_stock_codes", side_effect=lambda codes: set(codes)),
			patch.object(api, "_stock_in_hand", side_effect=self.stock_in_hand),
		]
		self.receipt_call, self.invoice_call, self.codes_call, self.stock_call = [p.start() for p in stack]
		for p in stack:
			self.addCleanup(p.stop)

	def stock_in_hand(self, pairs):
		step = len(self.reads)
		self.reads.append(list(pairs))
		return {code: self.stock[code][min(step, 1)] for code, _wh in pairs}

	def receive(self, items=None, **extra):
		items = (
			items
			if items is not None
			else [{"item_code": "A", "qty_received": 6}, {"item_code": "B", "qty_received": 5}]
		)
		return api.receive_goods("PUR-ORD-2026-00012", items, **extra)


class TestReceiveGoodsHappyPath(ReceiveCase):
	def test_the_receipt_the_bill_and_the_stock_come_back_together(self):
		data = self.receive(bill_no="INV-55", request_id="recv-abcdefgh")["data"]
		self.assertEqual((data["receipt"], data["invoice"]), ("MAT-PRE-2026-00004", "ACC-PINV-2026-00030"))
		self.assertEqual((data["receipt_number"], data["invoice_number"]), ("#G0004", "#B0030"))

	def test_stock_afterwards_is_stock_before_plus_the_quantity_received(self):
		data = self.receive()["data"]
		first = data["stock"][0]
		self.assertEqual(first, {"item_code": "A", "received": 6.0, "before": 10.0, "after": 16.0})
		self.assertEqual(first["after"], first["before"] + first["received"])

	def test_a_stock_figure_that_does_not_add_up_is_flagged(self):
		self.assertFalse(self.receive()["data"]["stock_ok"])  # B went 3 to 5 for 5 received
		self.stock["B"] = [3.0, 8.0]
		self.reads.clear()
		self.assertTrue(self.receive()["data"]["stock_ok"])

	def test_stock_is_read_before_the_receipt_and_after_the_bill(self):
		order = []
		self.receipt_call.side_effect = lambda *a, **k: order.append("receipt") or ("MAT-PRE-1", None)
		self.invoice_call.side_effect = lambda *a, **k: order.append("invoice") or self.invoice
		self.stock_call.side_effect = lambda pairs: order.append("stock") or {c: 1.0 for c, _w in pairs}
		self.receive()
		self.assertEqual(order, ["stock", "receipt", "invoice", "stock"])

	def test_it_commits_once_at_the_very_end_and_never_rolls_back(self):
		self.receive()
		self.db.commit.assert_called_once()
		self.db.rollback.assert_not_called()

	def test_the_commit_comes_after_the_bill_is_made(self):
		order = []
		self.invoice_call.side_effect = lambda *a, **k: order.append("invoice") or self.invoice
		self.db.commit.side_effect = lambda: order.append("commit")
		self.receive()
		self.assertEqual(order, ["invoice", "commit"])

	def test_the_order_row_is_locked_first(self):
		self.receive()
		self.db.get_value.assert_any_call("Purchase Order", "PUR-ORD-2026-00012", "name", for_update=True)

	def test_the_receipt_gets_the_quantities_received_and_nothing_else(self):
		self.receive(items=[{"item_code": "A", "qty_received": "2.5"}])
		args = self.receipt_call.call_args.args
		self.assertEqual(args[0], "PUR-ORD-2026-00012")
		self.assertEqual(args[1], [{"item_code": "A", "qty": 2.5}])

	def test_the_store_is_passed_on_when_chosen(self):
		self.receive(warehouse="Back Store - A")
		self.assertEqual(self.receipt_call.call_args.args[2], "Back Store - A")

	def test_the_bill_gets_the_bill_number_and_dates(self):
		self.receive(bill_no="  INV   55 ", bill_date="2026-10-13", due_date="2026-11-13")
		args = self.invoice_call.call_args.args
		self.assertEqual(args[1], "INV 55")
		self.assertEqual((args[2], args[3]), (date(2026, 10, 13), date(2026, 11, 13)))

	def test_the_receipt_is_marked_with_the_request_so_a_repeat_finds_it(self):
		self.receive(request_id="recv-abcdefgh")
		self.db.set_value.assert_called_once()
		args = self.db.set_value.call_args
		self.assertEqual(args.args[:3], ("Purchase Receipt", "MAT-PRE-2026-00004", "remarks"))
		self.assertIn("[recv-req:recv-abcdefgh:PUR-ORD-2026-00012]", args.args[3])

	def test_items_sent_as_json_text_are_read(self):
		data = self.receive(items='[{"item_code": "A", "qty_received": 1}]')
		self.assertTrue(data["success"])

	def test_a_partly_delivered_order_stays_open_for_the_rest(self):
		data = self.receive(items=[{"item_code": "A", "qty_received": 2}])["data"]
		self.assertTrue(data["order"]["open"])
		self.assertEqual(
			data["order"]["still_to_arrive"],
			[{"item_code": "A", "to_arrive": 4.0}, {"item_code": "B", "to_arrive": 5.0}],
		)

	def test_an_order_received_in_full_is_no_longer_open(self):
		data = self.receive()["data"]
		self.assertFalse(data["order"]["open"])
		self.assertEqual(data["order"]["still_to_arrive"], [])

	def test_an_item_that_holds_no_stock_is_not_in_the_stock_list(self):
		self.codes_call.side_effect = lambda codes: {"A"}
		data = self.receive()["data"]
		self.assertEqual([s["item_code"] for s in data["stock"]], ["A"])

	def test_it_is_not_a_repeat(self):
		self.assertFalse(self.receive()["data"]["repeat"])


class TestReceiveGoodsRefusals(ReceiveCase):
	def assertNothingChanged(self):
		self.receipt_call.assert_not_called()
		self.invoice_call.assert_not_called()
		self.db.commit.assert_not_called()

	def test_more_than_is_still_to_arrive_is_refused_and_says_how_much_is_left(self):
		reply = self.receive(items=[{"item_code": "A", "qty_received": 7}])
		self.assertFalse(reply["success"])
		self.assertEqual(reply["error_type"], "too_much")
		self.assertIn("6", reply["message"])
		self.assertNothingChanged()

	def test_exactly_what_is_left_is_accepted(self):
		self.assertTrue(self.receive(items=[{"item_code": "A", "qty_received": 6}])["success"])

	def test_zero_is_refused(self):
		reply = self.receive(items=[{"item_code": "A", "qty_received": 0}])
		self.assertEqual(reply["error_type"], "bad_qty")
		self.assertNothingChanged()

	def test_a_negative_quantity_is_refused(self):
		self.assertEqual(
			self.receive(items=[{"item_code": "A", "qty_received": -2}])["error_type"], "bad_qty"
		)

	def test_a_quantity_that_is_not_a_number_is_refused(self):
		self.assertEqual(
			self.receive(items=[{"item_code": "A", "qty_received": "abc"}])["error_type"], "bad_qty"
		)

	def test_nothing_ticked_is_refused(self):
		reply = self.receive(items=[])
		self.assertEqual(reply["error_type"], "no_items")
		self.assertNothingChanged()

	def test_an_item_that_is_not_on_the_order_is_refused(self):
		reply = self.receive(items=[{"item_code": "Z", "qty_received": 1}])
		self.assertEqual(reply["error_type"], "not_on_order")
		self.assertIn("Z", reply["message"])

	def test_an_item_listed_twice_is_refused(self):
		reply = self.receive(
			items=[{"item_code": "A", "qty_received": 1}, {"item_code": "A", "qty_received": 1}]
		)
		self.assertEqual(reply["error_type"], "named_twice")

	def test_an_order_with_the_same_item_on_two_lines_is_not_received_here(self):
		self.order.items.append(row(item_code="A", qty=2))
		reply = self.receive(items=[{"item_code": "A", "qty_received": 1}])
		self.assertEqual(reply["error_type"], "duplicate_lines")
		self.assertNothingChanged()

	def test_an_order_that_is_not_submitted_is_refused(self):
		self.order.docstatus = 0
		self.assertEqual(self.receive()["error_type"], "not_submitted")
		self.assertNothingChanged()

	def test_an_order_with_nothing_left_is_refused(self):
		self.order.items = [row(received_qty=10)]
		self.assertEqual(
			self.receive(items=[{"item_code": "A", "qty_received": 1}])["error_type"], "nothing_to_receive"
		)

	def test_a_closed_order_is_refused(self):
		self.order.status = "Closed"
		self.assertEqual(self.receive()["error_type"], "nothing_to_receive")

	def test_an_unknown_order_is_not_found(self):
		self.docs.pop("PUR-ORD-2026-00012")
		reply = self.receive()
		self.assertEqual(reply["error_type"], "not_found")

	def test_an_order_of_another_business_is_not_found(self):
		with patch.object(api, "resolve_company", return_value="Other Co"):
			reply = api.receive_goods(
				"PUR-ORD-2026-00012", [{"item_code": "A", "qty_received": 1}], company="Other Co"
			)
		self.assertEqual(reply["error_type"], "not_found")
		self.receipt_call.assert_not_called()

	def test_a_bad_request_id_is_refused_before_anything_is_read(self):
		reply = self.receive(request_id="no good!")
		self.assertEqual(reply["error_type"], "bad_request_id")
		self.db.get_value.assert_not_called()

	def test_a_bad_bill_date_is_refused(self):
		self.assertEqual(self.receive(bill_date="soon")["error_type"], "bad_bill_date")

	def test_a_due_date_before_the_bill_date_is_refused(self):
		self.assertEqual(
			self.receive(bill_date="2026-10-13", due_date="2026-10-01")["error_type"], "due_before_bill"
		)
		self.assertNothingChanged()

	def test_a_store_of_another_business_is_refused(self):
		self.db.get_value.side_effect = (
			lambda d, *a, **k: "Other Co" if d == "Warehouse" else self.get_value(d, *a, **k)
		)
		reply = self.receive(warehouse="Their Store")
		self.assertEqual(reply["error_type"], "bad_warehouse")

	def test_the_order_and_the_store_are_checked_for_another_business_first(self):
		self.receive(warehouse="Back Store - A")
		self.access.assert_any_call("Purchase Order", "PUR-ORD-2026-00012")
		self.access.assert_any_call("Warehouse", "Back Store - A")


class TestReceiveGoodsRollsBackEverything(ReceiveCase):
	def test_if_the_bill_step_fails_everything_is_rolled_back(self):
		self.invoice_call.side_effect = frappe.ValidationError("Supplier is on hold")
		reply = self.receive()
		self.assertFalse(reply["success"])
		self.db.rollback.assert_called()
		self.db.commit.assert_not_called()

	def test_after_a_failed_bill_no_receipt_or_bill_is_reported(self):
		self.invoice_call.side_effect = frappe.ValidationError("Supplier is on hold")
		reply = self.receive()
		self.assertNotIn("data", reply)
		self.assertIn("nothing was changed", reply["message"])
		self.assertIn("Supplier is on hold", reply["message"])

	def test_the_rollback_comes_after_the_receipt_was_made(self):
		order = []
		self.receipt_call.side_effect = lambda *a, **k: order.append("receipt") or ("MAT-PRE-1", None)
		self.invoice_call.side_effect = RuntimeError("database lost")
		self.db.rollback.side_effect = lambda: order.append("rollback")
		self.receive()
		self.assertEqual(order, ["receipt", "rollback"])

	def test_an_unexpected_failure_is_logged_and_is_a_plain_sentence(self):
		self.invoice_call.side_effect = RuntimeError("Traceback (most recent call last): boom")
		reply = self.receive()
		self.assertEqual(reply["error_type"], "server_error")
		self.assertNotIn("Traceback", reply["message"])
		self.assertIn("nothing was changed", reply["message"])
		self.logged.assert_called()
		self.db.commit.assert_not_called()

	def test_if_the_receipt_step_fails_no_bill_is_attempted(self):
		self.receipt_call.return_value = (None, "Quantity exceeds what is pending")
		reply = self.receive()
		self.assertEqual(reply["error_type"], "receipt_failed")
		self.invoice_call.assert_not_called()
		self.db.rollback.assert_called()
		self.db.commit.assert_not_called()
		self.assertIn("Quantity exceeds what is pending", reply["message"])

	def test_a_failure_while_reading_stock_afterwards_rolls_back_too(self):
		self.stock_call.side_effect = [{"A": 1.0, "B": 1.0}, RuntimeError("lost")]
		reply = self.receive()
		self.assertFalse(reply["success"])
		self.db.rollback.assert_called()
		self.db.commit.assert_not_called()

	def test_a_failure_in_the_commit_itself_is_reported_not_swallowed(self):
		self.db.commit.side_effect = RuntimeError("commit lost")
		reply = self.receive()
		self.assertFalse(reply["success"])
		self.db.rollback.assert_called()


class TestReceiveGoodsRepeat(ReceiveCase):
	def earlier(self, receipts, invoices):
		answers = {"Purchase Receipt": receipts, "Purchase Invoice": invoices}
		patcher = patch.object(
			frappe, "get_all", create=True, side_effect=lambda doctype, **k: answers[doctype]
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_repeat_returns_the_first_result_and_changes_nothing(self):
		self.earlier(["MAT-PRE-2026-00004"], ["ACC-PINV-2026-00030"])
		reply = self.receive(request_id="recv-abcdefgh")
		self.assertTrue(reply["success"])
		self.assertTrue(reply["data"]["repeat"])
		self.assertEqual(
			(reply["data"]["receipt"], reply["data"]["invoice"]),
			("MAT-PRE-2026-00004", "ACC-PINV-2026-00030"),
		)
		self.receipt_call.assert_not_called()
		self.invoice_call.assert_not_called()
		self.db.commit.assert_not_called()

	def test_a_repeat_never_raises_the_stock_again(self):
		self.earlier(["MAT-PRE-2026-00004"], ["ACC-PINV-2026-00030"])
		self.receive(request_id="recv-abcdefgh")
		self.stock_call.assert_not_called()
		self.db.set_value.assert_not_called()

	def test_a_new_request_id_is_a_new_delivery(self):
		self.receive(request_id="recv-abcdefgh")
		self.receipt_call.assert_called_once()

	def test_the_earlier_receipt_is_looked_up_by_the_request_marker(self):
		seen = []
		patcher = patch.object(
			frappe, "get_all", create=True, side_effect=lambda doctype, **k: seen.append((doctype, k)) or []
		)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.receive(request_id="recv-abcdefgh")
		doctype, kwargs = seen[0]
		self.assertEqual(doctype, "Purchase Receipt")
		self.assertIn("recv-req:recv-abcdefgh:PUR-ORD-2026-00012", kwargs["filters"]["remarks"][1])
		self.assertEqual(kwargs["filters"]["company"], "Acme")

	def test_without_a_request_id_nothing_is_looked_up(self):
		seen = []
		patcher = patch.object(
			frappe, "get_all", create=True, side_effect=lambda doctype, **k: seen.append(doctype) or []
		)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.receive()
		self.assertEqual(seen, [])

	def test_a_receipt_whose_bill_is_gone_is_explained_not_doubled(self):
		self.earlier(["MAT-PRE-2026-00004"], [])
		reply = self.receive(request_id="recv-abcdefgh")
		self.assertEqual(reply["error_type"], "already_received")
		self.receipt_call.assert_not_called()


class TestTheReceiptAndBillHelpers(ApiCase):
	def test_the_receipt_is_made_by_the_existing_call(self):
		with patch(
			"murzak_pos.api.stock.create_pr_from_po",
			return_value={"success": True, "data": {"purchase_receipt": "MAT-PRE-1"}},
		) as made:
			name, reason = api._make_receipt("PO-1", [{"item_code": "A", "qty": 2.0}], None)
		self.assertEqual((name, reason), ("MAT-PRE-1", None))
		made.assert_called_once_with("PO-1", items=[{"item_code": "A", "qty": 2.0}], submit=1)

	def test_a_store_is_sent_per_line_only_when_chosen(self):
		with patch(
			"murzak_pos.api.stock.create_pr_from_po",
			return_value={"success": True, "data": {"purchase_receipt": "MAT-PRE-1"}},
		) as made:
			api._make_receipt("PO-1", [{"item_code": "A", "qty": 2.0}], "Back - A")
		self.assertEqual(made.call_args.kwargs["items"][0]["warehouse"], "Back - A")

	def test_a_refusal_from_the_existing_call_gives_its_reason_without_tags(self):
		with patch(
			"murzak_pos.api.stock.create_pr_from_po",
			return_value={"success": False, "message": "<b>Qty exceeds</b> pending"},
		):
			name, reason = api._make_receipt("PO-1", [{"item_code": "A", "qty": 2.0}], None)
		self.assertEqual((name, reason), (None, "Qty exceeds pending"))

	def test_a_reply_with_no_receipt_name_counts_as_a_failure(self):
		with patch("murzak_pos.api.stock.create_pr_from_po", return_value={"success": True, "data": {}}):
			name, _reason = api._make_receipt("PO-1", [{"item_code": "A", "qty": 2.0}], None)
		self.assertIsNone(name)

	def test_the_bill_is_made_from_the_receipt_saved_then_submitted(self):
		invoice = MagicMock()
		with patch(
			"erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
			return_value=invoice,
		) as made:
			result = api._make_invoice(
				"MAT-PRE-1", "INV 55", date(2026, 10, 13), None, "PO-1", "recv-abcdefgh"
			)
		made.assert_called_once_with("MAT-PRE-1")
		self.assertIs(result, invoice)
		self.assertEqual(invoice.bill_no, "INV 55")
		self.assertEqual(invoice.bill_date, date(2026, 10, 13))
		self.assertEqual([c[0] for c in invoice.method_calls], ["insert", "submit"])
		self.assertIn("[recv-req:recv-abcdefgh:PO-1]", invoice.remarks)

	def test_a_bill_without_a_number_or_dates_leaves_them_to_erpnext(self):
		invoice = MagicMock(spec=["insert", "submit", "remarks"])
		with patch(
			"erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
			return_value=invoice,
		):
			api._make_invoice("MAT-PRE-1", "", None, None, "PO-1", None)
		self.assertFalse(hasattr(invoice, "bill_no"))

	def test_stock_is_summed_over_stores(self):
		self.db.get_value.side_effect = lambda doctype, filters, field: {"S1": 4, "S2": 6}[
			filters["warehouse"]
		]
		self.assertEqual(api._stock_in_hand([("A", "S1"), ("A", "S2")]), {"A": 10.0})

	def test_an_item_with_no_stock_record_has_none(self):
		self.db.get_value.side_effect = lambda *a, **k: None
		self.assertEqual(api._stock_in_hand([("A", "S1")]), {"A": 0.0})

	def test_a_line_with_no_store_has_no_stock_to_read(self):
		self.assertEqual(api._stock_in_hand([("A", "")]), {"A": 0.0})
		self.db.get_value.assert_not_called()


class DirectBillCase(ApiCase):
	def setUp(self):
		super().setUp()
		self.made = MagicMock(name="invoice")
		self.made.get.side_effect = lambda key, default=None: {
			"name": "ACC-PINV-2026-00040",
			"grand_total": 1000,
			"outstanding_amount": 1000,
		}.get(key, default)
		stack = [
			patch.object(frappe, "get_doc", create=True, return_value=self.made),
			patch.object(
				api,
				"_order_lines",
				side_effect=lambda items, company, warehouse, day: [
					dict(i, schedule_date=day, warehouse=warehouse or "Stores - A") for i in items
				],
			),
			patch.object(api.business_lists, "first_mode_of_payment", return_value="Cash"),
			patch.object(api.business_lists, "mode_of_payment_usable", return_value=True),
			patch.object(api, "_mode_account", return_value="Cash - A"),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)
		self.get_doc_call = frappe.get_doc

	def bill(self, **extra):
		items = extra.pop("items", [{"item_code": "A", "qty": 10, "rate": 100}])
		return api.create_direct_bill("SUP-1", items, **extra)

	def values(self):
		return self.get_doc_call.call_args.args[0]


class TestDirectBill(DirectBillCase):
	def test_the_bill_is_saved_submitted_and_committed(self):
		reply = self.bill(bill_no="INV-1", request_id="bill-abcdefgh")
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"]["invoice"], "ACC-PINV-2026-00040")
		self.assertEqual(
			[c[0] for c in self.made.method_calls if c[0] in ("insert", "submit")], ["insert", "submit"]
		)
		self.db.commit.assert_called_once()

	def test_the_stock_is_raised_at_once(self):
		self.bill()
		self.assertEqual(self.values()["update_stock"], 1)

	def test_the_lines_carry_no_order_only_date(self):
		self.bill()
		self.assertNotIn("schedule_date", self.values()["items"][0])

	def test_the_bill_number_and_dates_are_kept(self):
		self.bill(bill_no=" INV  1 ", bill_date="2026-10-01", due_date="2026-10-31")
		values = self.values()
		self.assertEqual(
			(values["bill_no"], values["bill_date"], values["due_date"]),
			("INV 1", date(2026, 10, 1), date(2026, 10, 31)),
		)

	def test_marking_it_paid_sets_the_amount_the_way_and_the_account(self):
		self.bill(paid_amount=600, mode_of_payment="Cash")
		values = self.values()
		self.assertEqual(
			(
				values["is_paid"],
				values["paid_amount"],
				values["mode_of_payment"],
				values["cash_bank_account"],
			),
			(1, 600.0, "Cash", "Cash - A"),
		)

	def test_without_an_amount_it_is_left_unpaid(self):
		self.bill()
		self.assertNotIn("is_paid", self.values())

	def test_the_payment_code_is_kept_in_the_remarks(self):
		self.bill(paid_amount=100, mode_of_payment="M-Pesa", reference_no="QWE123RTY4")
		self.assertIn("QWE123RTY4", self.values()["remarks"])

	def test_paying_more_than_the_bill_is_refused(self):
		reply = self.bill(paid_amount=1001)
		self.assertEqual(reply["error_type"], "paid_too_much")
		self.db.commit.assert_not_called()

	def test_a_payment_of_zero_is_refused(self):
		self.assertEqual(self.bill(paid_amount=0)["error_type"], "bad_paid")

	def test_a_way_of_paying_with_no_account_is_refused(self):
		with patch.object(api, "_mode_account", return_value=None):
			self.assertEqual(self.bill(paid_amount=100, mode_of_payment="Cash")["error_type"], "no_account")
		self.db.commit.assert_not_called()

	def test_no_lines_is_refused(self):
		self.assertEqual(self.bill(items=[])["error_type"], "no_items")

	def test_a_zero_quantity_is_refused(self):
		self.assertEqual(self.bill(items=[{"item_code": "A", "qty": 0, "rate": 1}])["error_type"], "bad_qty")

	def test_an_unknown_supplier_is_refused(self):
		self.db.exists.return_value = False
		self.assertEqual(self.bill()["error_type"], "no_supplier")

	def test_the_supplier_and_the_store_are_checked_for_another_business_first(self):
		self.bill(warehouse="Back - A")
		self.access.assert_any_call("Supplier", "SUP-1")
		self.access.assert_any_call("Warehouse", "Back - A")

	def test_a_failure_rolls_back_and_says_nothing_was_changed(self):
		self.made.submit.side_effect = frappe.ValidationError("Warehouse is mandatory")
		reply = self.bill()
		self.assertFalse(reply["success"])
		self.assertIn("nothing was changed", reply["message"])
		self.db.rollback.assert_called()
		self.db.commit.assert_not_called()

	def test_a_repeat_returns_the_first_bill(self):
		with patch.object(frappe, "get_all", create=True, return_value=["ACC-PINV-2026-00040"]):
			reply = self.bill(request_id="bill-abcdefgh")
		self.assertTrue(reply["data"]["repeat"])
		self.db.commit.assert_not_called()
		self.made.insert.assert_not_called()

	def test_a_bad_request_id_is_refused(self):
		self.assertEqual(self.bill(request_id="no good")["error_type"], "bad_request_id")


class TestNotes(DirectBillCase):
	def test_a_note_on_a_direct_bill_is_saved_before_the_commit(self):
		order = []
		self.made.add_comment.side_effect = lambda *a: order.append("note")
		self.db.commit.side_effect = lambda: order.append("commit")
		self.bill(notes="  Delivered   by van ")
		self.made.add_comment.assert_called_once_with("Comment", "Delivered by van")
		self.assertEqual(order, ["note", "commit"])

	def test_no_note_adds_no_comment(self):
		self.bill()
		self.made.add_comment.assert_not_called()

	def test_a_failed_note_rolls_the_whole_bill_back(self):
		self.made.add_comment.side_effect = RuntimeError("lost")
		reply = self.bill(notes="x")
		self.assertFalse(reply["success"])
		self.db.rollback.assert_called()
		self.db.commit.assert_not_called()

	def test_a_note_can_be_added_to_an_order(self):
		reply = api.add_purchase_note("PUR-ORD-2026-00012", " Call before delivery ", "Purchase Order")
		self.assertEqual(reply["data"]["note"]["text"], "Call before delivery")
		self.made.add_comment.assert_called_once_with("Comment", "Call before delivery")
		self.db.commit.assert_called_once()

	def test_an_empty_note_is_refused(self):
		reply = api.add_purchase_note("PUR-ORD-2026-00012", "   ")
		self.assertEqual(reply["error_type"], "no_note")
		self.db.commit.assert_not_called()

	def test_a_note_on_an_unknown_purchase_is_not_found(self):
		self.db.exists.return_value = False
		self.assertEqual(api.add_purchase_note("NOPE", "hello")["error_type"], "not_found")

	def test_a_cashier_cannot_add_a_note(self):
		self.tier = "cashier"
		with self.assertRaises(frappe.PermissionError):
			api.add_purchase_note("X", "hello")
		self.access.assert_not_called()

	def test_the_record_is_checked_for_another_business_before_it_is_changed(self):
		api.add_purchase_note("PUR-ORD-2026-00012", "hi", "Purchase Order")
		self.access.assert_any_call("Purchase Order", "PUR-ORD-2026-00012")
		self.access.assert_any_call("Purchase Invoice", "PUR-ORD-2026-00012")

	def test_a_failed_comment_is_rolled_back(self):
		self.made.add_comment.side_effect = RuntimeError("lost")
		reply = api.add_purchase_note("PUR-ORD-2026-00012", "hi", "Purchase Order")
		self.assertFalse(reply["success"])
		self.db.rollback.assert_called()

	def test_the_panel_lists_the_notes_oldest_first(self):
		comments = [
			{"content": "<div>First</div>", "creation": datetime(2026, 10, 2, 9, 0)},
			{"content": "Second", "creation": datetime(2026, 10, 3, 9, 0)},
		]
		with (
			patch.object(frappe, "get_all", create=True, return_value=comments),
			patch.object(frappe, "get_doc", create=True, return_value=order_doc()),
			patch.object(api, "_linked_parents", return_value=[]),
			patch.object(api, "_receipt_views", return_value=[]),
			patch.object(api, "_payment_modes", return_value=[]),
		):
			self.db.exists.side_effect = lambda kind, name=None: kind == "Purchase Order"
			notes = api.get_purchase("PUR-ORD-2026-00012")["data"]["notes"]
		self.assertEqual(
			notes, [{"text": "First", "date": "2026-10-02"}, {"text": "Second", "date": "2026-10-03"}]
		)


class TestMisc(ApiCase):
	def test_plain_text_removes_tags_and_extra_space(self):
		self.assertEqual(api.plain_text("<div>Item  <b>A</b>\nfailed</div>"), "Item A failed")

	def test_plain_text_is_cut(self):
		self.assertEqual(len(api.plain_text("x" * 1000)), 240)

	def test_the_calls_are_whitelisted_posts_where_they_change_something(self):
		for call in (api.receive_goods, api.create_direct_bill):
			self.assertIn(call, frappe.whitelisted)
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[call], ["POST"])
		for call in (api.list_purchases, api.get_purchase):
			self.assertIn(call, frappe.whitelisted)


if __name__ == "__main__":
	unittest.main()
