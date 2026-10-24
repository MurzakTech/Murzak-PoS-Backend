"""
A cashier's limits on a sale, checked every time a Sales Invoice or POS Invoice is saved (hooks.py), whichever way it
was made: the till, an edited draft, an offline upload or the desk. The rules are in cashier_rules.py.

- A sale may not be dated in the future, nor more than a week back.
- A cashier may take at most the business's limit (10% unless the owner changed it) off the price-list price, per line
  and on the whole sale; the shop's own pricing rules and batch discounts do not count.

Owners and managers (role_tiers) may go beyond both, so a sale over the limit is rung up by a manager. Returns and the
invoices made when a shift closes are not checked here.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from murzak_pos.api import cashier_rules, role_tiers

LIMIT_KEY = "murzak_cashier_discount_limit_{company}"


def discount_limit(company: str) -> float:
	"""The most a cashier of `company` may take off, in percent."""
	value = frappe.db.get_default(LIMIT_KEY.format(company=company))
	return flt(value) if value not in (None, "") else cashier_rules.DEFAULT_DISCOUNT_LIMIT


def _checks_apply(doc) -> bool:
	if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate or frappe.flags.in_import:
		return False
	if doc.get("is_return") or doc.get("is_consolidated"):
		return False
	if frappe.session.user == "Administrator":
		return False
	return role_tiers.get_tier() not in (role_tiers.OWNER, role_tiers.MANAGER)


def _pricing_rule_percent(item, list_price: float) -> float:
	"""How much the shop's own ERPNext pricing rules take off this line, in percent of the list price."""
	try:
		names = json.loads(item.get("pricing_rules") or "[]")
	except (TypeError, ValueError):
		names = [n.strip() for n in str(item.get("pricing_rules") or "").split(",") if n.strip()]
	percent = 0.0
	for name in names if isinstance(names, list) else []:
		rule = frappe.get_cached_value(
			"Pricing Rule",
			name,
			["rate_or_discount", "discount_percentage", "discount_amount", "rate"],
			as_dict=True,
		)
		if not rule:
			continue
		if rule.rate_or_discount == "Discount Percentage":
			percent += flt(rule.discount_percentage)
		elif rule.rate_or_discount == "Discount Amount" and list_price:
			percent += flt(rule.discount_amount) / list_price * 100
		elif rule.rate_or_discount == "Rate" and list_price and flt(rule.rate):
			percent += max(list_price - flt(rule.rate), 0) / list_price * 100
	return percent


def _batch_rule_percent(doc, item, list_price: float) -> float:
	"""How much the shop's own batch discount (Inventory Discount Rule) takes off this line, in percent."""
	from murzak_pos.murzak_pos.doctype.inventory_discount_rule.inventory_discount_rule import (
		get_applicable_inventory_discount,
	)

	rule = get_applicable_inventory_discount(
		item_code=item.item_code,
		company=doc.company,
		warehouse=item.get("warehouse"),
		batch_no=item.get("batch_no"),
		item_group=item.get("item_group"),
	)
	if not rule:
		return 0.0
	if rule.discount_type == "Percentage":
		return flt(rule.discount_value)
	return flt(rule.discount_value) / list_price * 100 if list_price else 0.0


def enforce_on_sale(doc, method=None) -> None:
	"""Sales Invoice and POS Invoice validate: refuse a date or a discount beyond a cashier's limits."""
	if not _checks_apply(doc):
		return

	problem = cashier_rules.date_problem(getdate(doc.posting_date), getdate(nowdate()))
	if problem == "future":
		frappe.throw(_("A sale cannot be dated in the future."), frappe.ValidationError)
	if problem == "too_old":
		frappe.throw(
			_(
				"A sale dated more than {0} days ago needs a manager. Ask a manager to sign in and send it."
			).format(cashier_rules.DAYS_BACK),
			frappe.ValidationError,
		)

	limit = discount_limit(doc.company)
	lines = []
	for item in doc.get("items") or []:
		list_price = flt(item.get("price_list_rate"))
		lines.append(
			{
				"item_code": item.item_code,
				"qty": flt(item.get("qty")),
				"list_price": list_price,
				"rate": flt(item.get("rate")),
				"shop_discount_percent": _pricing_rule_percent(item, list_price)
				+ _batch_rule_percent(doc, item, list_price),
			}
		)
	over = cashier_rules.discount_problems(lines, flt(doc.get("discount_amount")), limit)
	if not over:
		return
	names = [frappe.db.get_value("Item", code, "item_name") or code for code in over if code != "sale"] or [
		_("the whole sale")
	]
	frappe.throw(
		_(
			"The discount on {0} is more than the {1}% a cashier may give. A manager must ring up this sale."
		).format(", ".join(names), f"{limit:g}"),
		frappe.ValidationError,
	)


@frappe.whitelist(methods=["POST"])
def set_cashier_discount_limit(percent: float, company: str | None = None) -> dict:
	"""The owner sets how much a cashier of their business may take off (0 to 100 percent)."""
	from murzak_pos.api.payment_gateway_common import resolve_company

	role_tiers.require_access("team")
	company = resolve_company(company)
	percent = flt(percent)
	if not 0 <= percent <= 100:
		frappe.throw(_("The limit must be between 0 and 100 percent."), frappe.ValidationError)
	frappe.db.set_default(LIMIT_KEY.format(company=company), str(percent))
	return {"success": True, "data": {"company": company, "percent": percent}}


@frappe.whitelist()
def get_cashier_discount_limit(company: str | None = None) -> dict:
	from murzak_pos.api.payment_gateway_common import resolve_company

	company = resolve_company(company)
	return {"success": True, "data": {"company": company, "percent": discount_limit(company)}}
