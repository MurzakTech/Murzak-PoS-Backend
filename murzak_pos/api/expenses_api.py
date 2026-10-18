"""
The Expenses page: add an expense, list the running costs, and the choices the form offers (ticket P5-1).

`get_expense_options`, `create_expense`, `list_expenses` and `get_receipt` return what the contract
docs/redesign/P5-1-contract.md describes. They are new; no existing call, report, account or record is changed.

How it is built
- The rules and the arithmetic are in expenses_calc.py (plain functions, no database). This file reads and writes the
  database in small functions, one job each, and joins the two.
- An expense is ONE submitted Journal Entry: credit the account behind the "paid from" choice (the default account of
  the shop's own Mode of Payment for Cash, M-Pesa or Bank), debit an existing expense account (the category). No
  account is ever created or changed. The entry is made, submitted and (when a receipt photo came with it) the photo is
  attached in ONE transaction that is committed once, at the very end, and rolled back if any step fails.
- The list reads the general ledger the way Home reads Running costs (`dashboard_api._get_operating_expenses`: the same
  tables, the same conditions, the same excluded account types and accounts), so the total of the list for a period
  equals Home's Running costs for the same period. Rows made here are told apart by the marker in the entry's remark.
- Only an owner or a manager may use these calls (`role_tiers`): a cashier is refused before anything is read.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import frappe
from frappe import _

from murzak_pos.api import expenses_calc as rules
from murzak_pos.api import overview_calc as calc
from murzak_pos.api import role_tiers
from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.dashboard_api import NON_OPERATING_EXPENSE_TYPES
from murzak_pos.api.payment_gateway_common import resolve_company

# --- the clock (small on purpose, so tests can replace it) ------------------------------------------


def _now_utc() -> datetime:
	return datetime.now(timezone.utc)


# --- who may use these calls -------------------------------------------------------------------------


def _require_manager() -> str:
	"""Owner or manager only. Done first, before any record is read. Returns the tier."""
	tier = role_tiers.get_tier()
	if tier is None:
		frappe.throw(_("Please sign in first."), frappe.AuthenticationError)
	if tier == role_tiers.CASHIER:
		frappe.throw(_("Only an owner or a manager can use Expenses."), frappe.PermissionError)
	return tier


def _refusal(error_type: str, message: str, field: str | None = None) -> dict:
	"""An expected refusal: a plain sentence and a short code the page can act on (the reply is still HTTP 200)."""
	out = {"success": False, "message": message, "error_type": error_type}
	if field:
		out["field"] = field
	return out


def plain_text(error, limit: int = 240) -> str:
	"""The words of an error without tags, line breaks or a long tail, so a person can read it."""
	text = re.sub(r"<[^>]*>", " ", str(error or ""))
	text = re.sub(r"\s+", " ", text).strip()
	return text[:limit]


def _refuse_period(code: str):
	"""Refuse a period that cannot be used, in words the person can act on."""
	if code == "need_dates":
		frappe.throw(
			_("Please give both a start date and an end date for the expenses list."),
			frappe.ValidationError,
		)
	if code == "bad_date":
		frappe.throw(_("Expenses list dates must look like 2026-10-13."), frappe.ValidationError)
	if code == "order":
		frappe.throw(
			_("The expenses list start date must not be after the end date."), frappe.ValidationError
		)
	if code == "too_long":
		frappe.throw(_("Please choose an expenses list period of one year or less."), frappe.ValidationError)
	frappe.throw(
		_("Choose an expenses list period: today, week, 7d, 30d, month or custom."), frappe.ValidationError
	)


# --- what the form offers ----------------------------------------------------------------------------


def _company_facts(company: str) -> dict:
	"""The company settings the calls need: currency, short tag, default cost centre and the accounts Home leaves out."""
	row = frappe.get_cached_value(
		"Company",
		company,
		[
			"default_currency",
			"abbr",
			"cost_center",
			"default_expense_account",
			"stock_adjustment_account",
			"stock_received_but_not_billed",
		],
	)
	currency, abbr, cost_center, expense, adjustment, not_billed = (list(row or []) + [None] * 6)[:6]
	return {
		"currency": currency,
		"abbr": abbr,
		"cost_center": cost_center,
		# exactly the two accounts dashboard_api._get_operating_expenses leaves out
		"home_excluded": [a for a in (expense, adjustment) if a],
		"not_billed": not_billed,
	}


def _categories(company: str, facts: dict) -> list[dict]:
	"""The existing expense accounts an expense may be booked to: [{id, label, group}] (see expenses_calc.is_category)."""
	accounts = frappe.get_all(
		"Account",
		filters={"company": company, "root_type": "Expense", "is_group": 0, "disabled": 0},
		fields=[
			"name",
			"account_name",
			"account_type",
			"parent_account",
			"root_type",
			"is_group",
			"disabled",
		],
		order_by="account_name asc",
		limit_page_length=0,
	)
	excluded = [*facts["home_excluded"], *([facts["not_billed"]] if facts["not_billed"] else [])]
	return rules.category_views(accounts, excluded, facts["abbr"])


def _paid_from_options(company: str, facts: dict) -> list[dict]:
	"""Cash, M-Pesa and Bank, each with whether the shop has an account for it (from its Mode of Payment accounts)."""
	modes = frappe.get_all(
		"Mode of Payment", filters={"enabled": 1}, fields=["name", "type"], limit_page_length=0
	)
	links = frappe.get_all(
		"Mode of Payment Account",
		filters={"parenttype": "Mode of Payment", "company": company},
		fields=["parent", "default_account"],
		limit_page_length=0,
	)
	account_of = {}
	for link in links:
		if link.get("default_account") and link["parent"] not in account_of:
			account_of[link["parent"]] = link["default_account"]
	candidates = [
		{"name": m["name"], "type": m.get("type"), "account": account_of.get(m["name"])} for m in modes
	]
	names = sorted({c["account"] for c in candidates if c["account"]})
	metas = {}
	if names:
		for meta in frappe.get_all(
			"Account",
			filters={"name": ["in", names]},
			fields=["name", "company", "is_group", "disabled", "freeze_account", "account_currency"],
			limit_page_length=0,
		):
			metas[meta["name"]] = meta
	return rules.paid_from_options(candidates, metas, company, facts["currency"])


@frappe.whitelist()
def get_expense_options(company=None):
	"""What the Add expense form offers: categories, the paid-from choices with whether each can be used, today's date."""
	_require_manager()
	company = resolve_company(company)
	try:
		facts = _company_facts(company)
		today = calc.nairobi_today(_now_utc())
		return {
			"success": True,
			"data": {
				"company": company,
				"today": today.isoformat(),
				"categories": _categories(company, facts),
				"paid_from": [
					{k: v for k, v in option.items() if k != "account"}
					for option in _paid_from_options(company, facts)
				],
				"limits": {
					"note_length": rules.MAX_NOTE_LENGTH,
					"receipt_bytes": rules.MAX_RECEIPT_BYTES,
					"receipt_types": sorted(rules.RECEIPT_TYPES.values()),
				},
			},
		}
	except Exception:
		frappe.log_error("Expense options error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the expense choices. Please try again.")}


