"""
The tourism levy report (restaurants and bars): the month's sales of each store and 2% of them, before and after VAT.
The rules are in levy_rules.py. For the owner and managers.

Only stores whose kind of shop is a restaurant and bar are levied; in a business whose own kind is restaurant, every
store without a kind of its own counts as one. A shift's POS sales merged into one Sales Invoice are counted once.
"""

from __future__ import annotations

from datetime import datetime, timezone

import frappe
from frappe import _

from murzak_pos.api import industry_profile as profiles
from murzak_pos.api import levy_rules as rules
from murzak_pos.api import role_tiers
from murzak_pos.api.overview_calc import nairobi_today
from murzak_pos.api.payment_gateway_common import resolve_company

_SALES_SQL = """
	select item.warehouse as warehouse,
		sum(item.base_net_amount) as net,
		sum(item.base_net_amount * ifnull(inv.base_grand_total / nullif(inv.base_net_total, 0), 1)) as gross
	from `tab{doctype} Item` item
	join `tab{doctype}` inv on inv.name = item.parent
	where inv.company = %(company)s and inv.docstatus = 1
		and inv.posting_date between %(start)s and %(end)s {extra}
	group by item.warehouse
"""


def _sales(company: str, start, end) -> list[dict]:
	rows = []
	for doctype, extra in (("POS Invoice", ""), ("Sales Invoice", "and inv.is_consolidated = 0")):
		rows.extend(
			frappe.db.sql(
				_SALES_SQL.format(doctype=doctype, extra=extra),
				{"company": company, "start": start, "end": end},
				as_dict=True,
			)
		)
	return rows


def _levied_stores(company: str) -> tuple[dict, set]:
	"""Each store's name, and the stores the levy applies to (restaurants and bars)."""
	from murzak_pos.api.industry_profile_api import (
		FIELD,
		_business_kind,
		_industry_code,
		_outlet_has_field,
		_shop_type,
	)

	has_field = _outlet_has_field()
	stores = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		fields=["name", "warehouse_name"] + ([FIELD] if has_field else []),
	)
	business_key = profiles.resolve(None, _shop_type(), _business_kind(company))[0]
	names, levied = {}, set()
	for store in stores:
		names[store.name] = store.warehouse_name or store.name
		own = store.get(FIELD) if has_field else None
		key = profiles.resolve(_industry_code(own))[0] if own else business_key
		if key == profiles.RESTAURANT:
			levied.add(store.name)
	return names, levied


@frappe.whitelist()
def get_tourism_levy(company: str | None = None, month: str | None = None) -> dict:
	"""The tourism levy for a month ("yyyy-mm", this month by default): sales and 2% of them, by store."""
	role_tiers.require_access("profit")
	company = resolve_company(company)
	try:
		start, end, label = rules.month_range(month, nairobi_today(datetime.now(timezone.utc)))
	except ValueError:
		frappe.throw(_("Choose a month that has started, such as 2026-10."), frappe.ValidationError)
	names, levied = _levied_stores(company)
	data = rules.levy_report(_sales(company, start, end), names, levied)
	data.update({"company": company, "month": label, "from_date": str(start), "to_date": str(end)})
	return {"success": True, "data": data}
