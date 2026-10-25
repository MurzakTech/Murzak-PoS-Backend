"""
Staff rota and clock-in (docs/ROTA.md).

Planning: a manager or owner plans each person's working shifts (`save_rota_shift`, `delete_rota_shift`). Everyone in
the business can see the week's rota (`list_rota`).

Clocking: a person clocks in and out for themselves (`clock_in`, `clock_out`, `get_clock_state`). A clock-in is matched to
the shift they are rostered for, and records how late they were. If someone forgets to clock out, a manager closes the
entry (`close_clock_entry`). `list_timesheet` gives the hours: a manager sees everyone, anyone else sees only their own.

The rules (what is a valid shift, who is late, how hours are added up) are in rota_rules.py (no database).
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from murzak_pos.api import role_tiers
from murzak_pos.api import rota_rules as rules
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import get_user_companies, resolve_company

SHIFT_FIELDS = ["name", "user", "shift_date", "start_time", "end_time", "note"]
ENTRY_FIELDS = ["name", "user", "clock_in", "clock_out", "late_minutes", "rota_shift", "closed_by", "note"]


def _refusal(code: str, detail: str = "", **data) -> dict:
	"""An answer that says no in words. `error_type` is the code; the request itself did not fail."""
	messages = {
		"not_found": _("That record was not found."),
		"bad_date": _("The date could not be read."),
		"bad_time": _("The start and end times could not be read. Use hours and minutes, for example 08:30."),
		"end_before_start": _("The end must be after the start."),
		"too_long": _(
			"A shift cannot be longer than 16 hours. Plan a night shift as two shifts, one on each day."
		),
		"overlap": _("{0} is already rostered at that time."),
		"no_such_person": _("That person was not found."),
		"person_disabled": _("That person's account is switched off."),
		"other_business": _("That person does not work for this business."),
		"already_clocked_in": _("You are already clocked in."),
		"not_clocked_in": _("You are not clocked in."),
		"not_open": _("That entry is already closed."),
		"before_start": _("The clock-out must be after the clock-in."),
		"in_future": _("The clock-out cannot be in the future."),
		"range_too_long": _("Choose a period of two months or less."),
		"not_allowed": _("You can only see your own hours."),
	}
	message = messages.get(code, detail or _("That did not work. Please try again."))
	if "{0}" in message:
		message = message.format(detail)
	result = {"success": False, "message": message, "error_type": code}
	if data:
		result["data"] = data
	return result


def _signed_in() -> str:
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	return user


def _is_manager() -> bool:
	return role_tiers.get_tier() in (role_tiers.OWNER, role_tiers.MANAGER)


def _require_manager() -> None:
	_signed_in()
	if not _is_manager():
		frappe.throw(_("Only an owner or manager can do this."), frappe.PermissionError)


def _person_name(user) -> str:
	return frappe.db.get_value("User", user, "full_name") or user


def _shift_view(row) -> dict:
	begins, ends = rules.parse_time(row["start_time"]), rules.parse_time(row["end_time"])
	return {
		"id": row["name"],
		"user": row["user"],
		"person": _person_name(row["user"]),
		"date": str(rules.parse_date(row["shift_date"])),
		"start": begins.strftime("%H:%M"),
		"end": ends.strftime("%H:%M"),
		"note": row.get("note") or "",
	}


def _rule_shift(row) -> dict:
	"""A stored shift in the form the rules read."""
	return {"date": row["shift_date"], "start": row["start_time"], "end": row["end_time"]}


def _entry_view(row, now=None) -> dict:
	clock_in = row["clock_in"]
	clock_out = row.get("clock_out")
	view = {
		"id": row["name"],
		"user": row["user"],
		"person": _person_name(row["user"]),
		"clock_in": str(clock_in),
		"clock_out": str(clock_out) if clock_out else None,
		"late_minutes": row.get("late_minutes") or 0,
		"closed_by": row.get("closed_by") or None,
		"note": row.get("note") or "",
	}
	if clock_out:
		view["hours"] = rules.hours(rules.worked_minutes(clock_in, clock_out))
	elif now is not None:
		view["stale"] = rules.is_stale(clock_in, now)
	return view


# --- planning ---------------------------------------------------------------------------------------


def _person_problem(company, user):
	"""Why `user` cannot be rostered for `company`, as an error code; None when they can."""
	if not user or not frappe.db.exists("User", user):
		return "no_such_person"
	if not frappe.db.get_value("User", user, "enabled"):
		return "person_disabled"
	if company not in get_user_companies(user):
		return "other_business"
	return None


@frappe.whitelist()
def save_rota_shift(user, shift_date, start_time, end_time, note=None, name=None):
	"""Plan a shift (or, with `name`, change one). A manager or owner only."""
	_require_manager()
	if name:
		ensure_record_access("Rota Shift", name)
		if not frappe.db.exists("Rota Shift", name):
			return _refusal("not_found")
		company = frappe.db.get_value("Rota Shift", name, "company")
	else:
		company = resolve_company()
	problem = _person_problem(company, user)
	if problem:
		return _refusal(problem)
	shift, code = rules.clean_shift(shift_date, start_time, end_time, note)
	if code:
		return _refusal(code)

	for other in frappe.get_all(
		"Rota Shift",
		filters={"company": company, "user": user, "shift_date": shift["date"]},
		fields=["name", "start_time", "end_time"],
	):
		if other["name"] == name:
			continue
		if rules.overlaps(
			shift["start"],
			shift["end"],
			rules.parse_time(other["start_time"]),
			rules.parse_time(other["end_time"]),
		):
			return _refusal("overlap", _person_name(user))

	values = {
		"company": company,
		"user": user,
		"shift_date": shift["date"],
		"start_time": shift["start"],
		"end_time": shift["end"],
		"note": shift["note"],
	}
	if name:
		doc = frappe.get_doc("Rota Shift", name)
		doc.update(values)
	else:
		doc = frappe.get_doc({"doctype": "Rota Shift", "created_by": frappe.session.user, **values})
	doc.flags.ignore_permissions = True
	if name:
		doc.save()
	else:
		doc.insert()
	frappe.db.commit()
	return {"success": True, "message": _("Shift saved."), "data": {"shift": _shift_view(doc.as_dict())}}


@frappe.whitelist()
def delete_rota_shift(name):
	"""Remove a planned shift. Clock entries already made against it keep their hours. A manager or owner only."""
	_require_manager()
	ensure_record_access("Rota Shift", name)
	if not frappe.db.exists("Rota Shift", name):
		return _refusal("not_found")
	frappe.db.set_value("Clock Entry", {"rota_shift": name}, "rota_shift", None, update_modified=False)
	frappe.delete_doc("Rota Shift", name, ignore_permissions=True)
	frappe.db.commit()
	return {"success": True, "message": _("Shift removed."), "data": {"id": name}}


@frappe.whitelist()
def list_rota(week_start=None):
	"""The week's rota (Monday to Sunday) for the business, and who can be planned. Everyone in the business may read it."""
	_signed_in()
	company = resolve_company()
	first = rules.week_start(week_start)
	last = first + timedelta(days=6)
	rows = frappe.get_all(
		"Rota Shift",
		filters={"company": company, "shift_date": ["between", [first, last]]},
		fields=SHIFT_FIELDS,
		order_by="shift_date asc, start_time asc",
	)
	data = {
		"week_start": str(first),
		"week_end": str(last),
		"shifts": [_shift_view(r) for r in rows],
		"can_plan": _is_manager(),
	}
	if data["can_plan"]:
		data["people"] = _people_of(company)
	return {"success": True, "data": data}


