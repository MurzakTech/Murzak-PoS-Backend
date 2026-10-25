# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the hosted online store: the owner's settings and the public calls a customer makes. The things that
matter most are what a stranger can and cannot do: reach a closed store, set a price, flood the shop with orders or
see another business's products. No database is needed; every Frappe call is replaced.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import delivery_fees, online_orders, online_store_api, storefront_api

OPEN_STORE = {
	**online_store_api.DEFAULTS,
	"enabled": True,
	"slug": "duka-ya-amina",
	"store_name": "Duka ya Amina",
	"whatsapp_number": "254712345678",
	"accept_cash": True,
	"accept_mpesa": True,
	"pickup_address": "Moi Avenue 4",
}


def unwrapped(fn):
	"""The call without its rate limit, which needs a real request and cache."""
	while hasattr(fn, "__wrapped__"):
		fn = fn.__wrapped__
	return fn


class TestSlugRules(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "db", MagicMock(), create=True)
		patcher.start()
		self.addCleanup(patcher.stop)
		frappe.db.get_default.return_value = None

	def test_good_names(self):
		for slug in ("duka-ya-amina", "abc", "shop254", "a" * 40):
			self.assertIsNone(online_store_api.slug_problem(slug, "Duka Ltd"), slug)

	def test_bad_and_reserved_names(self):
		for slug in (
			"ab",
			"-abc",
			"abc-",
			"Has Space",
			"UPPER",
			"a" * 41,
			"with_underscore",
			"api",
			"admin",
			"login",
			"",
		):
			self.assertIsNotNone(online_store_api.slug_problem(slug, "Duka Ltd"), slug)

	def test_a_name_taken_by_another_business_is_refused_but_your_own_is_fine(self):
		frappe.db.get_default.return_value = "Other Ltd"
		self.assertIsNotNone(online_store_api.slug_problem("duka-ya-amina", "Duka Ltd"))
		frappe.db.get_default.return_value = "Duka Ltd"
		self.assertIsNone(online_store_api.slug_problem("duka-ya-amina", "Duka Ltd"))

	def test_company_for_slug_ignores_nonsense(self):
		self.assertIsNone(online_store_api.company_for_slug("../../etc"))
		self.assertIsNone(online_store_api.company_for_slug(None))
		frappe.db.get_default.assert_not_called()

	def test_whatsapp_numbers(self):
		self.assertEqual(online_store_api.normalize_whatsapp("0712 345 678"), "254712345678")
		self.assertEqual(online_store_api.normalize_whatsapp("+254 712 345 678"), "254712345678")
		self.assertEqual(online_store_api.normalize_whatsapp(""), "")
		for bad in ("12", "abc123", "0123456789012345678"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				online_store_api.normalize_whatsapp(bad)


class TestSaveStore(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(online_store_api, "require_online_settings"),
			patch.object(online_store_api, "resolve_company", return_value="Duka Ltd"),
			patch.object(online_store_api, "read_store", return_value=dict(online_store_api.DEFAULTS)),
			patch.object(online_store_api, "company_for_slug", return_value=None),
			patch.object(online_store_api, "_mpesa_ready", return_value=True),
			patch.object(online_store_api, "now_datetime", return_value="2026-10-24 10:00:00"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		frappe.db.exists.return_value = True

	def save(self, **fields):
		return online_store_api.save_online_store(json.dumps(fields))

	def test_a_complete_store_can_open_and_its_name_is_reserved(self):
		out = self.save(
			enabled=1, slug="duka-ya-amina", accept_cash=1, delivery_enabled=1, whatsapp_number="0712345678"
		)
		self.assertTrue(out["success"], out)
		self.assertEqual(out["data"]["whatsapp_number"], "254712345678")
		frappe.db.set_default.assert_any_call("murzak_store_slug_duka-ya-amina", "Duka Ltd")

	def test_cannot_open_without_a_name_a_way_to_receive_or_a_way_to_pay(self):
		self.assertFalse(self.save(enabled=1, slug="", accept_cash=1)["success"])
		self.assertFalse(
			self.save(enabled=1, slug="duka", delivery_enabled=0, pickup_enabled=0, accept_cash=1)["success"]
		)
		self.assertFalse(self.save(enabled=1, slug="duka", accept_cash=0, accept_mpesa=0)["success"])

	def test_mpesa_needs_mpesa_set_up(self):
		online_store_api._mpesa_ready.return_value = False
		out = self.save(enabled=1, slug="duka", accept_cash=0, accept_mpesa=1)
		self.assertFalse(out["success"])
		self.assertIn("M-Pesa", out["message"])

	def test_a_taken_name_is_refused(self):
		online_store_api.company_for_slug.return_value = "Other Ltd"
		self.assertFalse(self.save(slug="duka-ya-amina")["success"])

	def test_changing_the_name_releases_the_old_one(self):
		online_store_api.read_store.return_value = {**online_store_api.DEFAULTS, "slug": "old-name"}
		self.save(slug="new-name", accept_cash=1)
		frappe.db.set_default.assert_any_call("murzak_store_slug_old-name", "")
		frappe.db.set_default.assert_any_call("murzak_store_slug_new-name", "Duka Ltd")

	def test_delivery_rules_are_saved_checked_and_needed_before_opening(self):
		out = self.save(
			enabled=1,
			slug="duka",
			accept_cash=1,
			delivery_mode="area",
			delivery_areas=[{"name": "Westlands", "fee": 200}],
		)
		self.assertTrue(out["success"], out)
		self.assertEqual(out["data"]["delivery_areas"], [{"name": "Westlands", "fee": 200.0}])
		# An area shop with no areas, or a distance shop with no location, cannot open
		self.assertFalse(self.save(enabled=1, slug="duka", accept_cash=1, delivery_mode="area")["success"])
		self.assertFalse(
			self.save(enabled=1, slug="duka", accept_cash=1, delivery_mode="distance")["success"]
		)
		# ... unless it only offers pickup
		self.assertTrue(
			self.save(
				enabled=1,
				slug="duka",
				accept_cash=1,
				delivery_enabled=0,
				pickup_enabled=1,
				delivery_mode="area",
			)["success"]
		)

	def test_bad_delivery_rules_are_refused_with_a_message(self):
		out = self.save(delivery_flat_fee=-5)
		self.assertFalse(out["success"])
		self.assertTrue(out["message"])
		self.assertFalse(self.save(delivery_mode="drone")["success"])

	def test_a_closed_store_may_be_saved_unfinished(self):
		self.assertTrue(self.save(enabled=0, store_name="Draft")["success"])

	def test_bad_inputs_are_refused_not_crashed(self):
		self.assertFalse(online_store_api.save_online_store("not json")["success"])
		self.assertFalse(online_store_api.save_online_store(json.dumps([1]))["success"])
		self.assertFalse(self.save(minimum_order="lots")["success"])
		frappe.db.exists.return_value = False
		self.assertFalse(self.save(warehouse="Nowhere - X")["success"])

	def test_only_the_businesss_own_products_are_shown_or_hidden(self):
		with (
			patch.object(online_store_api, "require_online_catalogue"),
			patch.object(frappe, "get_all", return_value=["RICE"]) as get_all,
		):
			out = online_store_api.set_items_online(["RICE", "OTHERS-ITEM"], show=0)
		self.assertEqual(get_all.call_args.kwargs["filters"]["custom_company"], "Duka Ltd")
		frappe.db.set_value.assert_called_once_with("Item", "RICE", "custom_show_online", 0)
		self.assertEqual(out["data"], {"updated": 1, "skipped": 1, "show": False})

	def test_bulk_hide_is_bounded(self):
		with patch.object(online_store_api, "require_online_catalogue"):
			with self.assertRaises(frappe.ValidationError):
				online_store_api.set_items_online(
					[f"I{n}" for n in range(online_store_api.MAX_BULK_ITEMS + 1)]
				)
			with self.assertRaises(frappe.ValidationError):
				online_store_api.set_items_online([])


class TestReadStore(unittest.TestCase):
	def test_one_shops_areas_are_never_shared_with_another(self):
		with patch.object(frappe, "db", MagicMock(), create=True):
			frappe.db.get_default.return_value = None
			first = online_store_api.read_store("Duka Ltd")
			first["delivery_areas"].append({"name": "Westlands", "fee": 200})
			second = online_store_api.read_store("Other Ltd")
		self.assertEqual(second["delivery_areas"], [])
		self.assertEqual(online_store_api.DEFAULTS["delivery_areas"], [])

	def test_a_store_saved_before_delivery_fees_still_delivers_free(self):
		old = json.dumps({"enabled": True, "slug": "duka", "delivery_enabled": True})
		with patch.object(frappe, "db", MagicMock(), create=True):
			frappe.db.get_default.return_value = old
			shop = online_store_api.read_store("Duka Ltd")
		self.assertEqual(shop["delivery_mode"], "free")
		self.assertTrue(shop["enabled"])


class TestOpenStore(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "db", MagicMock(), create=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_unknown_closed_and_mismatched_stores_all_look_the_same(self):
		cases = [
			(None, None),
			("Duka Ltd", {**OPEN_STORE, "enabled": False}),
			("Duka Ltd", {**OPEN_STORE, "slug": "someone-else"}),
		]
		for company, settings in cases:
			with (
				patch.object(online_store_api, "company_for_slug", return_value=company),
				patch.object(online_store_api, "read_store", return_value=settings),
			):
				with self.assertRaises(frappe.DoesNotExistError):
					storefront_api.open_store("duka-ya-amina")

	def test_an_open_store_is_found(self):
		with (
			patch.object(online_store_api, "company_for_slug", return_value="Duka Ltd"),
			patch.object(online_store_api, "read_store", return_value=OPEN_STORE),
		):
			self.assertEqual(storefront_api.open_store("Duka-Ya-Amina")[0], "Duka Ltd")

	def test_what_customers_see_has_no_internals(self):
		frappe.db.get_value.return_value = "KES"
		shown = storefront_api.public_store("Duka Ltd", OPEN_STORE)
		self.assertEqual(shown["payment_options"], ["cash", "mpesa"])
		self.assertNotIn("warehouse", shown)
		self.assertNotIn("enabled", shown)

	def test_exact_stock_is_hidden_unless_low(self):
		base = {
			"item_code": "A",
			"item_name": "A",
			"category": "",
			"unit": "",
			"price": 1,
			"description": "",
			"image": "",
			"in_stock": True,
		}
		self.assertIsNone(storefront_api._public_product({**base, "stock": 400})["low_stock"])
		self.assertEqual(storefront_api._public_product({**base, "stock": 3})["low_stock"], 3)
		self.assertIsNone(storefront_api._public_product({**base, "stock": None})["low_stock"])
		self.assertNotIn("stock", storefront_api._public_product({**base, "stock": 400}))


class TestCheckout(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(storefront_api, "open_store", return_value=("Duka Ltd", dict(OPEN_STORE))),
			patch.object(storefront_api, "_within_limit", return_value=True),
			patch.object(storefront_api, "_currency", return_value="KES"),
			patch.object(
				online_orders,
				"place_order",
				return_value={
					"created": True,
					"order": "SO-1",
					"token": "tok-123456789",
					"total": 300.0,
					"lines": [{"item_code": "RICE", "item_name": "Rice", "qty": 2.0, "rate": 150.0}],
				},
			),
			patch.object(
				storefront_api,
				"send_mpesa_prompt",
				return_value={"sent": True, "message": "Check your phone"},
			),
		]
		self.mocks = [p.start() for p in patches]
		for p in patches:
			self.addCleanup(p.stop)
		self.checkout = unwrapped(storefront_api.checkout)

	def go(self, **over):
		args = dict(
			slug="duka-ya-amina",
			customer_name="Amina Wanjiru",
			phone="0712 345 678",
			lines=[{"item_code": "RICE", "qty": 2, "rate": 1}],
			delivery_method="Delivery",
			payment="cash",
			address="Moi Avenue 4, Nairobi",
		)
		args.update(over)
		return self.checkout(**args)

	def test_a_good_order_returns_a_tracking_code_and_a_whatsapp_link(self):
		out = self.go()
		self.assertTrue(out["success"])
		self.assertEqual(out["data"]["order"], "SO-1")
		self.assertEqual(out["data"]["token"], "tok-123456789")
		self.assertTrue(out["data"]["whatsapp_link"].startswith("https://wa.me/254712345678?text="))
		self.assertIn("SO-1", out["data"]["whatsapp_link"])
		self.assertIsNone(out["data"]["payment_prompt"])
		storefront_api.send_mpesa_prompt.assert_not_called()

	def test_the_order_is_recorded_for_the_website_channel_with_the_phone_normalised(self):
		self.go()
		call = online_orders.place_order.call_args
		self.assertEqual(call.args[:2], ("Duka Ltd", "Website"))
		self.assertEqual(call.kwargs["phone"], "254712345678")
		self.assertFalse(call.kwargs.get("trust_prices", False))

	def test_mpesa_sends_a_prompt_for_the_orders_total(self):
		out = self.go(payment="mpesa")
		storefront_api.send_mpesa_prompt.assert_called_once_with("Duka Ltd", "SO-1", "254712345678", 300.0)
		self.assertTrue(out["data"]["payment_prompt"]["sent"])

	def test_a_failed_prompt_does_not_lose_the_order(self):
		storefront_api.send_mpesa_prompt.return_value = {"sent": False, "message": "M-Pesa is busy"}
		out = self.go(payment="mpesa")
		self.assertTrue(out["success"])
		self.assertFalse(out["data"]["payment_prompt"]["sent"])

	def test_pickup_uses_the_shops_address_not_the_customers(self):
		self.go(delivery_method="Pickup", address="ignored")
		self.assertEqual(online_orders.place_order.call_args.kwargs["address"], "Moi Avenue 4")

	def test_bad_requests_are_refused_before_anything_is_recorded(self):
		bad = [
			dict(customer_name="A"),
			dict(phone="12345"),
			dict(delivery_method="Drone"),
			dict(delivery_method="Delivery", address=""),
			dict(payment="bitcoin"),
		]
		for over in bad:
			with self.assertRaises(frappe.ValidationError, msg=str(over)):
				self.go(**over)
		online_orders.place_order.assert_not_called()

	def test_a_method_the_shop_does_not_offer_is_refused(self):
		storefront_api.open_store.return_value = ("Duka Ltd", {**OPEN_STORE, "pickup_enabled": False})
		with self.assertRaises(frappe.ValidationError):
			self.go(delivery_method="Pickup")
		storefront_api.open_store.return_value = ("Duka Ltd", {**OPEN_STORE, "accept_mpesa": False})
		with self.assertRaises(frappe.ValidationError):
			self.go(payment="mpesa")

	def test_too_many_orders_from_one_phone_are_refused(self):
		storefront_api._within_limit.return_value = False
		with self.assertRaises(frappe.ValidationError):
			self.go()
		online_orders.place_order.assert_not_called()

	def test_the_shops_minimum_order_is_passed_on(self):
		storefront_api.open_store.return_value = ("Duka Ltd", {**OPEN_STORE, "minimum_order": 500})
		self.go()
		self.assertEqual(online_orders.place_order.call_args.kwargs["minimum_total"], 500.0)


class TestCheckoutDeliveryFees(TestCheckout):
	"""Checkout with a shop that charges for delivery."""

	def shop(self, **over):
		storefront_api.open_store.return_value = ("Duka Ltd", {**OPEN_STORE, **over})

	def setUp(self):
		super().setUp()
		patcher = patch.object(online_orders, "subtotal_for", return_value=800.0)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_flat_fee_is_added_by_the_server(self):
		self.shop(delivery_mode="flat", delivery_flat_fee=150)
		self.go()
		self.assertEqual(online_orders.place_order.call_args.kwargs["delivery_fee"], 150.0)

	def test_an_area_fee_and_the_area_goes_on_the_address(self):
		self.shop(delivery_mode="area", delivery_areas=[{"name": "Westlands", "fee": 200}])
		self.go(delivery_area="westlands")
		kwargs = online_orders.place_order.call_args.kwargs
		self.assertEqual(kwargs["delivery_fee"], 200.0)
		self.assertTrue(kwargs["address"].startswith("westlands, "))

	def test_an_area_the_shop_does_not_serve_is_refused_before_anything_is_recorded(self):
		self.shop(delivery_mode="area", delivery_areas=[{"name": "Westlands", "fee": 200}])
		with self.assertRaises(frappe.ValidationError):
			self.go(delivery_area="Mombasa")
		online_orders.place_order.assert_not_called()

	def test_distance_pricing_uses_the_customers_location(self):
		self.shop(
			delivery_mode="distance",
			shop_latitude=0.0,
			shop_longitude=36.0,
			distance_per_km=10,
			distance_max_km=100,
			road_factor=1.0,
		)
		self.go(latitude="0.1", longitude="36.0")
		fee = online_orders.place_order.call_args.kwargs["delivery_fee"]
		self.assertAlmostEqual(fee, 112.0, delta=1)
		with self.assertRaises(frappe.ValidationError):
			self.go(latitude="2", longitude="36")

	def test_pickup_never_pays_a_delivery_fee(self):
		self.shop(delivery_mode="flat", delivery_flat_fee=150)
		self.go(delivery_method="Pickup")
		self.assertEqual(online_orders.place_order.call_args.kwargs["delivery_fee"], 0.0)

	def test_free_above_applies_to_the_goods_total(self):
		self.shop(delivery_mode="flat", delivery_flat_fee=150, free_delivery_above=500)
		self.go()
		self.assertEqual(online_orders.place_order.call_args.kwargs["delivery_fee"], 0.0)

	def test_the_browser_cannot_set_the_fee(self):
		self.shop(delivery_mode="flat", delivery_flat_fee=150)
		with self.assertRaises(TypeError):
			self.go(delivery_fee=0)

	def test_quote_tells_the_customer_the_fee_or_why_not(self):
		self.shop(delivery_mode="area", delivery_areas=[{"name": "Westlands", "fee": 200}])
		quote = unwrapped(storefront_api.quote_delivery)
		good = quote("duka-ya-amina", [{"item_code": "RICE", "qty": 1}], delivery_area="Westlands")
		self.assertEqual(good["data"]["fee"], 200.0)
		bad = quote("duka-ya-amina", [{"item_code": "RICE", "qty": 1}], delivery_area="Mombasa")
		self.assertFalse(bad["data"]["deliverable"])
		self.assertTrue(bad["data"]["message"])
		self.shop(delivery_mode="distance", shop_latitude=0.0, shop_longitude=36.0, distance_max_km=50)
		with self.assertRaises(frappe.ValidationError):
			quote("duka-ya-amina", [{"item_code": "RICE", "qty": 1}], latitude="x", longitude="36")

	def test_quote_refused_for_a_shop_that_does_not_deliver(self):
		self.shop(delivery_enabled=False)
		with self.assertRaises(frappe.ValidationError):
			unwrapped(storefront_api.quote_delivery)("duka-ya-amina", [{"item_code": "RICE", "qty": 1}])

	def test_customers_are_told_the_rules_but_not_where_the_shop_is(self):
		shown = storefront_api.public_delivery(
			{
				**OPEN_STORE,
				"delivery_mode": "distance",
				"shop_latitude": -1.2,
				"shop_longitude": 36.8,
				"distance_max_km": 20,
				"free_delivery_above": 3000,
			}
		)
		self.assertTrue(shown["needs_location"])
		self.assertEqual((shown["longest_distance_km"], shown["free_above"]), (20.0, 3000.0))
		self.assertNotIn("shop_latitude", json.dumps(shown))
		self.assertIsNone(
			storefront_api.public_delivery({**OPEN_STORE, "delivery_mode": "free"})["free_above"]
		)


class TestPayForOrder(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(
				storefront_api,
				"send_mpesa_prompt",
				return_value={"sent": True, "message": "Check your phone"},
			),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		self.pay = unwrapped(storefront_api.pay_for_order)

	def order(self, **over):
		fields = dict(
			name="SO-1",
			company="Duka Ltd",
			docstatus=1,
			grand_total=300,
			custom_online_payment="M-Pesa",
			custom_online_paid=0,
			custom_online_contact="254712345678",
			custom_online_fulfilment="New",
		)
		fields.update(over)
		frappe.db.get_value.return_value = frappe._dict(fields)

	def test_an_unpaid_mpesa_order_gets_a_new_prompt(self):
		self.order()
		out = self.pay("a-long-enough-token")
		self.assertTrue(out["success"])
		storefront_api.send_mpesa_prompt.assert_called_once_with("Duka Ltd", "SO-1", "254712345678", 300)

	def test_a_paid_order_is_not_charged_again(self):
		self.order(custom_online_paid=1)
		self.assertTrue(self.pay("a-long-enough-token")["data"]["paid"])
		storefront_api.send_mpesa_prompt.assert_not_called()

	def test_finished_and_cash_orders_and_unknown_codes_are_refused(self):
		self.order(custom_online_fulfilment="Cancelled")
		with self.assertRaises(frappe.ValidationError):
			self.pay("a-long-enough-token")
		self.order(custom_online_payment="Pay on delivery or pickup")
		with self.assertRaises(frappe.ValidationError):
			self.pay("a-long-enough-token")
		frappe.db.get_value.return_value = None
		with self.assertRaises(frappe.DoesNotExistError):
			self.pay("a-long-enough-token")
		with self.assertRaises(frappe.DoesNotExistError):
			self.pay("short")
		storefront_api.send_mpesa_prompt.assert_not_called()


class TestPaymentMarksOrderPaid(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "db", MagicMock(), create=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def doc(self, **over):
		fields = dict(status="Success", invoice_type="Sales Order", invoice="SO-1", company="Duka Ltd")
		fields.update(over)
		return frappe._dict(fields)

	def test_a_successful_payment_marks_the_order_paid(self):
		frappe.db.get_value.return_value = "Duka Ltd"
		online_orders.on_payment_update(self.doc())
		frappe.db.set_value.assert_called_once_with("Sales Order", "SO-1", "custom_online_paid", 1)

	def test_other_payments_and_other_businesses_change_nothing(self):
		frappe.db.get_value.return_value = "Duka Ltd"
		online_orders.on_payment_update(self.doc(status="Pending"))
		online_orders.on_payment_update(self.doc(status="Failed"))
		online_orders.on_payment_update(self.doc(invoice_type="Sales Invoice"))
		online_orders.on_payment_update(self.doc(invoice=None))
		frappe.db.get_value.return_value = "Other Ltd"
		online_orders.on_payment_update(self.doc())
		frappe.db.set_value.assert_not_called()


if __name__ == "__main__":
	unittest.main()
