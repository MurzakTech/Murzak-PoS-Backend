"""
Delivery platform payouts: matching what Glovo, Uber Eats or Bolt Food paid against the orders it carried. The rules
are in payout_rules.py.

A payout is kept as a Platform Payout (written once) and each order it settles points to it (custom_channel_payout),
so an order is never settled twice. This is a check, not bookkeeping: no journal entry is made; the money received is
recorded in the books the way the business already records it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from murzak_pos.api import payout_rules as rules
from murzak_pos.api import role_tiers
from murzak_pos.api.payment_gateway_common import resolve_company

PAYOUT = "Platform Payout"
SALE_DOCTYPES = ("POS Invoice", "Sales Invoice")
FIELDS = ("custom_order_channel", "custom_channel_ref", "custom_channel_payout")


def _ready() -> None:
	if not frappe.db.table_exists(PAYOUT):
		frappe.throw(
			_(
				"Platform payouts cannot be recorded on this site yet. Please ask for the server to be updated."
			)
		)


def _doctypes() -> list[str]:
	return [d for d in SALE_DOCTYPES if all(frappe.get_meta(d).has_field(f) for f in FIELDS)]


def _orders(
	company: str, platform: str, unsettled_only: bool = False, from_date=None, to_date=None
) -> list[dict]:
	"""The platform's orders in the POS, newest first (merged shift invoices left out, so none is counted twice)."""
	rows = []
	for doctype in _doctypes():
		where = {"company": company, "docstatus": 1, "custom_order_channel": platform}
		if doctype == "Sales Invoice":
			where["is_consolidated"] = 0
		if unsettled_only:
			where["custom_channel_payout"] = ["is", "not set"]
		if from_date and to_date:
			where["posting_date"] = ["between", [from_date, to_date]]
		for r in frappe.get_all(
			doctype,
			filters=where,
			fields=[
				"name",
				"posting_date",
				"base_grand_total",
				"custom_channel_ref",
				"custom_channel_payout",
			],
			order_by="posting_date desc",
			limit_page_length=5000,
		):
			rows.append(
				{
					"invoice": r.name,
					"doctype": doctype,
					"date": str(r.posting_date),
					"ref": r.custom_channel_ref or "",
					"total": flt(r.base_grand_total),
					"payout": r.custom_channel_payout or None,
				}
			)
	rows.sort(key=lambda r: (r["date"], r["invoice"]), reverse=True)
	return rows


def _commission(company: str, platform: str) -> float:
	from murzak_pos.api.restaurant_settings_api import read_settings

	for p in read_settings(company).get("platforms") or []:
		if p["name"].lower() == platform.lower():
			return flt(p.get("commission_percent"))
	return 0.0


def _platform(platform) -> str:
	name = " ".join(str(platform or "").split())[:60]
	if not name:
		frappe.throw(_("Choose the delivery platform."), frappe.ValidationError)
	return name


@frappe.whitelist()
def list_unsettled_orders(platform: str, company: str | None = None, from_date=None, to_date=None) -> dict:
	"""The platform's orders no payout has settled yet, newest first, and those without an order number. Owner and managers."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	platform = _platform(platform)
	orders = _orders(company, platform, unsettled_only=True, from_date=from_date, to_date=to_date)
	return {
		"success": True,
		"data": {
			"platform": platform,
			"orders": orders,
			"total": round(sum(o["total"] for o in orders), 2),
			"without_number": sum(1 for o in orders if not o["ref"]),
			"commission_percent": _commission(company, platform),
		},
	}


def _plan(company: str, platform: str, order_refs, amount) -> dict:
	try:
		if isinstance(order_refs, str) and order_refs.strip().startswith("["):
			order_refs = frappe.parse_json(order_refs)
		refs = rules.parse_refs(order_refs)
	except rules.PayoutError as error:
		frappe.throw(str(error), frappe.ValidationError)
	if not refs:
		frappe.throw(_("Enter the order numbers on the platform's statement."), frappe.ValidationError)
	found = rules.match(refs, _orders(company, platform))
	if not found["matched"]:
		frappe.throw(
			_("None of these order numbers is an unsettled order of {0}.").format(platform),
			frappe.ValidationError,
		)
	try:
		figures = rules.summary(found["matched"], amount, _commission(company, platform))
	except rules.PayoutError as error:
		frappe.throw(str(error), frappe.ValidationError)
	return {**found, "summary": figures}


@frappe.whitelist(methods=["POST"])
def check_payout(platform: str, order_refs, amount, company: str | None = None) -> dict:
	"""What recording the payout would do, without saving: the orders matched, those already settled, the numbers
	found nowhere, and the commission taken against the commission agreed. Owner and managers."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	platform = _platform(platform)
	return {"success": True, "data": _plan(company, platform, order_refs, amount)}