def _people_of(company) -> list[dict]:
	"""Active people of a business who can be rostered."""
	users = set(
		frappe.get_all("User Permission", filters={"allow": "Company", "for_value": company}, pluck="user")
	)
	if frappe.db.has_column("User", "custom_company"):
		users.update(frappe.get_all("User", filters={"custom_company": company}, pluck="name"))
	users.discard("Administrator")
	users.discard("Guest")
	if not users:
		return []
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", sorted(users)], "enabled": 1, "user_type": "System User"},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)
	return [{"user": r["name"], "name": r.get("full_name") or r["name"]} for r in rows]


# --- clocking ---------------------------------------------------------------------------------------


def _open_entry(company, user):
	rows = frappe.get_all(
		"Clock Entry",
		filters={"company": company, "user": user, "open_key": user},
		fields=ENTRY_FIELDS,
		limit=1,
	)
	return rows[0] if rows else None


def _todays_shifts(company, user, day):
	return frappe.get_all(
		"Rota Shift",
		filters={"company": company, "user": user, "shift_date": day},
		fields=SHIFT_FIELDS,
		order_by="start_time asc",
	)


def _state(company, user, now) -> dict:
	entry = _open_entry(company, user)
	shifts = [_shift_view(r) for r in _todays_shifts(company, user, now.date())]
	return {
		"clocked_in": entry is not None,
		"entry": _entry_view(entry, now) if entry else None,
		"shifts_today": shifts,
	}


