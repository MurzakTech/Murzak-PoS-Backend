"""
The rules for cash put into or taken out of the till during a shift. Plain functions: no database, no Frappe.

A shift's count at the end only balances if every movement of cash is written down: the float topped up, petty cash
spent, a supplier paid from the drawer, notes moved to the safe. `shift_api.py` keeps these in the Till Cash Movement
table and the shift-end count adds them to the cash it expects.
"""

from __future__ import annotations

import re

PAID_IN = "Paid In"
PAID_OUT = "Paid Out"
SAFE_DROP = "Safe Drop"
KINDS = (PAID_IN, PAID_OUT, SAFE_DROP)

# Money that leaves the drawer, so it must not be more than the drawer holds
LEAVES_THE_TILL = (PAID_OUT, SAFE_DROP)

# What a person may pick at the till for each kind, first is the default shown. Money spent on the business (petty cash,
# supplies, a supplier paid from the drawer) is an expense, not a cash movement: it is recorded in Expenses, which takes it
# out of the drawer by itself.
OFFERED_BY_KIND = {
	PAID_IN: ("Float top-up", "Change", "Other"),
	PAID_OUT: ("Staff advance", "Change", "Other"),
	SAFE_DROP: ("Bank deposit", "Other"),
}
# Reasons the system writes itself when cash leaves the drawer for something recorded elsewhere. They are accepted but not
# offered, and old recordings with the reasons that moved to Expenses stay valid.
EXPENSE_REASON = "Expense"
REFUND_REASON = "Customer refund"
SYSTEM_REASONS = (EXPENSE_REASON, REFUND_REASON)
LEGACY_REASONS = ("Petty cash", "Supplier payment")
REASONS_BY_KIND = {
	PAID_IN: OFFERED_BY_KIND[PAID_IN],
	PAID_OUT: (*OFFERED_BY_KIND[PAID_OUT], *SYSTEM_REASONS, *LEGACY_REASONS),
	SAFE_DROP: OFFERED_BY_KIND[SAFE_DROP],
}
REASONS = tuple(sorted({reason for reasons in REASONS_BY_KIND.values() for reason in reasons}))

MAX_AMOUNT = 10_000_000
MAX_NOTE = 140
MAX_ID = 64
CLIENT_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


class MovementError(Exception):
	"""A movement that cannot be recorded. `code` names the reason."""

	def __init__(self, code: str, detail: str = ""):
		super().__init__(code)
		self.code = code
		self.detail = detail


def _amount(value) -> float:
	try:
		amount = round(float(value), 2)
	except (TypeError, ValueError):
		raise MovementError("bad_amount") from None
	if amount != amount or amount <= 0:  # NaN or not above zero
		raise MovementError("bad_amount")
	if amount > MAX_AMOUNT:
		raise MovementError("too_large")
	return amount


def clean(kind, amount, reason, note=None, client_id=None, allow_system=False) -> dict:
	"""
	A movement ready to save, or a MovementError: bad_kind, bad_amount, too_large, bad_reason, note_required,
	bad_client_id. "Other" needs a few words saying what the money was for. The reasons the system writes itself
	(an expense, a refund) are refused unless `allow_system` is set, which only the calls that record those do.
	"""
	if kind not in KINDS:
		raise MovementError("bad_kind")
	amount = _amount(amount)
	reason = (reason or "").strip() or OFFERED_BY_KIND[kind][0]
	if reason not in REASONS_BY_KIND[kind] or (reason in SYSTEM_REASONS and not allow_system):
		raise MovementError("bad_reason", reason)
	note = " ".join(str(note or "").split())[:MAX_NOTE]
	if reason == "Other" and len(note) < 3:
		raise MovementError("note_required")
	client_id = str(client_id).strip() if client_id else ""
	if client_id and (len(client_id) > MAX_ID or not CLIENT_ID.match(client_id)):
		raise MovementError("bad_client_id")
	return {"kind": kind, "amount": amount, "reason": reason, "note": note, "client_id": client_id or None}


def movement_rows(rows) -> list[dict]:
	"""Rows of {kind, mode_of_payment, amount} as the lines of the shift count read them: money in or money out."""
	found = []
	for row in rows or []:
		kind = row.get("kind")
		if kind not in KINDS:
			continue
		found.append(
			{
				"payment_type": "Receive" if kind == PAID_IN else "Pay",
				"mode_of_payment": row.get("mode_of_payment"),
				"amount": row.get("amount"),
			}
		)
	return found


def exceeds_till(kind, amount, cash_in_till) -> bool:
	"""Money leaving the till cannot be more than the till holds. A till whose cash is unknown is not blocked."""
	if kind not in LEAVES_THE_TILL or cash_in_till is None:
		return False
	return round(float(amount), 2) > round(float(cash_in_till), 2) + 0.005


def wants_till(value) -> bool:
	"""
	Whether an expense paid in cash should come out of the till. Not saying (None) means yes: the cash is taken from the
	drawer of the person's own open shift when there is one. Only a clear no (false, 0, "no") keeps it out of the drawer.
	"""
	if value is None or value == "":
		return True
	return str(value).strip().lower() not in ("0", "false", "no", "off")