# --- add an expense ----------------------------------------------------------------------------------


def _input_refusal(code: str) -> dict:
	"""The plain sentence, and the field it belongs beside, for each reason the typed values cannot be used."""
	if code == "no_amount":
		return _refusal(code, _("Enter the amount."), "amount")
	if code == "bad_amount":
		return _refusal(code, _("The amount must be a number, for example 1200."), "amount")
	if code == "not_above_zero":
		return _refusal(code, _("The amount must be more than 0."), "amount")
	if code == "too_big":
		return _refusal(code, _("That amount is too large. Please check it."), "amount")
	if code == "no_date":
		return _refusal(code, _("Choose the date of the expense."), "date")
	if code == "bad_date":
		return _refusal(code, _("The date must look like 2026-10-13."), "date")
	if code == "future_date":
		return _refusal(code, _("The date cannot be in the future."), "date")
	if code == "note_too_long":
		return _refusal(code, _("The note can be at most 140 characters."), "note")
	if code == "no_category":
		return _refusal(code, _("Choose a category."), "category")
	if code == "bad_category":
		return _refusal(code, _("That category is not available. Choose one from the list."), "category")
	if code == "no_paid_from":
		return _refusal(code, _("Choose what the money was paid from."), "paid_from")
	if code == "bad_paid_from":
		return _refusal(code, _("Choose Cash, M-Pesa or Bank."), "paid_from")
	if code == "bad_receipt":
		return _refusal(code, _("The photo could not be read. Please choose it again."), "receipt")
	if code == "receipt_too_big":
		return _refusal(code, _("The photo is larger than 5 MB. Choose a smaller one."), "receipt")
	if code == "receipt_not_image":
		return _refusal(code, _("The receipt must be a photo (JPG, PNG, GIF or WebP)."), "receipt")
	return _refusal("bad_request_id", _("This expense could not be checked for a repeat. Please try again."))


