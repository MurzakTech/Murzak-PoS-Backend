"""
Keep the site's background jobs switched on.

Frappe sends nothing from its email queue, and runs no scheduled job (alerts, stock sync, payment checks), while the scheduler is
off in System Settings. A new site can start with it off, so it is switched on after every update and on a fresh install.

This only changes the System Settings switch. If the server itself holds the scheduler off (maintenance mode, a paused
scheduler or a disabled one in the site's config file, or no worker process running), that is the host's to change and the
set-up health check keeps saying so.
"""

from __future__ import annotations

import frappe


def ensure_scheduler_on() -> None:
	"""Turn the scheduler on in System Settings when it is off. Safe to run again; never stops an update."""
	try:
		if not frappe.db.get_single_value("System Settings", "enable_scheduler"):
			frappe.db.set_single_value("System Settings", "enable_scheduler", 1)
	except Exception:
		frappe.log_error("The scheduler could not be switched on", frappe.get_traceback())
