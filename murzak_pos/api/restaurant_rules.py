"""
Rules for restaurants and bars that need no database: who must approve taking an item off a bill after the kitchen
has it (a void), and how the end-of-shift cash-up by waiter adds up.

Why voids matter: the commonest way staff take money in a restaurant or bar is to collect payment for an item and then
remove it from the bill. Once the kitchen or bar has an order, removing it needs a manager and a reason, and each void
is kept, so the owner can see who removed what.

Kept free of Frappe imports so it can be tested on its own (see test_restaurant_rules.py). The calls are in
restaurant_api.py.
"""

from __future__ import annotations

MIN_REASON = 3
MAX_REASON = 140
MAX_LABEL = 140
MAX_QTY = 100_000

# Reasons the till offers as one tap. Any other reason may be typed.
QUICK_REASONS = (
	"Guest changed their mind",
	"Wrong item entered",
	"Item not available",
	"Guest complaint",
	"Spilled or broken",
)

NO_WAITER = "No waiter named"


def _clean(value, limit) -> str:
	return " ".join(str(value or "").split())[:limit]


def clean_reason(value) -> str | None:
	"""The reason, tidied, or None when it is too short to mean anything."""
	text = _clean(value, MAX_REASON)
	return text if len(text) >= MIN_REASON else None


def clean_label(value) -> str:
	"""The bill's table or tab name, tidied (may be empty for a counter sale)."""
	return _clean(value, MAX_LABEL)


def clean_qty(value) -> float | None:
	"""A quantity above nothing and below a sane ceiling, or None."""
	try:
		qty = float(value)
	except (TypeError, ValueError):
		return None
	return qty if 0 < qty <= MAX_QTY else None


def approver_needed(requester_tier: str | None) -> bool:
	"""A cashier or waiter needs a manager or the owner to approve; a manager or the owner approves their own void."""
	return requester_tier not in ("owner", "manager")


def can_approve(approver_tier: str | None) -> bool:
	"""Only a manager or the owner may approve a void."""
	return approver_tier in ("owner", "manager")


# --- the waiter cash-up -------------------------------------------------------------------------------------


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def cashup(
	invoices: list[dict],
	waiter_of: dict,
	payments: list[dict],
	cash_modes: set,
	voids: list[dict],
	waiter_of_user: dict,
	tips: list[dict] | None = None,
) -> dict:
	"""
	What each waiter took during the day or shift, and what they must hand over.

	invoices      [{name, grand_total, change_amount, owner}] the submitted sales (returns are negative)
	waiter_of     {invoice name: waiter name} from each sale's sales person; a sale with none goes to the person who rang
	              it up (via waiter_of_user), else to NO_WAITER
	payments      [{parent, mode_of_payment, amount}]
	cash_modes    the modes of payment that are cash (change given comes off these)
	voids         [{requested_by, amount}] approved voids
	waiter_of_user {user: waiter name} a till login's sales person, for voids and unnamed sales
	tips          [{waiter, recorded_by, amount, mode}] tips left for staff, kept apart from the sales; a tip with no waiter
	              named goes to the person who recorded it

	Each waiter: bills, sales total, the amount by payment mode, cash to hand over (cash taken minus change given),
	number and value of voids. Rounded to 2 places; waiters sorted by sales, largest first.
	"""
	rows: dict[str, dict] = {}

	def row(name):
		if name not in rows:
			rows[name] = {
				"waiter": name,
				"bills": 0,
				"sales": 0.0,
				"by_mode": {},
				"cash_to_hand_over": 0.0,
				"voids": 0,
				"voids_amount": 0.0,
				"tips": 0.0,
				"tips_by_mode": {},
			}
		return rows[name]

	owner_of = {}
	for inv in invoices:
		name = waiter_of.get(inv["name"]) or waiter_of_user.get(inv.get("owner")) or NO_WAITER
		owner_of[inv["name"]] = name
		r = row(name)
		r["bills"] += 1
		r["sales"] += _num(inv.get("grand_total"))
		# Change given back comes out of the cash drawer
		r["cash_to_hand_over"] -= _num(inv.get("change_amount"))

	for pay in payments:
		name = owner_of.get(pay.get("parent"))
		if name is None:
			continue
		mode = pay.get("mode_of_payment") or "Other"
		amount = _num(pay.get("amount"))
		r = row(name)
		r["by_mode"][mode] = r["by_mode"].get(mode, 0.0) + amount
		if mode in cash_modes:
			r["cash_to_hand_over"] += amount

	for void in voids:
		name = waiter_of_user.get(void.get("requested_by")) or void.get("requested_by") or NO_WAITER
		r = row(name)
		r["voids"] += 1
		r["voids_amount"] += _num(void.get("amount"))

	for tip in tips or []:
		name = (
			tip.get("waiter")
			or waiter_of_user.get(tip.get("recorded_by"))
			or tip.get("recorded_by")
			or NO_WAITER
		)
		r = row(name)
		amount = _num(tip.get("amount"))
		mode = tip.get("mode") or "Other"
		r["tips"] += amount
		r["tips_by_mode"][mode] = r["tips_by_mode"].get(mode, 0.0) + amount

	waiters = []
	for r in rows.values():
		r["sales"] = round(r["sales"], 2)
		r["cash_to_hand_over"] = round(r["cash_to_hand_over"], 2)
		r["voids_amount"] = round(r["voids_amount"], 2)
		r["tips"] = round(r["tips"], 2)
		r["tips_by_mode"] = {mode: round(amount, 2) for mode, amount in sorted(r["tips_by_mode"].items())}
		r["by_mode"] = {mode: round(amount, 2) for mode, amount in sorted(r["by_mode"].items())}
		waiters.append(r)
	waiters.sort(key=lambda r: (-r["sales"], r["waiter"]))

	totals = {
		"bills": sum(r["bills"] for r in waiters),
		"sales": round(sum(r["sales"] for r in waiters), 2),
		"cash_to_hand_over": round(sum(r["cash_to_hand_over"] for r in waiters), 2),
		"voids": sum(r["voids"] for r in waiters),
		"voids_amount": round(sum(r["voids_amount"] for r in waiters), 2),
		"tips": round(sum(r["tips"] for r in waiters), 2),
	}
	return {"waiters": waiters, "totals": totals}


# --- delivery platforms ------------------------------------------------------------------------------------


def platform_report(rows: list[dict], platforms: list[dict]) -> dict:
	"""
	Sales taken through each delivery platform and what the restaurant should expect to be paid: [{channel, orders,
	sales}] from the sales, with each platform's commission percent from the settings. A channel with no commission set
	shows the sales and no estimate.
	"""
	rates = {p["name"].lower(): p.get("commission_percent", 0) for p in platforms or []}
	out = []
	for row in rows or []:
		channel = row.get("channel") or ""
		sales = round(_num(row.get("sales")), 2)
		percent = rates.get(channel.lower())
		commission = round(sales * percent / 100, 2) if percent else 0.0
		out.append(
			{
				"channel": channel,
				"orders": int(row.get("orders") or 0),
				"sales": sales,
				"commission_percent": percent or 0.0,
				"commission": commission,
				"expected_payout": round(sales - commission, 2),
			}
		)
	out.sort(key=lambda r: -r["sales"])
	totals = {key: round(sum(r[key] for r in out), 2) for key in ("sales", "commission", "expected_payout")}
	totals["orders"] = sum(r["orders"] for r in out)
	return {"platforms": out, "totals": totals}