def _no_account(option: dict) -> dict:
	"""The sentence for a paid-from choice the shop has no usable account for."""
	label = option["label"]
	if option.get("reason") == "account_unusable":
		return _refusal(
			"account_unusable",
			_(
				"The {0} account cannot be used right now (it may be switched off, frozen or in another currency), so an expense cannot be paid from {0}. Choose another way, or ask your accountant to check it."
			).format(label),
			"paid_from",
		)
	return _refusal(
		"no_account",
		_(
			"This shop has no {0} account set up, so an expense cannot be paid from {0}. Choose another way, or ask your accountant to link an account to it."
		).format(label),
		"paid_from",
	)


def _failed(reason: str = "") -> str:
	"""The sentence for an expense that could not be saved: nothing was recorded."""
	base = _("The expense was not saved, so nothing was changed.")
	if reason:
		return f"{base} {reason}"
	return f"{base} {_('Please try again.')}"


@frappe.whitelist(methods=["POST"])
def create_expense(
	amount=None,
	category=None,
	paid_from=None,
	date=None,
	note=None,
	receipt=None,
	company=None,
	request_id=None,
):
	"""
	Record an expense: ONE submitted Journal Entry, money out of the paid-from account, into the category. A repeat with
	the same request_id returns the first result and changes nothing. If any step fails everything is rolled back and a
	plain sentence comes back.
	"""
	_require_manager()
	if isinstance(receipt, str) and receipt.lstrip().startswith("{"):
		receipt = frappe.parse_json(receipt)

	# What was typed, checked before anything is read or written
	request, request_ok = rules.clean_request_id(request_id)
	if not request_ok:
		return _input_refusal("bad_request_id")
	value, why = rules.clean_amount(amount)
	if why:
		return _input_refusal(why)
	today = calc.nairobi_today(_now_utc())
	day, why = rules.clean_day(date, today)
	if why:
		return _input_refusal(why)
	text, why = rules.clean_note(note)
	if why:
		return _input_refusal(why)
	category = str(category or "").strip()
	if not category:
		return _input_refusal("no_category")
	if not str(paid_from or "").strip():
		return _input_refusal("no_paid_from")
	key = rules.clean_paid_from(paid_from)
	if not key:
		return _input_refusal("bad_paid_from")
	photo, why = rules.clean_receipt(receipt)
	if why:
		return _input_refusal(why)
	company = resolve_company(company)

	try:
		return _create(company, value, category, key, day, text, photo, request)
	except Exception as error:
		# Anything not caught below: put everything back, so no half done expense is left
		frappe.db.rollback()
		frappe.log_error("Create expense error", frappe.get_traceback())
		return _refusal(
			"server_error", _failed(plain_text(error) if isinstance(error, frappe.ValidationError) else "")
		)


