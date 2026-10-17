"""
The rules behind the Expenses page (`expenses_api`), with no database and no Frappe.

Every function takes plain values (numbers, text, dicts, dates) and returns plain results, so each rule is tested with
a few lines of data. The database work, the role checks and the transaction are in expenses_api.py.

What an expense is: ONE submitted Journal Entry with two lines. The money goes OUT of an account for the "paid from"
choice (Cash, M-Pesa or Bank) and INTO an existing expense account, the category. The entry carries a marker in its
remark (`[expense:<request id>]`) so the list can tell the entries made on the Expenses page from other entries, and so
a retry of the same request finds the entry it already made.

How it reaches Net profit: Home counts running costs as everything booked to an expense account in the general ledger
(`dashboard_api._get_operating_expenses`), except cost of goods, stock adjustments and stock valuation entries. The debit
line of an expense lands on an expense account that passes that rule (`counts_as_running_cost`), so the Running costs
number rises by the amount and Net profit falls by it (`profit_calc.summarize_profit` takes the running costs off gross
profit). Categories are limited to accounts that pass the rule, so an expense can never be booked where Home would not
count it.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from datetime import date

from murzak_pos.api import overview_calc as calc

# --- the choices ---------------------------------------------------------------------------------------

CASH, MPESA, BANK, OTHER = "cash", "mpesa", "bank", "other"
PAID_FROM = ((CASH, "Cash"), (MPESA, "M-Pesa"), (BANK, "Bank"))
PAID_FROM_KEYS = tuple(key for key, _label in PAID_FROM)
PAID_FROM_LABELS = dict(PAID_FROM)

MAX_NOTE_LENGTH = 140
MAX_SEARCH_LENGTH = 60
MAX_AMOUNT = 100_000_000
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

MAX_RECEIPT_BYTES = 5 * 1024 * 1024
RECEIPT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}

# The account types Home leaves out of Running costs. This is a copy of NON_OPERATING_EXPENSE_TYPES in
# dashboard_api.py; the tests compare the two, so a change there is noticed here.
HOME_EXCLUDED_TYPES = (
	"Cost of Goods Sold",
	"Stock Adjustment",
	"Expenses Included In Valuation",
	"Expenses Included In Asset Valuation",
	"Round Off",
)
# Also never offered as a category: stock received but not billed holds goods that arrived before their bill
CATEGORY_EXCLUDED_TYPES = (*HOME_EXCLUDED_TYPES, "Stock Received But Not Billed")
# Accounts that do the same job under another type are caught by name
_NOT_A_COST_NAME = re.compile(
	r"cost of goods|cost of sales|stock adjustment|stock received but not billed|round(ing)? off",
	re.IGNORECASE,
)


# --- numbers and text ----------------------------------------------------------------------------------


def clean_amount(value) -> tuple[float | None, str | None]:
	"""
	(amount, None) or (None, why). The amount is above 0 and at most MAX_AMOUNT, with at most 2 decimals.
	Text such as "1,200.50" or "KES 1200" is read; "abc", "nan", "-5" and "0" are not.
	"""
	if isinstance(value, bool):
		return None, "bad_amount"
	if isinstance(value, int | float):
		number = float(value)
	else:
		text = re.sub(r"(?i)kes|ksh|\s|,", "", str(value if value is not None else ""))
		if not text:
			return None, "no_amount"
		try:
			number = float(text)
		except ValueError:
			return None, "bad_amount"
	if math.isnan(number) or math.isinf(number):
		return None, "bad_amount"
	if number <= 0:
		return None, "not_above_zero"
	amount = calc.money(number)
	if amount <= 0:
		return None, "not_above_zero"
	if amount > MAX_AMOUNT:
		return None, "too_big"
	return amount, None


def clean_day(value, today: date) -> tuple[date | None, str | None]:
	"""(date, None) or (None, why): the day must be given, look like 2026-10-13 and not be after today (Nairobi)."""
	if value in (None, ""):
		return None, "no_date"
	try:
		day = calc.parse_date(value)
	except (ValueError, TypeError):
		return None, "bad_date"
	if day > today:
		return None, "future_date"
	return day, None


def clean_note(value) -> tuple[str, str | None]:
	"""
	(note, None) or ("", "note_too_long"). Tags are removed, white space is tidied to single spaces, and square
	brackets become round ones so a note can never look like the marker. At most MAX_NOTE_LENGTH characters; a longer
	note is refused, not cut, so nothing the person wrote is lost silently.
	"""
	text = re.sub(r"<[^>]*>", " ", str(value or ""))
	text = text.replace("[", "(").replace("]", ")")
	text = re.sub(r"\s+", " ", text).strip()
	if len(text) > MAX_NOTE_LENGTH:
		return "", "note_too_long"
	return text, None


def clean_search(value) -> str:
	return re.sub(r"\s+", " ", str(value or "")).strip()[:MAX_SEARCH_LENGTH]


def like_pattern(text: str) -> str:
	"""A SQL LIKE pattern that finds `text` anywhere (the characters LIKE treats specially are taken literally)."""
	return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def strip_abbr(name, abbr) -> str:
	"""An account's name without the company tag ERPNext adds: 'Office Rent - AB' becomes 'Office Rent'."""
	text = str(name or "").strip()
	tag = f" - {abbr}" if abbr else ""
	if tag and text.endswith(tag):
		return text[: -len(tag)].strip()
	return text


