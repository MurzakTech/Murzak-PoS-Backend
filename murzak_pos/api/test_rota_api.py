# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the rota and clock-in calls (rota_api.py) that need no database: who may plan, what is refused, that a
person cannot be clocked in twice, and who sees whose hours. Every Frappe call is replaced.
"""

import contextlib
import unittest
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import rota_api

NOW = datetime(2026, 10, 26, 9, 0)


@contextlib.contextmanager
def world(
	caller="mgr@shop.test",
	tier="manager",
	shifts=None,
	open_entry=None,
	person_ok=True,
	companies=("Shop A",),
):
	db = MagicMock()
	db.exists.return_value = True
	db.get_value.side_effect = lambda doctype, name=None, field=None, *a, **k: {
		("User", "enabled"): 1 if person_ok else 0,
		("User", "full_name"): "Pat",
		("Rota Shift", "company"): "Shop A",
	}.get((doctype, field), None)
	saved = SimpleNamespace(doc=MagicMock())
	saved.doc.as_dict.return_value = {
		"name": "RS-1",
		"user": "pat@shop.test",
		"shift_date": date(2026, 10, 26),
		"start_time": time(8),
		"end_time": time(16),
		"note": "",
	}

	def get_all(doctype, **kwargs):
		if doctype == "Rota Shift":
			return list(shifts or [])
		if doctype == "Clock Entry":
			return [open_entry] if open_entry and kwargs.get("filters", {}).get("open_key") else []
		return []

	with (
		patch.object(rota_api.frappe, "db", db, create=True),
		patch.object(rota_api.frappe, "session", MagicMock(user=caller)),
		patch.object(rota_api.frappe, "get_all", side_effect=get_all),
		patch.object(rota_api.frappe, "get_doc", return_value=saved.doc) as get_doc,
		patch.object(rota_api.frappe, "delete_doc") as delete_doc,
		patch.object(rota_api, "ensure_record_access") as guard,
		patch.object(rota_api, "resolve_company", return_value="Shop A"),
		patch.object(rota_api, "get_user_companies", return_value=set(companies)),
		patch.object(rota_api.role_tiers, "get_tier", return_value=tier),
		patch.object(rota_api, "now_datetime", return_value=NOW),
	):
		yield SimpleNamespace(db=db, get_doc=get_doc, delete_doc=delete_doc, guard=guard, doc=saved.doc)


def plan(**over):
	args = {"user": "pat@shop.test", "shift_date": "2026-10-26", "start_time": "08:00", "end_time": "16:00"}
	args.update(over)
	return rota_api.save_rota_shift(**args)


class PlanCase(unittest.TestCase):
	def test_a_manager_plans_a_shift(self):
		with world() as w:
			out = plan(note="front till")
		self.assertTrue(out["success"])
		w.doc.insert.assert_called_once()
		made = w.get_doc.call_args.args[0]
		self.assertEqual(made["company"], "Shop A")
		self.assertEqual(made["created_by"], "mgr@shop.test")
		self.assertEqual(made["start_time"], time(8))
		self.assertEqual(out["data"]["shift"]["start"], "08:00")

	def test_a_cashier_may_not_plan(self):
		with world(caller="amina@shop.test", tier="cashier"), self.assertRaises(frappe.PermissionError):
			plan()

	def test_nobody_signed_in(self):
		with world(caller="Guest", tier=None), self.assertRaises(frappe.AuthenticationError):
			plan()

	def test_overlap_is_refused(self):
		clash = [{"name": "RS-9", "start_time": time(15), "end_time": time(20)}]
		with world(shifts=clash) as w:
			out = plan()
		self.assertEqual(out["error_type"], "overlap")
		w.doc.insert.assert_not_called()

	def test_touching_shifts_are_fine(self):
		touch = [{"name": "RS-9", "start_time": time(16), "end_time": time(20)}]
		with world(shifts=touch):
			self.assertTrue(plan()["success"])

	def test_changing_a_shift_ignores_itself(self):
		same = [{"name": "RS-1", "start_time": time(8), "end_time": time(16)}]
		with world(shifts=same) as w:
			out = plan(name="RS-1", end_time="17:00")
		self.assertTrue(out["success"])
		w.doc.save.assert_called_once()
		w.guard.assert_called_with("Rota Shift", "RS-1")

	def test_person_problems(self):
		with world(person_ok=False):
			self.assertEqual(plan()["error_type"], "person_disabled")
		with world(companies=("Shop B",)):
			pass
		with world() as w:
			w.db.exists.return_value = False
			self.assertEqual(plan()["error_type"], "no_such_person")

	def test_person_of_another_business(self):
		with world() as w, patch.object(rota_api, "get_user_companies", return_value={"Shop B"}):
			self.assertEqual(plan()["error_type"], "other_business")
			w.doc.insert.assert_not_called()

	def test_bad_shift_values(self):
		with world():
			self.assertEqual(plan(start_time="late")["error_type"], "bad_time")
			self.assertEqual(plan(start_time="17:00")["error_type"], "end_before_start")
			self.assertEqual(plan(shift_date="soon")["error_type"], "bad_date")

	def test_delete_frees_the_clock_entries_and_removes_the_shift(self):
		with world() as w:
			out = rota_api.delete_rota_shift("RS-1")
		self.assertTrue(out["success"])
		w.db.set_value.assert_called_once()
		self.assertEqual(w.db.set_value.call_args.args[:2], ("Clock Entry", {"rota_shift": "RS-1"}))
		w.delete_doc.assert_called_once()
		w.guard.assert_called_with("Rota Shift", "RS-1")

	def test_a_cashier_may_not_delete(self):
		with world(caller="amina@shop.test", tier="cashier"), self.assertRaises(frappe.PermissionError):
			rota_api.delete_rota_shift("RS-1")


class ClockCase(unittest.TestCase):
	def test_clock_in_matches_the_shift_and_counts_lateness(self):
		mine = [
			{
				"name": "RS-1",
				"user": "pat@shop.test",
				"shift_date": date(2026, 10, 26),
				"start_time": time(8),
				"end_time": time(16),
				"note": "",
			}
		]
		with world(caller="pat@shop.test", tier="cashier", shifts=mine) as w:
			out = rota_api.clock_in()
		self.assertTrue(out["success"])
		made = w.get_doc.call_args.args[0]
		self.assertEqual(
			(made["rota_shift"], made["late_minutes"], made["open_key"]), ("RS-1", 60, "pat@shop.test")
		)

	def test_clock_in_without_a_shift_is_fine(self):
		with world(caller="pat@shop.test", tier="cashier") as w:
			out = rota_api.clock_in()
		self.assertTrue(out["success"])
		made = w.get_doc.call_args.args[0]
		self.assertEqual((made["rota_shift"], made["late_minutes"]), (None, 0))

	def test_already_clocked_in(self):
		entry = {
			"name": "CE-1",
			"user": "pat@shop.test",
			"clock_in": datetime(2026, 10, 26, 8),
			"clock_out": None,
			"late_minutes": 0,
		}
		with world(caller="pat@shop.test", tier="cashier", open_entry=entry) as w:
			out = rota_api.clock_in()
		self.assertEqual(out["error_type"], "already_clocked_in")
		w.doc.insert.assert_not_called()

	def test_two_quick_taps_are_stopped_by_the_database(self):
		with world(caller="pat@shop.test", tier="cashier") as w:
			w.doc.insert.side_effect = frappe.UniqueValidationError
			out = rota_api.clock_in()
		self.assertEqual(out["error_type"], "already_clocked_in")
		w.db.rollback.assert_called_once()

	def test_clock_out_closes_the_open_entry_and_frees_the_key(self):
		entry = {
			"name": "CE-1",
			"user": "pat@shop.test",
			"clock_in": datetime(2026, 10, 26, 8),
			"clock_out": None,
			"late_minutes": 0,
		}
		with world(caller="pat@shop.test", tier="cashier", open_entry=entry) as w:
			w.db.get_value.side_effect = lambda *a, **k: {
				"name": "CE-1",
				"user": "pat@shop.test",
				"clock_in": datetime(2026, 10, 26, 8),
				"clock_out": NOW,
				"late_minutes": 0,
			}
			out = rota_api.clock_out()
		self.assertTrue(out["success"])
		values = w.db.set_value.call_args.args[2]
		self.assertEqual(values, {"clock_out": NOW, "open_key": None})
		self.assertEqual(out["data"]["entry"]["hours"], 1.0)

	def test_clock_out_when_not_clocked_in(self):
		with world(caller="pat@shop.test", tier="cashier"):
			self.assertEqual(rota_api.clock_out()["error_type"], "not_clocked_in")


class ManagerCloseCase(unittest.TestCase):
	def entry(self, w, clock_out=None):
		w.db.get_value.side_effect = lambda *a, **k: (
			{"company": "Shop A", "clock_in": datetime(2026, 10, 25, 8), "clock_out": clock_out}
			if k.get("as_dict") and len(a) == 3 and isinstance(a[2], list) and "company" in a[2]
			else {
				"name": "CE-1",
				"user": "pat@shop.test",
				"clock_in": datetime(2026, 10, 25, 8),
				"clock_out": datetime(2026, 10, 25, 17),
				"late_minutes": 0,
			}
		)

	def test_closes_a_forgotten_entry(self):
		with world() as w:
			self.entry(w)
			out = rota_api.close_clock_entry("CE-1", "2026-10-25 17:00:00")
		self.assertTrue(out["success"])
		values = w.db.set_value.call_args.args[2]
		self.assertEqual((values["open_key"], values["closed_by"]), (None, "mgr@shop.test"))
		w.guard.assert_called_with("Clock Entry", "CE-1")

	def test_refusals(self):
		with world() as w:
			self.entry(w, clock_out=datetime(2026, 10, 25, 17))
			self.assertEqual(
				rota_api.close_clock_entry("CE-1", "2026-10-25 17:00:00")["error_type"], "not_open"
			)
		with world() as w:
			self.entry(w)
			self.assertEqual(
				rota_api.close_clock_entry("CE-1", "2026-10-25 07:00:00")["error_type"], "before_start"
			)
			self.assertEqual(
				rota_api.close_clock_entry("CE-1", "2026-10-27 07:00:00")["error_type"], "in_future"
			)
			self.assertEqual(rota_api.close_clock_entry("CE-1", "")["error_type"], "bad_date")

	def test_a_cashier_may_not(self):
		with world(caller="pat@shop.test", tier="cashier"), self.assertRaises(frappe.PermissionError):
			rota_api.close_clock_entry("CE-1", "2026-10-25 17:00:00")


class TimesheetCase(unittest.TestCase):
	def run_sheet(self, caller, tier, user=None):
		rows = [
			{
				"name": "CE-1",
				"user": "pat@shop.test",
				"clock_in": datetime(2026, 10, 26, 8),
				"clock_out": datetime(2026, 10, 26, 16),
				"late_minutes": 0,
			}
		]
		captured = {}

		def get_all(doctype, **kwargs):
			captured.update(kwargs)
			return rows

		with world(caller=caller, tier=tier), patch.object(rota_api.frappe, "get_all", side_effect=get_all):
			out = rota_api.list_timesheet("2026-10-26", "2026-10-26", user)
		return out, captured

	def test_a_manager_sees_everyone(self):
		out, got = self.run_sheet("mgr@shop.test", "manager")
		self.assertNotIn("user", got["filters"])
		self.assertEqual(out["data"]["people"][0]["hours"], 8.0)

	def test_a_manager_may_pick_one_person(self):
		_, got = self.run_sheet("mgr@shop.test", "owner", "pat@shop.test")
		self.assertEqual(got["filters"]["user"], "pat@shop.test")

	def test_a_cashier_sees_only_their_own(self):
		_, got = self.run_sheet("pat@shop.test", "cashier")
		self.assertEqual(got["filters"]["user"], "pat@shop.test")

	def test_a_cashier_cannot_ask_for_another(self):
		out, _ = self.run_sheet("pat@shop.test", "cashier", "sam@shop.test")
		self.assertEqual(out["error_type"], "not_allowed")

	def test_range_is_checked(self):
		with world():
			self.assertEqual(
				rota_api.list_timesheet("2026-01-01", "2026-12-31")["error_type"], "range_too_long"
			)


class RotaListCase(unittest.TestCase):
	def test_everyone_sees_the_week_but_only_managers_get_the_people(self):
		rows = [
			{
				"name": "RS-1",
				"user": "pat@shop.test",
				"shift_date": date(2026, 10, 26),
				"start_time": time(8),
				"end_time": time(16),
				"note": "",
			}
		]
		with world(caller="pat@shop.test", tier="cashier", shifts=rows):
			out = rota_api.list_rota("2026-10-28")
		self.assertEqual(out["data"]["week_start"], "2026-10-26")
		self.assertEqual(out["data"]["week_end"], "2026-11-01")
		self.assertFalse(out["data"]["can_plan"])
		self.assertNotIn("people", out["data"])
		self.assertEqual(out["data"]["shifts"][0]["start"], "08:00")


if __name__ == "__main__":
	unittest.main()