def _create(company, value, category, key, day, text, photo, request):
	"""The work of create_expense, inside its try."""
	if request:
		# One request at a time per business, so a double tap cannot slip in between the check and the entry
		frappe.db.get_value("Company", company, "name", for_update=True)
		earlier = _earlier(request, company)
		if earlier:
			return _saved(frappe.get_doc("Journal Entry", earlier), company, repeat=True)

	facts = _company_facts(company)
	if category not in {c["id"] for c in _categories(company, facts)}:
		return _input_refusal("bad_category")
	options = _paid_from_options(company, facts)
	option = rules.option_for(options, key)
	if not option or not option["available"]:
		return _no_account(option or {"label": rules.PAID_FROM_LABELS[key]})

	doc = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": company,
			"posting_date": day.isoformat(),
			"user_remark": rules.remark(text, request),
			"accounts": rules.journal_lines(value, category, option["account"], facts["cost_center"]),
		}
	)
	# The caller is already known to be an owner or a manager (checked first); an owner or manager without
	# accounting roles may still add an expense, which is the decision behind this page
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.submit()
	if photo:
		_attach_receipt(doc.name, photo)
	frappe.db.commit()
	return _saved(doc, company, repeat=False, options=options, has_receipt=bool(photo), facts=facts)


def _earlier(request, company):
	"""The submitted entry already made for this request, if any."""
	found = frappe.get_all(
		"Journal Entry",
		filters={
			"docstatus": 1,
			"company": company,
			"user_remark": ["like", rules.marker_pattern(request)],
		},
		pluck="name",
		limit_page_length=1,
	)
	return found[0] if found else None


def _attach_receipt(entry: str, photo: dict) -> None:
	"""The receipt photo as a private file attached to the entry (Frappe's standard attachment)."""
	attachment = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"receipt-{entry}.{photo['ext']}",
			"attached_to_doctype": "Journal Entry",
			"attached_to_name": entry,
			"content": photo["content"],
			"is_private": 1,
		}
	)
	attachment.flags.ignore_permissions = True
	attachment.insert(ignore_permissions=True)


def _receipt_names(entries) -> dict:
	"""{entry name: file name} for the entries that have a receipt photo (the newest file of each)."""
	if not entries:
		return {}
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Journal Entry", "attached_to_name": ["in", list(entries)]},
		fields=["name", "attached_to_name"],
		order_by="creation asc",
		limit_page_length=0,
	)
	return {f["attached_to_name"]: f["name"] for f in files}


def _full_names(users) -> dict:
	users = sorted({u for u in users if u})
	if not users:
		return {}
	rows = frappe.get_all(
		"User", filters={"name": ["in", users]}, fields=["name", "full_name"], limit_page_length=0
	)
	return {r["name"]: r.get("full_name") or r["name"] for r in rows}


def _saved(doc, company, repeat, options=None, has_receipt=None, facts=None):
	"""The answer for an entry made by create_expense: the same row the list shows."""
	facts = facts or _company_facts(company)
	if options is None:
		options = _paid_from_options(company, facts)
	lines = list(doc.accounts)
	debit = next((line for line in lines if calc.num(line.debit_in_account_currency) > 0), None)
	credit = next((line for line in lines if calc.num(line.credit_in_account_currency) > 0), None)
	if has_receipt is None:
		has_receipt = doc.name in _receipt_names([doc.name])
	account = debit.account if debit else None
	row = rules.row_view(
		{
			"voucher_type": "Journal Entry",
			"voucher_no": doc.name,
			"account": account,
			"account_name": frappe.db.get_value("Account", account, "account_name") if account else None,
			"posting_date": doc.posting_date,
			"amount": calc.num(debit.debit_in_account_currency) if debit else 0,
			"remarks": doc.get("remark"),
			"user_remark": doc.user_remark,
		},
		credit_account=credit.account if credit else None,
		options=options,
		has_receipt=has_receipt,
		added_by=_full_names([doc.owner]).get(doc.owner),
		abbr=facts["abbr"],
	)
	return {"success": True, "data": {"expense": row, "repeat": bool(repeat)}}


# --- the list ----------------------------------------------------------------------------------------


