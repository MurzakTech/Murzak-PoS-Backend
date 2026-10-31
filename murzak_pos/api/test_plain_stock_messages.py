# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Guards the wording of the messages a person meets when they change stock.

The messages of the bulk stock update and of the stock entries and counts it uses are read by shop owners, not by
developers: no field names ("item_code", "expense_account"), no "endpoint" or "parameter", no "warehouse" (the screens
say "store") and no "reconciliation". Needs no database: it only reads the source file.
"""

import ast
import pathlib
import unittest

SOURCE = pathlib.Path(__file__).resolve().parent / "inventory_api.py"
FUNCTIONS = ("bulk_update_stock", "create_stock_entry", "create_stock_reconciliation")
DEVELOPER_WORDS = (
	"item_code",
	"expense_account",
	"stock_adjustment_account",
	"endpoint",
	"parameter",
	"json",
	"warehouse",
	"reconciliation",
	"stock entry",
	"non-empty",
	"non-inventory",
)


def _messages(node):
	"""Every fixed message in a function: the text of _("...") and of "message" / "error" values."""
	found = []
	for child in ast.walk(node):
		if isinstance(child, ast.Call) and getattr(child.func, "id", "") == "_" and child.args:
			if isinstance(child.args[0], ast.Constant) and isinstance(child.args[0].value, str):
				found.append(child.args[0].value)
		if isinstance(child, ast.Dict):
			for key, value in zip(child.keys, child.values, strict=False):
				if isinstance(key, ast.Constant) and key.value in ("message", "error"):
					if isinstance(value, ast.Constant) and isinstance(value.value, str):
						found.append(value.value)
	return found


class TestStockMessagesArePlain(unittest.TestCase):
	def test_no_developer_words_reach_the_person(self):
		tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
		functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
		checked = 0
		for name in FUNCTIONS:
			for message in _messages(functions[name]):
				checked += 1
				for word in DEVELOPER_WORDS:
					with self.subTest(function=name, message=message, word=word):
						self.assertNotIn(word, message.lower())
		self.assertGreater(checked, 20, "expected to read the messages of all three functions")


if __name__ == "__main__":
	unittest.main()
