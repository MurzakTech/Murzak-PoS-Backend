# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the online catalogue and online orders: what a shop shows, what a customer can order, and the
guarantees every channel gets (no double orders, prices set by the shop, stock respected). No database is
needed; every Frappe call is replaced.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import online_catalogue, online_orders


def row(**fields):
	return frappe._dict(fields)


class FakeOrder:
	"""Stands in for a Sales Order: remembers what was set on it and what was done to it."""

	def __init__(self):
		self.items = []
		self.flags = frappe._dict()
		self.name = "SO-0001"
		self.grand_total = 0
		self.comments = []
		self.submitted = False

	def append(self, _table, values):
		self.items.append(frappe._dict(values, item_name=values["item_code"], amount=values["qty"] * values["rate"]))
		self.grand_total = sum(i.amount for i in self.items)

	def insert(self):
		pass

	def submit(self):
		self.submitted = True

	def add_comment(self, kind, text):
		self.comments.append(text)


class TestCleanLines(unittest.TestCase):
	def test_repeated_product_is_added_together(self):
		lines = online_orders.clean_lines(
			[{"item_code": "RICE", "qty": 1}, {"item_code": "RICE", "qty": "2"}, {"item_code": "TEA", "qty": 1}]
		)
		self.assertEqual({l["item_code"]: l["qty"] for l in lines}, {"RICE": 3.0, "TEA": 1.0})

	def test_json_text_is_accepted(self):
		self.assertEqual(online_orders.clean_lines('[{"item_code": "RICE", "qty": 2}]')[0]["qty"], 2.0)

	def test_bad_orders_are_refused(self):
		bad = [
			None,
			[],
			"not json",
			["RICE"],
			[{"item_code": "RICE", "qty": 0}],
			[{"item_code": "RICE", "qty": -1}],
			[{"item_code": "RICE", "qty": "nan"}],
			[{"item_code": "RICE", "qty": 10**9}],
			[{"item_code": "", "qty": 1}],
			[{"item_code": f"I{n}", "qty": 1} for n in range(online_orders.MAX_LINES + 1)],
		]
		for lines in bad:
			with self.assertRaises(frappe.ValidationError, msg=repr(lines)[:60]):
				online_orders.clean_lines(lines)


class TestOnlineCatalogue(unittest.TestCase):
	def test_only_this_business_and_visible_products(self):
		filters = online_catalogue.online_filters("Duka Ltd")
		self.assertEqual(filters["custom_company"], "Duka Ltd")
		self.assertEqual(filters["custom_show_online"], 1)
		self.assertEqual(filters["disabled"], 0)
		self.assertEqual(filters["is_sales_item"], 1)

	def test_stock_is_what_is_left_after_reservations(self):
		bins = [
			row(item_code="RICE", actual_qty=10, reserved_qty=3),
			row(item_code="TEA", actual_qty=2, reserved_qty=5),
		]
		with patch.object(frappe, "get_all", return_value=bins):
			self.assertEqual(online_catalogue.available_stock(["RICE", "TEA"], "Main - D"), {"RICE": 7.0, "TEA": 0.0})

	def test_no_warehouse_means_no_stock_lookup(self):
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(online_catalogue.available_stock(["RICE"], ""), {})
			get_all.assert_not_called()

	def test_rows_show_service_always_available_and_empty_shelf_not(self):
		items = [
			row(name="RICE", item_name="Rice", item_group="Food", stock_uom="Kg", standard_rate=150, is_stock_item=1),
			row(name="DELIVERY", item_name="Delivery", item_group="Services", stock_uom="Nos", standard_rate=100, is_stock_item=0),
		]
		with patch.object(online_catalogue, "available_stock", return_value={}):
			rows = online_catalogue.rows_for(items, "Main - D")
		by_code = {r["item_code"]: r for r in rows}
		self.assertFalse(by_code["RICE"]["in_stock"])
		self.assertEqual(by_code["RICE"]["stock"], 0.0)
		self.assertTrue(by_code["DELIVERY"]["in_stock"])
		self.assertIsNone(by_code["DELIVERY"]["stock"])

	def test_every_page_is_read_for_a_large_shop(self):
		pages = {1: [1] * 100, 2: [1] * 100, 3: [1] * 5}
		with patch.object(online_catalogue, "list_items", side_effect=lambda *a, page=1, **k: pages.get(page, [])):
			total = sum(len(batch) for batch in online_catalogue.iter_all_items("Duka Ltd", "Main - D"))
		self.assertEqual(total, 205)

	def test_page_size_is_capped(self):
		with patch.object(frappe, "get_all", return_value=[]) as get_all:
			online_catalogue.list_items("Duka Ltd", "Main - D", page_size=10**6)
		self.assertEqual(get_all.call_args.kwargs["limit_page_length"], online_catalogue.MAX_PAGE_SIZE)


