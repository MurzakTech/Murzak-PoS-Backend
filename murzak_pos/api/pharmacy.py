"""
Selling prescription-only medicines: which items on a sale are flagged, and the dispensing record a sale of
them must carry. The rules themselves are in pharmacy_rules.py.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api.pharmacy_rules import check, clean_dispensing, remark

FLAG_FIELD = "custom_prescription_only"


def prescription_items(item_codes) -> list[str]:
	"""The items among these that are marked prescription-only. None when this site does not have the flag yet."""
	codes = [c for c in dict.fromkeys(item_codes) if c]
	if not codes or not frappe.get_meta("Item").has_field(FLAG_FIELD):
		return []
	flagged = frappe.get_all("Item", filters={"item_code": ["in", codes], FLAG_FIELD: 1}, pluck="item_code")
	return [code for code in codes if code in set(flagged)]


def sale_remark(items, dispensing, sold_offline: bool) -> str | None:
	"""
	The line to write on an invoice for its prescription-only items, or None when it has none.
	Refuses the sale when it has them and no complete dispensing record.
	"""
	flagged = prescription_items([row.get("item_code") for row in items])
	record = clean_dispensing(dispensing)
	status = check(flagged, record, bool(sold_offline))
	if status == "refuse":
		names = ", ".join(flagged)
		frappe.throw(
			_(
				"This sale includes prescription-only medicines ({0}). Enter the patient's name and who prescribed it."
			).format(names),
			frappe.ValidationError,
		)
	return remark(
		flagged, record, frappe.db.get_value("User", frappe.session.user, "full_name") or "", status
	)
