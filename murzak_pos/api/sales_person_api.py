"""
Sales people and their commission.

A business keeps its own sales people (ERPNext's Sales Person, tagged with the company it belongs to). A sale can be
credited to one, either named by the till or, when the cashier's login is linked to a sales person, automatically.
The sale then carries ERPNext's "Sales Team" with the person's commission rate, so the commission is part of the
invoice and is reversed by a return.

`get_commission_report` adds up, per person, what they sold, what was returned and the commission earned.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company

PERSON = "Sales Person"
ROOT = "Sales Team"  # ERPNext's top node of the sales person tree
MANAGE_ROLES = frozenset({"System Manager", "Sales Manager"})
REPORT_ROLES = frozenset({"System Manager", "Sales Manager", "Accounts Manager"})
MAX_RATE = 100.0


def _require(roles: frozenset, message: str) -> None:
	if frappe.session.user != "Administrator" and not roles.intersection(frappe.get_roles()):
		frappe.throw(message, frappe.PermissionError)


def commission(amount, rate) -> float:
	"""The commission on an amount at a rate in percent."""
	return flt(amount) * flt(rate) / 100.0


def checked_rate(rate) -> float:
	value = flt(rate)
	if value < 0 or value > MAX_RATE:
		frappe.throw(_("The commission rate must be between 0 and 100 percent."), frappe.ValidationError)
	return value


def _row(person) -> dict:
	return {
		"name": person.name,
		"sales_person_name": person.sales_person_name,
		"company": person.custom_company,
		"user": person.custom_user,
		"commission_rate": flt(person.commission_rate),
		"enabled": cint(person.enabled),
	}


def _person_name(name: str, company: str) -> str:
	"""Sales person names are shared by the whole site, so a name used by another business gets the company's abbreviation."""
	if not frappe.db.exists(PERSON, name):
		return name
	if frappe.db.get_value(PERSON, name, "custom_company") == company:
		frappe.throw(
			_("{0} is already a sales person of this business.").format(name), frappe.ValidationError
		)
	return f"{name} - {frappe.db.get_value('Company', company, 'abbr')}"


def _check_user(user: str | None, company: str, exclude: str | None = None) -> None:
	if not user:
		return
	if not frappe.db.exists("User", user):
		frappe.throw(_("User {0} does not exist.").format(user), frappe.ValidationError)
	taken = frappe.db.get_value(
		PERSON, {"custom_user": user, "custom_company": company, "name": ["!=", exclude or ""]}
	)
	if taken:
		frappe.throw(_("{0} is already linked to {1}.").format(user, taken), frappe.ValidationError)


