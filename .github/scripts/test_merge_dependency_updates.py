"""
The rules for which dependency updates may be merged at night. Needs no network and no GitHub.

Run:  python .github/scripts/test_merge_dependency_updates.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import merge_dependency_updates as rules

GREEN = [
	{"__typename": "CheckRun", "name": "Lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
	{"__typename": "CheckRun", "name": "Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
]


def pull_request(**over):
	base = {
		"number": 7,
		"title": "Bump lodash from 4.17.20 to 4.17.21",
		"body": "Updates `lodash` from 4.17.20 to 4.17.21",
		"headRefName": "dependabot/npm_and_yarn/lodash-4.17.21",
		"isDraft": False,
		"mergeStateStatus": "CLEAN",
		"statusCheckRollup": GREEN,
		"files": [{"path": "package.json"}, {"path": "package-lock.json"}],
		"createdAt": "2026-11-24T00:00:00Z",
	}
	base.update(over)
	return base


class TestWhichUpdatesMayShip(unittest.TestCase):
	def test_a_green_patch_update_may(self):
		ok, _reason = rules.decide(pull_request())
		self.assertTrue(ok)

	def test_a_green_minor_update_may(self):
		ok, _ = rules.decide(pull_request(title="Bump axios from 1.6.0 to 1.7.2", body=""))
		self.assertTrue(ok)

	def test_a_major_update_never_does(self):
		ok, reason = rules.decide(pull_request(title="Bump react-router from 6.30.1 to 7.1.0", body=""))
		self.assertFalse(ok)
		self.assertIn("major", reason)

	def test_a_github_actions_major_written_as_one_number_is_caught(self):
		ok, _ = rules.decide(pull_request(title="Bump actions/checkout from 4 to 5", body=""))
		self.assertFalse(ok)

	def test_a_zero_dot_x_minor_step_counts_as_major(self):
		ok, _ = rules.decide(pull_request(title="Bump thing from 0.9.4 to 0.10.0", body=""))
		self.assertFalse(ok)

	def test_a_zero_dot_x_patch_step_is_fine(self):
		ok, _ = rules.decide(pull_request(title="Bump thing from 0.9.4 to 0.9.5", body=""))
		self.assertTrue(ok)

	def test_one_major_hidden_in_a_group_stops_the_whole_group(self):
		body = (
			"Updates `axios` from 1.6.0 to 1.6.2\n"
			"Updates `uuid` from 8.3.2 to 9.0.0\n"
			"| Package | From | To |\n| --- | --- | --- |\n"
			"| [axios](https://x) | `1.6.0` | `1.6.2` |\n| [uuid](https://y) | `8.3.2` | `9.0.0` |\n"
		)
		ok, reason = rules.decide(
			pull_request(title="Bump the minor-and-patch group with 2 updates", body=body)
		)
		self.assertFalse(ok)
		self.assertIn("8.3.2", reason)

	def test_a_group_of_safe_updates_may(self):
		body = "Updates `axios` from 1.6.0 to 1.6.2\nUpdates `dayjs` from 1.11.9 to 1.11.13\n"
		ok, _ = rules.decide(pull_request(title="Bump the minor-and-patch group with 2 updates", body=body))
		self.assertTrue(ok)

	def test_the_table_alone_is_enough_to_see_a_major(self):
		body = "| [uuid](https://y) | `8.3.2` | `9.0.0` |\n"
		ok, _ = rules.decide(pull_request(title="Bump the group with 1 update", body=body))
		self.assertFalse(ok)

	def test_versions_that_cannot_be_read_are_not_trusted(self):
		ok, reason = rules.decide(
			pull_request(title="Bump the group with 3 updates", body="no versions here")
		)
		self.assertFalse(ok)
		self.assertIn("person", reason)

	def test_a_version_with_a_suffix_is_still_read(self):
		self.assertEqual(rules.version_changes("from 1.2.3-beta.1 to 1.2.3"), [("1.2.3", "1.2.3")])


class TestChecks(unittest.TestCase):
	def test_a_failed_check_stops_it(self):
		red = [
			*GREEN,
			{"__typename": "CheckRun", "name": "Tests", "status": "COMPLETED", "conclusion": "FAILURE"},
		]
		ok, reason = rules.decide(pull_request(statusCheckRollup=red))
		self.assertFalse(ok)
		self.assertIn("failure", reason)

	def test_a_check_still_running_stops_it(self):
		running = [
			*GREEN,
			{"__typename": "CheckRun", "name": "Build", "status": "IN_PROGRESS", "conclusion": ""},
		]
		ok, reason = rules.decide(pull_request(statusCheckRollup=running))
		self.assertFalse(ok)
		self.assertIn("not finished", reason)

	def test_no_checks_at_all_stops_it(self):
		ok, _ = rules.decide(pull_request(statusCheckRollup=[]))
		self.assertFalse(ok)

	def test_skipped_and_neutral_checks_are_fine(self):
		mixed = [
			*GREEN,
			{"__typename": "CheckRun", "name": "A", "status": "COMPLETED", "conclusion": "SKIPPED"},
			{"__typename": "CheckRun", "name": "B", "status": "COMPLETED", "conclusion": "NEUTRAL"},
		]
		ok, _ = rules.decide(pull_request(statusCheckRollup=mixed))
		self.assertTrue(ok)

	def test_an_old_style_status_must_be_success(self):
		bad = [*GREEN, {"__typename": "StatusContext", "context": "ci/other", "state": "PENDING"}]
		ok, _ = rules.decide(pull_request(statusCheckRollup=bad))
		self.assertFalse(ok)

	def test_a_branch_that_is_behind_or_conflicted_is_left(self):
		for state in ("BEHIND", "DIRTY", "UNSTABLE", "BLOCKED"):
			ok, _ = rules.decide(pull_request(mergeStateStatus=state))
			self.assertFalse(ok, state)


class TestWhoseWork(unittest.TestCase):
	def test_a_pull_request_from_a_person_is_never_touched(self):
		ok, reason = rules.decide(pull_request(headRefName="claude/something"))
		self.assertFalse(ok)
		self.assertIn("Dependabot", reason)

	def test_a_draft_is_left(self):
		ok, _ = rules.decide(pull_request(isDraft=True))
		self.assertFalse(ok)


class TestWhetherToDeploy(unittest.TestCase):
	def test_workflow_files_alone_need_no_deploy(self):
		self.assertFalse(rules.needs_deploy([".github/workflows/ci.yml", ".github/dependabot.yml"]))

	def test_anything_else_does(self):
		self.assertTrue(rules.needs_deploy(["package.json", "package-lock.json"]))
		self.assertTrue(rules.needs_deploy([".github/workflows/ci.yml", "package.json"]))

	def test_an_empty_list_needs_none(self):
		self.assertFalse(rules.needs_deploy([]))


if __name__ == "__main__":
	unittest.main()
