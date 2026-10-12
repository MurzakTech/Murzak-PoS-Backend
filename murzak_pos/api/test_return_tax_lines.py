# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""A sales return reverses the VAT of the sale it returns. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import sales_api


class CopyTaxLinesCase(unittest.TestCase):
	def test_each_tax_line_of_the_sale_is_put_on_the_return(self):
		lines = [
			frappe._dict(
				charge_type="On Net Total",
				account_head="VAT - A",
				description="VAT",
				rate=16,
				included_in_print_rate=1,
			)
		]
		doc = MagicMock()
		with patch.object(sales_api.frappe, "get_all", return_value=lines):
			sales_api._copy_tax_lines("SINV-1", doc)
		doc.append.assert_called_once_with("taxes", dict(lines[0]))

	def test_a_sale_with_no_tax_lines_adds_nothing(self):
		doc = MagicMock()
		with patch.object(sales_api.frappe, "get_all", return_value=[]):
			sales_api._copy_tax_lines("SINV-1", doc)
		doc.append.assert_not_called()
