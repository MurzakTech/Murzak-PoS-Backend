# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Unit tests for _new_api_keys in staff_api: a business owner (who is not a System Manager) can add a person.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import staff_api


class TestNewApiKeys(unittest.TestCase):
	def setUp(self):
		self.session = frappe._dict(user="owner@shop.test")
		self.seen = []

		def set_user(user):
			self.session.user = user

		def generate_keys(user_name):
			# Frappe's own call: it refuses everyone but a System Manager (the Administrator is one)
			self.seen.append((user_name, self.session.user))
			if self.session.user != "Administrator":
				raise frappe.PermissionError("No permission")
			return {"api_key": "key", "api_secret": "secret"}

		self.generate_keys = MagicMock(side_effect=generate_keys)
		# Stand-ins for each test, put back afterwards, as the other unit tests do
		for p in (
			patch.object(frappe, "session", self.session, create=True),
			patch.object(frappe, "set_user", MagicMock(side_effect=set_user), create=True),
			patch.object(staff_api, "generate_keys", self.generate_keys),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_the_keys_are_made_for_the_new_person_and_the_owner_is_put_back(self):
		keys = staff_api._new_api_keys("new@shop.test")
		self.assertEqual(keys["api_key"], "key")
		self.assertEqual(self.seen, [("new@shop.test", "Administrator")])
		self.assertEqual(self.session.user, "owner@shop.test")

	def test_the_owner_is_put_back_even_when_making_the_keys_fails(self):
		self.generate_keys.side_effect = frappe.ValidationError("boom")
		with self.assertRaises(frappe.ValidationError):
			staff_api._new_api_keys("new@shop.test")
		self.assertEqual(self.session.user, "owner@shop.test")


if __name__ == "__main__":
	unittest.main()
