"""
Medical insurance at a pharmacy: the insurers and their co-payment rules, the claims made from sales, and the
insurer's payments against them. The rules are in insurance_rules.py.

A covered sale is billed to the insurer (a Customer made for each insurer of the business); the patient's co-payment
is paid at the till and the rest stays owed by the insurer, as a credit sale. The claim details sit on the invoice
(custom_insurer, custom_member_no...). An insurer's payment becomes one Payment Entry per claim, on the Sales Invoice
(or, for a POS sale, on the Sales Invoice its shift was merged into), so what is owed goes down in the books too.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import flt, getdate

from murzak_pos.api import insurance_rules as rules
from murzak_pos.api import role_tiers
from murzak_pos.api.payment_gateway_common import resolve_company

PREFIX = "murzak_insurers"
CLAIM_FIELDS = (
	"custom_insurer",
	"custom_insured_patient",
	"custom_member_no",
	"custom_scheme",
	"custom_preauth_no",
	"custom_copay",
	"custom_claim_status",
	"custom_claim_ref",
)
SALE_DOCTYPES = ("POS Invoice", "Sales Invoice")


def _key(company: str) -> str:
	key = f"{PREFIX}:{company}"
	return key if len(key) <= 140 else f"{PREFIX}:{hashlib.sha1(company.encode('utf-8')).hexdigest()}"


def read_insurers(company: str) -> list[dict]:
	try:
		return rules.clean_insurers(frappe.db.get_default(_key(company)) or "[]")
	except rules.InsuranceError:
		return []


def _has_fields(doctype: str) -> bool:
	meta = frappe.get_meta(doctype)
	return all(meta.has_field(f) for f in CLAIM_FIELDS)


def _ensure_customer(company: str, name: str, existing: str | None) -> str:
	"""The insurer's Customer record for this business, made on first use."""
	if existing and frappe.db.get_value("Customer", existing, "custom_company") == company:
		return existing
	found = frappe.db.get_value("Customer", {"customer_name": name, "custom_company": company}, "name")
	if found:
		return found
	from murzak_pos.api.party_names import name_for_new_record
	from murzak_pos.api.sales_api import _leaf_customer_group

	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name_for_new_record("Customer", name, company),
			"customer_type": "Company",
			"customer_group": _leaf_customer_group(),
			"custom_company": company,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


# --- the insurers ------------------------------------------------------------------------------------------


@frappe.whitelist()
def get_insurers(company: str | None = None) -> dict:
	"""The pharmacy's insurers, each with its co-payment rule and its customer record. For anyone in the business."""
	company = resolve_company(company)
	return {
		"success": True,
		"data": {"insurers": read_insurers(company), "can_change": role_tiers.can("profit")},
	}


