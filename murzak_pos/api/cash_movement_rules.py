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

REASONS = (
	"Float top-up",
	"Change",
	"Petty cash",
	"Supplier payment",
	"Staff advance",
	"Bank deposit",
	"Other",
)
# What a person is likely to need for each kind, first is the default shown
REASONS_BY_KIND = {
	PAID_IN: ("Float top-up", "Change", "Other"),
	PAID_OUT: ("Petty cash", "Supplier payment", "Staff advance", "Change", "Other"),
	SAFE_DROP: ("Bank deposit", "Other"),
}

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


def clean(kind, amount, reason, note=None, client_id=None) -> dict:
	"""
	A movement ready to save, or a MovementError: bad_kind, bad_amount, too_large, bad_reason, note_required,
	bad_client_id. "Other" needs a few words saying what the money was for.
	"""
	if kind not in KINDS:
		raise MovementError("bad_kind")
	amount = _amount(amount)
	reason = (reason or "").strip() or REASONS_BY_KIND[kind][0]
	if reason not in REASONS_BY_KIND[kind]:
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
