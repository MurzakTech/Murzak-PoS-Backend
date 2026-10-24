# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the WooCommerce connector: sending products without duplicating or silently failing, keeping stock
current, and receiving the shop's orders safely. No database or network is needed; every Frappe call and every
call to the shop is replaced.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import ecommerce_api, online_webhooks

SETTINGS = {
	"enabled": True,
	"store_url": "https://myshop.example.com",
	"consumer_key": "ck_1",
	"consumer_secret": "cs_1",
	"auto_sync_stock": True,
	"sync_orders": True,
}


def product(code, price=100.0, stock=5.0):
	return {
		"item_code": code,
		"item_name": code.title(),
		"price": price,
		"stock": stock,
		"in_stock": bool(stock),
		"description": "",
		"image": "",
		"category": "Food",
		"unit": "Nos",
	}


class FakeShop:
	"""A WooCommerce shop that already has some SKUs, and records the batches it is sent."""

	def __init__(self, existing=(), reject=()):
		self.ids = {sku: 1000 + n for n, sku in enumerate(existing)}
		self.reject = set(reject)
		self.lookups = []
		self.batches = []

	def __call__(self, settings, method, path, params=None, body=None):
		if path == "products" and method == "GET":
			skus = params["sku"].split(",")
			self.lookups.append(skus)
			return [{"id": self.ids[s], "sku": s} for s in skus if s in self.ids]
		self.batches.append(body)
		answer = {"create": [], "update": []}
		for key in ("create", "update"):
			for entry in body[key]:
				code = entry.get("sku")
				answer[key].append({"error": {"message": "bad"}} if code in self.reject else {"id": 1})
		return answer


class TestPushProducts(unittest.TestCase):
	def test_new_products_are_created_and_known_ones_updated_not_duplicated(self):
		shop = FakeShop(existing=["RICE"])
		with patch.object(ecommerce_api, "woo_request", shop):
			result = ecommerce_api.push_products(SETTINGS, [product("RICE"), product("TEA")])
		self.assertEqual((result["created"], result["updated"], result["failed"]), (1, 1, 0))
		batch = shop.batches[0]
		self.assertEqual([c["sku"] for c in batch["create"]], ["TEA"])
		self.assertEqual(batch["update"][0]["id"], 1000)
		# An existing product keeps its website name and pictures
		self.assertNotIn("name", batch["update"][0])
		self.assertNotIn("images", batch["update"][0])

	def test_a_large_catalogue_goes_in_batches(self):
		shop = FakeShop()
		rows = [product(f"I{n}") for n in range(120)]
		with patch.object(ecommerce_api, "woo_request", shop):
			result = ecommerce_api.push_products(SETTINGS, rows)
		self.assertEqual(result["created"], 120)
		self.assertEqual([len(b) for b in shop.lookups], [50, 50, 20])

	def test_a_product_the_shop_rejects_is_reported_not_counted(self):
		shop = FakeShop(reject=["TEA"])
		with patch.object(ecommerce_api, "woo_request", shop):
			result = ecommerce_api.push_products(SETTINGS, [product("RICE"), product("TEA")])
		self.assertEqual((result["created"], result["failed"]), (1, 1))
		self.assertTrue(result["errors"][0].startswith("TEA"))

	def test_a_refusing_shop_fails_everything_once_and_stops(self):
		calls = MagicMock(side_effect=ecommerce_api.WooError("The online store refused the keys."))
		with patch.object(ecommerce_api, "woo_request", calls):
			result = ecommerce_api.push_products(SETTINGS, [product(f"I{n}") for n in range(120)])
		self.assertEqual(result["failed"], 50)
		self.assertEqual(calls.call_count, 1)
		self.assertIn("refused", result["errors"][0])

	def test_stock_only_touches_known_products_and_leaves_prices_alone(self):
		shop = FakeShop(existing=["RICE"])
		with patch.object(ecommerce_api, "woo_request", shop):
			result = ecommerce_api.push_products(
				SETTINGS, [product("RICE", stock=0), product("TEA")], stock_only=True
			)
		self.assertEqual((result["created"], result["updated"], result["skipped"]), (0, 1, 1))
		change = shop.batches[0]["update"][0]
		self.assertNotIn("regular_price", change)
		self.assertEqual(change["stock_quantity"], 0)
		self.assertEqual(change["stock_status"], "outofstock")

	def test_stock_payloads(self):
		self.assertEqual(ecommerce_api._stock_payload({"stock": 2.9})["stock_quantity"], 2)
		self.assertEqual(ecommerce_api._stock_payload({"stock": -3})["stock_quantity"], 0)
		self.assertFalse(ecommerce_api._stock_payload({"stock": None})["manage_stock"])


