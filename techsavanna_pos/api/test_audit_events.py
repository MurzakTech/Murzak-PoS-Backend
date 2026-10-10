# Plain unit tests for the audit wording. They need no Frappe site:
#   python -m unittest techsavanna_pos.api.test_audit_events
import json
import unittest

from techsavanna_pos.api.audit_events import (
	describe_activity,
	describe_version,
	humanize_field,
	merge_events,
)


def version(changed=(), **rest):
	return json.dumps(
		{"changed": [list(c) for c in changed], "added": [], "removed": [], "row_changed": [], **rest}
	)


class TestDescribeVersion(unittest.TestCase):
	def test_submit_and_cancel_win_over_other_changes(self):
		self.assertEqual(
			describe_version(version([["status", "Draft", "Unpaid"], ["docstatus", 0, 1]])),
			{"action": "Submitted", "summary": "Submitted"},
		)
		self.assertEqual(describe_version(version([["docstatus", 1, 2]]))["action"], "Cancelled")

	def test_field_changes_are_listed_in_plain_words(self):
		result = describe_version(version([["selling_price", 60, 65], ["modified", "a", "b"]]))
		self.assertEqual(result, {"action": "Updated", "summary": "Selling price: 60 → 65"})

	def test_empty_values_and_secrets(self):
		result = describe_version(version([["mobile_no", None, "0712"], ["new_password", "x", "y"]]))
		self.assertEqual(result["summary"], "Mobile no: empty → 0712; New password changed")
		self.assertNotIn("y", result["summary"].split("; ")[1])

	def test_table_rows(self):
		data = json.dumps(
			{
				"changed": [],
				"added": [["items", {"item_code": "A"}]],
				"removed": [],
				"row_changed": [["payments", 0, "r1", [["amount", 1, 2]]]],
			}
		)
		self.assertEqual(describe_version(data)["summary"], "Items: row added; Payments: row edited")

	def test_long_change_lists_are_shortened(self):
		changed = [[f"field_{i}", i, i + 1] for i in range(6)]
		self.assertTrue(describe_version(version(changed))["summary"].endswith("; and 2 more"))

	def test_first_save_reads_as_created(self):
		self.assertEqual(describe_version(version())["action"], "Created")
		self.assertEqual(describe_version("")["action"], "Created")

	def test_only_bookkeeping_changes(self):
		self.assertEqual(
			describe_version(version([["modified", "a", "b"]])),
			{"action": "Updated", "summary": "Saved with no visible changes"},
		)

	def test_broken_data_does_not_crash(self):
		self.assertEqual(describe_version("{not json")["action"], "Created")
		self.assertEqual(describe_version(version([["only_two", 1]]))["action"], "Created")


class TestDescribeActivity(unittest.TestCase):
	def test_sign_in_history(self):
		self.assertEqual(describe_activity("Login", "Success")["action"], "Signed in")
		self.assertEqual(describe_activity("Logout", "Success")["action"], "Signed out")
		self.assertEqual(describe_activity("Login", "Failed")["action"], "Sign-in failed")


class TestMergeEvents(unittest.TestCase):
	def test_newest_first_across_sources_with_paging(self):
		a = [{"id": "v-2", "timestamp": "2026-10-10 10:00"}, {"id": "v-1", "timestamp": "2026-10-08 09:00"}]
		b = [{"id": "a-1", "timestamp": "2026-10-09 12:00"}]
		first = merge_events([a, b], 0, 2)
		self.assertEqual([e["id"] for e in first["events"]], ["v-2", "a-1"])
		self.assertTrue(first["has_more"])
		second = merge_events([a, b], 2, 2)
		self.assertEqual([e["id"] for e in second["events"]], ["v-1"])
		self.assertFalse(second["has_more"])


class TestHumanizeField(unittest.TestCase):
	def test_humanize(self):
		self.assertEqual(humanize_field("grand_total"), "Grand total")


if __name__ == "__main__":
	unittest.main()
