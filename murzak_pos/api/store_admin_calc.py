# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
The rules behind editing a store and choosing who works at it (store_admin_api). Plain functions, no database, so
each rule can be tested alone.

A person with no store chosen can use every store; choosing a store for them limits them to the stores chosen.
That is why `everywhere` matters: taking someone off their last store gives them every store again, and the owner is
told so.
"""

import json
import re

MAX_PEOPLE = 200
MAX_LINE = 140
_BAD_NAME = re.compile(r"[<>\\/]")


class StoreAdminError(ValueError):
	"""Something asked for cannot be used. `code` says why; store_admin_api turns it into a plain sentence."""

	def __init__(self, code: str):
		super().__init__(code)
		self.code = code


def clean_line(value, limit: int = MAX_LINE) -> str:
	"""One tidy line typed by a person: single spaces, at most `limit` characters."""
	return " ".join(str(value or "").split())[:limit]


def clean_name(value) -> str:
	"""The new name of a store: 2 to 60 characters, none of < > / or a backslash."""
	name = clean_line(value, 61)
	if not name:
		raise StoreAdminError("need_name")
	if len(name) < 2 or len(name) > 60 or _BAD_NAME.search(name):
		raise StoreAdminError("bad_name")
	return name


def clean_text(value) -> str:
	"""A town or an address: one line, no angle brackets."""
	text = clean_line(value)
	if _BAD_NAME.search(text):
		raise StoreAdminError("bad_text")
	return text


def clean_people(people) -> list[str]:
	"""The people chosen for a store: a list (or JSON text) of user names, each once. An empty list is allowed (nobody chosen)."""
	if isinstance(people, str):
		try:
			people = json.loads(people or "[]")
		except ValueError:
			raise StoreAdminError("bad_people") from None
	if not isinstance(people, list):
		raise StoreAdminError("bad_people")
	if len(people) > MAX_PEOPLE:
		raise StoreAdminError("too_many_people")
	cleaned, seen = [], set()
	for person in people:
		if not isinstance(person, str) or not person.strip():
			raise StoreAdminError("bad_people")
		person = person.strip()
		if person not in seen:
			seen.add(person)
			cleaned.append(person)
	return cleaned


def plan_people(current, wanted) -> tuple[list[str], list[str]]:
	"""(to add, to remove) so that exactly the `wanted` people work at the store."""
	have, want = set(current or []), set(wanted or [])
	return sorted(want - have), sorted(have - want)


def members(users, permissions, warehouse) -> list[dict]:
	"""
	The people of the business for the chooser: { user, name, assigned, stores }. `assigned` says whether this store is
	one of theirs; `stores` is how many stores they are limited to (0 means they can use every store).
	"""
	count: dict[str, int] = {}
	here: set[str] = set()
	for row in permissions or []:
		count[row["user"]] = count.get(row["user"], 0) + 1
		if row["for_value"] == warehouse:
			here.add(row["user"])
	return [
		{
			"user": u["name"],
			"name": u.get("full_name") or u["name"],
			"assigned": u["name"] in here,
			"stores": count.get(u["name"], 0),
		}
		for u in sorted(users or [], key=lambda u: (u.get("full_name") or u["name"]).lower())
	]


def everywhere(removed, remaining_count: dict) -> list[str]:
	"""Those just taken off a store who now have no store left, so can use every store."""
	return [user for user in removed or [] if not remaining_count.get(user)]
