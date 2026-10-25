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
		self.items.append(
			frappe._dict(values, item_name=values["item_code"], amount=values["qty"] * values["rate"])
		)
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
			[
				{"item_code": "RICE", "qty": 1},
				{"item_code": "RICE", "qty": "2"},
				{"item_code": "TEA", "qty": 1},
			]
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

	def test_the_owner_can_list_hidden_and_all_products_but_customers_only_see_shown(self):
		self.assertEqual(online_catalogue.online_filters("Duka Ltd")["custom_show_online"], 1)
		self.assertEqual(online_catalogue.online_filters("Duka Ltd", "shown")["custom_show_online"], 1)
		self.assertEqual(online_catalogue.online_filters("Duka Ltd", "hidden")["custom_show_online"], 0)
		self.assertNotIn("custom_show_online", online_catalogue.online_filters("Duka Ltd", "all"))
		# Whatever is asked, a business only ever sees its own products
		for visibility in online_catalogue.VISIBILITY:
			self.assertEqual(
				online_catalogue.online_filters("Duka Ltd", visibility)["custom_company"], "Duka Ltd"
			)

	def test_a_row_says_whether_it_is_shown_and_an_old_product_counts_as_shown(self):
		items = [
			row(
				name="A",
				item_name="A",
				item_group="",
				stock_uom="",
				standard_rate=1,
				is_stock_item=0,
				custom_show_online=0,
			),
			row(name="B", item_name="B", item_group="", stock_uom="", standard_rate=1, is_stock_item=0),
		]
		with patch.object(online_catalogue, "available_stock", return_value={}):
			rows = online_catalogue.rows_for(items, "Main - D")
		self.assertEqual([r["show_online"] for r in rows], [False, True])

	def test_stock_is_what_is_left_after_reservations(self):
		bins = [
			row(item_code="RICE", actual_qty=10, reserved_qty=3),
			row(item_code="TEA", actual_qty=2, reserved_qty=5),
		]
		with patch.object(frappe, "get_all", return_value=bins):
			self.assertEqual(
				online_catalogue.available_stock(["RICE", "TEA"], "Main - D"), {"RICE": 7.0, "TEA": 0.0}
			)

	def test_no_warehouse_means_no_stock_lookup(self):
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(online_catalogue.available_stock(["RICE"], ""), {})
			get_all.assert_not_called()

	def test_rows_show_service_always_available_and_empty_shelf_not(self):
		items = [
			row(
				name="RICE",
				item_name="Rice",
				item_group="Food",
				stock_uom="Kg",
				standard_rate=150,
				is_stock_item=1,
			),
			row(
				name="DELIVERY",
				item_name="Delivery",
				item_group="Services",
				stock_uom="Nos",
				standard_rate=100,
				is_stock_item=0,
			),
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
		with patch.object(
			online_catalogue, "list_items", side_effect=lambda *a, page=1, **k: pages.get(page, [])
		):
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
		args = dict(
			company="Duka Ltd", channel="Website", lines=[{"item_code": "RICE", "qty": 2}], phone="0712345678"
		)
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


class TestDeliveryFeeOnTheOrder(unittest.TestCase):
	def setUp(self):
		self.order = FakeOrder()
		self.rice = row(name="RICE", item_name="Rice", standard_rate=150.0, stock_uom="Kg", is_stock_item=1)
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(frappe, "new_doc", return_value=self.order),
			patch.object(online_orders, "nowdate", return_value="2026-10-24"),
			patch.object(online_orders, "customer_for", return_value="Amina"),
			patch.object(online_orders, "_item_for_order", return_value=self.rice),
			patch.object(online_orders, "delivery_item", return_value="DELIVERY-D"),
			patch.object(online_catalogue, "default_warehouse", return_value="Main - D"),
			patch.object(online_catalogue, "available_stock", return_value={"RICE": 5.0}),
			patch("murzak_pos.api.realtime_events.notify_online_order"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		frappe.db.get_value.return_value = None

	def place(self, **kw):
		args = dict(company="Duka Ltd", channel="Website", lines=[{"item_code": "RICE", "qty": 2}])
		args.update(kw)
		return online_orders.place_order(**args)

	def test_the_fee_is_a_delivery_line_after_the_goods(self):
		result = self.place(delivery_fee=200)
		codes = [i.item_code for i in self.order.items]
		self.assertEqual(codes, ["RICE", "DELIVERY-D"])
		self.assertEqual(self.order.items[1].rate, 200.0)
		self.assertEqual(result["total"], 500.0)
		self.assertEqual(result["delivery_fee"], 200.0)

	def test_no_fee_means_no_delivery_line(self):
		self.place()
		self.assertEqual([i.item_code for i in self.order.items], ["RICE"])

	def test_the_minimum_order_counts_goods_not_delivery(self):
		with self.assertRaises(frappe.ValidationError):
			self.place(minimum_total=400, delivery_fee=500)

	def test_the_basket_total_uses_the_shops_prices(self):
		self.assertEqual(online_orders.subtotal_for("Duka Ltd", [{"item_code": "RICE", "qty": 3}]), 450.0)
		online_orders._item_for_order.return_value = None
		with self.assertRaises(frappe.ValidationError):
			online_orders.subtotal_for("Duka Ltd", [{"item_code": "RICE", "qty": 3}])


class TestDeliveryItem(unittest.TestCase):
	def setUp(self):
		patches = [patch.object(frappe, "db", MagicMock(), create=True), patch.object(frappe, "new_doc")]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def test_an_existing_delivery_item_is_reused(self):
		frappe.db.get_value.return_value = "DELIVERY-D"
		self.assertEqual(online_orders.delivery_item("Duka Ltd"), "DELIVERY-D")
		frappe.new_doc.assert_not_called()

	def test_it_is_made_once_as_a_service_kept_off_the_online_store(self):
		frappe.db.get_value.side_effect = [None, "D", "Services"]
		item = MagicMock()
		item.name = "DELIVERY-D"
		frappe.new_doc.return_value = item
		self.assertEqual(online_orders.delivery_item("Duka Ltd"), "DELIVERY-D")
		self.assertEqual(item.is_stock_item, 0)
		self.assertEqual(item.custom_show_online, 0)
		self.assertEqual(item.custom_company, "Duka Ltd")
		self.assertEqual(item.item_code, "DELIVERY-D")
		item.insert.assert_called_once_with(ignore_permissions=True)


class TestStaffCalls(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(online_orders, "require_online_orders"),
			patch.object(online_orders, "resolve_company", return_value="Duka Ltd"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def order_row(self, status, docstatus=1, company="Duka Ltd", channel="Website"):
		frappe.db.get_value.return_value = row(
			name="SO-1",
			company=company,
			custom_online_channel=channel,
			custom_online_fulfilment=status,
			docstatus=docstatus,
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


class TestSettlingAnOrderAtTheTill(unittest.TestCase):
	"""A till sale that names an online order: checked first, then linked, paid and the order's stock released."""

	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(online_orders, "require_online_orders"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def order(self, **changes):
		fields = dict(
			company="Duka Ltd",
			docstatus=1,
			status="To Deliver and Bill",
			custom_online_channel="Website",
			custom_online_fulfilment="Confirmed",
			custom_till_sale=None,
		)
		fields.update(changes)
		frappe.db.get_value.return_value = row(**fields)

	def test_the_order_is_locked_while_the_sale_is_checked(self):
		self.order()
		online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")
		self.assertTrue(frappe.db.get_value.call_args.kwargs["for_update"])

	def test_the_caller_needs_the_orders_permission(self):
		self.order()
		online_orders.require_online_orders.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")
		frappe.db.get_value.assert_not_called()

	def test_a_missing_foreign_or_till_order_is_not_found(self):
		for changes in ({"company": "Other Ltd"}, {"custom_online_channel": None}):
			self.order(**changes)
			with self.assertRaises(frappe.DoesNotExistError):
				online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")
		frappe.db.get_value.return_value = None
		with self.assertRaises(frappe.DoesNotExistError):
			online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")

	def test_a_cancelled_or_closed_order_cannot_be_sold(self):
		for changes in ({"custom_online_fulfilment": "Cancelled"}, {"status": "Closed"}, {"docstatus": 2}):
			self.order(**changes)
			with self.assertRaises(frappe.ValidationError):
				online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")

	def test_an_order_cannot_be_sold_twice(self):
		self.order(custom_till_sale="POS-INV-9")
		with self.assertRaises(frappe.ValidationError) as refused:
			online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")
		self.assertIn("POS-INV-9", str(refused.exception))

	def test_a_good_order_passes(self):
		self.order()
		online_orders.check_order_for_till_sale("SO-1", "Duka Ltd")

	def test_the_sale_carries_the_order_and_the_online_channel(self):
		sale = MagicMock()
		sale.meta.has_field.return_value = True
		online_orders.mark_sale_as_online(sale, "SO-1")
		sale.set.assert_any_call("custom_online_order", "SO-1")
		sale.set.assert_any_call("custom_sales_channel", "Online")

	def test_an_ordinary_sale_is_left_alone(self):
		sale = MagicMock()
		online_orders.mark_sale_as_online(sale, None)
		sale.set.assert_not_called()

	def test_settling_links_both_ways_marks_paid_and_releases_the_hold(self):
		sale = frappe._dict(doctype="POS Invoice", name="POS-INV-9", outstanding_amount=0)
		order = MagicMock(status="To Deliver and Bill")
		order.flags = frappe._dict()
		with patch.object(frappe, "get_doc", return_value=order):
			online_orders.settle_order_with_sale("SO-1", sale)
		frappe.db.set_value.assert_called_once_with(
			"Sales Order",
			"SO-1",
			{"custom_till_sale_type": "POS Invoice", "custom_till_sale": "POS-INV-9", "custom_online_paid": 1},
		)
		order.update_status.assert_called_once_with("Closed")

	def test_a_sale_on_credit_does_not_mark_the_order_paid(self):
		sale = frappe._dict(doctype="Sales Invoice", name="SINV-9", outstanding_amount=500)
		order = MagicMock(status="To Deliver and Bill")
		order.flags = frappe._dict()
		with patch.object(frappe, "get_doc", return_value=order):
			online_orders.settle_order_with_sale("SO-1", sale)
		values = frappe.db.set_value.call_args.args[2]
		self.assertNotIn("custom_online_paid", values)
		self.assertEqual(values["custom_till_sale"], "SINV-9")
		order.update_status.assert_called_once_with("Closed")

	def test_an_order_already_closed_is_not_closed_again(self):
		sale = frappe._dict(doctype="POS Invoice", name="POS-INV-9", outstanding_amount=0)
		order = MagicMock(status="Closed")
		with patch.object(frappe, "get_doc", return_value=order):
			online_orders.settle_order_with_sale("SO-1", sale)
		order.update_status.assert_not_called()

	def test_a_retried_sale_finishes_an_unsettled_order_once(self):
		sale = frappe._dict(doctype="POS Invoice", name="POS-INV-9", outstanding_amount=0)
		order = MagicMock(status="To Deliver and Bill")
		order.flags = frappe._dict()
		frappe.db.get_value.return_value = row(
			company="Duka Ltd", custom_online_channel="Website", custom_till_sale=None
		)
		with patch.object(frappe, "get_doc", side_effect=[sale, order]):
			online_orders.settle_recorded_sale("SO-1", "Duka Ltd", "POS Invoice", "POS-INV-9")
		frappe.db.set_value.assert_called_once()

	def test_a_retry_for_a_settled_order_changes_nothing(self):
		frappe.db.get_value.return_value = row(
			company="Duka Ltd", custom_online_channel="Website", custom_till_sale="POS-INV-9"
		)
		online_orders.settle_recorded_sale("SO-1", "Duka Ltd", "POS Invoice", "POS-INV-9")
		online_orders.settle_recorded_sale(None, "Duka Ltd", "POS Invoice", "POS-INV-9")
		frappe.db.set_value.assert_not_called()


class TestStaffCallsOnASoldOrder(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(online_orders, "require_online_orders"),
			patch.object(online_orders, "resolve_company", return_value="Duka Ltd"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def order_row(self, sold_in=None, paid=0):
		frappe.db.get_value.return_value = row(
			name="SO-1",
			company="Duka Ltd",
			custom_online_channel="Website",
			custom_online_fulfilment="Confirmed",
			docstatus=1,
			custom_till_sale=sold_in,
			custom_online_paid=paid,
		)

	def test_a_sold_order_cannot_be_invoiced_again(self):
		self.order_row(sold_in="POS-INV-9")
		with self.assertRaises(frappe.ValidationError):
			online_orders.create_invoice_for_online_order("SO-1")

	def test_a_sold_order_cannot_be_cancelled(self):
		self.order_row(sold_in="POS-INV-9")
		with patch.object(frappe, "get_doc") as get_doc:
			with self.assertRaises(frappe.ValidationError):
				online_orders.set_online_order_status("SO-1", "Cancelled")
		get_doc.assert_not_called()

	def test_a_sold_order_can_still_be_marked_delivered(self):
		self.order_row(sold_in="POS-INV-9")
		self.assertTrue(online_orders.set_online_order_status("SO-1", "Delivered")["success"])

	def test_marking_paid_twice_succeeds_and_changes_nothing(self):
		self.order_row(paid=0)
		frappe.db.get_value.side_effect = [
			frappe.db.get_value.return_value,
			0,
			frappe.db.get_value.return_value,
			1,
		]
		self.assertTrue(online_orders.mark_online_order_paid("SO-1")["success"])
		self.assertTrue(online_orders.mark_online_order_paid("SO-1")["success"])
		frappe.db.set_value.assert_called_once_with("Sales Order", "SO-1", "custom_online_paid", 1)


if __name__ == "__main__":
	unittest.main()