@frappe.whitelist(methods=["POST"])
def create_sales_person(
	sales_person_name: str, commission_rate: float = 0, user: str | None = None, company: str | None = None
) -> dict:
	_require(MANAGE_ROLES, _("Only a Sales Manager or the owner can add sales people."))
	company = resolve_company(company)
	name = (sales_person_name or "").strip()
	if not name:
		frappe.throw(_("A sales person needs a name."), frappe.ValidationError)
	_check_user(user, company)
	doc = frappe.get_doc(
		{
			"doctype": PERSON,
			"sales_person_name": _person_name(name, company),
			"parent_sales_person": ROOT,
			"is_group": 0,
			"enabled": 1,
			"commission_rate": checked_rate(commission_rate),
			"custom_company": company,
			"custom_user": user or None,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"success": True, "message": _("Sales person added"), "data": _row(doc)}


@frappe.whitelist(methods=["POST"])
def update_sales_person(
	name: str,
	commission_rate: float | None = None,
	user: str | None = None,
	enabled: int | None = None,
	company: str | None = None,
) -> dict:
	"""Change a sales person's commission rate, linked login or whether they are active. Past sales keep their rate."""
	_require(MANAGE_ROLES, _("Only a Sales Manager or the owner can change sales people."))
	ensure_record_access(PERSON, name)
	doc = frappe.get_doc(PERSON, name)
	company = resolve_company(doc.custom_company or company)
	if commission_rate is not None:
		doc.commission_rate = checked_rate(commission_rate)
	if user is not None:
		_check_user(user or None, company, exclude=doc.name)
		doc.custom_user = user or None
	if enabled is not None:
		doc.enabled = cint(enabled)
	doc.save(ignore_permissions=True)
	return {"success": True, "message": _("Sales person updated"), "data": _row(doc)}


@frappe.whitelist()
def list_sales_persons(company: str | None = None, include_disabled: int = 0) -> dict:
	company = resolve_company(company)
	filters = {"custom_company": company, "is_group": 0}
	if not cint(include_disabled):
		filters["enabled"] = 1
	rows = frappe.get_all(
		PERSON,
		filters=filters,
		fields=["name", "sales_person_name", "custom_company", "custom_user", "commission_rate", "enabled"],
		order_by="sales_person_name asc",
		limit_page_length=0,
	)
	data = [_row(row) for row in rows]
	return {"success": True, "data": {"data": data, "count": len(data)}}


# --- crediting a sale ----------------------------------------------------------------------------------


def person_for_sale(company: str, sales_person: str | None = None) -> dict | None:
	"""
	The sales person to credit: the one named, or the one linked to the signed-in cashier. None when there is neither.
	A person of another business, or one switched off, is refused when named; ignored when only linked.
	"""
	if sales_person:
		found = frappe.db.get_value(
			PERSON, sales_person, ["name", "custom_company", "commission_rate", "enabled"], as_dict=True
		)
		if not found or found.custom_company != company:
			frappe.throw(
				_("{0} is not a sales person of this business.").format(sales_person), frappe.ValidationError
			)
		if not cint(found.enabled):
			frappe.throw(_("{0} is switched off.").format(sales_person), frappe.ValidationError)
		return found
	return frappe.db.get_value(
		PERSON,
		{"custom_user": frappe.session.user, "custom_company": company, "enabled": 1},
		["name", "custom_company", "commission_rate", "enabled"],
		as_dict=True,
	)


def attach(doc, sales_person: str | None = None) -> None:
	"""Credit a draft sale to a sales person: the whole sale, at their commission rate."""
	person = person_for_sale(doc.company, sales_person)
	if not person:
		return
	rate = flt(person.commission_rate)
	doc.set("sales_team", [])
	doc.append(
		"sales_team", {"sales_person": person.name, "allocated_percentage": 100, "commission_rate": rate}
	)
	doc.commission_rate = rate


def copy_sales_team(source: str, doc) -> None:
	"""A return is credited to the same people as the sale it returns, so their commission falls with it."""
	rows = frappe.get_all(
		"Sales Team",
		filters={"parent": source, "parenttype": "Sales Invoice"},
		fields=["sales_person", "allocated_percentage", "commission_rate"],
		limit_page_length=0,
	)
	if not rows:
		return
	doc.set("sales_team", [])
	for row in rows:
		doc.append("sales_team", dict(row))
	doc.commission_rate = flt(rows[0].commission_rate)


# --- report --------------------------------------------------------------------------------------------


def report_rows(
	company: str, from_date: str | None, to_date: str | None, sales_person: str | None
) -> list[dict]:
	"""What each sales person sold and earned. Returns count against the sale they return."""
	rows: list[dict] = []
	for parent in ("Sales Invoice", "POS Invoice"):
		table = f"`tab{parent}`"
		conditions = ["t.parenttype = %(parent)s", "i.docstatus = 1", "i.company = %(company)s"]
		values = {"parent": parent, "company": company}
		if from_date:
			conditions.append("i.posting_date >= %(from_date)s")
			values["from_date"] = from_date
		if to_date:
			conditions.append("i.posting_date <= %(to_date)s")
			values["to_date"] = to_date
		if sales_person:
			conditions.append("t.sales_person = %(person)s")
			values["person"] = sales_person
		rows += frappe.db.sql(
			f"""
			select t.sales_person, i.name as invoice, i.is_return, t.allocated_amount, t.commission_rate
			from `tabSales Team` t join {table} i on i.name = t.parent
			where {" and ".join(conditions)}
			""",
			values,
			as_dict=True,
		)
	return rows


def summarise(rows: list[dict]) -> list[dict]:
	"""Per person: sales, returns, net, commission and the number of invoices."""
	people: dict[str, dict] = {}
	for row in rows:
		person = people.setdefault(
			row["sales_person"],
			{
				"sales_person": row["sales_person"],
				"sales": 0.0,
				"returns": 0.0,
				"commission": 0.0,
				"invoices": set(),
			},
		)
		amount = flt(row["allocated_amount"])
		if cint(row["is_return"]) or amount < 0:
			person["returns"] += abs(amount)
			person["commission"] -= commission(abs(amount), row["commission_rate"])
		else:
			person["sales"] += amount
			person["commission"] += commission(amount, row["commission_rate"])
		person["invoices"].add(row["invoice"])
	result = []
	for person in people.values():
		result.append(
			{
				"sales_person": person["sales_person"],
				"sales": round(person["sales"], 2),
				"returns": round(person["returns"], 2),
				"net_sales": round(person["sales"] - person["returns"], 2),
				"commission": round(person["commission"], 2),
				"invoices": len(person["invoices"]),
			}
		)
	return sorted(result, key=lambda r: (-r["net_sales"], r["sales_person"]))


@frappe.whitelist()
def get_commission_report(
	company: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	sales_person: str | None = None,
) -> dict:
	"""Sales, returns and commission per sales person for a period."""
	_require(REPORT_ROLES, _("Only a manager can see the commission report."))
	company = resolve_company(company)
	if sales_person:
		ensure_record_access(PERSON, sales_person)
	data = summarise(report_rows(company, from_date, to_date, sales_person))
	totals = {
		key: round(sum(r[key] for r in data), 2) for key in ("sales", "returns", "net_sales", "commission")
	}
	return {"success": True, "data": {"data": data, "count": len(data), "totals": totals}}
