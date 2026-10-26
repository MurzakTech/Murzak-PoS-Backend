# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the marketplace calls. What matters: only an owner can open it, a bad or hostile reply from the website
cannot reach the page, and the checkout address always points at the Murzak website. No database or network is
needed; every Frappe and network call is replaced.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe
import requests

from murzak_pos.api import marketplace_api

BASE = "https://murzaktech.tech"
GOOD = {
	"ok": True,
	"groups": [
		{
			"key": "online",
			"items": [
				{
					"id": "starter-storefront",
					"name": "Online Storefront (Managed)",
					"monthlyKes": 500,
					"setupKes": 0,
				},
				{
					"id": "starter-woocommerce",
					"name": "WordPress + WooCommerce (Managed)",
					"monthlyKes": 2500,
					"setupKes": 300,
				},
			],
		}
	],
}


def unwrapped(fn):
	while hasattr(fn, "__wrapped__"):
		fn = fn.__wrapped__
	return fn


class TestCleanGroups(unittest.TestCase):
	def test_keeps_plain_products_and_builds_the_checkout_address(self):
		groups = marketplace_api.clean_groups(GOOD, BASE)
		self.assertEqual([g["key"] for g in groups], ["online"])
		first = groups[0]["items"][0]
		self.assertEqual(first["id"], "starter-storefront")
		self.assertEqual((first["monthly"], first["setup"]), (500, 0))
		self.assertEqual(first["checkout_url"], f"{BASE}/checkout/new?serviceId=starter-storefront")

	def test_drops_anything_odd(self):
		hostile = {
			"groups": [
				{
					"key": "online",
					"items": [
						{"id": "../etc/passwd", "name": "x", "monthlyKes": 100},
						{"id": "has space", "name": "x", "monthlyKes": 100},
						{"id": "free-thing", "name": "x", "monthlyKes": 0},
						{"id": "negative", "name": "x", "monthlyKes": -5},
						{"id": "text-price", "name": "x", "monthlyKes": "lots"},
						"not a dict",
						{
							"id": "good-one",
							"name": "<script>alert(1)</script>" + "y" * 200,
							"monthlyKes": 100.9,
							"setupKes": "x",
						},
					],
				},
				{"key": "Bad Key!", "items": [{"id": "good-two", "name": "n", "monthlyKes": 1}]},
				{"key": "empty", "items": []},
				"nope",
			]
		}
		groups = marketplace_api.clean_groups(hostile, BASE)
		self.assertEqual([g["key"] for g in groups], ["online"])
		items = groups[0]["items"]
		self.assertEqual([i["id"] for i in items], ["good-one"])
		self.assertEqual((items[0]["monthly"], items[0]["setup"]), (100, 0))
		self.assertLessEqual(len(items[0]["name"]), 80)

	def test_checkout_address_cannot_be_redirected_by_the_reply(self):
		reply = {
			"groups": [
				{
					"key": "online",
					"items": [
						{"id": "ok-id", "name": "n", "monthlyKes": 1, "checkout_url": "https://evil.example"}
					],
				}
			]
		}
		url = marketplace_api.clean_groups(reply, BASE)[0]["items"][0]["checkout_url"]
		self.assertTrue(url.startswith(BASE + "/"))

	def test_garbage_replies_give_nothing(self):
		for payload in (None, [], "x", {}, {"groups": "x"}, {"groups": [None]}):
			self.assertEqual(marketplace_api.clean_groups(payload, BASE), [])

	def test_caps_the_size(self):
		many = {
			"groups": [
				{
					"key": f"g{n}",
					"items": [{"id": f"item-{m}", "name": "n", "monthlyKes": 1} for m in range(100)],
				}
				for n in range(50)
			]
		}
		groups = marketplace_api.clean_groups(many, BASE)
		self.assertEqual(len(groups), marketplace_api.MAX_GROUPS)
		self.assertTrue(all(len(g["items"]) == marketplace_api.MAX_ITEMS_PER_GROUP for g in groups))


class TestWebsiteBase(unittest.TestCase):
	def test_default_and_override(self):
		with patch.object(frappe, "conf", {}, create=True):
			self.assertEqual(marketplace_api.website_base(), marketplace_api.DEFAULT_WEBSITE)
		with patch.object(
			frappe, "conf", {"murzak_website_url": "https://staging.murzaktech.tech/"}, create=True
		):
			self.assertEqual(marketplace_api.website_base(), "https://staging.murzaktech.tech")

	def test_refuses_a_plain_http_address(self):
		with patch.object(frappe, "conf", {"murzak_website_url": "http://insecure.example"}, create=True):
			self.assertEqual(marketplace_api.website_base(), marketplace_api.DEFAULT_WEBSITE)


class TestGetMarketplace(unittest.TestCase):
	def setUp(self):
		self.cache = MagicMock()
		self.cache.get_value.return_value = None
		for name, value in (("conf", {}), ("cache", lambda: self.cache)):
			patcher = patch.object(frappe, name, value, create=True)
			patcher.start()
			self.addCleanup(patcher.stop)

	def _ask(self, response=None, error=None):
		with (
			patch.object(marketplace_api, "require_manager"),
			patch.object(marketplace_api.requests, "get") as get,
		):
			if error:
				get.side_effect = error
			else:
				get.return_value = response
			return unwrapped(marketplace_api.get_marketplace)(), get

	def test_returns_the_cleaned_catalogue(self):
		response = MagicMock(status_code=200)
		response.json.return_value = GOOD
		result, get = self._ask(response)
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["currency"], "KES")
		self.assertEqual(result["data"]["groups"][0]["items"][1]["monthly"], 2500)
		self.assertEqual(get.call_args.kwargs["allow_redirects"], False)
		self.cache.set_value.assert_called_once()

	def test_uses_the_cache_without_asking_again(self):
		self.cache.get_value.return_value = GOOD
		result, get = self._ask(MagicMock())
		self.assertTrue(result["success"])
		get.assert_not_called()

	def test_says_so_when_the_website_cannot_be_reached(self):
		result, _get = self._ask(error=requests.ConnectionError("down"))
		self.assertFalse(result["success"])
		self.cache.set_value.assert_not_called()

	def test_a_bad_status_or_body_is_not_cached(self):
		for response in (MagicMock(status_code=500), MagicMock(status_code=302)):
			result, _get = self._ask(response)
			self.assertFalse(result["success"])
		broken = MagicMock(status_code=200)
		broken.json.side_effect = ValueError("not json")
		result, _get = self._ask(broken)
		self.assertFalse(result["success"])
		self.cache.set_value.assert_not_called()

	def test_only_an_owner_may_open_it(self):
		with (
			patch.object(marketplace_api, "require_manager", side_effect=frappe.PermissionError),
			patch.object(marketplace_api.requests, "get") as get,
		):
			with self.assertRaises(frappe.PermissionError):
				unwrapped(marketplace_api.get_marketplace)()
			get.assert_not_called()