@frappe.whitelist(methods=["POST"])
def record_payout(
	platform: str,
	order_refs,
	amount,
	payout_date=None,
	statement_ref: str | None = None,
	mode_of_payment: str | None = None,
	company: str | None = None,
) -> dict:
	"""Keep the payout and mark the orders it settles, so none is settled twice. Owner and managers."""
	from murzak_pos.api.overview_calc import nairobi_today

	role_tiers.require_access("profit")
	_ready()
	company = resolve_company(company)
	platform = _platform(platform)
	if mode_of_payment and not frappe.db.exists("Mode of Payment", mode_of_payment):
		frappe.throw(_("Choose how the payout was received."), frappe.ValidationError)
	today = nairobi_today(datetime.now(timezone.utc))
	try:
		day = getdate(payout_date) if payout_date else today
	except Exception:
		day = None
	if not day or day > today:
		frappe.throw(_("Choose the day the payout arrived (today or earlier)."), frappe.ValidationError)
	plan = _plan(company, platform, order_refs, amount)
	figures = plan["summary"]
	doc = frappe.get_doc(
		{
			"doctype": PAYOUT,
			"company": company,
			"platform": platform,
			"payout_date": day,
			"statement_ref": " ".join(str(statement_ref or "").split())[:140],
			"amount_received": figures["received"],
			"mode_of_payment": mode_of_payment or None,
			"orders_matched": figures["orders"],
			"sales_matched": figures["sales"],
			"expected_commission": figures["expected_commission"],
			"actual_commission": figures["actual_commission"],
			"difference": figures["difference"],
			"unmatched_refs": "\n".join(plan["not_found"])[:5000],
			"recorded_by": frappe.session.user,
			"recorded_at": now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)
	for order in plan["matched"]:
		frappe.db.set_value(
			order["doctype"], order["invoice"], "custom_channel_payout", doc.name, update_modified=False
		)
	return {
		"success": True,
		"message": _("Payout recorded."),
		"data": {"payout": doc.name, **plan},
	}


@frappe.whitelist()
def list_payouts(company: str | None = None, platform: str | None = None) -> dict:
	"""The payouts recorded, newest first (the last 200). Owner and managers."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	if not frappe.db.table_exists(PAYOUT):
		return {"success": True, "data": {"payouts": []}}
	filters = {"company": company}
	if platform:
		filters["platform"] = _platform(platform)
	rows = frappe.get_all(
		PAYOUT,
		filters=filters,
		fields=[
			"name",
			"platform",
			"payout_date",
			"statement_ref",
			"amount_received",
			"orders_matched",
			"sales_matched",
			"expected_commission",
			"actual_commission",
			"difference",
			"unmatched_refs",
		],
		order_by="payout_date desc, recorded_at desc",
		limit_page_length=200,
	)
	payouts = [
		{
			**{k: r[k] for k in ("name", "platform", "statement_ref")},
			"date": str(r.payout_date),
			"received": flt(r.amount_received),
			"orders": int(r.orders_matched or 0),
			"sales": flt(r.sales_matched),
			"expected_commission": flt(r.expected_commission),
			"actual_commission": flt(r.actual_commission),
			"difference": flt(r.difference),
			"not_found": [x for x in (r.unmatched_refs or "").split("\n") if x],
		}
		for r in rows
	]
	return {"success": True, "data": {"payouts": payouts}}