class TestRunCatalogSync(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(ecommerce_api, "read_settings", return_value=dict(SETTINGS)),
			patch.object(ecommerce_api, "write_settings"),
			patch.object(ecommerce_api, "now_datetime", return_value="2026-10-24 10:00:00"),
			patch.object(ecommerce_api.online_catalogue, "default_warehouse", return_value="Main - D"),
			patch.object(
				ecommerce_api.online_catalogue, "iter_all_items", return_value=iter([[product("RICE")]])
			),
		]
		self.mocks = [p.start() for p in patches]
		for p in patches:
			self.addCleanup(p.stop)

	def sync(self, outcome):
		with patch.object(ecommerce_api, "push_products", return_value=outcome):
			return ecommerce_api.run_catalog_sync("Duka Ltd")

	def test_success_is_recorded_honestly(self):
		out = self.sync({"created": 1, "updated": 0, "failed": 0, "skipped": 0, "errors": []})
		self.assertTrue(out["success"])
		self.assertEqual(out["data"]["status"], "success")
		self.assertEqual(out["data"]["online_synced_count"], 1)
		self.assertEqual(ecommerce_api.write_settings.call_args.args[1]["last_sync_status"], "success")

	def test_nothing_sent_is_a_failure_not_a_success(self):
		out = self.sync(
			{
				"created": 0,
				"updated": 0,
				"failed": 1,
				"skipped": 0,
				"errors": ["The online store refused the keys."],
			}
		)
		self.assertFalse(out["success"])
		self.assertEqual(out["data"]["status"], "failed")
		self.assertIn("refused", out["message"])
		self.assertEqual(ecommerce_api.write_settings.call_args.args[1]["last_sync_status"], "failed")

	def test_some_sent_some_not_is_partial(self):
		out = self.sync({"created": 1, "updated": 0, "failed": 2, "skipped": 0, "errors": ["TEA: bad"]})
		self.assertTrue(out["success"])
		self.assertEqual(out["data"]["status"], "partial")
		self.assertEqual(out["data"]["error_count"], 2)

	def test_no_warehouse_is_a_clear_refusal(self):
		ecommerce_api.online_catalogue.default_warehouse.return_value = ""
		out = ecommerce_api.run_catalog_sync("Duka Ltd")
		self.assertFalse(out["success"])


