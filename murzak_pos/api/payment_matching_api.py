"""
Matching M-Pesa payments against the M-Pesa statement.

Every till payment and every M-Pesa prompt already stores the M-Pesa receipt number: on the payment row of a till
sale and on the Payment Entry made for a payment collected later. What was missing is bringing the statement in and
matching it. This module does that with ERPNext's own Bank Transaction and Bank Reconciliation machinery:

* each business gets a **clearing account** per payment method ("M-Pesa Clearing", "Card Clearing",
  "PayPal Clearing"): money sits there until the provider pays it into the bank;
* each business gets a **bank account for the M-Pesa statement**, on the M-Pesa clearing account, with a saved
  column mapping so ERPNext's own statement import also understands the M-Pesa file;
* `import_mpesa_statement` reads the statement file into Bank Transactions (importing it twice adds nothing) and then
  matches it;
* `match_mpesa_statement` matches each paid-in line to the sale or payment that holds its receipt number and
  reconciles the two in ERPNext;
* `get_payment_exceptions` lists what does not match, with the reason.

What ERPNext's own matching does with a till sale (checked for this module): a sale's payment row is found only by
its amount, because ERPNext reads no reference from it, so many sales of the same amount cannot be told apart. Here the
line is matched on the receipt number first, which is exact. A business whose sales are POS Invoices (the ones not on
eTIMS) has no payment row in the bank account until the shift is merged, so its lines are matched to the receipt only;
the exceptions list says so.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from murzak_pos.api import statement_rules
from murzak_pos.api.payment_gateway_common import require_gateway_admin, resolve_company

STATEMENT_BANK = "M-Pesa"
TRANSACTION_TYPE = "M-Pesa"
CLEARING_NAMES = {"M-Pesa": "M-Pesa Clearing", "Pesapal": "Card Clearing", "PayPal": "PayPal Clearing"}
# How the M-Pesa statement's columns become Bank Transaction fields (ERPNext's own statement import reads this)
COLUMN_MAPPING = (
	("date", "Completion Time"),
	("deposit", "Paid In"),
	("withdrawal", "Withdrawn"),
	("description", "Details"),
	("reference_number", "Receipt No."),
	("transaction_id", "Receipt No."),
)
DEFAULT_DAYS = 3
MAX_EXCEPTIONS = 500
AMOUNT_TOLERANCE = 0.005

NO_STATEMENT_LINE = "No statement line"
NO_RECEIPT = "No receipt on record"
NOT_IN_BOOKS = "Receipt found, but no sale or payment holds it"
AMOUNT_DIFFERS = "Amount differs from the sale or payment"
SEVERAL_SALES = "More than one sale or payment holds this receipt"


def _reply(data=None, message: str | None = None, **extra) -> dict:
	result = {"success": True}
	if message:
		result["message"] = message
	if data is not None:
		result["data"] = data
	result.update(extra)
	return result


# --- clearing accounts and the statement's bank account --------------------------------------------


def clearing_account(company: str, gateway: str) -> str:
	"""The business's clearing account for a payment method, made if it does not exist yet."""
	from murzak_pos.api.bank_accounts_api import _create_ledger_account

	name = CLEARING_NAMES[gateway]
	existing = frappe.db.get_value(
		"Account", {"company": company, "account_name": name, "is_group": 0}, "name"
	)
	return existing or _create_ledger_account(company, name, "Bank").name


def ensure_clearing_accounts(company: str) -> dict[str, str | None]:
	"""The three clearing accounts of a new business. Never blocks setting the business up."""
	return {gateway: default_payment_account(company, gateway) for gateway in CLEARING_NAMES}


def default_payment_account(company: str, gateway: str) -> str | None:
	"""
	The clearing account to start a payment method's settings with. A business whose chart of accounts has no group
	to file it under simply gets none, so setting up payments is never blocked by it.
	"""
	try:
		return clearing_account(company, gateway)
	except Exception:
		frappe.log_error(
			"Clearing account could not be made", f"{company} {gateway}\n{frappe.get_traceback()}"
		)
		return None


def _statement_bank() -> str:
	"""The shared M-Pesa bank, with the column mapping for its statement."""
	if not frappe.db.exists("Bank", STATEMENT_BANK):
		frappe.get_doc({"doctype": "Bank", "bank_name": STATEMENT_BANK}).insert(ignore_permissions=True)
	bank = frappe.get_doc("Bank", STATEMENT_BANK)
	if not bank.get("bank_transaction_mapping"):
		for field, column in COLUMN_MAPPING:
			bank.append("bank_transaction_mapping", {"bank_transaction_field": field, "file_field": column})
		bank.save(ignore_permissions=True)
	return STATEMENT_BANK


