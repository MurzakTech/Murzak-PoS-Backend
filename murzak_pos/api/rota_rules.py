"""
Staff rota and clock-in: the rules, with no database (api/rota_api.py makes the calls).

A rota shift is one person's planned working time on one day. A clock entry is the time a person really clocked in
and out. Planned shifts do not run past midnight: a night shift is planned as two shifts, one on each day.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

NOTE_LIMIT = 140
MAX_SHIFT_HOURS = 16
# Clocking in is welcome this long before a planned shift starts, and a person is late only after the grace
EARLY_MINUTES = 120
GRACE_MINUTES = 5
# An entry left open this long is taken to be a forgotten clock-out
STALE_HOURS = 16
MAX_RANGE_DAYS = 62


# --- reading what arrives ----------------------------------------------------------------------------


def parse_date(value) -> date | None:
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	try:
		return date.fromisoformat(str(value).strip()[:10])
	except (TypeError, ValueError):
		return None


def parse_time(value) -> time | None:
	"""A time from "HH:MM", "HH:MM:SS", a time, or the timedelta Frappe gives for a Time field."""
	if isinstance(value, time):
		return value.replace(microsecond=0)
	if isinstance(value, timedelta):
		seconds = int(value.total_seconds())
		if not 0 <= seconds < 86400:
			return None
		return time(seconds // 3600, seconds % 3600 // 60, seconds % 60)
	text = str(value or "").strip()
	for fmt in ("%H:%M", "%H:%M:%S"):
		try:
			return datetime.strptime(text, fmt).time()
		except ValueError:
			continue
	return None


def clean_note(note) -> str:
	return " ".join(str(note or "").split())[:NOTE_LIMIT]


def clean_shift(shift_date, start, end, note=None):
	"""A valid planned shift as (shift, None), or (None, error code): bad_date, bad_time, end_before_start, too_long."""
	day = parse_date(shift_date)
	if day is None:
		return None, "bad_date"
	begins, ends = parse_time(start), parse_time(end)
	if begins is None or ends is None:
		return None, "bad_time"
	if ends <= begins:
		return None, "end_before_start"
	minutes = (datetime.combine(day, ends) - datetime.combine(day, begins)).total_seconds() / 60
	if minutes > MAX_SHIFT_HOURS * 60:
		return None, "too_long"
	return {"date": day, "start": begins, "end": ends, "note": clean_note(note)}, None


def overlaps(start_a, end_a, start_b, end_b) -> bool:
	"""True when two times of one day share any time. Shifts that only touch (one ends as the next starts) do not."""
	return start_a < end_b and start_b < end_a


def window(shift) -> tuple[datetime, datetime]:
	"""The start and end of a planned shift as moments."""
	day = parse_date(shift["date"])
	return datetime.combine(day, parse_time(shift["start"])), datetime.combine(day, parse_time(shift["end"]))


# --- clocking in ------------------------------------------------------------------------------------


def pick_shift(now: datetime, shifts):
	"""
	The planned shift a clock-in belongs to: one that is on now, else the nearest one starting within EARLY_MINUTES.
	None when the person is not rostered around now.
	"""
	best, best_gap = None, None
	for shift in shifts or []:
		begins, ends = window(shift)
		if begins - timedelta(minutes=EARLY_MINUTES) <= now <= ends:
			gap = abs((now - begins).total_seconds())
			if best_gap is None or gap < best_gap:
				best, best_gap = shift, gap
	return best


def late_minutes(clock_in: datetime, shift) -> int:
	"""Minutes late past the grace; 0 when on time, early or not rostered."""
	if not shift:
		return 0
	begins, _ = window(shift)
	late = int((clock_in - begins).total_seconds() // 60)
	return late if late > GRACE_MINUTES else 0


def worked_minutes(clock_in: datetime, clock_out: datetime) -> int:
	return max(0, int((clock_out - clock_in).total_seconds() // 60))


def hours(minutes) -> float:
	return round((minutes or 0) / 60, 2)


def is_stale(clock_in: datetime, now: datetime) -> bool:
	return (now - clock_in) > timedelta(hours=STALE_HOURS)


def closing_problem(clock_in: datetime, clock_out: datetime, now: datetime) -> str | None:
	"""Why a manager cannot close an entry at `clock_out`: before_start, in_future; None when fine."""
	if clock_out <= clock_in:
		return "before_start"
	if clock_out > now:
		return "in_future"
	return None


# --- weeks and ranges -------------------------------------------------------------------------------


def week_start(day) -> date:
	"""The Monday of the week holding `day`."""
	day = parse_date(day) or date.today()
	return day - timedelta(days=day.weekday())


def range_problem(start, end) -> str | None:
	"""bad_date, end_before_start or range_too_long; None when the range is fine."""
	first, last = parse_date(start), parse_date(end)
	if first is None or last is None:
		return "bad_date"
	if last < first:
		return "end_before_start"
	if (last - first).days >= MAX_RANGE_DAYS:
		return "range_too_long"
	return None


def totals_by_person(entries):
	"""
	Hours per person from clock entries ({user, clock_in, clock_out, late_minutes}). An open entry adds nothing to
	the hours; the person is listed as still in. Sorted by name of the person's user id.
	"""
	people: dict[str, dict] = {}
	for entry in entries or []:
		row = people.setdefault(
			entry["user"],
			{
				"user": entry["user"],
				"minutes": 0,
				"late_minutes": 0,
				"late_count": 0,
				"days": set(),
				"still_in": False,
			},
		)
		row["days"].add(parse_date(entry["clock_in"]))
		if entry.get("clock_out") is None:
			row["still_in"] = True
		else:
			row["minutes"] += worked_minutes(entry["clock_in"], entry["clock_out"])
		if entry.get("late_minutes"):
			row["late_minutes"] += entry["late_minutes"]
			row["late_count"] += 1
	out = []
	for user in sorted(people):
		row = people[user]
		out.append(
			{
				"user": user,
				"hours": hours(row["minutes"]),
				"days": len(row["days"]),
				"late_count": row["late_count"],
				"late_minutes": row["late_minutes"],
				"still_in": row["still_in"],
			}
		)
	return out