def page_numbers(page, page_size, total) -> dict:
	"""
	The page to read: `page` (from 1), `page_size`, `pages` and `offset`. A size that is missing or wrong is 20, one
	above 100 is 100; a page below 1 is the first and one past the end is the last, so a list is never blank because
	of a stale page number. An empty list has 1 page.
	"""
	size = _whole(page_size, DEFAULT_PAGE_SIZE)
	if size < 1:
		size = DEFAULT_PAGE_SIZE
	size = min(size, MAX_PAGE_SIZE)
	count = max(_whole(total, 0), 0)
	pages = max(math.ceil(count / size), 1)
	number = min(max(_whole(page, 1), 1), pages)
	return {"page": number, "page_size": size, "pages": pages, "offset": (number - 1) * size}


def _whole(value, default: int) -> int:
	try:
		return int(float(value))
	except (TypeError, ValueError, OverflowError):
		return default


# --- paid from ----------------------------------------------------------------------------------------

_MPESA = re.compile(r"m-?pesa", re.IGNORECASE)
_CASH = re.compile(r"^cash$", re.IGNORECASE)
# Cards and credit are not ways of paying an expense out of the shop's own money
_NOT_PAID_FROM = re.compile(r"^((credit|debit|on) )?(card|credit)$", re.IGNORECASE)


def clean_paid_from(value) -> str | None:
	"""cash, mpesa or bank from what was sent ('Cash', 'M-Pesa', 'mpesa', 'bank'...); None for anything else."""
	text = re.sub(r"[\s_-]", "", str(value or "")).lower()
	return text if text in PAID_FROM_KEYS else None


def classify_mode(name, mode_type) -> str | None:
	"""
	Which "paid from" choice a Mode of Payment stands for, or None when it is not one of the three.
	M-Pesa is found by name; Cash by the name Cash or the type Cash; Bank by the type Bank (a bank draft, a cheque,
	a wire transfer). Card and credit are not offered.
	"""
	text = str(name or "").strip()
	if not text or _NOT_PAID_FROM.match(text):
		return None
	if _MPESA.search(text):
		return MPESA
	if _CASH.match(text) or str(mode_type or "").strip().lower() == "cash":
		return CASH
	if str(mode_type or "").strip().lower() == "bank":
		return BANK
	return None


def account_usable(meta, company, currency) -> bool:
	"""
	Whether money can be taken out of this account: it exists, belongs to the company, is not a group, not disabled,
	not frozen, and is kept in the company's currency (a foreign currency account needs an exchange rate, which an
	expense does not ask for).
	"""
	if not meta:
		return False
	if meta.get("company") != company:
		return False
	if calc.num(meta.get("is_group")) or calc.num(meta.get("disabled")):
		return False
	if str(meta.get("freeze_account") or "").strip().lower() == "yes":
		return False
	account_currency = meta.get("account_currency")
	return not (account_currency and currency and account_currency != currency)