def statement_account(company: str, create: bool = True) -> str | None:
	"""The business's bank account for its M-Pesa statement (on the M-Pesa clearing account)."""
	ledger = frappe.db.get_value(
		"Account", {"company": company, "account_name": CLEARING_NAMES["M-Pesa"], "is_group": 0}, "name"
	)
	existing = frappe.db.get_value(
		"Bank Account", {"company": company, "bank": STATEMENT_BANK, "is_company_account": 1}, "name"
	)
	if existing or not create:
		return existing
	account = frappe.get_doc(
		{
			"doctype": "Bank Account",
			"account_name": f"{company} Statement",
			"bank": _statement_bank(),
			"account": ledger or clearing_account(company, "M-Pesa"),
			"company": company,
			"is_company_account": 1,
		}
	)
	account.insert(ignore_permissions=True)
	return account.name


@frappe.whitelist(methods=["POST"])
def setup_payment_clearing(company: str | None = None) -> dict:
	"""
	Set a business up for matching: the three clearing accounts, the account set on any payment settings that have none,
	and the bank account for the M-Pesa statement. Safe to run again.
	"""
	require_gateway_admin()
	company = resolve_company(company)
	from murzak_pos.api.payment_gateway_common import ensure_mode_of_payment

	accounts = {gateway: clearing_account(company, gateway) for gateway in CLEARING_NAMES}
	filled = []

	mpesa = frappe.db.get_value(
		"MPESA Settings", {"company": company}, ["name", "payment_account", "mode_of_payment"], as_dict=True
	)
	if mpesa and not mpesa.payment_account:
		frappe.db.set_value("MPESA Settings", mpesa.name, "payment_account", accounts["M-Pesa"])
		if mpesa.mode_of_payment:
			ensure_mode_of_payment(mpesa.mode_of_payment, "Phone", company, accounts["M-Pesa"])
		filled.append("M-Pesa")
	for row in frappe.get_all(
		"POS Gateway Settings",
		filters={"company": company},
		fields=["name", "gateway", "payment_account", "mode_of_payment"],
	):
		if row.payment_account or row.gateway not in accounts:
			continue
		frappe.db.set_value("POS Gateway Settings", row.name, "payment_account", accounts[row.gateway])
		if row.mode_of_payment:
			ensure_mode_of_payment(row.mode_of_payment, "Bank", company, accounts[row.gateway])
		filled.append(row.gateway)

	return _reply(
		{
			"clearing_accounts": accounts,
			"settings_filled": filled,
			"statement_account": statement_account(company),
		},
		_("Payment matching is set up"),
	)


# --- importing the statement -----------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def import_mpesa_statement(csv_text: str, company: str | None = None) -> dict:
	"""
	Read an M-Pesa statement (the CSV from the M-Pesa portal) into Bank Transactions, then match it.

	A receipt already imported is skipped, so uploading the same statement twice adds nothing.
	"""
	require_gateway_admin()
	company = resolve_company(company)
	account = statement_account(company)
	read = statement_rules.read_statement(csv_text)
	if not read["rows"]:
		frappe.throw(
			read["problems"][0] if read["problems"] else _("The statement has no payments in it."),
			frappe.ValidationError,
		)

	currency = frappe.db.get_value(
		"Account", frappe.db.get_value("Bank Account", account, "account"), "account_currency"
	)
	known = set(
		frappe.get_all(
			"Bank Transaction",
			filters={"bank_account": account, "docstatus": ["!=", 2]},
			pluck="transaction_id",
			limit_page_length=0,
		)
	)
	created = already_there = 0
	for line in read["rows"]:
		if line["receipt"] in known:
			already_there += 1
			continue
		transaction = frappe.get_doc(
			{
				"doctype": "Bank Transaction",
				"date": line["date"],
				"bank_account": account,
				"company": company,
				"currency": currency,
				"deposit": line["deposit"],
				"withdrawal": line["withdrawal"],
				"description": line["details"],
				"reference_number": line["receipt"],
				"transaction_id": line["receipt"],
				"transaction_type": TRANSACTION_TYPE,
			}
		)
		transaction.insert(ignore_permissions=True)
		transaction.submit()
		known.add(line["receipt"])
		created += 1
	matched = _match(company, account)
	return _reply(
		{
			"imported": created,
			"already_imported": already_there,
			"repeated_in_file": read["repeated_in_file"],
			"not_completed": read["skipped_not_completed"],
			"problems": read["problems"],
			"matching": matched,
		},
		_("{0} statement lines imported").format(created),
	)


