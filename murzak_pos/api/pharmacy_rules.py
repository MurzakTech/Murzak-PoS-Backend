"""
Rules for selling prescription-only medicines that need no database: what a dispensing record holds, when a
sale must carry one, and the note written on the invoice.

Kept free of Frappe imports so it can be tested on its own (see test_pharmacy_rules.py).
"""

from __future__ import annotations

import json

MAX_NAME = 140
MAX_REFERENCE = 60

# What the till sends with a sale that includes prescription-only medicines
FIELDS = ("patient", "prescriber", "reference")


def _clean(value, limit) -> str:
	return " ".join(str(value or "").split())[:limit]


def clean_dispensing(raw) -> dict | None:
	"""
	The dispensing record sent with a sale, tidied: the patient, the prescriber and a prescription reference.
	None when nothing usable was sent. May arrive as an object or as JSON text.
	"""
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			return None
	if not isinstance(raw, dict):
		return None
	record = {
		"patient": _clean(raw.get("patient"), MAX_NAME),
		"prescriber": _clean(raw.get("prescriber"), MAX_NAME),
		"reference": _clean(raw.get("reference"), MAX_REFERENCE),
	}
	return record if any(record.values()) else None


def is_complete(record) -> bool:
	"""A prescription sale needs at least who it is for and who prescribed it."""
	return bool(record and record.get("patient") and record.get("prescriber"))


def check(prescription_items: list[str], record, sold_offline: bool) -> str:
	"""
	What to do with a sale: "ok", "refuse" or "flag".

	A sale with prescription-only medicines and no complete record is refused, so it cannot be
	made by skipping the screen. A sale already made offline is never refused, because the medicine
	has left the shop and the sale has to be recorded; it is flagged instead, so it can be followed up.
	"""
	if not prescription_items or is_complete(record):
		return "ok"
	return "flag" if sold_offline else "refuse"


def remark(prescription_items: list[str], record, dispensed_by: str, status: str) -> str | None:
	"""The line written on the invoice, so the dispensing record can be read back from the sale."""
	if not prescription_items:
		return None
	items = ", ".join(prescription_items)
	if status == "flag":
		return f"PRESCRIPTION DETAILS MISSING for: {items}. Sold offline; the dispensing record must be completed."
	record = record or {}
	parts = [f"patient {record.get('patient')}", f"prescriber {record.get('prescriber')}"]
	if record.get("reference"):
		parts.append(f"prescription ref {record['reference']}")
	if dispensed_by:
		parts.append(f"dispensed by {dispensed_by}")
	return f"Prescription-only medicines ({items}): " + "; ".join(parts) + "."


def dispensing_fields(prescription_items: list[str], record, dispensed_by: str, status: str) -> dict:
	"""
	The dispensing record as separate fields on the invoice, so the dispensing register can list it without reading
	the remarks. Empty when the sale has no prescription-only medicines.
	"""
	if not prescription_items:
		return {}
	record = record or {}
	complete = status != "flag"
	return {
		"custom_rx_status": "Complete" if complete else "Missing",
		"custom_rx_items": ", ".join(prescription_items)[:MAX_ITEMS_TEXT],
		"custom_rx_patient": record.get("patient", "") if complete else "",
		"custom_rx_prescriber": record.get("prescriber", "") if complete else "",
		"custom_rx_reference": record.get("reference", "") if complete else "",
		"custom_rx_dispensed_by": _clean(dispensed_by, MAX_NAME),
	}


MAX_ITEMS_TEXT = 1000


# --- antibiotics --------------------------------------------------------------------------------------------
#
# Kenyan law already sells antibiotics only on prescription, and the Ministry of Health is clamping down on sales
# without one. A pharmacy that adds a product named after a common antibiotic gets it as prescription-only unless
# the pharmacist deliberately says otherwise. Names are compared as letters only, so "Co-Trimoxazole 480mg" and
# "cotrimoxazole" match alike.