@frappe.whitelist(methods=["POST"])
def save_insurers(insurers, company: str | None = None) -> dict:
	"""Keep the insurers (owner and managers). Each gets its customer record on first use."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	try:
		clean = rules.clean_insurers(insurers)
	except rules.InsuranceError as error:
		frappe.throw(str(error), frappe.ValidationError)
	for insurer in clean:
		insurer["customer"] = _ensure_customer(company, insurer["name"], insurer.get("customer"))
	frappe.db.set_default(_key(company), json.dumps(clean, separators=(",", ":")))
	return {"success": True, "message": _("Saved."), "data": {"insurers": clean, "can_change": True}}


def claim_for_sale(company: str, customer: str, raw) -> dict:
	"""
	The claim fields for a sale billed to an insurer, or {} for an ordinary sale. Refused in plain words when the
	insurance details cannot be accepted (called by sales_api before anything is saved).
	"""
	record = rules.clean_insurance(raw)
	if not record:
		return {}
	try:
		insurer = rules.check_insurance(record, read_insurers(company), customer)
	except rules.InsuranceError as error:
		frappe.throw(str(error), frappe.ValidationError)
	return rules.claim_fields({**record, "insurer": insurer["name"]})


# --- the claims --------------------------------------------------------------------------------------------


def _claims(company: str, filters: dict) -> list[dict]:
	rows = []
	for doctype in SALE_DOCTYPES:
		if not _has_fields(doctype):
			continue
		where = {"company": company, "docstatus": 1, "custom_insurer": ["is", "set"], **filters}
		if doctype == "Sales Invoice":
			where["is_consolidated"] = 0
		fields = ["name", "posting_date", "grand_total", "outstanding_amount", "customer", *CLAIM_FIELDS]
		if doctype == "POS Invoice":
			fields.append("consolidated_invoice")
		for r in frappe.get_all(
			doctype, filters=where, fields=fields, order_by="posting_date desc", limit_page_length=2000
		):
			insurer_share = round(flt(r.grand_total) - flt(r.custom_copay), 2)
			rows.append(
				{
					"invoice": r.name,
					"doctype": doctype,
					"date": str(r.posting_date),
					"insurer": r.custom_insurer,
					"patient": r.custom_insured_patient,
					"member_no": r.custom_member_no,
					"scheme": r.custom_scheme,
					"preauth": r.custom_preauth_no,
					"total": flt(r.grand_total),
					"copay": flt(r.custom_copay),
					"insurer_share": insurer_share,
					"status": r.custom_claim_status or rules.TO_CLAIM,
					"claim_ref": r.custom_claim_ref or "",
					"merged_into": r.get("consolidated_invoice") or None,
				}
			)
	rows.sort(key=lambda r: (r["date"], r["invoice"]), reverse=True)
	return rows


@frappe.whitelist()
def get_claims(company: str | None = None, insurer: str | None = None, status: str | None = None) -> dict:
	"""Insurance claims, newest first, with totals by status. Owner and managers (they name patients)."""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	filters = {}
	if insurer:
		filters["custom_insurer"] = insurer
	if status in rules.STATUSES:
		filters["custom_claim_status"] = status
	rows = _claims(company, filters)
	by_status = {
		s: round(sum(r["insurer_share"] for r in rows if r["status"] == s), 2) for s in rules.STATUSES
	}
	return {
		"success": True,
		"data": {
			"claims": rows,
			"by_status": by_status,
			"insurers": [i["name"] for i in read_insurers(company)],
		},
	}


def _set_status(doctype: str, name: str, values: dict) -> None:
	for field, value in values.items():
		frappe.db.set_value(doctype, name, field, value, update_modified=False)


def _own_claim(company: str, invoice: str) -> tuple[str, dict]:
	for doctype in SALE_DOCTYPES:
		if not _has_fields(doctype):
			continue
		row = frappe.db.get_value(
			doctype,
			invoice,
			[
				"company",
				"docstatus",
				"custom_insurer",
				"custom_claim_status",
				"grand_total",
				"custom_copay",
				"customer",
			]
			+ (["consolidated_invoice"] if doctype == "POS Invoice" else []),
			as_dict=True,
		)
		if row and row.custom_insurer:
			if row.company != company:
				frappe.throw(_("That claim belongs to another business."), frappe.PermissionError)
			return doctype, row
	frappe.throw(_("Claim {0} was not found.").format(invoice), frappe.DoesNotExistError)


@frappe.whitelist(methods=["POST"])
def mark_claimed(invoices, claim_ref: str | None = None, company: str | None = None) -> dict:
	"""Mark claims as sent to the insurer, with the claim or batch reference. Owner and managers."""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	names = frappe.parse_json(invoices) if isinstance(invoices, str) else invoices
	ref = " ".join(str(claim_ref or "").split())[:140]
	done = []
	for name in names or []:
		doctype, row = _own_claim(company, name)
		if row.custom_claim_status in (rules.TO_CLAIM, rules.REJECTED, None, ""):
			_set_status(doctype, name, {"custom_claim_status": rules.CLAIMED, "custom_claim_ref": ref})
			done.append(name)
	return {"success": True, "message": _("Saved."), "data": {"claimed": done}}


@frappe.whitelist(methods=["POST"])
def set_claim_rejected(invoice: str, reason: str | None = None, company: str | None = None) -> dict:
	"""Mark a claim the insurer turned down. What it owes stays in the books until it is collected or written off."""
	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	doctype, _row = _own_claim(company, invoice)
	note = " ".join(str(reason or "").split())[:140]
	_set_status(doctype, invoice, {"custom_claim_status": rules.REJECTED, "custom_claim_ref": note})
	return {"success": True, "message": _("Saved."), "data": {"invoice": invoice}}


@frappe.whitelist(methods=["POST"])
def record_insurer_payment(
	invoices,
	amount,
	mode_of_payment: str,
	reference: str | None = None,
	company: str | None = None,
) -> dict:
	"""
	An insurer's payment shared over the claims it covers (in the order sent), each claim taking what is still owed on
	it. One Payment Entry per claim, on its Sales Invoice (a POS sale's merged invoice), so the books follow. A POS sale
	whose shift is still open cannot take a payment yet (ERPNext cannot point one at it), and says so.
	"""
	from murzak_pos.api.overview_calc import nairobi_today
	from murzak_pos.api.sales_list_api import _mode_account

	role_tiers.require_access("all_sales")
	company = resolve_company(company)
	account = _mode_account(mode_of_payment, company) if mode_of_payment else None
	if not account:
		frappe.throw(_("Choose the bank account the insurer paid into."), frappe.ValidationError)
	names = frappe.parse_json(invoices) if isinstance(invoices, str) else invoices
	claims, targets = [], {}
	for name in names or []:
		doctype, row = _own_claim(company, name)
		target = (
			("Sales Invoice", name)
			if doctype == "Sales Invoice"
			else ("Sales Invoice", row.get("consolidated_invoice"))
		)
		if not target[1]:
			frappe.throw(
				_(
					"The sale {0} is on a till shift that is still open. Close the shift, then record the payment."
				).format(name),
				frappe.ValidationError,
			)
		owed_target = flt(frappe.db.get_value("Sales Invoice", target[1], "outstanding_amount"))
		share = round(flt(row.grand_total) - flt(row.custom_copay), 2)
		claims.append({"invoice": name, "owed": min(share, owed_target)})
		targets[name] = (doctype, target)
	try:
		plan = rules.allocate(amount, claims)
	except rules.InsuranceError as error:
		frappe.throw(str(error), frappe.ValidationError)

	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	today = nairobi_today(datetime.now(timezone.utc))
	ref = " ".join(str(reference or "").split())[:140]
	entries = []
	for part in plan:
		doctype, target = targets[part["invoice"]]
		entry = get_payment_entry(target[0], target[1], party_amount=part["amount"])
		entry.posting_date = today
		entry.mode_of_payment = mode_of_payment
		entry.paid_to = account["account"]
		if account.get("account_currency"):
			entry.paid_to_account_currency = account["account_currency"]
		entry.paid_amount = part["amount"]
		entry.received_amount = part["amount"]
		for r in entry.references:
			r.allocated_amount = part["amount"]
		entry.reference_no = ref or part["invoice"]
		entry.reference_date = getdate(today)
		entry.remarks = f"Insurance claim {part['invoice']}" + (f", insurer payment {ref}" if ref else "")
		entry.insert(ignore_permissions=True)
		entry.submit()
		_set_status(doctype, part["invoice"], {"custom_claim_status": part["status"]})
		entries.append(
			{
				"invoice": part["invoice"],
				"payment": entry.name,
				"amount": part["amount"],
				"status": part["status"],
			}
		)
	return {"success": True, "message": _("Insurer payment recorded."), "data": {"payments": entries}}
