"""
Show Kenyan shillings as "KES" in the ERP, the same as in the POS.

ERPNext ships the symbol "Sh" for KES, so lists in the ERP read "Sh 13,920.00" while the POS reads "KES 13,920.00".
A site still on the shipped symbol is changed to "KES". A symbol the business chose for itself is left alone.
"""

from __future__ import annotations

import frappe

SHIPPED_SYMBOLS = ("", "Sh", "KSh")
WANTED = "KES"


def needs_change(current) -> bool:
	"""True when the symbol is empty or one of the ones ERPNext ships for KES."""
	return (current or "").strip() in SHIPPED_SYMBOLS


def use_kes_symbol() -> None:
	"""Set KES's symbol to "KES" on a site still using the shipped one. Safe to run again."""
	if not frappe.db.exists("Currency", "KES"):
		return
	if needs_change(frappe.db.get_value("Currency", "KES", "symbol")):
		frappe.db.set_value("Currency", "KES", "symbol", WANTED)
		frappe.clear_cache()
