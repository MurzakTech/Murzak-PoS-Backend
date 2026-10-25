# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the time zone a shop keeps its dates in. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

from murzak_pos.api import shop_timezone as tz


class UseKenyaTimeCase(unittest.TestCase):
	def run_with(self, current):
		db = MagicMock()
		db.get_single_value.return_value = current
		with patch.object(tz.frappe, "db", db, create=True), patch.object(tz.frappe, "clear_cache") as clear:
			tz.use_kenya_time()
		return db, clear

	def test_a_shop_on_the_india_default_moves_to_kenya(self):
		db, clear = self.run_with("Asia/Kolkata")
		db.set_single_value.assert_called_once_with("System Settings", "time_zone", "Africa/Nairobi")
		clear.assert_called_once_with()

	def test_a_shop_with_no_time_zone_chosen_moves_to_kenya(self):
		for blank in (None, ""):
			with self.subTest(blank=blank):
				db, _clear = self.run_with(blank)
				db.set_single_value.assert_called_once_with("System Settings", "time_zone", "Africa/Nairobi")

	def test_a_shop_already_on_kenya_time_is_left_alone(self):
		db, clear = self.run_with("Africa/Nairobi")
		db.set_single_value.assert_not_called()
		clear.assert_not_called()

	def test_a_shop_that_chose_another_time_zone_is_left_alone(self):
		for chosen in ("Africa/Kampala", "Europe/London", "America/New_York"):
			with self.subTest(chosen=chosen):
				db, _clear = self.run_with(chosen)
				db.set_single_value.assert_not_called()
