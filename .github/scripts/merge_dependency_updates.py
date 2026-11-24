#!/usr/bin/env python3
"""
Ships the safe dependency updates that Dependabot has opened, at night, one at a time.

Why this exists: a merge into `main` deploys to every shop. Merged at 11am on a trading day, a library
update would go live while the tills are busy. This runs from a night-time schedule instead, so the
deploy lands when the shops are closed, and it only touches an update when all of these are true:

- the pull request was opened by Dependabot and is not a draft
- every check on it has finished and passed (nothing pending, nothing failed), and GitHub says it is clean
- no package in it jumps to a new MAJOR version (a major version is where things break; those stay open
  for a person to read and decide). A version we cannot read counts as unsafe.

Only ONE pull request is merged per run. If two things break after an update, one change a night means
it is obvious which one did it, and it can be reverted on its own.

A merge made with the workflow's own token does not start the deploy workflow (GitHub blocks that on
purpose, to stop loops), so after a merge that changes more than the `.github` folder this starts the
deploy itself. A merge that only changes workflow files has nothing to deploy.

Switch it off without a code change: set the repository variable DEPENDENCY_AUTOMERGE to "false"
(Settings > Secrets and variables > Actions > Variables). It then only reports what it would have done.

Usage: merge_dependency_updates.py [--dry-run]
Needs the `gh` command and GH_TOKEN. Uses only the Python standard library.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

DEPLOY_WORKFLOW = os.environ.get("DEPLOY_WORKFLOW", "deploy.yml")
MAX_MERGES_PER_RUN = 1
PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}

# "Bump lodash from 4.17.20 to 4.17.21" and "Updates `lodash` from 4.17.20 to 4.17.21"
SENTENCE_CHANGE = re.compile(r"\bfrom\s+`?v?(\d+(?:\.\d+)*)[^\s`]*`?\s+to\s+`?v?(\d+(?:\.\d+)*)", re.I)
# Grouped updates also list a table row per package: | [lodash](https://...) | `4.17.20` | `4.17.21` |
TABLE_CHANGE = re.compile(
	r"\|\s*\[[^\]]+\]\([^)]*\)\s*\|\s*`?v?(\d+(?:\.\d+)*)[^|`]*`?\s*\|\s*`?v?(\d+(?:\.\d+)*)[^|`]*`?\s*\|"
)


def _numbers(version: str) -> list[int]:
	return [int(part) for part in version.split(".")]


def major_of(version: str) -> tuple[int, ...]:
	"""What counts as the 'major' part. For 0.x versions the minor number counts too, because in 0.x
	a minor step is allowed to break things (0.9 to 0.10 is treated like 1 to 2)."""
	parts = _numbers(version)
	if parts[0] == 0 and len(parts) > 1:
		return (0, parts[1])
	return (parts[0],)


def version_changes(text: str) -> list[tuple[str, str]]:
	"""Every 'from X to Y' version change the pull request text states."""
	found = SENTENCE_CHANGE.findall(text or "") + TABLE_CHANGE.findall(text or "")
	return list(dict.fromkeys(found))


def check_problem(rollup: list[dict]) -> str | None:
	"""Why the checks are not all green, or None when every one has finished and passed."""
	if not rollup:
		return "no checks have run on it yet"
	for item in rollup:
		name = item.get("name") or item.get("context") or "a check"
		if item.get("__typename") == "StatusContext":
			if item.get("state") != "SUCCESS":
				return f"{name} is {str(item.get('state')).lower()}"
		elif item.get("status") != "COMPLETED":
			return f"{name} has not finished"
		elif item.get("conclusion") not in PASSING:
			return f"{name} {str(item.get('conclusion')).lower()}"
	return None


def decide(pr: dict) -> tuple[bool, str]:
	"""Whether this pull request may be merged tonight, and the reason in plain words."""
	if not str(pr.get("headRefName", "")).startswith("dependabot/"):
		return False, "not a Dependabot branch"
	if pr.get("isDraft"):
		return False, "it is a draft"
	problem = check_problem(pr.get("statusCheckRollup") or [])
	if problem:
		return False, problem
	if pr.get("mergeStateStatus") != "CLEAN":
		return False, f"GitHub does not call it clean ({str(pr.get('mergeStateStatus')).lower()})"
	changes = version_changes(f"{pr.get('title', '')}\n{pr.get('body', '')}")
	if not changes:
		return False, "its version changes could not be read, so a person should look"
	for old, new in changes:
		if major_of(old) != major_of(new):
			return False, f"a major version change ({old} to {new}) needs a person to read the release notes"
	return True, f"{len(changes)} minor or patch update(s), all checks passed"


def needs_deploy(paths: list[str]) -> bool:
	"""False when the pull request only touches workflow files, which the server never runs."""
	return any(not path.startswith(".github/") for path in paths)


def gh(*args: str) -> str:
	result = subprocess.run(["gh", *args], capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
	return result.stdout


def open_dependabot_pull_requests() -> list[dict]:
	fields = "number,title,body,headRefName,isDraft,mergeStateStatus,statusCheckRollup,files,createdAt"
	out = gh("pr", "list", "--state", "open", "--author", "app/dependabot", "--limit", "50", "--json", fields)
	return sorted(json.loads(out), key=lambda pr: pr["createdAt"])


def say(line: str) -> None:
	print(line)
	summary = os.environ.get("GITHUB_STEP_SUMMARY")
	if summary:
		with open(summary, "a", encoding="utf-8") as handle:
			handle.write(line + "\n")


def main(argv: list[str]) -> int:
	switched_off = os.environ.get("DEPENDENCY_AUTOMERGE", "true").strip().lower() == "false"
	dry_run = "--dry-run" in argv or os.environ.get("DRY_RUN", "").lower() == "true" or switched_off
	say("## Night-time dependency updates")
	if switched_off:
		say("Automatic merging is switched off (DEPENDENCY_AUTOMERGE is false). Reporting only.")
	elif dry_run:
		say("Dry run: reporting only, nothing is merged.")

	pull_requests = open_dependabot_pull_requests()
	if not pull_requests:
		say("No open Dependabot pull requests. Nothing to do.")
		return 0

	merged = 0
	for pr in pull_requests:
		label = f"#{pr['number']} {pr['title']}"
		ok, reason = decide(pr)
		if not ok:
			say(f"- Left for a person: {label}: {reason}")
			continue
		if merged >= MAX_MERGES_PER_RUN:
			say(f"- Waiting for tomorrow night: {label} (one update per night)")
			continue
		if dry_run:
			say(f"- Would merge: {label}: {reason}")
			merged += 1
			continue
		gh("pr", "merge", str(pr["number"]), "--merge")
		merged += 1
		say(f"- Merged: {label}: {reason}")
		if needs_deploy([item["path"] for item in pr.get("files", [])]):
			gh("workflow", "run", DEPLOY_WORKFLOW, "--ref", "main")
			say(f"  Started {DEPLOY_WORKFLOW} so the change reaches the shops tonight.")
		else:
			say("  Only workflow files changed, so no deploy is needed.")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