@frappe.whitelist()
def get_clock_state():
	"""Whether the caller is clocked in, since when, and the shifts they are rostered for today."""
	user = _signed_in()
	return {"success": True, "data": _state(resolve_company(), user, now_datetime())}


@frappe.whitelist()
def clock_in(note=None):
	"""The caller clocks in now. Matched to the shift they are rostered for, with the minutes they are late."""
	user = _signed_in()
	company = resolve_company()
	now = now_datetime()
	if _open_entry(company, user):
		return _refusal("already_clocked_in")
	shifts = _todays_shifts(company, user, now.date())
	planned = rules.pick_shift(now, [dict(_rule_shift(s), name=s["name"]) for s in shifts])
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Clock Entry",
				"company": company,
				"user": user,
				"clock_in": now,
				"rota_shift": planned["name"] if planned else None,
				"late_minutes": rules.late_minutes(now, planned),
				"note": rules.clean_note(note),
				"open_key": user,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		frappe.db.commit()
	except frappe.UniqueValidationError:
		frappe.db.rollback()
		return _refusal("already_clocked_in")
	return {"success": True, "message": _("You are clocked in."), "data": _state(company, user, now)}


@frappe.whitelist()
def clock_out():
	"""The caller clocks out now."""
	user = _signed_in()
	company = resolve_company()
	now = now_datetime()
	entry = _open_entry(company, user)
	if not entry:
		return _refusal("not_clocked_in")
	frappe.db.set_value(
		"Clock Entry", entry["name"], {"clock_out": now, "open_key": None}, update_modified=True
	)
	frappe.db.commit()
	done = frappe.db.get_value("Clock Entry", entry["name"], ENTRY_FIELDS, as_dict=True)
	return {
		"success": True,
		"message": _("You are clocked out."),
		"data": {**_state(company, user, now), "entry": _entry_view(done)},
	}


@frappe.whitelist()
def close_clock_entry(name, clock_out):
	"""A manager closes an entry someone forgot to close, at the time they really left."""
	_require_manager()
	ensure_record_access("Clock Entry", name)
	row = frappe.db.get_value("Clock Entry", name, ["company", "clock_in", "clock_out"], as_dict=True)
	if not row:
		return _refusal("not_found")
	if row["clock_out"]:
		return _refusal("not_open")
	at = get_datetime(clock_out) if clock_out else None
	if at is None:
		return _refusal("bad_date")
	problem = rules.closing_problem(row["clock_in"], at, now_datetime())
	if problem:
		return _refusal(problem)
	frappe.db.set_value(
		"Clock Entry",
		name,
		{"clock_out": at, "open_key": None, "closed_by": frappe.session.user},
		update_modified=True,
	)
	frappe.db.commit()
	done = frappe.db.get_value("Clock Entry", name, ENTRY_FIELDS, as_dict=True)
	return {"success": True, "message": _("Entry closed."), "data": {"entry": _entry_view(done)}}


@frappe.whitelist()
def list_timesheet(start, end, user=None):
	"""
	Clock entries from `start` to `end` (dates, inclusive) with hours per person. A manager or owner sees everyone
	(or one person with `user`); anyone else sees only their own.
	"""
	caller = _signed_in()
	problem = rules.range_problem(start, end)
	if problem:
		return _refusal(problem)
	company = resolve_company()
	filters = {
		"company": company,
		"clock_in": ["between", [f"{rules.parse_date(start)} 00:00:00", f"{rules.parse_date(end)} 23:59:59"]],
	}
	if _is_manager():
		if user:
			filters["user"] = user
	else:
		if user and user != caller:
			return _refusal("not_allowed")
		filters["user"] = caller
	now = now_datetime()
	rows = frappe.get_all("Clock Entry", filters=filters, fields=ENTRY_FIELDS, order_by="clock_in asc")
	people = rules.totals_by_person(rows)
	for person in people:
		person["person"] = _person_name(person["user"])
	return {
		"success": True,
		"data": {"entries": [_entry_view(r, now) for r in rows], "people": people},
	}
