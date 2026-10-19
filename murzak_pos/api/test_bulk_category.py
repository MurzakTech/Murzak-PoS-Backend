# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for `ensure_category`: the older bulk import creates a missing category instead of refusing the row."""

import unittest
from unittest.mock import MagicMock, patch

from murzak_pos.api import product_api


class TestEnsureCategory(unittest.TestCase):
	def test_matches_existing_ignoring_case_and_spaces(self):
		with (
			patch.object(product_api.frappe, "get_all", return_value=["Drinks", "All Item Groups"]),
			patch.object(product_api.frappe, "new_doc") as new_doc,
		):
			self.assertEqual(product_api.ensure_category("  drinks "), "Drinks")
			new_doc.assert_not_called()

	def test_creates_missing_once_per_run(self):
		made = MagicMock()
		made.name = "Beer"
		known = {}
		with (
			patch.object(product_api.frappe, "get_all", return_value=["All Item Groups"]),
			patch.object(product_api.frappe, "new_doc", return_value=made) as new_doc,
		):
			self.assertEqual(product_api.ensure_category("Beer", known), "Beer")
			self.assertEqual(product_api.ensure_category("beer", known), "Beer")
			self.assertEqual(new_doc.call_count, 1)
		self.assertEqual(made.parent_item_group, "All Item Groups")
		self.assertEqual(made.is_group, 0)
		made.insert.assert_called_once_with(ignore_permissions=True)

	def test_blank_means_default_group(self):
		self.assertEqual(product_api.ensure_category("   "), "All Item Groups")
