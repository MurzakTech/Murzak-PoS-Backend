"""
Cash accounts, banks and bank accounts for the Settings > Bank Accounts screen, the payment method
screens and the purchase payment dialog.

Three different things are managed here, and they are easy to mix up:

* a cash or bank *ledger account*: the account in the books (Cash In Hand, Bank Accounts...) that money
  is posted to;
* a *bank*: the institution (name and SWIFT code), shared by every business;
* a *bank account*: the business's account number at a bank, which points at a ledger account.

Every call is limited to the businesses the caller belongs to. Changes need an accounts manager.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from murzak_pos.api.payment_gateway_common import require_gateway_admin, resolve_company

CASH = "Cash"
BANK = "Bank"
RECEIVABLE = "Receivable"
# What the screens may create, and what they may list
CREATABLE_TYPES = (CASH, BANK)
LISTABLE_TYPES = (CASH, BANK, RECEIVABLE)
# The group in the chart of accounts a new account is filed under
PARENT_NAMES = {CASH: "Cash In Hand", BANK: "Bank Accounts"}
ACCOUNT_FIELDS = [
	"name",
	"account_name",
	"account_number",
	"account_type",
	"account_currency",
	"parent_account",
	"company",
	"disabled",
]
BANK_ACCOUNT_FIELDS = [
	"name",
	"account_name",
	"bank",
	"account",
	"bank_account_no",
	"branch_code",
	"iban",
	"company",
	"disabled",
]


def _text(value) -> str:
	return (value or "").strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _reply(data=None, message: str | None = None, **extra) -> dict:
	result = {"success": True}
	if message:
		result["message"] = message
	if data is not None:
		result["data"] = data
	result.update(extra)
	return result


def clean_account_type(value, allowed=CREATABLE_TYPES) -> str:
	"""The account type in its proper spelling, or a plain-words refusal."""
	wanted = _text(value).lower()
	for kind in allowed:
		if kind.lower() == wanted:
			return kind
	frappe.throw(_("Account type must be one of: {0}.").format(", ".join(allowed)), frappe.ValidationError)


def _parent_group(company: str, account_type: str) -> str:
	"""The chart-of-accounts group a new cash or bank account goes under for this business."""
	name = PARENT_NAMES[account_type]
	parent = frappe.db.get_value(
		"Account", {"company": company, "account_name": name, "is_group": 1}, "name"
	) or frappe.db.get_value(
		"Account", {"company": company, "account_type": account_type, "is_group": 1}, "name"
	)
	if not parent:
		frappe.throw(
			_(
				"{0} has no '{1}' group in its chart of accounts yet, so the account cannot be created."
			).format(company, name),
			frappe.ValidationError,
		)
	return parent


def _own_account(name: str, company: str | None = None) -> frappe._dict:
	"""An account of a business the caller belongs to, or a refusal that does not reveal other businesses."""
	row = frappe.db.get_value("Account", name, ACCOUNT_FIELDS, as_dict=True) if name else None
	if not row:
		frappe.throw(_("Account {0} was not found.").format(name), frappe.DoesNotExistError)
	resolve_company(row.company)
	if company and row.company != company:
		frappe.throw(_("Account {0} was not found.").format(name), frappe.DoesNotExistError)
	return row


def _create_ledger_account(
	company: str, account_name: str, account_type: str, currency=None, account_number=None
) -> frappe.model.document.Document:
	account = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": account_name,
			"account_number": account_number or None,
			"company": company,
			"parent_account": _parent_group(company, account_type),
			"account_type": account_type,
			"account_currency": currency or frappe.get_cached_value("Company", company, "default_currency"),
			"is_group": 0,
		}
	)
	account.insert(ignore_permissions=True)
	return account


# --- cash and bank ledger accounts -----------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def create_cash_or_bank_account(
	account_name: str,
	account_type: str,
	company: str | None = None,
	account_currency: str | None = None,
	account_number: str | None = None,
) -> dict:
	"""Add a cash or bank account to a business's books."""
	require_gateway_admin()
	company = resolve_company(company)
	account_name = _text(account_name)
	if not account_name:
		frappe.throw(_("Give the account a name."), frappe.ValidationError)
	kind = clean_account_type(account_type)
	account = _create_ledger_account(
		company, account_name, kind, _text(account_currency), _text(account_number)
	)
	return _reply(
		{
			"name": account.name,
			"account_name": account.account_name,
			"account_type": account.account_type,
			"company": account.company,
		},
		_("Account created successfully"),
	)


@frappe.whitelist()
def list_cash_and_bank_accounts(company: str | None = None, account_type: str | None = None) -> dict:
	"""
	A business's cash and bank accounts. Asking for `Receivable` lists its debtors accounts instead,
	which the payment method screen uses for credit sales.
	"""
	company = resolve_company(company)
	types = [clean_account_type(account_type, LISTABLE_TYPES)] if _text(account_type) else [CASH, BANK]
	rows = frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "account_type": ["in", types]},
		fields=ACCOUNT_FIELDS,
		order_by="account_name asc",
		limit_page_length=0,
	)
	return _reply(rows, count=len(rows))


