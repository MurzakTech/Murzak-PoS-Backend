"""
Kitchen and bar tickets: orders sent from a till to the stations, and what the stations do with them.
A ticket never touches stock, accounts or reports.

Contract: KITCHEN_TICKETS_SERVER_API.md in the Murzak-PoS-Frontend repository.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint

from techsavanna_pos.api.held_sales_api import _iso, _to_utc, _utc_now
from techsavanna_pos.api.payment_gateway_common import resolve_company

STATUSES = ("New", "Preparing", "Ready", "Served")
ORDER_TYPES = ("table", "counter")
# Who may change the stations, the categories each serves and whether station screens are used
SETTINGS_ROLES = {"System Manager", "Accounts Manager", "Sales Manager"}

MAX_TICKETS = 20  # in one send: one per station, with room to spare
MAX_LINES = 100  # per ticket
MAX_LINES_TEXT = 64 * 1024
MAX_LIST = 500
MAX_ID = 140
MAX_LABEL = 40
MAX_STATION = 40
MAX_STATIONS = 12
MAX_NOTES = 12
MAX_NOTE = 80
MAX_NAME = 20
MAX_GROUP = 140
KEEP_DAYS = 90
LOCK_SECONDS = 10


class _Refused(Exception):
	"""A request the server declines, with a reason in plain words."""


def _person(user):
	return (frappe.db.get_value("User", user, "full_name") or user) if user else ""


def _as_object(value, default):
	"""JSON arrives either already read or as text."""
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			return default
	return default if value is None else value


def _text(value, limit) -> str:
	return " ".join(str(value or "").split())[:limit]


# ------------------------------------------------------------------ settings


def _clean_settings(raw) -> dict:
	"""
	The settings every till of a business shares, checked and cut down to what the till uses.
	Anything else in the request is dropped, so the table only ever holds the five agreed keys.
	"""
	unreadable = _Refused(_("These kitchen settings could not be read."))
	raw = _as_object(raw, None)
	if not isinstance(raw, dict):
		raise unreadable

	stations_raw = raw.get("stations") or []
	notes_raw = raw.get("quickNotes") or []
	if not isinstance(stations_raw, list) or not isinstance(notes_raw, list):
		raise unreadable
	if len(stations_raw) > MAX_STATIONS:
		raise _Refused(_("A business can have at most {0} stations.").format(MAX_STATIONS))
	if len(notes_raw) > MAX_NOTES:
		raise _Refused(_("There can be at most {0} quick notes.").format(MAX_NOTES))

	stations = []
	for number, station in enumerate(stations_raw, start=1):
		if not isinstance(station, dict):
			raise unreadable
		name = _text(station.get("name"), MAX_NAME)
		if not name:
			raise _Refused(_("Every station needs a name."))
		groups = station.get("groups") or []
		if not isinstance(groups, list):
			raise unreadable
		stations.append(
			{
				"id": _text(station.get("id"), MAX_ID) or f"station-{number}",
				"name": name,
				"groups": [g for g in (_text(group, MAX_GROUP) for group in groups) if g],
			}
		)

	names = [station["name"].lower() for station in stations]
	if len(set(names)) != len(names):
		raise _Refused(_("Two stations have the same name."))

	return {
		"enabled": raw.get("enabled") is True,
		"screens": raw.get("screens") is True,
		"stations": stations,
		"defaultStation": _text(raw.get("defaultStation"), MAX_ID),
		"quickNotes": [n for n in (_text(note, MAX_NOTE) for note in notes_raw) if n],
	}


def _may_change_settings() -> bool:
	return frappe.session.user == "Administrator" or bool(SETTINGS_ROLES.intersection(frappe.get_roles()))


@frappe.whitelist(methods=["POST"])
def get_kitchen_settings(company=None):
	"""The settings every till of the business shares, or null when none were saved yet."""
	company = resolve_company(company)
	raw = frappe.db.get_value("POS Kitchen Settings", {"company": company}, "settings")
	settings = _as_object(raw, None) if raw else None
	return {"success": True, "data": {"settings": settings if isinstance(settings, dict) else None}}


@frappe.whitelist(methods=["POST"])
def save_kitchen_settings(settings, company=None):
	"""Save the shared settings. Only managers may."""
	company = resolve_company(company)
	if not _may_change_settings():
		return {"success": False, "message": _("Only a manager can change the kitchen settings.")}
	try:
		clean = _clean_settings(settings)
	except _Refused as refusal:
		return {"success": False, "message": str(refusal)}

	text = json.dumps(clean, separators=(",", ":"))
	# Access was checked by resolve_company above, so the save itself skips the desk permissions
	name = frappe.db.get_value("POS Kitchen Settings", {"company": company})
	doc = frappe.get_doc("POS Kitchen Settings", name) if name else frappe.new_doc("POS Kitchen Settings")
	doc.update({"company": company, "settings": text})
	doc.save(ignore_permissions=True)
	return {"success": True, "data": {}}


# ------------------------------------------------------------------ tickets


def _lines_text(lines) -> str:
	"""Check what the ticket says to make, change and stop making, and return it as JSON text."""
	unreadable = _Refused(_("A ticket could not be read."))
	lines = _as_object(lines, None)
	if not isinstance(lines, dict):
		raise unreadable

	kept = {}
	total = 0
	for kind in ("adds", "changes", "voids"):
		part = lines.get(kind) or []
		if not isinstance(part, list) or not all(isinstance(line, dict) for line in part):
			raise unreadable
		kept[kind] = part
		total += len(part)
	if total == 0:
		raise _Refused(_("A ticket needs at least one item."))
	if total > MAX_LINES:
		raise _Refused(_("A ticket can have at most {0} lines.").format(MAX_LINES))

	text = json.dumps(kept, separators=(",", ":"))
	if len(text.encode("utf-8")) > MAX_LINES_TEXT:
		raise _Refused(_("A ticket is too large."))
	return text


def _clean_tickets(tickets) -> list[dict]:
	"""The tickets of one send, checked. Each carries the values the table keeps."""
	tickets = _as_object(tickets, None)
	if not isinstance(tickets, list) or not tickets:
		raise _Refused(_("There is nothing to send."))
	if len(tickets) > MAX_TICKETS:
		raise _Refused(_("There are too many tickets in one send."))

	clean, seen = [], set()
	for ticket in tickets:
		if not isinstance(ticket, dict):
			raise _Refused(_("A ticket could not be read."))
		client_id = _text(ticket.get("client_id"), MAX_ID)
		station = _text(ticket.get("station"), MAX_STATION)
		if not client_id or not station:
			raise _Refused(_("A ticket needs an id and a station."))
		if client_id in seen:
			raise _Refused(_("Two tickets have the same id."))
		seen.add(client_id)

		order_type = ticket.get("order_type")
		created = _to_utc(ticket.get("created_at")) if ticket.get("created_at") else None
		clean.append(
			{
				"client_id": client_id,
				"station": station,
				"round": max(cint(ticket.get("round")), 1),
				"label": _text(ticket.get("label"), MAX_LABEL),
				"order_type": order_type if order_type in ORDER_TYPES else "table",
				"created_at": created or _utc_now(),
				"lines": _lines_text(ticket.get("lines")),
			}
		)
	return clean


def _ticket(d) -> dict:
	"""A ticket in the shape the till and the station screen read."""
	try:
		lines = json.loads(d.lines or "{}")
	except ValueError:
		lines = {}
	return {
		"id": d.name,
		"client_id": d.client_id,
		"station": d.station,
		"number": d.number,
		"round": d.round or 1,
		"label": d.label or "",
		"order_type": d.order_type or "table",
		"sent_by": d.owner,
		"sent_by_name": _person(d.owner),
		"created_at": _iso(d.created_at),
		"status": d.status or "New",
		"status_by": d.status_by or "",
		"status_by_name": _person(d.status_by),
		"status_at": _iso(d.status_at) or "",
		"lines": {
			"adds": lines.get("adds") or [],
			"changes": lines.get("changes") or [],
			"voids": lines.get("voids") or [],
		},
	}


@contextmanager
def _numbering_lock(company, warehouse, day):
	"""
	Only one send per business, store and day at a time.

	Two tills sending at the same moment must not take the same number, and a till that retries must not
	create its tickets twice. A database lock makes them take turns. The tickets are committed before the lock
	is let go, so that the next sender sees them.
	"""
	key = "kt:" + hashlib.md5(f"{company}|{warehouse or ''}|{day}".encode()).hexdigest()
	got = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (key, LOCK_SECONDS))
	if not got or got[0][0] != 1:
		raise _Refused(_("The kitchen is busy just now. Please try again."))
	try:
		yield
		frappe.db.commit()
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (key,))


def _next_number(company, warehouse, day) -> int:
	row = frappe.db.sql(
		"""SELECT COALESCE(MAX(number), 0) FROM `tabKitchen Ticket`
		WHERE company=%s AND IFNULL(warehouse, '')=%s AND ticket_date=%s""",
		(company, warehouse or "", day),
	)
	return cint(row[0][0]) + 1


def _existing(company, client_ids) -> dict:
	rows = frappe.get_all(
		"Kitchen Ticket",
		filters={"company": company, "client_id": ["in", client_ids]},
		fields=["*"],
	)
	return {row.client_id: frappe._dict(row) for row in rows}


@frappe.whitelist(methods=["POST"])
def send_tickets(tickets, company=None, warehouse=None):
	"""
	Hand tickets to their stations, one per station. The server numbers them: every ticket of one send shares
	one number, which starts again at 1 each day.

	Sending a ticket the server already has (the same client_id) returns the ticket it has, with its original
	number, and creates nothing: the till retries after a dropped connection.
	"""
	company = resolve_company(company)
	try:
		clean = _clean_tickets(tickets)
	except _Refused as refusal:
		return {"success": False, "message": str(refusal)}

	warehouse = warehouse or None
	day = frappe.utils.today()
	ids = [ticket["client_id"] for ticket in clean]

	try:
		with _numbering_lock(company, warehouse, day):
			found = _existing(company, ids)
			fresh = [ticket for ticket in clean if ticket["client_id"] not in found]
			if fresh:
				# A retry that finds some of its tickets already saved gives the rest the same number
				number = next(iter(found.values())).number if found else _next_number(company, warehouse, day)
				for ticket in fresh:
					doc = frappe.new_doc("Kitchen Ticket")
					doc.update(
						{
							**ticket,
							"company": company,
							"warehouse": warehouse,
							"ticket_date": day,
							"number": number,
							"status": "New",
						}
					)
					# Access was checked by resolve_company above, so the save skips the desk permissions
					doc.insert(ignore_permissions=True)
					found[ticket["client_id"]] = doc
	except _Refused as refusal:
		return {"success": False, "message": str(refusal)}

	return {"success": True, "data": {"tickets": [_ticket(found[client_id]) for client_id in ids]}}


@frappe.whitelist(methods=["POST"])
def list_tickets(company=None, warehouse=None, station=None, statuses=None, since=None):
	"""
	Tickets for a station screen, or for a till that wants to know what is ready. Oldest first.
	With `warehouse`, that store's tickets and tickets sent with no store.
	"""
	company = resolve_company(company)
	filters = {"company": company}
	if station:
		filters["station"] = _text(station, MAX_STATION)

	asked = _as_object(statuses, None)
	if asked:
		wanted = [s for s in asked if s in STATUSES] if isinstance(asked, list) else []
		if not wanted:  # only statuses that do not exist were asked for
			return {"success": True, "data": {"tickets": []}}
		filters["status"] = ["in", wanted]

	if since:
		moment = _to_utc(since)
		if moment is None:
			return {"success": False, "message": _("The time to list from could not be read.")}
		filters["created_at"] = [">=", moment]

	kwargs = {}
	if warehouse:
		kwargs["or_filters"] = [["warehouse", "is", "not set"], ["warehouse", "=", warehouse]]

	# The newest tickets are the ones that matter if there are ever more than the limit
	rows = frappe.get_all(
		"Kitchen Ticket",
		filters=filters,
		fields=["*"],
		order_by="created_at desc",
		limit_page_length=MAX_LIST,
		**kwargs,
	)
	return {"success": True, "data": {"tickets": [_ticket(frappe._dict(r)) for r in reversed(rows)]}}


@frappe.whitelist(methods=["POST"])
def set_ticket_status(id, status, company=None):
	"""Move a ticket along: New, Preparing, Ready, Served. Any of the four, so a mistaken tap can be undone."""
	company = resolve_company(company)
	if status not in STATUSES:
		return {"success": False, "message": _("That is not a ticket status.")}

	name = frappe.db.get_value("Kitchen Ticket", {"name": str(id), "company": company})
	if not name:
		return {"success": False, "message": _("This ticket was not found.")}

	doc = frappe.get_doc("Kitchen Ticket", name)
	doc.update({"status": status, "status_by": frappe.session.user, "status_at": _utc_now()})
	doc.save(ignore_permissions=True)
	return {"success": True, "data": _ticket(doc)}


def purge_old_tickets():
	"""
	Daily job: remove tickets from more than KEEP_DAYS days ago.

	Until then they are the record, cancelled items included: in a bar, quietly removing sent items is a
	classic way to hide drinks that were served. The station screen only shows the last day.
	"""
	cutoff = _utc_now() - timedelta(days=KEEP_DAYS)
	frappe.db.sql("DELETE FROM `tabKitchen Ticket` WHERE created_at < %s", (cutoff,))
