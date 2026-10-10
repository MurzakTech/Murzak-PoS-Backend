"""
Audit trail: who did what, and when, for one business.

Reads Frappe's own history (Version, Deleted Document, Activity Log) and shows
each business only the actions of its own staff. The wording of each event is
worked out in audit_events.py.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate

from techsavanna_pos.api.audit_events import (
	ACTIONS,
	MODULES,
	SECURITY_MODULE,
	TRACKED_DOCTYPES,
	describe_activity,
	describe_version,
	merge_events,
)
from techsavanna_pos.api.payment_gateway_common import resolve_company

# Roles that may read the audit trail. Business owners hold System Manager.
AUDIT_ROLES = {"System Manager", "Accounts Manager", "Auditor"}

MAX_PAGE_LENGTH = 100
# When filtering by an action that is only known after reading a Version, stop
# scanning after this many rows so one request cannot read the whole history.
MAX_VERSION_SCAN = 5000
VERSION_BATCH = 500

VERSION_ACTIONS = {"Created", "Updated", "Submitted", "Cancelled"}
SIGN_IN_ACTIONS = {"Signed in", "Signed out", "Sign-in failed"}


def _require_audit_access() -> None:
	if frappe.session.user == "Administrator":
		return
	if not AUDIT_ROLES.intersection(frappe.get_roles()):
		frappe.throw(
			_("Only an owner, Accounts Manager or Auditor can view the audit trail."),
			frappe.PermissionError,
		)


def _company_users(company: str) -> list[str]:
	"""Everyone who works for the company: custom_company, default company or a Company user permission."""
	users = set()
	if frappe.db.has_column("User", "custom_company"):
		users.update(frappe.get_all("User", filters={"custom_company": company}, pluck="name"))
	users.update(
		frappe.get_all("User Permission", filters={"allow": "Company", "for_value": company}, pluck="user")
	)
	users.update(
		frappe.get_all("DefaultValue", filters={"defkey": "company", "defvalue": company}, pluck="parent")
	)
	users.discard("__default")
	users.discard("Guest")
	return sorted(users)


def _date_filter(field: str, from_date: str | None, to_date: str | None) -> list:
	filters = []
	if from_date:
		filters.append([field, ">=", str(getdate(from_date))])
	if to_date:
		# to_date is inclusive: everything before the start of the next day.
		filters.append([field, "<", str(add_days(getdate(to_date), 1))])
	return filters


def _doctypes_for(module: str | None, doctype: str | None) -> list[str]:
	if doctype:
		return [doctype] if doctype in TRACKED_DOCTYPES else []
	return [d for d, m in TRACKED_DOCTYPES.items() if not module or m == module]


def _version_events(users, doctypes, dates, search, action, need) -> list[dict]:
	if not doctypes:
		return []
	filters = [["ref_doctype", "in", doctypes], ["owner", "in", users], *dates]
	if search:
		filters.append(["docname", "like", f"%{search}%"])

	events: list[dict] = []
	start = 0
	while len(events) < need and start < MAX_VERSION_SCAN:
		rows = frappe.get_all(
			"Version",
			filters=filters,
			fields=["name", "ref_doctype", "docname", "data", "owner", "creation"],
			order_by="creation desc, name desc",
			limit_start=start,
			limit_page_length=VERSION_BATCH if action else need,
		)
		for row in rows:
			described = describe_version(row.data)
			if action and described["action"] != action:
				continue
			events.append(
				{
					"id": f"v-{row.name}",
					"timestamp": str(row.creation),
					"user": row.owner,
					"action": described["action"],
					"module": TRACKED_DOCTYPES.get(row.ref_doctype, ""),
					"doctype": row.ref_doctype,
					"docname": row.docname,
					"summary": described["summary"],
				}
			)
		if not action or len(rows) < VERSION_BATCH:
			break
		start += VERSION_BATCH
	return events[:need]


def _deleted_events(users, doctypes, dates, search, need) -> list[dict]:
	if not doctypes:
		return []
	filters = [["deleted_doctype", "in", doctypes], ["owner", "in", users], *dates]
	if search:
		filters.append(["deleted_name", "like", f"%{search}%"])
	rows = frappe.get_all(
		"Deleted Document",
		filters=filters,
		fields=["name", "deleted_doctype", "deleted_name", "owner", "creation"],
		order_by="creation desc, name desc",
		limit_page_length=need,
	)
	return [
		{
			"id": f"d-{row.name}",
			"timestamp": str(row.creation),
			"user": row.owner,
			"action": "Deleted",
			"module": TRACKED_DOCTYPES.get(row.deleted_doctype, ""),
			"doctype": row.deleted_doctype,
			"docname": row.deleted_name,
			"summary": "Deleted",
		}
		for row in rows
	]


def _sign_in_events(users, dates, search, action, need) -> list[dict]:
	filters = [["operation", "in", ["Login", "Logout"]], ["user", "in", users], *dates]
	if action == "Sign-in failed":
		filters.append(["status", "=", "Failed"])
	elif action == "Signed in":
		filters += [["operation", "=", "Login"], ["status", "!=", "Failed"]]
	elif action == "Signed out":
		filters.append(["operation", "=", "Logout"])
	if search:
		filters.append(["user", "like", f"%{search}%"])
	rows = frappe.get_all(
		"Activity Log",
		filters=filters,
		fields=["name", "user", "operation", "status", "subject", "ip_address", "creation"],
		order_by="creation desc, name desc",
		limit_page_length=need,
	)
	events = []
	for row in rows:
		described = describe_activity(row.operation, row.status, row.subject)
		summary = described["summary"]
		if row.ip_address:
			summary = f"{summary} (from {row.ip_address})"
		events.append(
			{
				"id": f"a-{row.name}",
				"timestamp": str(row.creation),
				"user": row.user,
				"action": described["action"],
				"module": SECURITY_MODULE,
				"doctype": "",
				"docname": "",
				"summary": summary,
			}
		)
	return events


@frappe.whitelist()
def list_audit_events(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	user: str | None = None,
	module: str | None = None,
	doctype: str | None = None,
	action: str | None = None,
	search: str | None = None,
	limit_start: int = 0,
	limit_page_length: int = 50,
) -> dict:
	"""
	List audit events for the caller's business, newest first.

	Filters: date range (inclusive), staff member, module, document type, action
	and a search on the document number (or the email address for sign-ins).
	"""
	_require_audit_access()
	company = resolve_company(company)

	start = max(cint(limit_start), 0)
	page_length = min(max(cint(limit_page_length) or 50, 1), MAX_PAGE_LENGTH)
	need = start + page_length + 1
	search = (search or "").strip()

	users = _company_users(company)
	if user:
		users = [u for u in users if u == user]
	if action and action not in ACTIONS:
		frappe.throw(_("Unknown action {0}.").format(action), frappe.ValidationError)
	if module and module not in MODULES:
		frappe.throw(_("Unknown area {0}.").format(module), frappe.ValidationError)

	sources: list[list[dict]] = []
	if users:
		doctypes = _doctypes_for(module, doctype)
		if not action or action in VERSION_ACTIONS:
			sources.append(
				_version_events(
					users, doctypes, _date_filter("creation", from_date, to_date), search, action, need
				)
			)
		if not action or action == "Deleted":
			sources.append(
				_deleted_events(users, doctypes, _date_filter("creation", from_date, to_date), search, need)
			)
		if (
			(not action or action in SIGN_IN_ACTIONS)
			and not doctype
			and module in (None, "", SECURITY_MODULE)
		):
			sources.append(
				_sign_in_events(users, _date_filter("creation", from_date, to_date), search, action, need)
			)

	result = merge_events(sources, start, page_length)

	names = {
		u.name: u.full_name
		for u in frappe.get_all(
			"User",
			filters={"name": ["in", list({e["user"] for e in result["events"]}) or [""]]},
			fields=["name", "full_name"],
		)
	}
	for event in result["events"]:
		event["user_name"] = names.get(event["user"]) or event["user"]

	return {
		"success": True,
		"data": result["events"],
		"has_more": result["has_more"],
	}


@frappe.whitelist()
def get_audit_filter_options(company: str | None = None) -> dict:
	"""Choices for the audit trail filters: staff, areas, document types and actions."""
	_require_audit_access()
	company = resolve_company(company)
	users = _company_users(company)
	staff = (
		frappe.get_all(
			"User",
			filters={"name": ["in", users]},
			fields=["name", "full_name"],
			order_by="full_name asc",
		)
		if users
		else []
	)
	return {
		"success": True,
		"data": {
			"users": [{"value": s.name, "label": s.full_name or s.name} for s in staff],
			"modules": MODULES,
			"doctypes": [{"value": d, "module": m} for d, m in TRACKED_DOCTYPES.items()],
			"actions": list(ACTIONS),
		},
	}
