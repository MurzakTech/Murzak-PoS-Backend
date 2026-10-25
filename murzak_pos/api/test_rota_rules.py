# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the rota and clock-in rules (rota_rules.py). No database."""

import unittest
from datetime import date, datetime, time, timedelta

from murzak_pos.api import rota_rules as rules

DAY = date(2026, 10, 26)  # a Monday


def shift(start="08:00", end="16:00", day=DAY):
	return {"date": day, "start": start, "end": end}


class ParseCase(unittest.TestCase):
	def test_times(self):
		self.assertEqual(rules.parse_time("08:30"), time(8, 30))
		self.assertEqual(rules.parse_time("08:30:15"), time(8, 30, 15))
		self.assertEqual(rules.parse_time(timedelta(hours=9, minutes=5)), time(9, 5))
		self.assertEqual(rules.parse_time(time(7, 0, 0, 500)), time(7, 0))

	def test_bad_times(self):
		for bad in ("", None, "25:00", "8", "noon", timedelta(hours=30), timedelta(seconds=-5)):
			self.assertIsNone(rules.parse_time(bad), bad)

	def test_dates(self):
		self.assertEqual(rules.parse_date("2026-10-26"), DAY)
		self.assertEqual(rules.parse_date(datetime(2026, 10, 26, 9, 0)), DAY)
		self.assertIsNone(rules.parse_date("26/10/2026"))
		self.assertIsNone(rules.parse_date(None))


class CleanShiftCase(unittest.TestCase):
	def test_a_good_shift(self):
		out, code = rules.clean_shift("2026-10-26", "08:00", "16:00", "  front   till ")
		self.assertIsNone(code)
		self.assertEqual(out, {"date": DAY, "start": time(8, 0), "end": time(16, 0), "note": "front till"})

	def test_refusals(self):
		self.assertEqual(rules.clean_shift("x", "08:00", "16:00")[1], "bad_date")
		self.assertEqual(rules.clean_shift("2026-10-26", "8am", "16:00")[1], "bad_time")
		self.assertEqual(rules.clean_shift("2026-10-26", "16:00", "08:00")[1], "end_before_start")
		self.assertEqual(rules.clean_shift("2026-10-26", "08:00", "08:00")[1], "end_before_start")
		self.assertEqual(rules.clean_shift("2026-10-26", "05:00", "22:00")[1], "too_long")

	def test_sixteen_hours_is_allowed(self):
		self.assertIsNone(rules.clean_shift("2026-10-26", "06:00", "22:00")[1])

	def test_note_is_cut(self):
		self.assertEqual(len(rules.clean_note("x" * 300)), rules.NOTE_LIMIT)


class OverlapCase(unittest.TestCase):
	def test_overlap(self):
		self.assertTrue(rules.overlaps(time(8), time(12), time(11), time(15)))
		self.assertTrue(rules.overlaps(time(8), time(16), time(10), time(11)))

	def test_touching_is_not_overlap(self):
		self.assertFalse(rules.overlaps(time(8), time(12), time(12), time(16)))

	def test_apart(self):
		self.assertFalse(rules.overlaps(time(8), time(10), time(14), time(16)))


class ClockInCase(unittest.TestCase):
	def at(self, hour, minute=0):
		return datetime(2026, 10, 26, hour, minute)

	def test_picks_the_shift_that_is_on(self):
		a, b = dict(shift("06:00", "10:00"), name="A"), dict(shift("14:00", "18:00"), name="B")
		self.assertEqual(rules.pick_shift(self.at(15), [a, b])["name"], "B")

	def test_early_clock_in_matches_the_coming_shift(self):
		a = dict(shift("08:00", "16:00"), name="A")
		self.assertEqual(rules.pick_shift(self.at(6, 30), [a])["name"], "A")

	def test_too_early_or_too_late_matches_nothing(self):
		a = shift("08:00", "16:00")
		self.assertIsNone(rules.pick_shift(self.at(5, 30), [a]))
		self.assertIsNone(rules.pick_shift(self.at(16, 1), [a]))
		self.assertIsNone(rules.pick_shift(self.at(9), []))

	def test_lateness_after_the_grace(self):
		s = shift("08:00", "16:00")
		self.assertEqual(rules.late_minutes(self.at(8, 5), s), 0)
		self.assertEqual(rules.late_minutes(self.at(8, 6), s), 6)
		self.assertEqual(rules.late_minutes(self.at(7, 40), s), 0)
		self.assertEqual(rules.late_minutes(self.at(9, 30), s), 90)
		self.assertEqual(rules.late_minutes(self.at(9, 30), None), 0)

	def test_hours_worked(self):
		self.assertEqual(rules.worked_minutes(self.at(8), self.at(16, 30)), 510)
		self.assertEqual(rules.worked_minutes(self.at(9), self.at(8)), 0)
		self.assertEqual(rules.hours(510), 8.5)
		self.assertEqual(rules.hours(None), 0)

	def test_stale_entry(self):
		self.assertFalse(rules.is_stale(self.at(8), self.at(23)))
		self.assertTrue(rules.is_stale(self.at(8), datetime(2026, 10, 27, 1)))

	def test_closing_problems(self):
		now = self.at(20)
		self.assertEqual(rules.closing_problem(self.at(8), self.at(8), now), "before_start")
		self.assertEqual(rules.closing_problem(self.at(8), self.at(21), now), "in_future")
		self.assertIsNone(rules.closing_problem(self.at(8), self.at(17), now))


class RangeCase(unittest.TestCase):
	def test_week_starts_on_monday(self):
		self.assertEqual(rules.week_start("2026-10-28"), DAY)
		self.assertEqual(rules.week_start("2026-11-01"), DAY)
		self.assertEqual(rules.week_start("2026-10-26"), DAY)

	def test_ranges(self):
		self.assertIsNone(rules.range_problem("2026-10-01", "2026-10-31"))
		self.assertEqual(rules.range_problem("x", "2026-10-31"), "bad_date")
		self.assertEqual(rules.range_problem("2026-10-31", "2026-10-01"), "end_before_start")
		self.assertEqual(rules.range_problem("2026-01-01", "2026-12-31"), "range_too_long")


class TotalsCase(unittest.TestCase):
	def test_totals_per_person(self):
		entries = [
			{
				"user": "b@x",
				"clock_in": datetime(2026, 10, 26, 8),
				"clock_out": datetime(2026, 10, 26, 16),
				"late_minutes": 0,
			},
			{
				"user": "a@x",
				"clock_in": datetime(2026, 10, 26, 9),
				"clock_out": datetime(2026, 10, 26, 13, 30),
				"late_minutes": 12,
			},
			{
				"user": "a@x",
				"clock_in": datetime(2026, 10, 27, 9),
				"clock_out": datetime(2026, 10, 27, 12),
				"late_minutes": 0,
			},
			{"user": "b@x", "clock_in": datetime(2026, 10, 27, 8), "clock_out": None, "late_minutes": 0},
		]
		out = rules.totals_by_person(entries)
		self.assertEqual([p["user"] for p in out], ["a@x", "b@x"])
		a, b = out
		self.assertEqual(
			(a["hours"], a["days"], a["late_count"], a["late_minutes"], a["still_in"]), (7.5, 2, 1, 12, False)
		)
		self.assertEqual((b["hours"], b["days"], b["still_in"]), (8.0, 2, True))
