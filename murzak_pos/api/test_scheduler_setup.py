import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import scheduler_setup as setup


class TestEnsureSchedulerOn(unittest.TestCase):
	def run_with(self, current):
		db = MagicMock(**{"get_single_value.return_value": current})
		with patch.object(frappe, "db", db, create=True):
			setup.ensure_scheduler_on()
		return db

	def test_it_is_switched_on_when_off(self):
		db = self.run_with(0)
		db.set_single_value.assert_called_once_with("System Settings", "enable_scheduler", 1)

	def test_nothing_changes_when_already_on(self):
		db = self.run_with(1)
		db.set_single_value.assert_not_called()

	def test_a_failure_never_stops_an_update(self):
		broken = MagicMock(**{"get_single_value.side_effect": RuntimeError("locked")})
		with (
			patch.object(frappe, "db", broken, create=True),
			patch.object(setup.frappe, "log_error") as log,
		):
			setup.ensure_scheduler_on()
		log.assert_called_once()
