"""
Rules for selling prescription-only medicines that need no database: what a dispensing record holds, when a
sale must carry one, and the note written on the invoice.

Kept free of Frappe imports so it can be tested on its own (see test_pharmacy_rules.py).
"""

from __future__ import annotations

import json

MAX_NAME = 140
MAX_REFERENCE = 60

# What the till sends with a sale that includes prescription-only medicines
FIELDS = ("patient", "prescriber", "reference")


def _clean(value, limit) -> str:
	return " ".join(str(value or "").split())[:limit]


def clean_dispensing(raw) -> dict | None:
	"""
	The dispensing record sent with a sale, tidied: the patient, the prescriber and a prescription reference.
	None when nothing usable was sent. May arrive as an object or as JSON text.
	"""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			return None
	if not isinstance(raw, dict):
		return None
	record = {
		"patient": _clean(raw.get("patient"), MAX_NAME),
		"prescriber": _clean(raw.get("prescriber"), MAX_NAME),
		"reference": _clean(raw.get("reference"), MAX_REFERENCE),
	}
	return record if any(record.values()) else None


def is_complete(record) -> bool:
	"""A prescription sale needs at least who it is for and who prescribed it."""
	return bool(record and record.get("patient") and record.get("prescriber"))


def check(prescription_items: list[str], record, sold_offline: bool) -> str:
	"""
	What to do with a sale: "ok", "refuse" or "flag".

	A sale with prescription-only medicines and no complete record is refused, so it cannot be
	made by skipping the screen. A sale already made offline is never refused, because the medicine
	has left the shop and the sale has to be recorded; it is flagged instead, so it can be followed up.
	"""
	if not prescription_items or is_complete(record):
		return "ok"
	return "flag" if sold_offline else "refuse"


def remark(prescription_items: list[str], record, dispensed_by: str, status: str) -> str | None:
	"""The line written on the invoice, so the dispensing record can be read back from the sale."""
	if not prescription_items:
		return None
	items = ", ".join(prescription_items)
	if status == "flag":
		return f"PRESCRIPTION DETAILS MISSING for: {items}. Sold offline; the dispensing record must be completed."
	record = record or {}
	parts = [f"patient {record.get('patient')}", f"prescriber {record.get('prescriber')}"]
	if record.get("reference"):
		parts.append(f"prescription ref {record['reference']}")
	if dispensed_by:
		parts.append(f"dispensed by {dispensed_by}")
	return f"Prescription-only medicines ({items}): " + "; ".join(parts) + "."


def add_to_remarks(existing, note) -> str | None:
	"""The note added to what the invoice already says, without writing it twice."""
	if not note:
		return existing
	if existing and note in existing:
		return existing
	return f"{existing}\n{note}" if existing else note