def _ledger(company, period, excluded, category=None, search=None):
	"""
	(SQL, values) for the ledger lines Home counts as running costs, one group per entry and account, newest first once
	ordered. This is the query of `dashboard_api._get_operating_expenses` with the same conditions: not cancelled, this
	company, an expense account whose type is not one Home leaves out, not the company's cost of goods or stock
	adjustment account, posted in the period.
	"""
	values = {
		"company": company,
		"from": period["from"],
		"to": period["to"],
		"types": tuple(NON_OPERATING_EXPENSE_TYPES),
	}
	where = (
		"gl.is_cancelled = 0 AND gl.company = %(company)s AND a.root_type = 'Expense' "
		"AND (a.account_type IS NULL OR a.account_type NOT IN %(types)s) "
		"AND gl.posting_date BETWEEN %(from)s AND %(to)s"
	)
	if excluded:
		values["excluded"] = tuple(excluded)
		where += " AND gl.account NOT IN %(excluded)s"
	if category:
		values["category"] = category
		where += " AND gl.account = %(category)s"
	if search:
		values["like"] = rules.like_pattern(search)
		where += (
			" AND (a.account_name LIKE %(like)s OR gl.voucher_no LIKE %(like)s "
			"OR SUBSTRING_INDEX(IFNULL(NULLIF(je.user_remark, ''), IFNULL(gl.remarks, '')), %(marker)s, 1) LIKE %(like)s)"
		)
		values["marker"] = rules.MARKER_START
	sql = (
		"SELECT gl.voucher_type AS voucher_type, gl.voucher_no AS voucher_no, gl.account AS account, "
		"a.account_name AS account_name, gl.posting_date AS posting_date, "
		"SUM(gl.debit - gl.credit) AS amount, MAX(gl.remarks) AS remarks, MAX(je.user_remark) AS user_remark, "
		"MAX(je.owner) AS added_by_user, MAX(gl.creation) AS creation "
		"FROM `tabGL Entry` gl JOIN `tabAccount` a ON a.name = gl.account "
		"LEFT JOIN `tabJournal Entry` je ON gl.voucher_type = 'Journal Entry' AND je.name = gl.voucher_no "
		f"WHERE {where} "
		"GROUP BY gl.voucher_type, gl.voucher_no, gl.account, a.account_name, gl.posting_date "
		"HAVING SUM(gl.debit - gl.credit) <> 0"
	)
	return sql, values


def _count_and_total(sql, values) -> tuple[int, float]:
	found = frappe.db.sql(
		f"SELECT COUNT(*) AS n, IFNULL(SUM(g.amount), 0) AS total FROM ({sql}) g", values, as_dict=True
	)
	if not found:
		return 0, 0.0
	return int(found[0].get("n") or 0), calc.money(found[0].get("total"))


def _top_category(sql, values) -> dict | None:
	found = frappe.db.sql(
		"SELECT g.account AS id, g.account_name AS label, SUM(g.amount) AS amount "
		f"FROM ({sql}) g GROUP BY g.account, g.account_name HAVING SUM(g.amount) > 0 "
		"ORDER BY SUM(g.amount) DESC, g.account LIMIT 1",
		values,
		as_dict=True,
	)
	return found[0] if found else None


def _page(sql, values, numbers) -> list[dict]:
	return frappe.db.sql(
		f"SELECT * FROM ({sql}) g ORDER BY g.posting_date DESC, g.creation DESC, g.voucher_no DESC, g.account "
		"LIMIT %(limit)s OFFSET %(offset)s",
		{**values, "limit": numbers["page_size"], "offset": numbers["offset"]},
		as_dict=True,
	)