# --- matching --------------------------------------------------------------------------------------


def _receipts(company: str) -> dict[str, dict]:
	"""The business's successful M-Pesa receipts, by receipt number."""
	rows = frappe.get_all(
		"MPESA Transaction Log",
		filters={"company": company, "status": "Success", "mpesa_receipt_number": ["is", "set"]},
		fields=["mpesa_receipt_number", "amount", "completed_at", "invoice_type", "invoice", "payment_entry"],
		limit_page_length=0,
	)
	return {r.mpesa_receipt_number.strip().upper(): r for r in rows}


def _vouchers(company: str, ledger: str, receipt: str, receipt_row=None) -> list[dict]:
	"""The payments in the books that hold a receipt number and are still waiting to be cleared in the bank account."""
	found = [
		{"doctype": "Payment Entry", "name": r.name, "amount": flt(r.paid_amount)}
		for r in frappe.get_all(
			"Payment Entry",
			filters={
				"company": company,
				"docstatus": 1,
				"reference_no": receipt,
				"paid_to": ledger,
				"clearance_date": ["is", "not set"],
			},
			fields=["name", "paid_amount"],
		)
	]
	rows = frappe.db.sql(
		"""
		SELECT p.parent AS name, p.amount AS amount
		FROM `tabSales Invoice Payment` p
		INNER JOIN `tabSales Invoice` s ON s.name = p.parent
		WHERE s.docstatus = 1 AND s.company = %(company)s AND p.reference_no = %(receipt)s
			AND p.account = %(ledger)s AND p.clearance_date IS NULL
		""",
		{"company": company, "receipt": receipt, "ledger": ledger},
		as_dict=True,
	)
	found += [{"doctype": "Sales Invoice", "name": r.name, "amount": flt(r.amount)} for r in rows]
	if found or not receipt_row:
		return found

	# A sale made before receipt numbers were kept on payment rows: the receipt record names its invoice
	if receipt_row.invoice_type == "Sales Invoice" and receipt_row.invoice:
		rows = frappe.db.sql(
			"""
			SELECT p.parent AS name, p.amount AS amount FROM `tabSales Invoice Payment` p
			INNER JOIN `tabSales Invoice` s ON s.name = p.parent
			WHERE s.docstatus = 1 AND p.parent = %(invoice)s AND p.account = %(ledger)s AND p.clearance_date IS NULL
			""",
			{"invoice": receipt_row.invoice, "ledger": ledger},
			as_dict=True,
		)
		found += [{"doctype": "Sales Invoice", "name": r.name, "amount": flt(r.amount)} for r in rows]
	return found


def classify_line(deposit: float, vouchers: list[dict], receipt_known: bool) -> tuple[str, str | None]:
	"""
	What to do with a paid-in line: ("reconcile", None) when exactly one payment holds its receipt for the same amount,
	otherwise ("problem", the reason).
	"""
	if len(vouchers) > 1:
		return "problem", SEVERAL_SALES
	if vouchers:
		if abs(flt(vouchers[0]["amount"]) - flt(deposit)) <= AMOUNT_TOLERANCE:
			return "reconcile", None
		return "problem", AMOUNT_DIFFERS
	return "problem", NOT_IN_BOOKS if receipt_known else NO_RECEIPT


def _unreconciled_deposits(account: str) -> list[dict]:
	return frappe.get_all(
		"Bank Transaction",
		filters={
			"bank_account": account,
			"docstatus": 1,
			"deposit": [">", 0],
			"unallocated_amount": [">", 0],
		},
		fields=["name", "date", "deposit", "reference_number", "transaction_id", "description"],
		order_by="date asc, creation asc",
		limit_page_length=0,
	)


def reconcile_line(transaction_name: str, voucher: dict) -> None:
	"""Reconcile one bank line with one payment, the way ERPNext's Bank Reconciliation Tool does."""
	line = frappe.get_doc("Bank Transaction", transaction_name)
	line.add_payment_entries(
		[
			{
				"payment_doctype": voucher["doctype"],
				"payment_name": voucher["name"],
				"amount": voucher["amount"],
			}
		]
	)
	line.validate_duplicate_references()
	line.allocate_payment_entries()
	line.update_allocated_amount()
	line.set_status()
	line.save(ignore_permissions=True)


