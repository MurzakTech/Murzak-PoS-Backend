"""
Approvals: a refund or a purchase order above a business's limit waits for a manager's approval.

Each business sets its own limits in "Approval Settings" (0 switches a check off). Above the limit a refund or purchase
order is saved as a draft with an "Approval Request"; an approver (see approval_rules.py) then approves it, which
submits the document, or rejects it. A document above the limit cannot be submitted any other way either: a hook on
submit refuses it unless an approved request covers its amount. Every request and decision is a tracked record, so the
audit trail shows who asked and who decided.

This is deliberately not the Frappe Workflow engine: a workflow applies to a whole document type on the whole site,
while these limits differ by business.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from murzak_pos.api import approval_rules as rules
from murzak_pos.api.payment_gateway_common import resolve_company

SETTINGS = "Approval Settings"
REQUEST = "Approval Request"
# Who may read or change a business's approval limits
SETTINGS_ROLES = frozenset({"Business Owner", "System Manager", "Accounts Manager"})
LIMIT_FIELD = {rules.REFUND: "refund_limit", rules.PURCHASE_ORDER: "purchase_order_limit"}
KIND_OF = {"Sales Invoice": rules.REFUND, "Purchase Order": rules.PURCHASE_ORDER}


def _require_settings_role() -> None:
	if frappe.session.user != "Administrator" and not SETTINGS_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only a manager can change approval limits."), frappe.PermissionError)


def limits(company: str) -> dict[str, float]:
	row = frappe.db.get_value(SETTINGS, company, ["refund_limit", "purchase_order_limit"], as_dict=True)
	return {
		rules.REFUND: flt(row.refund_limit) if row else 0.0,
		rules.PURCHASE_ORDER: flt(row.purchase_order_limit) if row else 0.0,
	}


@frappe.whitelist()
def get_approval_settings(company: str | None = None) -> dict:
	_require_settings_role()
	company = resolve_company(company)
	found = limits(company)
	return {
		"success": True,
		"data": {
			"company": company,
			"refund_limit": found[rules.REFUND],
			"purchase_order_limit": found[rules.PURCHASE_ORDER],
		},
	}


@frappe.whitelist(methods=["POST"])
def save_approval_settings(
	refund_limit: float | None = None, purchase_order_limit: float | None = None, company: str | None = None
) -> dict:
	_require_settings_role()
	company = resolve_company(company)
	if frappe.db.exists(SETTINGS, company):
		doc = frappe.get_doc(SETTINGS, company)
	else:
		doc = frappe.get_doc({"doctype": SETTINGS, "company": company})
	if refund_limit is not None:
		doc.refund_limit = flt(refund_limit)
	if purchase_order_limit is not None:
		doc.purchase_order_limit = flt(purchase_order_limit)
	doc.save(ignore_permissions=True)
	return get_approval_settings(company)


# --- what needs approval -----------------------------------------------------------------------------


def _amount(doc) -> float:
	return abs(flt(doc.get("rounded_total")) or flt(doc.get("grand_total")))


def kind_for(doc) -> str | None:
	"""Which approval a document would need, or None when it is not something that needs one."""
	kind = KIND_OF.get(doc.doctype)
	if kind == rules.REFUND and not doc.get("is_return"):
		return None
	return kind


def requires_approval(doc) -> bool:
	kind = kind_for(doc)
	if not kind or not doc.get("company"):
		return False
	return rules.needs_approval(_amount(doc), limits(doc.company)[kind])


def is_approved(doc) -> bool:
	"""True when an approved request covers this document and its present amount."""
	rows = frappe.get_all(
		REQUEST,
		filters={"reference_doctype": doc.doctype, "reference_name": doc.name, "status": rules.APPROVED},
		fields=["amount"],
		limit_page_length=0,
	)
	return any(_amount(doc) <= flt(row.amount) + 0.005 for row in rows)


def ask_for_approval(doc, reason: str | None = None) -> str:
	"""Record a request for a saved draft. One waiting request per document: asking again returns the same one."""
	existing = frappe.db.get_value(
		REQUEST,
		{"reference_doctype": doc.doctype, "reference_name": doc.name, "status": rules.PENDING},
		"name",
	)
	if existing:
		frappe.db.set_value(REQUEST, existing, "amount", _amount(doc))
		return existing
	request = frappe.get_doc(
		{
			"doctype": REQUEST,
			"company": doc.company,
			"kind": kind_for(doc),
			"status": rules.PENDING,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"amount": _amount(doc),
			"requested_by": frappe.session.user,
			"requested_on": now_datetime(),
			"reason": reason or "",
		}
	)
	request.insert(ignore_permissions=True)
	return request.name


def waiting_reply(doc, request: str) -> dict:
	"""The reply for a document that was saved but not submitted, because it is waiting for approval."""
	return {
		"approval_required": True,
		"approval_request": request,
		"document": doc.name,
		"amount": _amount(doc),
		"limit": limits(doc.company)[kind_for(doc)],
	}


def enforce_on_submit(doc, method: str | None = None) -> None:
	"""Hook: a refund or purchase order above the limit cannot be submitted without an approved request."""
	if requires_approval(doc) and not is_approved(doc):
		frappe.throw(
			_("{0} {1} is above this business's approval limit. It needs a manager's approval first.").format(
				_(doc.doctype), doc.name
			),
			frappe.ValidationError,
		)


# --- deciding ----------------------------------------------------------------------------------------


def _load_request(name: str):
	if not frappe.db.exists(REQUEST, name):
		frappe.throw(_("Approval request {0} does not exist.").format(name), frappe.DoesNotExistError)
	request = frappe.get_doc(REQUEST, name)
	resolve_company(request.company)  # refuses a business the caller does not belong to
	return request


def _check_may_decide(request) -> None:
	if request.status != rules.PENDING:
		frappe.throw(_("This request has already been {0}.").format(_(request.status).lower()))
	problem = rules.decision_problem(
		request.kind, request.requested_by, frappe.session.user, frappe.get_roles()
	)
	if problem:
		frappe.throw(_(problem), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def approve_request(name: str) -> dict:
	"""Approve a request and submit the refund or purchase order it is about."""
	request = _load_request(name)
	_check_may_decide(request)
	doc = frappe.get_doc(request.reference_doctype, request.reference_name)
	if doc.docstatus != 0:
		frappe.throw(_("{0} is no longer a draft, so there is nothing to approve.").format(doc.name))
	# The amount may have been edited since it was asked for; the approval covers what the approver sees now
	request.amount = _amount(doc)
	request.status = rules.APPROVED
	request.decided_by = frappe.session.user
	request.decided_on = now_datetime()
	request.save(ignore_permissions=True)
	doc.flags.ignore_permissions = True
	doc.submit()
	return {
		"success": True,
		"message": _("Approved and submitted"),
		"data": _row(request) | {"docstatus": doc.docstatus},
	}


@frappe.whitelist(methods=["POST"])
def reject_request(name: str, reason: str | None = None) -> dict:
	"""Turn a request down. The draft stays, so it can be corrected or deleted."""
	request = _load_request(name)
	_check_may_decide(request)
	request.status = rules.REJECTED
	request.decided_by = frappe.session.user
	request.decided_on = now_datetime()
	if reason:
		request.reason = reason
	request.save(ignore_permissions=True)
	return {"success": True, "message": _("Request rejected"), "data": _row(request)}


ROW_FIELDS = [
	"name",
	"company",
	"kind",
	"status",
	"reference_doctype",
	"reference_name",
	"amount",
	"requested_by",
	"requested_on",
	"decided_by",
	"decided_on",
	"reason",
]


def _row(request) -> dict:
	return {
		"name": request.name,
		"company": request.company,
		"kind": request.kind,
		"status": request.status,
		"reference_doctype": request.reference_doctype,
		"reference_name": request.reference_name,
		"amount": flt(request.amount),
		"requested_by": request.requested_by,
		"requested_on": str(request.requested_on or ""),
		"decided_by": request.decided_by,
		"decided_on": str(request.decided_on or ""),
		"reason": request.reason,
	}


@frappe.whitelist()
def list_approvals(company: str | None = None, status: str | None = None, page_size: int = 50) -> dict:
	"""The business's approval requests, newest first. Any signed-in member of the business may look."""
	company = resolve_company(company)
	filters = {"company": company}
	if status:
		if status not in (rules.PENDING, rules.APPROVED, rules.REJECTED):
			frappe.throw(_("Status must be Pending, Approved or Rejected."), frappe.ValidationError)
		filters["status"] = status
	found = frappe.get_all(
		REQUEST,
		filters=filters,
		fields=ROW_FIELDS,
		order_by="creation desc",
		limit_page_length=min(max(cint(page_size), 1), 200),
	)
	data = [_row(row) for row in found]
	return {"success": True, "data": {"data": data, "count": len(data)}}
