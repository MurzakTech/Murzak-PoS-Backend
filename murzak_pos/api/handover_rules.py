"""
Handing a till from one cashier to the next: the rules, with no database (api/handover_api.py makes the calls).

The outgoing shift is closed with a count. The next cashier's shift opens with the counted cash as its float, so
the next count starts from what was really in the drawer. A difference found in the count stays with the cashier
who counted it out; it is not carried over.
"""

from __future__ import annotations

NOTE_LIMIT = 140
CASH = "cash"


def clean_note(note) -> str:
	"""A short free-text note, trimmed and cut to NOTE_LIMIT characters."""
	return " ".join(str(note or "").split())[:NOTE_LIMIT]


def cash_float(figure_lines) -> tuple[str | None, float]:
	"""
	The cash mode and the amount counted in it, which is the float the next shift opens with. (None, 0.0) when the
	till has no cash line. Only a counted line gives a float; an uncounted one is never guessed.
	"""
	for line in figure_lines or []:
		if line.get("key") == CASH and line.get("counted") is not None:
			return line["mode"], round(float(line["counted"]), 2) + 0.0
	return None, 0.0


def refuse_recipient(outgoing, incoming, exists, enabled, same_business) -> str | None:
	"""Why `incoming` cannot take the till, as an error code; None when they can."""
	if not incoming or not exists:
		return "no_such_person"
	if incoming == outgoing:
		return "same_person"
	if not enabled:
		return "person_disabled"
	if not same_business:
		return "other_business"
	return None


def handover_text(outgoing_name, incoming_name, counted, difference, note="") -> str:
	"""The line kept on both shifts."""
	text = f"Till handed over from {outgoing_name} to {incoming_name}. Cash counted: {counted:,.2f}."
	if difference:
		word = "over" if difference > 0 else "short"
		text += f" Count was {word} by {abs(difference):,.2f}."
	if note:
		text += f" Note: {note}"
	return text
