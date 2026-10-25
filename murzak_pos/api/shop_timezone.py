"""
The time zone a shop keeps its dates and times in.

Frappe falls back to Asia/Kolkata (India) when a site has no time zone chosen, and our shops are never given one.
India is 2.5 hours ahead of Kenya, so from 21:30 to midnight in Kenya a sale already carried tomorrow's date, which put
evening sales on the wrong day in the sales, profit and tax reports. A shop on the India default (or with none chosen)
is moved to Kenya's time. A shop that chose any other time zone is left alone.
"""

from __future__ import annotations

import frappe

KENYA = "Africa/Nairobi"
FRAPPE_FALLBACK = "Asia/Kolkata"


def use_kenya_time() -> None:
	"""Move a shop on the India default to Kenya's time. Safe to run again."""
	current = frappe.db.get_single_value("System Settings", "time_zone")
	if current and current != FRAPPE_FALLBACK:
		return
	frappe.db.set_single_value("System Settings", "time_zone", KENYA)
	frappe.clear_cache()
