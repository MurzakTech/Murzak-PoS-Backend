# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""The Swahili translation file is complete and keeps every placeholder. No database is needed."""

import ast
import csv
import os
import re
import unittest

APP = os.path.dirname(os.path.dirname(__file__))
SW_FILE = os.path.join(APP, "translations", "sw.csv")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def translations() -> dict[str, str]:
	with open(SW_FILE, encoding="utf-8", newline="") as handle:
		return {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}


def messages_in_code() -> set[str]:
	found = set()
	for root, _dirs, files in os.walk(APP):
		for name in files:
			if not name.endswith(".py") or name.startswith("test_"):
				continue
			with open(os.path.join(root, name), encoding="utf-8") as handle:
				tree = ast.parse(handle.read())
			for node in ast.walk(tree):
				if (
					isinstance(node, ast.Call)
					and getattr(node.func, "id", "") in ("_", "__")
					and node.args
					and isinstance(node.args[0], ast.Constant)
					and isinstance(node.args[0].value, str)
				):
					found.add(node.args[0].value)
	return found


class SwahiliFileCase(unittest.TestCase):
	def test_every_translation_has_text_and_keeps_the_placeholders_of_its_source(self):
		for source, target in translations().items():
			with self.subTest(source=source[:60]):
				self.assertTrue(target.strip(), "the translation is empty")
				self.assertEqual(sorted(PLACEHOLDER.findall(source)), sorted(PLACEHOLDER.findall(target)))

	def test_every_message_in_the_code_has_a_swahili_translation(self):
		missing = sorted(messages_in_code() - set(translations()))
		self.assertEqual(
			missing,
			[],
			"these messages have no Swahili yet; add them to murzak_pos/translations/sw.csv "
			"(see docs/TRANSLATIONS.md): " + "; ".join(m[:60] for m in missing[:10]),
		)


class LanguageCallCase(unittest.TestCase):
	def test_only_listed_languages_can_be_chosen(self):
		from unittest.mock import MagicMock, patch

		import frappe

		from murzak_pos.api import language_api

		with patch.object(language_api.frappe, "session", MagicMock(user="a@x")):
			with self.assertRaises(frappe.ValidationError):
				language_api.set_language("fr")
		self.assertEqual(set(language_api.SUPPORTED), {"en", "sw"})
