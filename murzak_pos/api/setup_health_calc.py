"""
Setup health: is the shop ready to work, and if not, what to fix.

A shop can look fine and still be broken: the background scheduler off, M-Pesa half set up, no store, no till profile. The
owner used to find out when a sale or a goods receipt failed. This turns a handful of facts about the site into a short
list, each line green (fine), amber (works, but worth a look) or red (will fail), with one plain sentence on what it means
and where to fix it. It only reads; it never changes a setting.

Kept free of Frappe imports so it can be tested on its own. The facts are gathered in setup_health_api.py.
"""

from __future__ import annotations

GREEN, AMBER, RED = "green", "amber", "red"


def _check(check_id, title, status, detail, where=""):
	return {"id": check_id, "title": title, "status": status, "detail": detail, "where": where}


def build_report(facts: dict) -> dict:
	"""
	The checks for these facts. A fact that is missing is treated as unknown, which is amber, never green.

	facts: scheduler ("on" | "off" | "unknown"), pos_profile (None or {"update_stock", "has_store"}), stores (int),
	cash (bool), mpesa (None, or {"prompt_ready", "problem", "code_entry"}), etims ("active" | "inactive" | "absent"),
	customer_defaults (bool), shop_type (bool), fiscal_year (bool), currency (str or None)
	"""
	checks = []

	scheduler = facts.get("scheduler", "unknown")
	if scheduler == "on":
		checks.append(_check("scheduler", "Background jobs", GREEN, "Background jobs are running."))
	elif scheduler == "off":
		checks.append(
			_check(
				"scheduler",
				"Background jobs",
				RED,
				"Background jobs are switched off. Payment checks, reports and clean-ups will not run until they are turned on.",
				"Ask your technical support to turn the scheduler on for this site.",
			)
		)
	else:
		checks.append(
			_check(
				"scheduler",
				"Background jobs",
				AMBER,
				"We could not tell whether background jobs are running.",
				"Ask your technical support to check the scheduler.",
			)
		)

	stores = int(facts.get("stores") or 0)
	if stores > 0:
		checks.append(
			_check(
				"stores", "A store to sell from", GREEN, f"{stores} store{'s' if stores != 1 else ''} set up."
			)
		)
	else:
		checks.append(
			_check(
				"stores",
				"A store to sell from",
				RED,
				"There is no store, so nothing can be sold or received.",
				"Settings > Business, or Stock > Stores.",
			)
		)

	profile = facts.get("pos_profile")
	if not profile:
		checks.append(
			_check(
				"till",
				"Till profile",
				RED,
				"The till has no profile yet, so it cannot open.",
				"Settings > Till and payments.",
			)
		)
	elif not profile.get("update_stock"):
		checks.append(
			_check(
				"till",
				"Till profile",
				AMBER,
				"Sales do not take items out of stock, so your stock levels will drift from what is on the shelf.",
				"Settings > Till and payments > Take sold items out of stock.",
			)
		)
	else:
		checks.append(
			_check("till", "Till profile", GREEN, "The till is set up and sales take items out of stock.")
		)

	if facts.get("cash"):
		checks.append(_check("cash", "Cash payments", GREEN, "Cash is set up."))
	else:
		checks.append(
			_check(
				"cash",
				"Cash payments",
				RED,
				"Cash is not set up as a way of paying, so the till cannot take cash.",
				"Settings > Till and payments > Payment methods.",
			)
		)

	mpesa = facts.get("mpesa")
	if mpesa is None:
		checks.append(
			_check(
				"mpesa",
				"M-Pesa",
				AMBER,
				"M-Pesa is not set up. The till will not offer it.",
				"Settings > Till and payments > Set up M-Pesa.",
			)
		)
	elif mpesa.get("prompt_ready"):
		checks.append(
			_check(
				"mpesa",
				"M-Pesa",
				GREEN,
				"M-Pesa is set up and can send a payment prompt to the customer's phone.",
			)
		)
	else:
		detail = mpesa.get("problem") or "M-Pesa cannot send a payment prompt yet."
		tail = (
			" Cashiers can still take the customer's M-Pesa code."
			if mpesa.get("code_entry")
			else " Cashiers cannot take M-Pesa until this is fixed."
		)
		checks.append(_check("mpesa", "M-Pesa", AMBER, detail + tail, "Settings > Payment gateways."))

	etims = facts.get("etims", "absent")
	if etims == "active":
		checks.append(_check("etims", "Tax (eTIMS)", GREEN, "eTIMS is set up and switched on."))
	elif etims == "inactive":
		checks.append(
			_check(
				"etims",
				"Tax (eTIMS)",
				AMBER,
				"eTIMS is set up but switched off, so sales and bills are not sent to KRA.",
				"Settings > Tax.",
			)
		)
	else:
		checks.append(
			_check(
				"etims",
				"Tax (eTIMS)",
				AMBER,
				"eTIMS is not set up. This is fine if your shop is not registered for it; if it is, sales and bills are not being sent to KRA.",
				"Settings > Tax.",
			)
		)

	if facts.get("customer_defaults", True):
		checks.append(
			_check(
				"customers",
				"New customer defaults",
				GREEN,
				"New customers get a sensible group and territory.",
			)
		)
	else:
		checks.append(
			_check(
				"customers",
				"New customer defaults",
				AMBER,
				"New customers may get an odd group or territory (such as Government or Rest Of The World).",
				"Selling Settings: choose the default customer group and territory.",
			)
		)

	if facts.get("shop_type"):
		checks.append(_check("shop_type", "Shop type", GREEN, "Your shop type is set."))
	else:
		checks.append(
			_check(
				"shop_type",
				"Shop type",
				AMBER,
				"Your shop type is not set, so no starter products are offered.",
				"Settings > Business > Shop type.",
			)
		)

	if facts.get("fiscal_year"):
		checks.append(_check("fiscal_year", "Financial year", GREEN, "The financial year covers today."))
	else:
		checks.append(
			_check(
				"fiscal_year",
				"Financial year",
				RED,
				"No financial year covers today's date, so sales and expenses cannot be recorded.",
				"Ask your accountant or support to add this year's financial year.",
			)
		)

	counts = {GREEN: 0, AMBER: 0, RED: 0}
	for item in checks:
		counts[item["status"]] += 1
	total = len(checks)
	return {
		"checks": checks,
		"summary": {"green": counts[GREEN], "amber": counts[AMBER], "red": counts[RED]},
		"ok": counts[RED] == 0,
		# How much is in place: the share of checks that are fine, whole numbers
		"percent": round(100 * counts[GREEN] / total) if total else 0,
		"total": total,
	}
