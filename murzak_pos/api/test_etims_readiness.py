# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the eTIMS readiness checklist. No database is needed."""

import unittest

from murzak_pos.api import etims_readiness

ALL_DONE = {
	"kenya_app": True,
	"active_settings": True,
	"branch": True,
	"dimension": True,
	"vat_setup": True,
	"vat_registered": True,
}


class ChecklistCase(unittest.TestCase):
	def test_everything_in_place_is_ready(self):
		checklist = etims_readiness.build_checklist(ALL_DONE)
		self.assertTrue(etims_readiness.is_ready(checklist))
		self.assertEqual(len(checklist), 6)

	def test_one_missing_piece_means_not_ready_and_is_named(self):
		checklist = etims_readiness.build_checklist({**ALL_DONE, "branch": False})
		self.assertFalse(etims_readiness.is_ready(checklist))
		missing = [row["key"] for row in checklist if not row["done"]]
		self.assertEqual(missing, ["branch"])

	def test_every_row_explains_what_to_do(self):
		for row in etims_readiness.build_checklist({key: False for key in ALL_DONE}):
			self.assertTrue(row["help"])
			self.assertTrue(row["label"])


if __name__ == "__main__":
	unittest.main()
