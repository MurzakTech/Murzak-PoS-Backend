"""
Who may change the shop type, and when. Kept free of Frappe imports so it can be tested on its own.

The shop type decides which starter products a shop is offered. Setting it the first time is free. Changing it afterwards
is limited to once in COOLDOWN_DAYS, because a shop that flips between types hides and shows products and confuses its
staff, and each change is written to the business's record so there is a trail of who did it and when.
"""

from __future__ import annotations

from datetime import datetime, timedelta

COOLDOWN_DAYS = 30
AUDIT_PREFIX = "Shop type set to "


def change_decision(
	current: str | None, wanted: str | None, last_change: datetime | None, now: datetime
) -> dict:
	"""
	Whether the shop type may be changed from `current` to `wanted` right now.

	Returns {"allowed": bool, "reason": "" | "none" | "same" | "cooldown", "next_change_on": date | None}.
	"""
	if not wanted:
		return {"allowed": False, "reason": "none", "next_change_on": None}
	if not current:
		return {"allowed": True, "reason": "", "next_change_on": None}
	if wanted == current:
		return {"allowed": False, "reason": "same", "next_change_on": None}
	if last_change is not None:
		reopens = last_change + timedelta(days=COOLDOWN_DAYS)
		if now < reopens:
			return {"allowed": False, "reason": "cooldown", "next_change_on": reopens.date()}
	return {"allowed": True, "reason": "", "next_change_on": None}


def next_change_on(last_change: datetime | None, now: datetime):
	"""The date the shop type can next be changed, or None when it can be changed now."""
	if last_change is None:
		return None
	reopens = last_change + timedelta(days=COOLDOWN_DAYS)
	return reopens.date() if now < reopens else None
