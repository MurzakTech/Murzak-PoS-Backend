# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Checks that every DocType in this app has the code class Frappe looks for.

Frappe finds a DocType's code by its name with the spaces removed: "MPESA Settings" must be a class
called MPESASettings. A different spelling (MpesaSettings) used to be ignored, but current Frappe 15 then
decides the DocType has no code and DELETES it on every `bench migrate`, which the deploy runs each time.

No database is needed: this only reads the DocType files.
"""

import glob
import json
import os
import re
import unittest

DOCTYPES = os.path.join(os.path.dirname(__file__), "..", "murzak_pos", "doctype")


class DocTypeControllerNamesCase(unittest.TestCase):
	def test_every_doctype_has_the_class_frappe_expects(self):
		files = sorted(glob.glob(os.path.join(DOCTYPES, "*", "*.json")))
		self.assertTrue(files, "No DocType files were found; the folder may have moved")

		wrong = []
		for path in files:
			with open(path) as handle:
				name = json.load(handle)["name"]
			expected = name.replace(" ", "").replace("-", "")
			with open(path[: -len(".json")] + ".py") as handle:
				classes = re.findall(r"^class (\w+)\(", handle.read(), re.MULTILINE)
			if expected not in classes:
				wrong.append(f"{name}: expected a class named {expected}, found {classes or 'none'}")

		self.assertEqual(wrong, [], "These DocTypes would be deleted by migrate:\n" + "\n".join(wrong))


if __name__ == "__main__":
	unittest.main()