def _mode_rank(candidate: dict, key: str):
	"""Cash and M-Pesa prefer the mode of that exact name; bank prefers a name with 'bank' in it; then alphabetical."""
	name = candidate["name"]
	if key == CASH:
		first = 0 if _CASH.match(name) else 1
	elif key == MPESA:
		first = 0 if re.match(r"^m-?pesa$", name, re.IGNORECASE) else 1
	else:
		first = 0 if "bank" in name.lower() else 1
	return (first, name.lower(), name)


def paid_from_options(modes, accounts, company, currency) -> list[dict]:
	"""
	The three "paid from" choices, each with whether it can be used.

	modes     [{name, type, account}]: the enabled modes of payment with their default account for this company
	accounts  {account name: {company, is_group, disabled, freeze_account, account_currency}}
	Each choice is {key, label, available, reason, mode_of_payment, account}. A choice is available when some mode of
	payment of that kind has an account that can be used (`account_usable`); when several do, the preferred one wins
	(see `_mode_rank`). `reason` is a code for the sentence the page shows when it is not available:
	no_mode (the shop has no mode of payment of that kind), no_account (it has one but no account for this company),
	account_unusable (the account cannot be used).
	"""
	grouped: dict[str, list[dict]] = {key: [] for key in PAID_FROM_KEYS}
	for mode in modes or []:
		key = classify_mode(mode.get("name"), mode.get("type"))
		if key:
			grouped[key].append({"name": str(mode["name"]), "account": mode.get("account")})
	options = []
	for key, label in PAID_FROM:
		candidates = sorted(grouped[key], key=lambda c: _mode_rank(c, key))
		chosen = next(
			(
				c
				for c in candidates
				if c["account"] and account_usable(accounts.get(c["account"]), company, currency)
			),
			None,
		)
		if chosen:
			reason = None
		elif not candidates:
			reason = "no_mode"
		elif not any(c["account"] for c in candidates):
			reason = "no_account"
		else:
			reason = "account_unusable"
		options.append(
			{
				"key": key,
				"label": label,
				"available": chosen is not None,
				"reason": reason,
				"mode_of_payment": chosen["name"] if chosen else None,
				"account": chosen["account"] if chosen else None,
			}
		)
	return options


def option_for(options, key) -> dict | None:
	return next((o for o in options if o["key"] == key), None)


def paid_from_of(account, options, abbr) -> dict:
	"""What a credit account is called on a row: its choice when it is one of the options, else its own name."""
	for option in options:
		if option.get("account") and option["account"] == account:
			return {"key": option["key"], "label": option["label"]}
	return {"key": OTHER, "label": strip_abbr(account, abbr)}


# --- categories ----------------------------------------------------------------------------------------


def counts_as_running_cost(root_type, account_type, account, excluded_accounts=()) -> bool:
	"""
	Home's rule for Running costs, in plain code: the account is an expense account, its type is not one of the
	types Home leaves out, and it is not the company's cost of goods or stock adjustment account. The same rule is in
	SQL in `dashboard_api._get_operating_expenses` and in expenses_api (which the tests compare to this).
	"""
	if root_type != "Expense":
		return False
	if account_type and account_type in HOME_EXCLUDED_TYPES:
		return False
	return account not in set(excluded_accounts or ())


def is_category(account: dict, excluded_accounts=()) -> bool:
	"""
	Whether an existing account may be offered as a category: an enabled, unfrozen leaf account that Home counts as a
	running cost, and not cost of goods, stock adjustment or stock received but not billed (by type, by the company's
	own settings, or by name).
	"""
	if not account:
		return False
	if calc.num(account.get("is_group")) or calc.num(account.get("disabled")):
		return False
	if str(account.get("freeze_account") or "").strip().lower() == "yes":
		return False
	if account.get("account_type") in CATEGORY_EXCLUDED_TYPES:
		return False
	if _NOT_A_COST_NAME.search(str(account.get("account_name") or account.get("name") or "")):
		return False
	return counts_as_running_cost(
		account.get("root_type"), account.get("account_type"), account.get("name"), excluded_accounts
	)


def category_views(accounts, excluded_accounts, abbr) -> list[dict]:
	"""[{id, label, group}] for the accounts that may be categories, by label."""
	out = []
	for account in accounts or []:
		if not is_category(account, excluded_accounts):
			continue
		parent = account.get("parent_account")
		out.append(
			{
				"id": account["name"],
				"label": account.get("account_name") or strip_abbr(account["name"], abbr),
				"group": strip_abbr(parent, abbr) if parent else "",
			}
		)
	out.sort(key=lambda c: (c["label"].lower(), c["id"]))
	return out


