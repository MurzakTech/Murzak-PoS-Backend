"""
Saving a draft purchase order again, cancelling a purchase invoice, and asking whether a business sends
its purchases to KRA.

These are the calls behind the Purchases screens. They are reached as `murzak_pos.api.purchase.<name>`
(purchase.py re-exports them), which is where the screens look for them.

Every call is limited to the businesses the caller belongs to.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

from murzak_pos.api.access_control import ensure_record_access, require_any_role, require_stock_role
from murzak_pos.api.payment_gateway_common import resolve_company

# People who may cancel a supplier invoice, which reverses the stock and the books
CANCEL_PURCHASE_ROLES = frozenset({"System Manager", "Accounts Manager", "Purchase Manager"})
SETTINGS_DOCTYPE = "Navari KRA eTims Settings"


def _order_lines(items, company: str, default_warehouse: str | None, order_date) -> list[dict]:
	"""
	The lines of a purchase order, checked: a known item, a quantity above zero and a warehouse of this business.

	A line with no date it is needed by is needed on the day of the order, since ERPNext refuses an earlier date.
	"""
	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not items:
		frappe.throw(_("Add at least one item."), frappe.ValidationError)

	rows = []
	for number, line in enumerate(items, start=1):
		code = (line.get("item_code") or "").strip()
		if not code or not frappe.db.exists("Item", code):
			frappe.throw(_("Row {0}: item {1} was not found.").format(number, code), frappe.ValidationError)
		ensure_record_access("Item", code)
		if flt(line.get("qty")) <= 0:
			frappe.throw(
				_("Row {0}: the quantity of {1} must be more than zero.").format(number, code),
				frappe.ValidationError,
			)

		warehouse = None
		if frappe.db.get_value("Item", code, "is_stock_item"):
			warehouse = (
				line.get("warehouse")
				or default_warehouse
				or frappe.db.get_value(
					"Item Default", {"parent": code, "company": company}, "default_warehouse"
				)
				or frappe.db.get_value("Company", company, "custom_default_warehouse")
			)
			if not warehouse:
				frappe.throw(
					_("Row {0}: choose a warehouse for {1}.").format(number, code), frappe.ValidationError
				)
		if warehouse:
			owner = frappe.db.get_value("Warehouse", warehouse, "company")
			if owner and owner != company:
				frappe.throw(
					_("Row {0}: warehouse {1} belongs to another business.").format(number, warehouse),
					frappe.ValidationError,
				)

		row = {
			"item_code": code,
			"qty": flt(line.get("qty")),
			"schedule_date": line.get("schedule_date") or order_date,
			"warehouse": warehouse,
		}
		if line.get("rate") not in (None, ""):
			row["rate"] = flt(line.get("rate"))
		rows.append(row)
	return rows


def _saved_order(doc, message: str) -> dict:
	"""The reply the Purchases screen reads after a purchase order is saved."""
	return {
		"status": "success",
		"code": "PO_SAVED",
		"message": message,
		"purchase_order": doc.name,
		"lpo_no": doc.name,
		"docstatus": doc.docstatus,
		"grand_total": doc.grand_total,
		"data": {
			"name": doc.name,
			"docstatus": doc.docstatus,
			"grand_total": doc.grand_total,
			"company": doc.company,
			"supplier": doc.supplier,
			"items_count": len(doc.items),
		},
	}


@frappe.whitelist(methods=["POST"])
def upsert_purchase_order(
	supplier: str,
	items,
	company: str | None = None,
	name: str | None = None,
	warehouse: str | None = None,
	transaction_date: str | None = None,
	idempotency_key: str | None = None,
	submit=0,
) -> dict:
	"""
	Save a purchase order: change the draft called `name`, or start a new one when there is no name.

	Only a draft can be changed; an order that is already submitted is left alone. When `submit` is set the
	order is submitted after it is saved. A new order sent twice with the same `idempotency_key` is saved once.
	"""
	require_stock_role()
	company = resolve_company(company)
	supplier = (supplier or "").strip()
	if not supplier or not frappe.db.exists("Supplier", supplier):
		frappe.throw(_("Choose a supplier."), frappe.ValidationError)
	ensure_record_access("Supplier", supplier)

	if name:
		if not frappe.db.exists("Purchase Order", name):
			frappe.throw(_("Purchase Order {0} was not found.").format(name), frappe.DoesNotExistError)
		ensure_record_access("Purchase Order", name)
		doc = frappe.get_doc("Purchase Order", name)
		if doc.company != company:
			frappe.throw(_("Purchase Order {0} was not found.").format(name), frappe.DoesNotExistError)
		if doc.docstatus != 0:
			frappe.throw(
				_("Purchase Order {0} is no longer a draft, so it cannot be changed.").format(name),
				frappe.ValidationError,
			)
		doc.supplier = supplier
		if transaction_date:
			doc.transaction_date = getdate(transaction_date)
		doc.set("items", _order_lines(items, company, warehouse, doc.transaction_date))
		doc.save()
		message = _("Purchase Order saved successfully")
	else:
		has_key = frappe.get_meta("Purchase Order").has_field("idempotency_key")
		if idempotency_key and has_key:
			earlier = frappe.db.get_value(
				"Purchase Order", {"idempotency_key": idempotency_key, "company": company}, "name"
			)
			if earlier:
				return _saved_order(
					frappe.get_doc("Purchase Order", earlier), _("Purchase Order saved successfully")
				)
		order_date = getdate(transaction_date) if transaction_date else today()
		values = {
			"doctype": "Purchase Order",
			"company": company,
			"supplier": supplier,
			"transaction_date": order_date,
			"items": _order_lines(items, company, warehouse, order_date),
		}
		if idempotency_key and has_key:
			values["idempotency_key"] = idempotency_key
		doc = frappe.get_doc(values)
		doc.insert()
		message = _("Purchase Order created successfully")

	if cint(submit):
		doc.submit()
		message = _("Purchase Order submitted successfully")
	return _saved_order(doc, message)


@frappe.whitelist(methods=["POST"])
def cancel_purchase_invoice(name: str) -> dict:
	"""Cancel a submitted purchase invoice. Stock and ledger entries are reversed by ERPNext."""
	require_any_role(CANCEL_PURCHASE_ROLES, _("cancel a purchase invoice"))
	if not name or not frappe.db.exists("Purchase Invoice", name):
		frappe.throw(_("Purchase Invoice {0} was not found.").format(name), frappe.DoesNotExistError)
	ensure_record_access("Purchase Invoice", name)
	doc = frappe.get_doc("Purchase Invoice", name)
	if doc.docstatus != 1:
		frappe.throw(
			_("Only a submitted purchase invoice can be cancelled."),
			frappe.ValidationError,
		)
	doc.cancel()
	return {
		"success": True,
		"message": _("Purchase Invoice cancelled successfully"),
		"data": {"name": doc.name, "docstatus": doc.docstatus},
	}


@frappe.whitelist()
def check_etims_registration_status(company: str | None = None) -> dict:
	"""Whether this business sends its sales and purchases to KRA, and the eTIMS settings record that says so."""
	company = resolve_company(company)
	settings = None
	if frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		settings = frappe.db.get_value(
			SETTINGS_DOCTYPE,
			{"company": company},
			["name", "is_active"],
			as_dict=True,
			order_by="is_active desc, modified desc",
		)
	return {
		"success": True,
		"data": {
			"company": company,
			"has_etims": bool(settings),
			"settings_name": settings.name if settings else None,
			"is_active": bool(settings and cint(settings.is_active)),
		},
	}