def _match(company: str, account: str) -> dict:
	"""Match every paid-in line still waiting. One line failing never stops the others."""
	ledger = frappe.db.get_value("Bank Account", account, "account")
	receipts = _receipts(company)
	counts = {"reconciled": 0, "left": 0, "failed": 0}
	for line in _unreconciled_deposits(account):
		receipt = (line.reference_number or line.transaction_id or "").strip().upper()
		row = receipts.get(receipt)
		vouchers = _vouchers(company, ledger, receipt, row)
		action, _reason = classify_line(line.deposit, vouchers, row is not None)
		if action != "reconcile":
			counts["left"] += 1
			continue
		try:
			reconcile_line(line.name, vouchers[0])
			frappe.db.commit()
			counts["reconciled"] += 1
		except Exception:
			frappe.db.rollback()
			counts["failed"] += 1
			frappe.log_error(
				"Statement line could not be reconciled", f"{line.name}\n{frappe.get_traceback()}"
			)
	return counts


@frappe.whitelist(methods=["POST"])
def match_mpesa_statement(company: str | None = None) -> dict:
	"""Match the statement lines that are waiting. Run after a statement is imported, or after sales were fixed."""
	require_gateway_admin()
	company = resolve_company(company)
	account = statement_account(company, create=False)
	if not account:
		frappe.throw(
			_("No M-Pesa statement has been imported for this business yet."), frappe.ValidationError
		)
	return _reply(_match(company, account))


# --- what does not match ---------------------------------------------------------------------------


def exception_rows(company: str, days: int = DEFAULT_DAYS) -> list[dict]:
	"""
	What does not match for a business, each with the reason.

	* a receipt that succeeded but has no statement line, once it is `days` old and falls inside the period the imported
	  statements cover (a receipt after the last statement is just not imported yet);
	* a paid-in statement line that is not reconciled: no receipt on record, a receipt that no sale or payment holds, a
	  different amount, or several sales for one receipt.
	"""
	account = statement_account(company, create=False)
	if not account:
		return []
	ledger = frappe.db.get_value("Bank Account", account, "account")
	lines = frappe.get_all(
		"Bank Transaction",
		filters={"bank_account": account, "docstatus": 1},
		fields=["date", "reference_number", "transaction_id"],
		limit_page_length=0,
	)
	if not lines:
		return []
	on_statement = {(line.reference_number or line.transaction_id or "").strip().upper() for line in lines}
	first, last = min(getdate(line.date) for line in lines), max(getdate(line.date) for line in lines)
	cutoff = add_days(nowdate(), -cint(days))
	receipts = _receipts(company)

	rows: list[dict] = []
	for receipt, row in receipts.items():
		finished = getdate(row.completed_at) if row.completed_at else None
		if receipt in on_statement or not finished or finished > getdate(cutoff):
			continue
		if not first <= finished <= last:
			continue
		rows.append(
			{
				"company": company,
				"date": str(finished),
				"reference": receipt,
				"amount": flt(row.amount),
				"problem": NO_STATEMENT_LINE,
				"detail": _("The payment succeeded but is not on the M-Pesa statement."),
			}
		)
	for line in _unreconciled_deposits(account):
		receipt = (line.reference_number or line.transaction_id or "").strip().upper()
		row = receipts.get(receipt)
		_action, reason = classify_line(
			line.deposit, _vouchers(company, ledger, receipt, row), row is not None
		)
		if reason:
			rows.append(
				{
					"company": company,
					"date": str(line.date),
					"reference": receipt,
					"amount": flt(line.deposit),
					"problem": reason,
					"detail": (line.description or "")[:140],
				}
			)
	rows.sort(key=lambda r: (r["date"], r["reference"]))
	return rows[:MAX_EXCEPTIONS]


@frappe.whitelist()
def get_payment_exceptions(company: str | None = None, days: int = DEFAULT_DAYS) -> dict:
	"""What does not match between the M-Pesa receipts and the statement, with the reason for each."""
	require_gateway_admin()
	company = resolve_company(company)
	rows = exception_rows(company, cint(days) or DEFAULT_DAYS)
	return _reply({"data": rows, "count": len(rows)})


def summary_counts(company: str, days: int = DEFAULT_DAYS) -> dict[str, int]:
	"""How many exceptions of each kind a business has, for its weekly summary."""
	counts: dict[str, int] = {}
	for row in exception_rows(company, days):
		counts[row["problem"]] = counts.get(row["problem"], 0) + 1
	return counts