# --- the entry and its marker --------------------------------------------------------------------------

_REQUEST_ID = re.compile(r"^[A-Za-z0-9.:-]{8,64}$")
_MARKER = re.compile(r"\[expense(?::([A-Za-z0-9.:-]{1,64}))?\]")
MARKER_START = "[expense"


def clean_request_id(request_id) -> tuple[str | None, bool]:
	"""(request id, valid). No id is allowed (the call then cannot recognise a retry); a bad one is not."""
	text = str(request_id or "").strip()
	if not text:
		return None, True
	return (text, True) if _REQUEST_ID.match(text) else (None, False)


def marker(request_id) -> str:
	return f"[expense:{request_id}]" if request_id else "[expense]"


def marker_pattern(request_id) -> str:
	"""A SQL LIKE pattern that finds the marker of one request in a remark."""
	return like_pattern(marker(request_id))


def remark(note, request_id) -> str:
	"""The remark kept on the entry: the note, then the marker."""
	return f"{note} {marker(request_id)}".strip()


def parse_remark(text) -> dict:
	"""{is_expense, request_id, note}: whether the remark carries the marker, and the note without it."""
	value = str(text or "")
	found = _MARKER.search(value)
	if not found:
		return {"is_expense": False, "request_id": None, "note": re.sub(r"\s+", " ", value).strip()}
	note = re.sub(r"\s+", " ", _MARKER.sub(" ", value)).strip()
	return {"is_expense": True, "request_id": found.group(1), "note": note}


def other_note(user_remark, gl_remarks) -> str:
	"""The note of an entry that was not made here: its own remark, else the ledger's, tidied and cut to 140."""
	text = str(user_remark or "").strip() or str(gl_remarks or "").strip()
	text = re.sub(r"<[^>]*>", " ", text)
	text = re.sub(r"\s+", " ", text).strip()
	if len(text) > MAX_NOTE_LENGTH:
		text = text[: MAX_NOTE_LENGTH - 1].rstrip() + "…"
	return text


def journal_lines(amount, expense_account, paid_account, cost_center=None) -> list[dict]:
	"""
	The two lines of the entry: debit the expense account (the category), credit the account the money came out of.
	The lines carry no party; both are in the company currency.
	"""
	debit = {"account": expense_account, "debit_in_account_currency": amount, "credit_in_account_currency": 0}
	if cost_center:
		debit["cost_center"] = cost_center
	credit = {"account": paid_account, "debit_in_account_currency": 0, "credit_in_account_currency": amount}
	return [debit, credit]


def running_costs_effect(lines, accounts, excluded_accounts=()) -> float:
	"""
	How much Home's Running costs rises when these entry lines are posted: debit minus credit on every line whose account
	counts as a running cost. `accounts` is {account name: {root_type, account_type}}.
	"""
	total = 0.0
	for line in lines:
		meta = accounts.get(line["account"]) or {}
		if counts_as_running_cost(
			meta.get("root_type"), meta.get("account_type"), line["account"], excluded_accounts
		):
			total += calc.num(line.get("debit_in_account_currency")) - calc.num(
				line.get("credit_in_account_currency")
			)
	return calc.money(total)


# --- the receipt photo ----------------------------------------------------------------------------------


def detect_image(content: bytes) -> str | None:
	"""jpg, png, gif or webp from the first bytes of a file; None when it is not one of them."""
	if content[:3] == b"\xff\xd8\xff":
		return "jpg"
	if content[:8] == b"\x89PNG\r\n\x1a\n":
		return "png"
	if content[:6] in (b"GIF87a", b"GIF89a"):
		return "gif"
	if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
		return "webp"
	return None


