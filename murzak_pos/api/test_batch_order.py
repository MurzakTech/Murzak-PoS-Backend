# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the batch a sale takes from when none is chosen. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

from murzak_pos.api import batch_order


class PickEarliestExpiryFirstCase(unittest.TestCase):
	def run_with(self, current):
		db = MagicMock()
		db.get_single_value.return_value = current
		with patch.object(batch_order.frappe, "db", db, create=True):
			batch_order.pick_earliest_expiry_first()
		return db

	def test_the_erpnext_default_is_switched_to_earliest_expiry(self):
		db = self.run_with("FIFO")
		db.set_single_value.assert_called_once_with(
			"Stock Settings", "pick_serial_and_batch_based_on", "Expiry"
		)

	def test_a_shop_that_chose_another_order_is_left_alone(self):
		self.run_with("LIFO").set_single_value.assert_not_called()

	def test_a_shop_already_on_earliest_expiry_is_left_alone(self):
		self.run_with("Expiry").set_single_value.assert_not_called()
