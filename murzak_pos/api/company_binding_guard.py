"""
Who belongs to which business may only be changed by this app's own code, never through Frappe's generic API.

A person belongs to a business by their profile company (User.custom_company), a Company user permission, or by
having created the company (see payment_gateway_common.get_user_companies). Business owners hold System Manager,
which lets Frappe's generic record API (/api/resource/...) change any of these, so an owner could add themselves
to another business. These hooks refuse that.

Allowed: the Administrator, migrations and installs, and the app's own calls, which save with
ignore_permissions=True after checking who is asking (staff_api, onboarding_api, warehouse_api).
"""

from __future__ import annotations

import frappe
from frappe import _


def is_trusted_change(doc) -> bool:
	"""The change comes from the Administrator, a migration or install, or this app's own checked code."""
	if frappe.session.user == "Administrator":
		return True
	if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate:
		return True
	return bool(doc.flags.ignore_permissions)


def guard_user_company(doc, method=None) -> None:
	"""User validate: the profile company cannot be changed through the generic API."""
	if is_trusted_change(doc) or not doc.meta.has_field("custom_company"):
		return
	if doc.is_new():
		changed = bool(doc.get("custom_company"))
	else:
		changed = doc.has_value_changed("custom_company")
	if changed:
		frappe.throw(_("A person's business can only be changed from the Team page."), frappe.PermissionError)


def guard_user_permission(doc, method=None) -> None:
	"""User Permission validate and on_trash: company permissions are managed by the app only."""
	if is_trusted_change(doc):
		return
	before = doc.get_doc_before_save() if not doc.is_new() else None
	if doc.allow != "Company" and not (before and before.allow == "Company"):
		return
	frappe.throw(_("Access to a business can only be changed from the Team page."), frappe.PermissionError)


def guard_company_owner(doc, method=None) -> None:
	"""Company validate: who created a business decides who owns it, and that cannot be rewritten."""
	if doc.is_new() or is_trusted_change(doc):
		return
	if doc.has_value_changed("owner"):
		frappe.throw(_("The owner of a business cannot be changed."), frappe.PermissionError)