def clean_receipt(receipt) -> tuple[dict | None, str | None]:
	"""
	(photo, None), (None, None) when there is none, or (None, why).

	`receipt` is a data URL ("data:image/jpeg;base64,...") or an object {content: <data URL or base64>}. The photo is
	{content: bytes, ext, content_type, size}. Why: bad_receipt (cannot be read), receipt_too_big (above 5 MB),
	receipt_not_image (not a jpg, png, gif or webp picture; the first bytes of the file decide, not its name).
	"""
	if receipt in (None, "", {}):
		return None, None
	data = receipt.get("content") if isinstance(receipt, dict) else receipt
	if not isinstance(data, str) or not data.strip():
		return None, "bad_receipt"
	text = data.strip()
	if text.startswith("data:"):
		head, _comma, text = text.partition(",")
		if ";base64" not in head:
			return None, "bad_receipt"
	text = re.sub(r"\s+", "", text)
	# Refuse a huge text before decoding it: base64 is 4 characters for 3 bytes
	if len(text) > (MAX_RECEIPT_BYTES * 4) // 3 + 8:
		return None, "receipt_too_big"
	try:
		content = base64.b64decode(text, validate=True)
	except (binascii.Error, ValueError):
		return None, "bad_receipt"
	if not content:
		return None, "bad_receipt"
	if len(content) > MAX_RECEIPT_BYTES:
		return None, "receipt_too_big"
	ext = detect_image(content)
	if not ext:
		return None, "receipt_not_image"
	return {"content": content, "ext": ext, "content_type": RECEIPT_TYPES[ext], "size": len(content)}, None


def receipt_data_url(content: bytes) -> tuple[str, str] | None:
	"""(data URL, content type) for an image file's bytes; None when they are not an image."""
	ext = detect_image(content)
	if not ext:
		return None
	kind = RECEIPT_TYPES[ext]
	return f"data:{kind};base64,{base64.b64encode(content).decode('ascii')}", kind


# --- the list ------------------------------------------------------------------------------------------

_SOURCES = {
	"Journal Entry": "Journal entry",
	"Purchase Invoice": "Supplier bill",
	"Payment Entry": "Payment",
	"Expense Claim": "Expense claim",
	"Stock Entry": "Stock entry",
	"Sales Invoice": "Sale",
	"POS Invoice": "Sale",
	"Asset": "Asset",
}


def source_label(voucher_type) -> str:
	"""The plain name of the kind of record behind a row that was not made on the Expenses page."""
	return _SOURCES.get(str(voucher_type or ""), "Other entry")


def row_view(
	raw: dict, *, credit_account=None, options=(), has_receipt=False, added_by=None, abbr=None
) -> dict:
	"""
	One list row from a ledger group (voucher_type, voucher_no, account, account_name, posting_date, amount,
	remarks, user_remark).

	An entry made on the Expenses page (its remark carries the marker) shows `kind: "expense"` with its note, what it was
	paid from and any receipt. Any other entry that Home counts shows `kind: "other"` and the plain name of its source.
	`amount` is what Home counts: debit minus credit, so a refund or correction is negative.
	"""
	parsed = parse_remark(raw.get("user_remark"))
	mine = raw.get("voucher_type") == "Journal Entry" and parsed["is_expense"]
	voucher_type, voucher_no = raw.get("voucher_type"), raw.get("voucher_no")
	account = raw.get("account")
	return {
		"id": voucher_no,
		"key": f"{voucher_type}:{voucher_no}:{account}",
		"date": calc.parse_date(raw["posting_date"]).isoformat(),
		"amount": calc.money(raw.get("amount")),
		"category": {
			"id": account,
			"label": raw.get("account_name") or strip_abbr(account, abbr),
		},
		"note": parsed["note"] if mine else other_note(raw.get("user_remark"), raw.get("remarks")),
		"kind": "expense" if mine else "other",
		"source": "Added on the Expenses page" if mine else source_label(voucher_type),
		"paid_from": paid_from_of(credit_account, list(options), abbr) if mine and credit_account else None,
		"has_receipt": bool(has_receipt) if mine else False,
		"added_by": added_by if mine else None,
		"reference_type": voucher_type,
		"reference": voucher_no,
	}


def summary_view(total, count, top: dict | None) -> dict:
	"""The three numbers above the list: spent, how many, and the category that cost most."""
	return {
		"total": calc.money(total),
		"count": int(calc.num(count)),
		"top_category": (
			{"id": top["id"], "label": top["label"], "amount": calc.money(top["amount"])} if top else None
		),
	}