@frappe.whitelist()
def get_account_details(name: str) -> dict:
	return _reply(_own_account(_text(name)))


@frappe.whitelist(methods=["POST"])
def update_account(
	name: str,
	account_name: str | None = None,
	account_number: str | None = None,
	disabled=None,
) -> dict:
	"""Rename a cash or bank account, change its number, or switch it off or on."""
	require_gateway_admin()
	account = _own_account(_text(name))
	if account.account_type not in (CASH, BANK):
		frappe.throw(_("Only cash and bank accounts can be changed here."), frappe.PermissionError)

	new_name = _text(account_name) or account.account_name
	new_number = account_number if account_number is not None else account.account_number
	final_name = account.name
	if new_name != account.account_name or _text(new_number) != _text(account.account_number):
		from erpnext.accounts.doctype.account.account import update_account_number

		final_name = update_account_number(account.name, new_name, _text(new_number) or None) or account.name
	if disabled is not None and cint(disabled) != cint(account.disabled):
		frappe.db.set_value("Account", final_name, "disabled", cint(disabled))
	return _reply({"name": final_name}, _("Account updated successfully"))


# --- banks ---------------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def create_bank(bank_name: str, swift_number: str | None = None, website: str | None = None) -> dict:
	"""Add a bank (the institution). Banks are shared, so an existing one is returned instead of a copy."""
	require_gateway_admin()
	bank_name = _text(bank_name)
	if not bank_name:
		frappe.throw(_("Give the bank a name."), frappe.ValidationError)
	existing = frappe.db.get_value("Bank", {"bank_name": bank_name}, "name")
	if existing:
		return _reply({"name": existing, "bank_name": bank_name}, _("This bank already exists"))
	bank = frappe.get_doc(
		{
			"doctype": "Bank",
			"bank_name": bank_name,
			"swift_number": _text(swift_number) or None,
			"website": _text(website) or None,
		}
	)
	bank.insert(ignore_permissions=True)
	return _reply({"name": bank.name, "bank_name": bank.bank_name}, _("Bank created successfully"))


@frappe.whitelist()
def list_banks() -> dict:
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	rows = frappe.get_all(
		"Bank",
		fields=["name", "bank_name", "swift_number", "website"],
		order_by="bank_name asc",
		limit_page_length=0,
	)
	return _reply(rows, count=len(rows))


# --- the business's accounts at a bank ----------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def create_bank_account(
	account_name: str,
	bank: str,
	company: str | None = None,
	bank_account_no: str | None = None,
	branch_code: str | None = None,
	iban: str | None = None,
) -> dict:
	"""
	Record the business's account at a bank. A ledger account of type Bank is made at the same time and
	linked to it, because ERPNext needs one behind every company bank account.
	"""
	require_gateway_admin()
	company = resolve_company(company)
	account_name = _text(account_name)
	if not account_name:
		frappe.throw(_("Give the bank account a name."), frappe.ValidationError)
	if not frappe.db.exists("Bank", _text(bank)):
		frappe.throw(_("The bank {0} does not exist. Add it first.").format(bank), frappe.ValidationError)

	ledger = _create_ledger_account(company, account_name, BANK)
	bank_account = frappe.get_doc(
		{
			"doctype": "Bank Account",
			"account_name": account_name,
			"bank": _text(bank),
			"account": ledger.name,
			"is_company_account": 1,
			"company": company,
			"bank_account_no": _text(bank_account_no) or None,
			"branch_code": _text(branch_code) or None,
			"iban": _text(iban) or None,
		}
	)
	bank_account.insert(ignore_permissions=True)
	return _reply(
		{"name": bank_account.name, "account_name": bank_account.account_name, "account": ledger.name},
		_("Bank account created successfully"),
	)


@frappe.whitelist()
def list_bank_accounts(company: str | None = None) -> dict:
	company = resolve_company(company)
	rows = frappe.get_all(
		"Bank Account",
		filters={"company": company, "is_company_account": 1},
		fields=BANK_ACCOUNT_FIELDS,
		order_by="account_name asc",
		limit_page_length=0,
	)
	return _reply(rows, count=len(rows))


@frappe.whitelist()
def get_bank_account_details(name: str) -> dict:
	row = frappe.db.get_value("Bank Account", _text(name), BANK_ACCOUNT_FIELDS, as_dict=True)
	if not row or not row.company:
		frappe.throw(_("Bank account {0} was not found.").format(name), frappe.DoesNotExistError)
	resolve_company(row.company)
	return _reply(row)
