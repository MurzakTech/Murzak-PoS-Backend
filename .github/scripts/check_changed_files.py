#!/usr/bin/env python3
"""
Checks the files a pull request changes, the way the repository's pre-commit hooks would.

Why only the changed files: the older files already have hundreds of lint findings, so checking
everything would fail forever and teach people to ignore it. Instead the rule is "leave it no worse":

- a NEW Python file must pass ruff (lint and format) cleanly
- an EXISTING Python file may not gain lint findings compared with the base branch
- every changed Python file must parse; every changed JSON and YAML file must be valid
- no leftover merge-conflict markers, and no trailing spaces on lines the pull request adds

Usage: check_changed_files.py <base-ref>      (for example: origin/main)
Needs `ruff` on the PATH (the version pinned in .pre-commit-config.yaml) and, optionally, PyYAML.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys


def run(*cmd: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
	return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=check)


def git(*args: str) -> str:
	return run("git", *args).stdout


def ruff_findings(path: str, source: str | None = None) -> int:
	"""How many lint findings ruff reports for the file, or for the given text standing in for it."""
	if source is None:
		result = run("ruff", "check", path, "--output-format", "concise", check=False)
	else:
		result = run(
			"ruff",
			"check",
			"--stdin-filename",
			path,
			"--output-format",
			"concise",
			"-",
			stdin=source,
			check=False,
		)
	return sum(1 for line in result.stdout.splitlines() if re.match(rf"{re.escape(path)}:\d+:\d+:", line))


def added_lines(base: str, path: str) -> list[tuple[int, str]]:
	"""The (line number, text) of every line the pull request adds to the file."""
	diff = git("diff", "-U0", f"{base}...HEAD", "--", path)
	added, number = [], 0
	for line in diff.splitlines():
		header = re.match(r"@@ -\S+ \+(\d+)(?:,\d+)? @@", line)
		if header:
			number = int(header.group(1))
		elif line.startswith("+") and not line.startswith("+++"):
			added.append((number, line[1:]))
			number += 1
	return added


def main(base: str) -> int:
	changed = []
	for entry in git("diff", "--name-status", "--diff-filter=AM", f"{base}...HEAD").splitlines():
		status, path = entry.split("\t", 1)
		changed.append((status, path))

	problems: list[str] = []
	checked = 0

	for status, path in changed:
		is_new = status == "A"
		with open(path, encoding="utf-8", errors="replace") as handle:
			text = handle.read()
		checked += 1

		if path.endswith(".py"):
			try:
				ast.parse(text, filename=path)
			except SyntaxError as error:
				problems.append(f"{path}:{error.lineno}: does not parse ({error.msg})")
				continue

			if is_new:
				found = ruff_findings(path)
				if found:
					problems.append(f"{path}: new file has {found} ruff finding(s); run `ruff check {path}`")
				if run("ruff", "format", "--check", path, check=False).returncode:
					problems.append(f"{path}: new file is not formatted; run `ruff format {path}`")
			else:
				before = ruff_findings(path, git("show", f"{base}:{path}"))
				after = ruff_findings(path)
				if after > before:
					problems.append(
						f"{path}: lint findings went from {before} to {after}; run `ruff check {path}` for the new ones"
					)

			for number, line in added_lines(base, path):
				if line != line.rstrip():
					problems.append(f"{path}:{number}: trailing spaces on an added line")

		elif path.endswith(".json"):
			try:
				json.loads(text)
			except ValueError as error:
				problems.append(f"{path}: not valid JSON ({error})")

		elif path.endswith((".yml", ".yaml")):
			try:
				import yaml

				yaml.safe_load(text)
			except ImportError:
				pass
			except Exception as error:
				problems.append(f"{path}: not valid YAML ({error})")

		if re.search(r"^(<{7} |={7}$|>{7} )", text, re.MULTILINE):
			problems.append(f"{path}: contains a leftover merge-conflict marker")

	print(f"Checked {checked} changed file(s) against {base}.")
	for problem in problems:
		print(f"  PROBLEM  {problem}")
	if problems:
		print(f"{len(problems)} problem(s) found.")
		return 1
	print("All good.")
	return 0


if __name__ == "__main__":
	if len(sys.argv) != 2:
		sys.exit(__doc__)
	sys.exit(main(sys.argv[1]))