ANTIBIOTIC_NAMES = (
	"antibiotic",
	"amoxicillin",
	"amoxycillin",
	"amoxiclav",
	"coamoxiclav",
	"augmentin",
	"ampicillin",
	"ampiclox",
	"cloxacillin",
	"flucloxacillin",
	"penicillin",
	"benzathine",
	"piperacillin",
	"azithromycin",
	"erythromycin",
	"clarithromycin",
	"ciprofloxacin",
	"levofloxacin",
	"norfloxacin",
	"ofloxacin",
	"moxifloxacin",
	"doxycycline",
	"tetracycline",
	"metronidazole",
	"flagyl",
	"tinidazole",
	"secnidazole",
	"cotrimoxazole",
	"septrin",
	"sulfamethoxazole",
	"trimethoprim",
	"nitrofurantoin",
	"cefalexin",
	"cephalexin",
	"cefadroxil",
	"cefuroxime",
	"cefixime",
	"cefpodoxime",
	"ceftriaxone",
	"cefotaxime",
	"ceftazidime",
	"cefazolin",
	"gentamicin",
	"amikacin",
	"clindamycin",
	"linezolid",
	"vancomycin",
	"chloramphenicol",
	"fusidic",
	"mupirocin",
	"neomycin",
	"rifampicin",
	"meropenem",
	"imipenem",
)


def _letters(text) -> str:
	return "".join(ch for ch in str(text or "").lower() if ch.isalpha())


def looks_like_antibiotic(*names) -> bool:
	"""Whether any of these names (a product name, its code, its description) names a common antibiotic."""
	letters = " ".join(_letters(name) for name in names if name)
	return bool(letters) and any(word in letters for word in ANTIBIOTIC_NAMES)


def prescription_default(chosen, is_pharmacy: bool, *names) -> bool:
	"""
	Whether a new product starts as prescription-only. What the person chose always wins (True or False); with no
	choice, a pharmacy's product named after a common antibiotic starts as prescription-only and anything else does not.
	"""
	if chosen is not None:
		return bool(chosen)
	return bool(is_pharmacy) and looks_like_antibiotic(*names)


def add_to_remarks(existing, note) -> str | None:
	"""The note added to what the invoice already says, without writing it twice."""
	if not note:
		return existing
	if existing and note in existing:
		return existing
	return f"{existing}\n{note}" if existing else note


# --- the dispensing register --------------------------------------------------------------------------------

REGISTER_MAX_DAYS = 366


def rx_codes(items_text) -> list[str]:
	"""The prescription-only item codes kept on an invoice (custom_rx_items), in order and once each."""
	codes = [code.strip() for code in str(items_text or "").split(", ")]
	return list(dict.fromkeys(code for code in codes if code))


def register_rows(invoices: list[dict], lines: list[dict], warehouse: str | None = None) -> list[dict]:
	"""
	One row per prescription-only medicine sold: when, what, how many, for whom, prescribed by whom and dispensed by
	whom. `invoices` carry the dispensing fields; `lines` are their item rows (parent, item_code, ...). Only the lines of
	the invoice's own prescription-only items are listed; newest first. With a warehouse, only lines sold from it.
	"""
	by_name = {inv["name"]: inv for inv in invoices}
	rows = []
	for line in lines:
		inv = by_name.get(line.get("parent"))
		if not inv or line.get("item_code") not in rx_codes(inv.get("custom_rx_items")):
			continue
		if warehouse and line.get("warehouse") != warehouse:
			continue
		rows.append(
			{
				"date": str(inv.get("posting_date") or ""),
				"time": str(inv.get("posting_time") or "")[:8],
				"invoice": inv["name"],
				"is_return": bool(inv.get("is_return")),
				"item_code": line.get("item_code"),
				"item_name": line.get("item_name") or line.get("item_code"),
				"qty": line.get("qty") or 0,
				"uom": line.get("uom") or "",
				"batch_no": line.get("batch_no") or "",
				"patient": inv.get("custom_rx_patient") or "",
				"prescriber": inv.get("custom_rx_prescriber") or "",
				"reference": inv.get("custom_rx_reference") or "",
				"dispensed_by": inv.get("custom_rx_dispensed_by") or "",
				"status": inv.get("custom_rx_status") or "",
			}
		)
	rows.sort(key=lambda r: (r["date"], r["time"], r["invoice"]), reverse=True)
	return rows