@frappe.whitelist()
def list_expenses(
	period="30d",
	from_date=None,
	to_date=None,
	category=None,
	search=None,
	page=1,
	page_size=20,
	company=None,
):
	"""The Expenses list for a period, a category and a search (see the module notes and the contract)."""
	_require_manager()
	today = calc.nairobi_today(_now_utc())
	try:
		resolved = calc.resolve_period(period, from_date, to_date, today)
	except calc.PeriodError as error:
		_refuse_period(error.code)
	company = resolve_company(company)
	category = str(category).strip() if isinstance(category, str) and category.strip() else None
	search = rules.clean_search(search)
	try:
		data = _list(company, resolved, category, search, page, page_size)
	except Exception:
		frappe.log_error("Expenses list error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the expenses. Please try again.")}
	return {"success": True, "data": data}


def _list(company, period, category, search, page, page_size) -> dict:
	facts = _company_facts(company)
	excluded = facts["home_excluded"]

	# The three numbers follow the period only, so the first is Home's Running costs for the same days
	whole_sql, whole_values = _ledger(company, period, excluded)
	whole_count, whole_total = _count_and_total(whole_sql, whole_values)
	top = _top_category(whole_sql, whole_values)

	list_sql, list_values = _ledger(company, period, excluded, category, search)
	total, _spent = _count_and_total(list_sql, list_values)
	numbers = rules.page_numbers(page, page_size, total)
	raw = _page(list_sql, list_values, numbers)

	mine = [
		r["voucher_no"]
		for r in raw
		if r["voucher_type"] == "Journal Entry" and rules.parse_remark(r.get("user_remark"))["is_expense"]
	]
	credits = {}
	if mine:
		for line in frappe.get_all(
			"Journal Entry Account",
			filters={
				"parenttype": "Journal Entry",
				"parent": ["in", mine],
				"credit_in_account_currency": [">", 0],
			},
			fields=["parent", "account"],
			order_by="idx asc",
			limit_page_length=0,
		):
			credits.setdefault(line["parent"], line["account"])
	receipts = _receipt_names(mine)
	names = _full_names([r.get("added_by_user") for r in raw])
	options = _paid_from_options(company, facts) if mine else []
	rows = [
		rules.row_view(
			r,
			credit_account=credits.get(r["voucher_no"]),
			options=options,
			has_receipt=r["voucher_no"] in receipts,
			added_by=names.get(r.get("added_by_user")),
			abbr=facts["abbr"],
		)
		for r in raw
	]
	return {
		"rows": rows,
		"total": total,
		"page": numbers["page"],
		"page_size": numbers["page_size"],
		"pages": numbers["pages"],
		"period": {
			"key": period["key"],
			"from": period["from"].isoformat(),
			"to": period["to"].isoformat(),
			"days": period["days"],
		},
		"summary": rules.summary_view(whole_total, whole_count, top),
		"spent_in_list": _spent,
	}


# --- the receipt photo -------------------------------------------------------------------------------


@frappe.whitelist()
def get_receipt(expense=None):
	"""
	The receipt photo of an expense made on this page, as a data URL the page can show in an image. The photo is a
	private file; it is read here (after the owner or manager check) so a manager without accounting roles can see it too.
	"""
	_require_manager()
	expense = str(expense or "").strip()
	if not expense:
		return _refusal("not_found", _("That expense was not found."))
	ensure_record_access("Journal Entry", expense)
	try:
		head = frappe.db.get_value("Journal Entry", expense, ["docstatus", "user_remark"], as_dict=True)
		if (
			not head
			or head.get("docstatus") != 1
			or not rules.parse_remark(head.get("user_remark"))["is_expense"]
		):
			return _refusal("not_found", _("That expense was not found."))
		name = _receipt_names([expense]).get(expense)
		if not name:
			return _refusal("no_receipt", _("This expense has no receipt photo."))
		content = frappe.get_doc("File", name).get_content()
		if isinstance(content, str):
			content = content.encode("latin-1", "ignore")
		shown = rules.receipt_data_url(content)
		if not shown:
			return _refusal("no_receipt", _("This expense has no receipt photo."))
		return {
			"success": True,
			"data": {"data_url": shown[0], "content_type": shown[1], "size": len(content)},
		}
	except Exception:
		frappe.log_error("Expense receipt error", frappe.get_traceback())
		return {"success": False, "message": _("Could not load the receipt photo. Please try again.")}
