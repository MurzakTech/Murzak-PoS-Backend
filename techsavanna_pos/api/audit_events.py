"""
Turns Frappe's raw history records into plain-language audit events.

Kept free of Frappe imports so it can be tested on its own. The database work
(which records, for which business) lives in audit_api.py.

Three Frappe logs feed the audit trail:
- Version: one row per save of a tracked document, with the fields that changed.
- Deleted Document: a copy of every deleted document.
- Activity Log: sign-ins, sign-outs and failed sign-in attempts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

# Documents worth auditing, grouped by the area of the business they belong to.
TRACKED_DOCTYPES: dict[str, str] = {
	"Sales Invoice": "Sales",
	"POS Invoice": "Sales",
	"POS Opening Entry": "Sales",
	"POS Closing Entry": "Sales",
	"Payment Entry": "Payments",
	"Purchase Order": "Purchases",
	"Purchase Receipt": "Purchases",
	"Purchase Invoice": "Purchases",
	"Stock Entry": "Inventory",
	"Stock Reconciliation": "Inventory",
	"Material Request": "Inventory",
	"Item": "Products",
	"Item Price": "Products",
	"Pricing Rule": "Products",
	"Customer": "Customers",
	"Supplier": "Suppliers",
	"User": "Users & Security",
}

SECURITY_MODULE = "Users & Security"

MODULES: list[str] = sorted(set(TRACKED_DOCTYPES.values()))

ACTIONS = (
	"Created",
	"Updated",
	"Submitted",
	"Cancelled",
	"Deleted",
	"Signed in",
	"Signed out",
	"Sign-in failed",
)

# Bookkeeping fields Frappe changes on every save; listing them would bury the real changes.
IGNORED_FIELDS = {
	"modified",
	"modified_by",
	"idx",
	"lft",
	"rgt",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
	"_seen",
	"last_login",
	"last_active",
	"last_ip",
	"last_known_versions",
}

# Fields that hold secrets: show that they changed, never their values.
SECRET_FIELDS = {"new_password", "password", "api_secret", "api_key"}

MAX_CHANGES_SHOWN = 4


def humanize_field(fieldname: str) -> str:
	"""grand_total -> Grand total"""
	text = (fieldname or "").replace("_", " ").strip()
	return text[:1].upper() + text[1:]


def _short(value) -> str:
	if value is None or value == "":
		return "empty"
	text = str(value)
	return text if len(text) <= 40 else text[:37] + "..."


def parse_version_data(raw) -> dict:
	"""Version.data is stored as JSON text; tolerate dicts, empty and broken values."""
	if isinstance(raw, dict):
		return raw
	try:
		data = json.loads(raw or "{}")
	except (TypeError, ValueError):
		return {}
	return data if isinstance(data, dict) else {}


def describe_version(raw) -> dict[str, str]:
	"""
	Work out what a saved Version means: an action and a one-line summary.

	A change of docstatus is the event that matters for business documents
	(0 -> 1 is Submitted, 1 -> 2 is Cancelled), so it wins over other changes.
	"""
	data = parse_version_data(raw)
	changed = [c for c in data.get("changed") or [] if isinstance(c, (list, tuple)) and len(c) == 3]

	for field, _old, new in changed:
		if field == "docstatus":
			if str(new) == "1":
				return {"action": "Submitted", "summary": "Submitted"}
			if str(new) == "2":
				return {"action": "Cancelled", "summary": "Cancelled"}

	parts: list[str] = []
	for field, old, new in changed:
		if field in IGNORED_FIELDS or field == "docstatus":
			continue
		if field in SECRET_FIELDS:
			parts.append(f"{humanize_field(field)} changed")
		else:
			parts.append(f"{humanize_field(field)}: {_short(old)} → {_short(new)}")

	for key, verb in (("added", "added"), ("removed", "removed")):
		for row in data.get(key) or []:
			table = row[0] if isinstance(row, (list, tuple)) and row else "row"
			parts.append(f"{humanize_field(table)}: row {verb}")

	for row in data.get("row_changed") or []:
		table = row[0] if isinstance(row, (list, tuple)) and row else "row"
		parts.append(f"{humanize_field(table)}: row edited")

	if not parts:
		# Frappe records a document's first save with no field changes.
		if not changed and not any(data.get(k) for k in ("added", "removed", "row_changed")):
			return {"action": "Created", "summary": "Created"}
		return {"action": "Updated", "summary": "Saved with no visible changes"}

	extra = len(parts) - MAX_CHANGES_SHOWN
	summary = "; ".join(parts[:MAX_CHANGES_SHOWN])
	if extra > 0:
		summary += f"; and {extra} more"
	return {"action": "Updated", "summary": summary}


def describe_activity(
	operation: str | None, status: str | None, subject: str | None = None
) -> dict[str, str]:
	"""Map an Activity Log row (sign-in history) to an action and summary."""
	if (status or "").lower() == "failed":
		return {"action": "Sign-in failed", "summary": subject or "Failed sign-in attempt"}
	if (operation or "").lower() == "logout":
		return {"action": "Signed out", "summary": subject or "Signed out"}
	return {"action": "Signed in", "summary": subject or "Signed in"}


def merge_events(sources: Iterable[list[dict]], start: int, page_length: int) -> dict[str, object]:
	"""
	Merge already newest-first event lists into one page.

	Each source must hold at least start + page_length + 1 rows when it has
	that many, so the page and the "is there more" answer are both exact.
	"""
	combined: list[dict] = []
	for rows in sources:
		combined.extend(rows)
	combined.sort(key=lambda e: (str(e.get("timestamp") or ""), str(e.get("id") or "")), reverse=True)
	page = combined[start : start + page_length]
	return {"events": page, "has_more": len(combined) > start + page_length}