def register_summary(rows: list[dict]) -> dict:
	"""How many sales and lines the register holds, and how many still miss their prescription details."""
	return {
		"sales": len({r["invoice"] for r in rows}),
		"lines": len(rows),
		"missing": len({r["invoice"] for r in rows if r["status"] == "Missing"}),
	}


# --- the controlled medicines register ------------------------------------------------------------------------
#
# Like the paper register: for each controlled medicine, the balance at the start of the period, then every movement
# in date order (received, dispensed, returned, adjusted, moved) with the balance after it, and the balance at the end.
# A sale shows the patient, the prescriber and who dispensed it.

MOVEMENT_KINDS = {
	"Purchase Receipt": ("Received from supplier", "Returned to supplier"),
	"Purchase Invoice": ("Received from supplier", "Returned to supplier"),
	"POS Invoice": ("Returned by patient", "Dispensed"),
	"Sales Invoice": ("Returned by patient", "Dispensed"),
	"Delivery Note": ("Returned by patient", "Dispensed"),
	"Stock Reconciliation": ("Count adjustment (more)", "Count adjustment (less)"),
	"Stock Entry": ("Stock in", "Stock out"),
}


def movement_kind(voucher_type, qty) -> str:
	"""What a movement was, in the register's words: by the document that made it and whether stock came in or went out."""
	came_in, went_out = MOVEMENT_KINDS.get(voucher_type, ("Stock in", "Stock out"))
	return came_in if (qty or 0) > 0 else went_out


def controlled_register(items: dict, entries: list[dict], start) -> list[dict]:
	"""
	The register, one block per controlled medicine. `items` maps a code to its name; `entries` are every movement of
	those medicines up to the end of the period ({item_code, date, time, voucher_type, reference, qty, warehouse,
	patient, prescriber, dispensed_by}); those before `start` make the opening balance. Medicines are A to Z.
	"""
	start = str(start)
	blocks = {
		code: {
			"item_code": code,
			"item_name": name,
			"opening": 0.0,
			"received": 0.0,
			"issued": 0.0,
			"lines": [],
		}
		for code, name in items.items()
	}
	for e in sorted(
		entries,
		key=lambda e: (str(e.get("date") or ""), str(e.get("time") or ""), str(e.get("reference") or "")),
	):
		block = blocks.get(e.get("item_code"))
		if block is None:
			continue
		qty = float(e.get("qty") or 0)
		if str(e.get("date") or "") < start:
			block["opening"] += qty
			continue
		if qty > 0:
			block["received"] += qty
		else:
			block["issued"] += -qty
		block["lines"].append(
			{
				"date": str(e.get("date") or ""),
				"time": str(e.get("time") or "")[:5],
				"kind": movement_kind(e.get("voucher_type"), qty),
				"reference": e.get("reference") or "",
				"warehouse": e.get("warehouse") or "",
				"qty_in": round(qty, 3) if qty > 0 else 0,
				"qty_out": round(-qty, 3) if qty < 0 else 0,
				"patient": e.get("patient") or "",
				"prescriber": e.get("prescriber") or "",
				"dispensed_by": e.get("dispensed_by") or "",
			}
		)
	result = []
	for block in sorted(blocks.values(), key=lambda b: str(b["item_name"] or b["item_code"]).lower()):
		balance = block["opening"]
		for line in block["lines"]:
			balance += line["qty_in"] - line["qty_out"]
			line["balance"] = round(balance, 3)
		block["opening"] = round(block["opening"], 3)
		block["received"] = round(block["received"], 3)
		block["issued"] = round(block["issued"], 3)
		block["closing"] = round(balance, 3)
		result.append(block)
	return result