class TestStockQueue(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(frappe, "enqueue"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def invoice(self):
		doc = MagicMock()
		doc.get.side_effect = lambda k, d=None: {
			"company": "Duka Ltd",
			"items": [
				frappe._dict(item_code="RICE"),
				frappe._dict(item_code="TEA"),
				frappe._dict(item_code="RICE"),
			],
		}.get(k, d)
		return doc

	def test_a_sale_queues_one_push_of_its_products(self):
		frappe.db.get_default.return_value = json.dumps({"enabled": True, "store_url": "https://x.co"})
		ecommerce_api.queue_stock_push(self.invoice())
		kwargs = frappe.enqueue.call_args.kwargs
		self.assertEqual(kwargs["item_codes"], ["RICE", "TEA"])
		self.assertEqual(kwargs["company"], "Duka Ltd")
		self.assertTrue(kwargs["enqueue_after_commit"])

	def test_a_business_without_an_online_shop_queues_nothing(self):
		for stored in (
			None,
			json.dumps({"enabled": False, "store_url": "https://x.co"}),
			json.dumps({"enabled": True, "store_url": "https://x.co", "auto_sync_stock": False}),
		):
			frappe.db.get_default.return_value = stored
			ecommerce_api.queue_stock_push(self.invoice())
		frappe.enqueue.assert_not_called()

	def test_a_fault_never_stops_the_sale(self):
		frappe.db.get_default.return_value = json.dumps({"enabled": True, "store_url": "https://x.co"})
		frappe.enqueue.side_effect = RuntimeError("queue is down")
		with patch.object(frappe, "log_error") as log:
			ecommerce_api.queue_stock_push(self.invoice())
		log.assert_called_once()


def woo_order(**over):
	order = {
		"id": 501,
		"status": "processing",
		"currency": "KES",
		"total": "1100.00",
		"shipping_total": "100.00",
		"payment_method_title": "M-Pesa",
		"date_paid": "2026-10-24T09:00:00",
		"customer_note": "Call at the gate",
		"billing": {
			"first_name": "Amina",
			"last_name": "Wanjiru",
			"phone": "+254 712 345 678",
			"email": "a@x.co",
		},
		"shipping": {"address_1": "Moi Avenue 4", "city": "Nairobi"},
		"shipping_lines": [{"method_id": "flat_rate"}],
		"line_items": [
			{"sku": "RICE", "name": "Rice", "quantity": 2, "total": "800.00"},
			{"sku": "TEA", "name": "Tea", "quantity": 1, "total": "200.00"},
		],
	}
	order.update(over)
	return order


class TestWebhook(unittest.TestCase):
	def test_signature(self):
		body = b'{"id": 1}'
		good = base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()
		self.assertTrue(online_webhooks.signature_ok("s3cret", body, good))
		self.assertFalse(online_webhooks.signature_ok("other", body, good))
		self.assertFalse(online_webhooks.signature_ok("s3cret", body + b" ", good))
		self.assertFalse(online_webhooks.signature_ok("s3cret", body, None))
		self.assertFalse(online_webhooks.signature_ok("", body, good))

	def test_order_is_turned_into_pos_order_arguments(self):
		args = online_webhooks.order_arguments(woo_order())
		self.assertEqual(args["channel"], "WooCommerce")
		self.assertEqual(args["customer_name"], "Amina Wanjiru")
		self.assertEqual(args["phone"], "+254712345678")
		self.assertEqual(args["address"], "Moi Avenue 4, Nairobi")
		self.assertEqual(args["delivery_method"], "Delivery")
		self.assertTrue(args["paid"])
		self.assertTrue(args["trust_prices"])
		self.assertEqual(args["external_ref"], "501")
		# The price is what the customer paid per unit: 800 for 2 kilos is 400 each
		self.assertEqual(args["lines"][0], {"item_code": "RICE", "qty": 2.0, "rate": 400.0})
		self.assertIn("Call at the gate", args["notes"])

	def test_local_pickup_is_pickup(self):
		args = online_webhooks.order_arguments(woo_order(shipping_lines=[{"method_id": "local_pickup"}]))
		self.assertEqual(args["delivery_method"], "Pickup")

	def test_a_product_with_no_sku_is_refused_with_its_name(self):
		order = woo_order(line_items=[{"sku": "", "name": "Mystery Box", "quantity": 1, "total": "10"}])
		with self.assertRaises(frappe.ValidationError) as caught:
			online_webhooks.order_arguments(order)
		self.assertIn("Mystery Box", str(caught.exception))


class TestHandleOrder(unittest.TestCase):
	def setUp(self):
		patches = [
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(frappe, "log_error"),
			patch.object(online_webhooks.ecommerce_api, "write_settings"),
			patch.object(online_webhooks, "now_datetime", return_value="2026-10-24 10:00:00"),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def test_a_paid_order_is_recorded(self):
		with patch.object(
			online_webhooks.online_orders, "place_order", return_value={"created": True, "order": "SO-1"}
		) as place:
			out = online_webhooks.handle_order("Duka Ltd", dict(SETTINGS), woo_order())
		self.assertEqual(out, {"recorded": True, "order": "SO-1"})
		self.assertEqual(place.call_args.args[0], "Duka Ltd")

	def test_a_repeated_delivery_is_not_a_second_order(self):
		with patch.object(
			online_webhooks.online_orders, "place_order", return_value={"created": False, "order": "SO-1"}
		):
			out = online_webhooks.handle_order("Duka Ltd", dict(SETTINGS), woo_order())
		self.assertFalse(out["recorded"])

	def test_an_order_waiting_for_payment_is_left_for_later(self):
		with patch.object(online_webhooks.online_orders, "place_order") as place:
			out = online_webhooks.handle_order("Duka Ltd", dict(SETTINGS), woo_order(status="pending"))
		place.assert_not_called()
		self.assertEqual(out["ignored"], "pending")

	def test_an_order_that_cannot_be_recorded_is_logged_and_shown_to_the_owner(self):
		boom = frappe.ValidationError("Product ZZZ is not available.")
		stored = dict(SETTINGS)
		with patch.object(online_webhooks.online_orders, "place_order", side_effect=boom):
			out = online_webhooks.handle_order("Duka Ltd", stored, woo_order())
		self.assertFalse(out["recorded"])
		self.assertIn("501", stored["last_order_message"])
		frappe.log_error.assert_called_once()

	def test_a_cancelled_website_order_cancels_the_pos_order(self):
		frappe.db.get_value.return_value = frappe._dict(name="SO-1", custom_online_fulfilment="New")
		with patch.object(online_webhooks.online_orders, "cancel_order_document") as cancel:
			out = online_webhooks.handle_order("Duka Ltd", dict(SETTINGS), woo_order(status="cancelled"))
		cancel.assert_called_once_with("SO-1")
		self.assertEqual(out["cancelled"], "SO-1")

	def test_a_delivered_order_is_not_cancelled_by_the_website(self):
		frappe.db.get_value.return_value = frappe._dict(name="SO-1", custom_online_fulfilment="Delivered")
		with patch.object(online_webhooks.online_orders, "cancel_order_document") as cancel:
			online_webhooks.handle_order("Duka Ltd", dict(SETTINGS), woo_order(status="cancelled"))
		cancel.assert_not_called()


class TestWebhookEndpoint(unittest.TestCase):
	def call(self, token="tok", body=b"{}", signature=None, stored=None):
		stored = dict(SETTINGS, webhook_secret="s3cret") if stored is None else stored
		request = MagicMock()
		request.get_data.return_value = body
		with (
			patch.object(frappe, "request", request, create=True),
			patch.object(frappe, "get_request_header", return_value=signature),
			patch.object(
				online_webhooks.ecommerce_api,
				"company_for_webhook_token",
				return_value="Duka Ltd" if token else None,
			),
			patch.object(online_webhooks.ecommerce_api, "read_settings", return_value=stored),
		):
			return self.unwrapped()(t=token)

	@staticmethod
	def unwrapped():
		"""The endpoint without its rate limit, which needs a real request and cache."""
		fn = online_webhooks.woocommerce_order
		while hasattr(fn, "__wrapped__"):
			fn = fn.__wrapped__
		return fn

	def sign(self, body):
		return base64.b64encode(hmac.new(b"s3cret", body, hashlib.sha256).digest()).decode()

	def test_unknown_address_and_bad_signature_are_both_refused(self):
		with self.assertRaises(frappe.AuthenticationError):
			self.call(token=None, signature="x")
		with self.assertRaises(frappe.AuthenticationError):
			self.call(body=b'{"line_items": []}', signature="forged")
		with self.assertRaises(frappe.AuthenticationError):
			self.call(body=b'{"line_items": []}', signature=None)

	def test_a_business_that_never_made_a_secret_cannot_be_called(self):
		body = b'{"line_items": []}'
		with self.assertRaises(frappe.AuthenticationError):
			self.call(body=body, signature=self.sign(body), stored=dict(SETTINGS))

	def test_woocommerces_test_message_is_accepted_and_ignored(self):
		body = b"webhook_id=7"
		self.assertEqual(self.call(body=body, signature=self.sign(body)), {"ignored": "not an order"})

	def test_a_signed_order_is_handled(self):
		body = json.dumps(woo_order()).encode()
		with patch.object(online_webhooks, "handle_order", return_value={"recorded": True}) as handle:
			out = self.call(body=body, signature=self.sign(body))
		self.assertEqual(out, {"recorded": True})
		self.assertEqual(handle.call_args.args[0], "Duka Ltd")

	def test_order_sync_switched_off_ignores_orders(self):
		body = json.dumps(woo_order()).encode()
		out = self.call(
			body=body,
			signature=self.sign(body),
			stored=dict(SETTINGS, webhook_secret="s3cret", sync_orders=False),
		)
		self.assertIn("ignored", out)


if __name__ == "__main__":
	unittest.main()
