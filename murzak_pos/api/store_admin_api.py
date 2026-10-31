"""
Editing a store and choosing who works at it, for the New look Locations tab.

`edit_store` changes a store's name, town and address (the owner and stock managers). `get_store_people` and
`set_store_people` show and set which people work at a store (the owner only). These sit beside the older warehouse
calls and are for the new screens: one short call each, answers in { success, data }, refusals in plain words.

A person with no store chosen can use every store. Choosing stores for them limits them to those stores. Taking a
person off their last store gives them every store again, so `set_store_people` says who that happened to.
"""

from __future__ import annotations

import frappe
from frappe import _

from murzak_pos.api import store_admin_calc as calc
from murzak_pos.api.access_control import (
	ensure_record_access,
	require_manager,
	require_store_role,
)
from murzak_pos.api.payment_gateway_common import resolve_company
from murzak_pos.api.warehouse_api import get_allowed_warehouses_for_user


def _refuse(code: str):
	"""Turn a calc.StoreAdminError code into a sentence the person can act on."""
	if code == "need_name":
		frappe.throw(_("Please type a name for the store."), frappe.ValidationError)
	if code == "bad_name":
		frappe.throw(
			_("A store name must be 2 to 60 characters, without the characters < > / or a backslash."),
			frappe.ValidationError,
		)
	if code == "bad_text":
		frappe.throw(_("The town and address cannot contain < > / or a backslash."), frappe.ValidationError)
	if code == "bad_people":
		frappe.throw(_("The list of people could not be understood."), frappe.ValidationError)
	if code == "too_many_people":
		frappe.throw(_("Please choose no more than 200 people at a time."), frappe.ValidationError)
	frappe.throw(_("That request could not be understood."), frappe.ValidationError)


def _own_store(company, warehouse):
	"""The store, checked to be one of this business's (a store of another business reads as not found)."""
	if not warehouse or frappe.db.get_value("Warehouse", warehouse, "company") != company:
		frappe.throw(_("That store was not found."), frappe.DoesNotExistError)


def _people_of(company) -> list[dict]:
	return frappe.get_all(
		"User",
		filters={"custom_company": company, "enabled": 1},
		fields=["name", "full_name"],
		limit_page_length=0,
	)


def _permissions(users) -> list[dict]:
	if not users:
		return []
	return frappe.get_all(
		"User Permission",
		filters={"allow": "Warehouse", "user": ["in", list(users)]},
		fields=["name", "user", "for_value"],
		limit_page_length=0,
	)


@frappe.whitelist(methods=["POST"])
def edit_store(warehouse, store_name=None, city=None, address=None, company=None):
	"""
	Change a store's name, town or address. A value left out stays as it is; a blank town or address clears it. A name
	another store of the business already uses is refused.
	"""
	require_store_role()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	_own_store(company, warehouse)
	allowed = get_allowed_warehouses_for_user()
	if allowed and warehouse not in allowed:
		frappe.throw(_("You do not have access to this store."), frappe.PermissionError)
	try:
		new_name = calc.clean_name(store_name) if store_name is not None else None
		new_city = calc.clean_text(city) if city is not None else None
		new_address = calc.clean_text(address) if address is not None else None
	except calc.StoreAdminError as error:
		_refuse(error.code)
	if new_name and frappe.db.exists(
		"Warehouse", {"warehouse_name": new_name, "company": company, "name": ["!=", warehouse]}
	):
		frappe.throw(_("You already have a store called {0}.").format(new_name), frappe.ValidationError)
	doc = frappe.get_doc("Warehouse", warehouse)
	if new_name:
		doc.warehouse_name = new_name
	if new_city is not None:
		doc.city = new_city
	if new_address is not None:
		doc.address_line_1 = new_address
	doc.save(ignore_permissions=True)  # the role check above is the permission for this page
	return {
		"success": True,
		"data": {
			"name": doc.name,
			"warehouse_name": doc.warehouse_name,
			"city": doc.city or "",
			"address": doc.address_line_1 or "",
		},
	}


@frappe.whitelist()
def get_store_people(warehouse, company=None):
	"""The people of the business, each marked with whether they work at this store and how many stores they are limited to."""
	require_manager()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	_own_store(company, warehouse)
	users = _people_of(company)
	rows = _permissions([u["name"] for u in users])
	return {
		"success": True,
		"data": {"warehouse": warehouse, "members": calc.members(users, rows, warehouse)},
	}


@frappe.whitelist(methods=["POST"])
def set_store_people(warehouse, people, company=None):
	"""
	Make exactly `people` the ones who work at the store: those not yet there are added, those no longer chosen are
	taken off. Only people of this business can be chosen. Answers who was added, who was taken off, and who now has
	no store left (and so can use every store).
	"""
	require_manager()
	ensure_record_access("Warehouse", warehouse)
	company = resolve_company(company)
	_own_store(company, warehouse)
	try:
		wanted = calc.clean_people(people)
	except calc.StoreAdminError as error:
		_refuse(error.code)
	users = _people_of(company)
	of_business = {u["name"] for u in users}
	strangers = [p for p in wanted if p not in of_business]
	if strangers:
		frappe.throw(_("You do not have access to this person."), frappe.PermissionError)
	rows = _permissions(of_business)
	here = {r["user"]: r["name"] for r in rows if r["for_value"] == warehouse}
	to_add, to_remove = calc.plan_people(here, wanted)
	for user in to_remove:
		frappe.delete_doc("User Permission", here[user], ignore_permissions=True)
	for user in to_add:
		permission = frappe.new_doc("User Permission")
		permission.user = user
		permission.allow = "Warehouse"
		permission.for_value = warehouse
		permission.apply_to_all_doctypes = 0
		permission.insert(ignore_permissions=True)
	left: dict[str, int] = {}
	for row in rows:
		if row["user"] in to_remove and row["for_value"] != warehouse:
			left[row["user"]] = left.get(row["user"], 0) + 1
	return {
		"success": True,
		"data": {
			"warehouse": warehouse,
			"added": to_add,
			"removed": to_remove,
			"everywhere": calc.everywhere(to_remove, left),
		},
	}
