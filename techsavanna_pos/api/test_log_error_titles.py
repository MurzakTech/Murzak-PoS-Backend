# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Guards the order of arguments to frappe.log_error.

Frappe 15 reads frappe.log_error(title, message). The title is saved in a 140-character
field, so passing a long message first makes saving the Error Log itself fail with
"value too long", which hides the real error from the user. Every positional title must
therefore be a fixed string (or _("fixed string")) of at most 140 characters.
Needs no database: it only reads the source files.
"""

import ast
import pathlib
import unittest

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
MAX_TITLE = 140


def _fixed_title(node):
	"""The text of a fixed title, or None when the title is built at run time."""
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return node.value
	if (
		isinstance(node, ast.Call)
		and isinstance(node.func, ast.Name)
		and node.func.id == "_"
		and len(node.args) == 1
		and isinstance(node.args[0], ast.Constant)
		and isinstance(node.args[0].value, str)
	):
		return node.args[0].value
	return None


def _log_error_calls():
	for path in sorted(APP_DIR.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		for node in ast.walk(tree):
			func = getattr(node, "func", None)
			if (
				isinstance(node, ast.Call)
				and isinstance(func, ast.Attribute)
				and func.attr == "log_error"
				and isinstance(func.value, ast.Name)
				and func.value.id == "frappe"
			):
				yield path.relative_to(APP_DIR.parent), node


class TestLogErrorTitles(unittest.TestCase):
	def test_titles_are_short_fixed_strings(self):
		problems = []
		for path, call in _log_error_calls():
			where = f"{path}:{call.lineno}"
			if call.args:
				title = _fixed_title(call.args[0])
				if title is None:
					problems.append(f"{where}: first argument is the title; pass a fixed string first, the message second")
				elif len(title) > MAX_TITLE:
					problems.append(f"{where}: title is longer than {MAX_TITLE} characters")
		self.assertEqual(problems, [], "\n" + "\n".join(problems))

	def test_finds_the_calls(self):
		self.assertGreater(sum(1 for _ in _log_error_calls()), 100)


if __name__ == "__main__":
	unittest.main()
