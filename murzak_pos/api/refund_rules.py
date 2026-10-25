"""
How much of a credit note can be paid back, and the mark that ties a refund's payment to the shift's cash count.
Plain functions: no database, no Frappe.

A sales return makes a credit note: the shop owes the customer that money. Paying it back (in cash, M-Pesa and so on)
is a separate act, and for cash it must come out of a till's drawer in an open shift so the shift's count agrees.
"""

from __future__ import annotations

# Payment Entries made to pay a refund start their remarks with this. The shift count reads the cash movement made
# for the refund instead, so it skips these (or the refund would come off the cash twice).
REMARK = "Till refund"
# The reason on the cash movement made for a refund paid in cash
REASON = "Customer refund"


class RefundError(Exception):
	"""A refund that cannot be paid. `code` names the reason."""

	def __init__(self, code: str, detail: str = ""):
		super().__init__(code)
		self.code = code
		self.detail = detail


def owed(outstanding) -> float:
	"""What the shop still owes on a credit note: its outstanding amount is negative, so this is its size, or 0."""
	try:
		value = float(outstanding or 0)
	except (TypeError, ValueError):
		return 0.0
	return round(-value, 2) if value < -0.005 else 0.0


def amount_to_pay(outstanding, requested=None) -> float:
	"""The amount to pay back: all that is owed, or the part asked for. RefundError: nothing_to_refund,
	bad_amount, more_than_owed."""
	still_owed = owed(outstanding)
	if still_owed <= 0:
		raise RefundError("nothing_to_refund")
	if requested in (None, ""):
		return still_owed
	try:
		amount = round(float(requested), 2)
	except (TypeError, ValueError):
		raise RefundError("bad_amount") from None
	if amount != amount or amount <= 0:
		raise RefundError("bad_amount")
	if amount > still_owed + 0.005:
		raise RefundError("more_than_owed", f"{still_owed:,.2f}")
	return amount


def remark_for(credit_note: str) -> str:
	return f"{REMARK}: {credit_note}"


def is_till_refund_remark(remarks) -> bool:
	return str(remarks or "").startswith(REMARK)
