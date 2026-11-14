"""
Medical insurance at a pharmacy, the rules that need no database.

A patient covered by an insurer (AAR, Jubilee, CIC, Britam, Madison and so on) pays only a co-payment at the counter;
the insurer owes the rest, which the pharmacy claims later. Each insurer has a co-payment rule: a percentage of the bill,
a fixed amount, or both (the larger applies), never more than the bill.

A claim moves through: To claim -> Claimed -> Part paid / Paid, or Rejected.

Kept free of Frappe imports so it can be tested on its own. The calls are in insurance_api.py.
"""

from __future__ import annotations

import json

MAX_INSURERS = 40
MAX_NAME = 80
MAX_TEXT = 60

TO_CLAIM = "To claim"
CLAIMED = "Claimed"
PART_PAID = "Part paid"
PAID = "Paid"
REJECTED = "Rejected"
STATUSES = (TO_CLAIM, CLAIMED, PART_PAID, PAID, REJECTED)


class InsuranceError(ValueError):
	"""Something about the insurance cannot be accepted; the message says why in plain words."""


def _text(value, limit=MAX_TEXT) -> str:
	return " ".join(str(value or "").split())[:limit]


def _number(value, what, ceiling) -> float:
	try:
		number = round(float(value or 0), 2)
	except (TypeError, ValueError):
		raise InsuranceError(f"{what} must be a number.") from None
	if number < 0 or number > ceiling:
		raise InsuranceError(f"{what} must be between 0 and {ceiling:g}.")
	return number


def clean_insurers(raw) -> list[dict]:
	"""The pharmacy's insurers: [{name, copay_percent, copay_amount, customer?}], tidied; JSON text is accepted too."""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw or "[]")
		except ValueError:
			raise InsuranceError("The insurers could not be read.") from None
	if not isinstance(raw, list):
		raise InsuranceError("The insurers could not be read.")
	out, seen = [], set()
	for row in raw:
		if not isinstance(row, dict):
			raise InsuranceError("An insurer could not be read.")
		name = _text(row.get("name"), MAX_NAME)
		if not name or name.lower() in seen:
			continue
		seen.add(name.lower())
		out.append(
			{
				"name": name,
				"copay_percent": _number(row.get("copay_percent"), f"The co-payment of {name}", 100),
				"copay_amount": _number(row.get("copay_amount"), f"The co-payment of {name}", 1_000_000),
				"customer": _text(row.get("customer"), 140) or None,
			}
		)
	if len(out) > MAX_INSURERS:
		raise InsuranceError(f"Keep insurers to {MAX_INSURERS} or fewer.")
	return out


def copay_for(insurer: dict, total) -> float:
	"""What the patient pays: the larger of the insurer's percentage and fixed amount, never more than the bill."""
	total = max(float(total or 0), 0.0)
	percent = total * float(insurer.get("copay_percent") or 0) / 100
	return round(min(total, max(percent, float(insurer.get("copay_amount") or 0))), 2)


def clean_insurance(raw) -> dict | None:
	"""The insurance details sent with a sale, tidied, or None when nothing usable was sent. JSON text is accepted."""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			return None
	if not isinstance(raw, dict):
		return None
	insurer = _text(raw.get("insurer"), MAX_NAME)
	if not insurer:
		return None
	try:
		copay = round(max(float(raw.get("copay") or 0), 0.0), 2)
	except (TypeError, ValueError):
		copay = 0.0
	return {
		"insurer": insurer,
		"patient": _text(raw.get("patient"), 140),
		"member_no": _text(raw.get("member_no")),
		"scheme": _text(raw.get("scheme"), 140),
		"preauth": _text(raw.get("preauth")),
		"copay": copay,
	}


def check_insurance(record: dict, insurers: list[dict], customer: str) -> dict:
	"""
	The insurer the sale is billed to, refused in plain words when it is not one of the pharmacy's insurers, when the
	sale's customer is not that insurer, or when the member number is missing (insurers reject claims without it).
	"""
	match = next((i for i in insurers if i["name"].lower() == record["insurer"].lower()), None)
	if not match:
		raise InsuranceError(f"{record['insurer']} is not one of this pharmacy's insurers.")
	if match.get("customer") and customer != match["customer"]:
		raise InsuranceError(f"The sale must be billed to {match['name']}, not to another customer.")
	if not record["member_no"]:
		raise InsuranceError("Enter the patient's member number, as on their insurance card.")
	if not record["patient"]:
		raise InsuranceError("Enter the patient's name.")
	return match


def claim_fields(record: dict) -> dict:
	"""The invoice fields that keep a sale's insurance claim."""
	return {
		"custom_insurer": record["insurer"],
		"custom_insured_patient": record["patient"],
		"custom_member_no": record["member_no"],
		"custom_scheme": record["scheme"],
		"custom_preauth_no": record["preauth"],
		"custom_copay": record["copay"],
		"custom_claim_status": TO_CLAIM,
	}


def allocate(amount, claims: list[dict]) -> list[dict]:
	"""
	An insurer's payment shared over the claims it covers, in the order given: each claim takes what is still owed on
	it until the money runs out. Refused when the payment is more than all of them owe together.
	`claims` are [{invoice, owed}]; returns [{invoice, amount, status}] for the claims that take something.
	"""
	left = round(float(amount or 0), 2)
	if left <= 0:
		raise InsuranceError("Enter the amount the insurer paid.")
	owed_total = round(sum(max(float(c.get("owed") or 0), 0) for c in claims), 2)
	if left > owed_total + 0.005:
		raise InsuranceError(
			f"The payment is more than these claims are owed ({owed_total:,.2f}). Choose more claims or check the amount."
		)
	out = []
	for claim in claims:
		owed = round(max(float(claim.get("owed") or 0), 0), 2)
		if left <= 0 or owed <= 0:
			continue
		take = round(min(owed, left), 2)
		left = round(left - take, 2)
		out.append(
			{
				"invoice": claim["invoice"],
				"amount": take,
				"status": PAID if take >= owed - 0.005 else PART_PAID,
			}
		)
	return out
