#!/usr/bin/env python3
"""
Runs the unit tests that need no database, against a real Frappe installed with pip.

A full `bench run-tests` needs MariaDB, Redis, ERPNext and a created site, which takes many minutes
to build. Most of this app's tests are written to need none of that (every Frappe call is mocked),
so they can run in about a minute on a plain Python install of Frappe. That is what this does.

How it works: Frappe is started against an empty throwaway site folder (no database connection), the
language is set to English as a real test run would, and every `test_*.py` file is run, except the
ones named in NEEDS_A_SITE below. A new test file is therefore picked up automatically.

Usage (from the repository root):  python .github/scripts/run_unit_tests.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

# Tests that talk to a real database or Redis. They run on a full bench with:
#   bench --site <site> run-tests --app techsavanna_pos
NEEDS_A_SITE = {
	"techsavanna_pos/api/test_mpesa_api.py": "uses the database",
	"techsavanna_pos/api/test_mpesa_client.py": "uses the database",
	"techsavanna_pos/api/test_payment_gateways.py": "uses the database and Redis",
	"techsavanna_pos/api/test_pos_shift_close.py": "needs an initialised site",
}
# The generated doctype tests are FrappeTestCase classes, which create records in a real database
NEEDS_A_SITE_FOLDERS = ("techsavanna_pos/techsavanna_pos/doctype/",)


def find_tests(root: str) -> tuple[list[str], list[str]]:
	"""The dotted names of the test modules to run, and the paths skipped because they need a site."""
	run, skipped = [], []
	for folder, _dirs, files in os.walk(os.path.join(root, "techsavanna_pos")):
		for name in sorted(files):
			if not (name.startswith("test_") and name.endswith(".py")):
				continue
			path = os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")
			if path in NEEDS_A_SITE or path.startswith(NEEDS_A_SITE_FOLDERS):
				skipped.append(path)
			else:
				run.append(path[: -len(".py")].replace("/", "."))
	return sorted(run), sorted(skipped)


def main() -> int:
	root = os.getcwd()
	sys.path.insert(0, root)

	import frappe

	with tempfile.TemporaryDirectory() as sites:
		os.makedirs(os.path.join(sites, "ci"))
		with open(os.path.join(sites, "apps.txt"), "w") as handle:
			handle.write("frappe\n")
		for path in (
			os.path.join(sites, "common_site_config.json"),
			os.path.join(sites, "ci", "site_config.json"),
		):
			with open(path, "w") as handle:
				json.dump({}, handle)

		frappe.init(site="ci", sites_path=sites)
		frappe.flags.in_test = True
		frappe.local.lang = "en"

		names, skipped = find_tests(root)
		print(f"Running {len(names)} test file(s) on Frappe {frappe.__version__}:")
		for name in names:
			print(f"  {name}")
		print(f"Not run here, because they need a full site ({len(skipped)}):")
		for path in skipped:
			print(f"  {path}")
		print()

		suite = unittest.defaultTestLoader.loadTestsFromNames(names)
		result = unittest.TextTestRunner(verbosity=1).run(suite)
		if result.testsRun == 0:
			print("No tests were found.")
			return 1
		return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
	sys.exit(main())