class TestPlaceOrder(unittest.TestCase):
	def setUp(self):
		self.order = FakeOrder()
		self.rice = row(name="RICE", item_name="Rice", standard_rate=150.0, stock_uom="Kg", is_stock_item=1)
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(frappe, "new_doc", return_value=self.order),
			patch.object(online_orders, "nowdate", return_value="2026-10-24"),
			patch.object(online_orders, "customer_for", return_value="Amina"),
			patch.object(online_orders, "_item_for_order", return_value=self.rice),
			patch.object(online_catalogue, "default_warehouse", return_value="Main - D"),
			patch.object(online_catalogue, "available_stock", return_value={"RICE": 5.0}),
			patch("murzak_pos.api.realtime_events.notify_online_order"),
		]
		self.mocks = [p.start() for p in patches]
		for p in patches:
			self.addCleanup(p.stop)
		frappe.db.get_value.return_value = None

	def place(self, **kw):
		args = dict(company="Duka Ltd", channel="Website", lines=[{"item_code": "RICE", "qty": 2}], phone="0712345678")
		args.update(kw)
		return online_orders.place_order(**args)

	def test_a_good_order_is_submitted_at_the_shops_price(self):
		result = self.place(lines=[{"item_code": "RICE", "qty": 2, "rate": 1}])
		self.assertTrue(result["created"])
		self.assertTrue(self.order.submitted)
		# The customer's browser said 1; the shop's price is 150
		self.assertEqual(self.order.items[0].rate, 150.0)
		self.assertEqual(result["total"], 300.0)
		self.assertEqual(self.order.custom_online_channel, "Website")
		self.assertTrue(self.order.custom_online_token)
		self.assertEqual(self.order.ignore_pricing_rule, 1)
		self.assertTrue(self.order.flags.ignore_permissions)

	def test_more_than_is_in_stock_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.place(lines=[{"item_code": "RICE", "qty": 6}])
		self.assertFalse(self.order.submitted)

	def test_an_order_already_recorded_is_not_recorded_again(self):
		frappe.db.get_value.return_value = row(name="SO-0099", custom_online_token="tok", grand_total=450)
		result = self.place(external_ref="WC-77")
		self.assertFalse(result["created"])
		self.assertEqual(result["order"], "SO-0099")
		self.assertFalse(self.order.submitted)

	def test_a_paid_woocommerce_order_keeps_its_price_and_is_flagged_when_stock_is_short(self):
		result = self.place(
			channel="WooCommerce",
			lines=[{"item_code": "RICE", "qty": 9, "rate": 120}],
			trust_prices=True,
			external_ref="WC-1",
		)
		self.assertTrue(result["created"])
		self.assertEqual(self.order.items[0].rate, 120.0)
		self.assertTrue(any("short" in c for c in self.order.comments))

	def test_unknown_product_is_refused(self):
		online_orders._item_for_order.return_value = None
		with self.assertRaises(frappe.ValidationError):
			self.place()

	def test_unknown_channel_and_delivery_method_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.place(channel="Telegram")
		with self.assertRaises(frappe.ValidationError):
			self.place(delivery_method="Drone")

	def test_no_warehouse_is_refused(self):
		online_catalogue.default_warehouse.return_value = ""
		with self.assertRaises(frappe.ValidationError):
			self.place()


class TestStaffCalls(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(online_orders, "_require_order_role"),
			patch.object(online_orders, "resolve_company", return_value="Duka Ltd"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def order_row(self, status, docstatus=1, company="Duka Ltd", channel="Website"):
		frappe.db.get_value.return_value = row(
			name="SO-1", company=company, custom_online_channel=channel, custom_online_fulfilment=status, docstatus=docstatus
		)

	def test_a_finished_order_cannot_be_moved(self):
		for status in ("Delivered", "Cancelled"):
			self.order_row(status)
			with self.assertRaises(frappe.ValidationError):
				online_orders.set_online_order_status("SO-1", "Ready")

	def test_an_order_moves_forward_and_is_saved(self):
		self.order_row("New")
		result = online_orders.set_online_order_status("SO-1", "Confirmed")
		self.assertTrue(result["success"])
		frappe.db.set_value.assert_called_with("Sales Order", "SO-1", "custom_online_fulfilment", "Confirmed")

	def test_another_business_order_is_not_found(self):
		self.order_row("New", company="Other Ltd")
		with self.assertRaises(frappe.DoesNotExistError):
			online_orders.set_online_order_status("SO-1", "Confirmed")

	def test_a_till_sale_is_not_an_online_order(self):
		self.order_row("New", channel=None)
		with self.assertRaises(frappe.DoesNotExistError):
			online_orders.mark_online_order_paid("SO-1")

	def test_cancelling_frees_the_stock_unless_already_invoiced(self):
		self.order_row("New")
		doc = MagicMock(per_billed=100, per_delivered=0)
		doc.flags = frappe._dict()
		with patch.object(frappe, "get_doc", return_value=doc):
			with self.assertRaises(frappe.ValidationError):
				online_orders.set_online_order_status("SO-1", "Cancelled")
			doc.cancel.assert_not_called()
			doc.per_billed = 0
			online_orders.set_online_order_status("SO-1", "Cancelled")
			doc.cancel.assert_called_once()

	def test_unknown_status_and_filters_are_refused(self):
		self.order_row("New")
		with self.assertRaises(frappe.ValidationError):
			online_orders.set_online_order_status("SO-1", "Teleported")
		with self.assertRaises(frappe.ValidationError):
			online_orders.list_online_orders(status="Teleported")

	def test_tracking_code_too_short_finds_nothing(self):
		with self.assertRaises(frappe.DoesNotExistError):
			online_orders.track_online_order("abc")

	def test_tracking_shows_no_customer_details(self):
		frappe.db.get_value.side_effect = [
			row(
				name="SO-1",
				company="Duka Ltd",
				custom_online_fulfilment="Ready",
				custom_delivery_method="Pickup",
				custom_online_paid=1,
				grand_total=300,
				transaction_date="2026-10-24",
			),
			"Duka Shop",
		]
		data = online_orders.track_online_order("a-long-enough-token")["data"]
		self.assertEqual(data["status"], "Ready")
		self.assertEqual(data["shop"], "Duka Shop")
		self.assertNotIn("phone", data)
		self.assertNotIn("customer", data)
		self.assertNotIn("address", data)


if __name__ == "__main__":
	unittest.main()
