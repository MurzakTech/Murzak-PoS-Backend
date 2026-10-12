"""
Reading an M-Pesa organisation statement.

Kept free of Frappe imports so it can be tested on its own. The database work (creating bank transactions,
matching them to receipts and sales) lives in payment_matching_api.py.

The statement a business downloads from the M-Pesa portal is a CSV file with one line per payment: a receipt
number, the time it completed, details, money paid in, money withdrawn and the balance. The column headings
differ a little between exports, so each column is found by the words in its heading; lines above the heading row
(a title, the account name, the period) are ignored.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

# What each column of the statement may be called (lower case, punctuation removed)
HEADINGS = {
	"receipt": {
		"receipt no",
		"receipt number",
		"receipt",
		"transaction id",
		"transaction code",
		"mpesa receipt",
	},
	"completed": {"completion time", "completed time", "completed", "date", "transaction date", "time"},
	"details": {"details", "description", "narration", "transaction details"},
	"status": {"transaction status", "status"},
	"paid_in": {"paid in", "paidin", "credit", "amount in"},
	"withdrawn": {"withdrawn", "debit", "paid out", "amount out", "withdrawal"},
}

DATE_FORMATS = (
	"%Y-%m-%d %H:%M:%S",
	"%Y-%m-%d %H:%M",
	"%d-%m-%Y %H:%M:%S",
	"%d-%m-%Y %H:%M",
	"%d/%m/%Y %H:%M:%S",
	"%d/%m/%Y %H:%M",
	"%Y/%m/%d %H:%M:%S",
	"%d-%b-%Y %H:%M:%S",
	"%d %b %Y %H:%M:%S",
	"%Y-%m-%d",
	"%d-%m-%Y",
	"%d/%m/%Y",
	"%d-%b-%Y",
)

MAX_LINES = 20000
MAX_PROBLEMS = 50


def normalize_heading(text) -> str:
	"""A column heading as lower-case words: 'Receipt No.' becomes 'receipt no'."""
	return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()


def parse_amount(value) -> float | None:
	"""A money amount as a number: '1,200.50', 'KES 300', '(45.00)'. Empty is 0, nonsense is None."""
	text = str(value if value is not None else "").strip()
	if not text:
		return 0.0
	negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
	cleaned = re.sub(r"[^0-9.]", "", text)
	if not cleaned or cleaned.count(".") > 1:
		return None
	number = float(cleaned)
	return -number if negative else number


def parse_moment(value) -> date | None:
	"""The date of a statement time, in any of the formats the portal and spreadsheet programs use."""
	text = str(value or "").strip()
	for form in DATE_FORMATS:
		try:
			return datetime.strptime(text, form).date()
		except ValueError:
			continue
	return None


def find_columns(row) -> dict[str, int]:
	"""Which column holds what, read from a heading row. Empty when this is not the heading row."""
	found: dict[str, int] = {}
	for position, heading in enumerate(row):
		words = normalize_heading(heading)
		for key, names in HEADINGS.items():
			if words in names and key not in found:
				found[key] = position
	return found


def is_heading_row(columns: dict[str, int]) -> bool:
	return "receipt" in columns and ("paid_in" in columns or "withdrawn" in columns)


def read_statement(text: str) -> dict:
	"""
	The payments in a statement file.

	Returns `rows` (each with receipt, date, details, deposit, withdrawal), `problems` (plain-words notes on lines
	that could not be read, at most MAX_PROBLEMS), `skipped_not_completed` and `repeated_in_file`.
	A line of a payment that did not complete is left out; a receipt number repeated in the file is kept once.
	"""
	result = {"rows": [], "problems": [], "skipped_not_completed": 0, "repeated_in_file": 0, "columns": []}

	def problem(line_number: int, message: str) -> None:
		if len(result["problems"]) < MAX_PROBLEMS:
			result["problems"].append(f"Line {line_number}: {message}")

	text = (text or "").lstrip("﻿")
	if not text.strip():
		result["problems"].append("The statement is empty.")
		return result
	try:
		dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
	except csv.Error:
		dialect = csv.excel
	columns: dict[str, int] = {}
	seen: set[str] = set()
	lines = 0
	for line_number, row in enumerate(csv.reader(io.StringIO(text), dialect), start=1):
		if not any(str(cell).strip() for cell in row):
			continue
		lines += 1
		if lines > MAX_LINES:
			result["problems"].append(f"Only the first {MAX_LINES} lines were read.")
			break
		if not columns:
			candidate = find_columns(row)
			if is_heading_row(candidate):
				columns = candidate
				result["columns"] = sorted(candidate)
			continue

		def cell(key: str) -> str:
			position = columns.get(key)
			return str(row[position]).strip() if position is not None and position < len(row) else ""

		receipt = cell("receipt").upper()
		if not receipt:
			problem(line_number, "there is no receipt number.")
			continue
		status = cell("status")
		if status and not status.lower().startswith("complete"):
			result["skipped_not_completed"] += 1
			continue
		paid_in, withdrawn = parse_amount(cell("paid_in")), parse_amount(cell("withdrawn"))
		if paid_in is None or withdrawn is None:
			problem(line_number, f"the amount of {receipt} could not be read.")
			continue
		deposit, withdrawal = abs(paid_in), abs(withdrawn)
		if not deposit and not withdrawal:
			problem(line_number, f"{receipt} has no amount.")
			continue
		moment = parse_moment(cell("completed"))
		if moment is None:
			problem(line_number, f"the time of {receipt} could not be read.")
			continue
		if receipt in seen:
			result["repeated_in_file"] += 1
			continue
		seen.add(receipt)
		result["rows"].append(
			{
				"receipt": receipt,
				"date": moment,
				"details": cell("details"),
				"deposit": deposit,
				"withdrawal": withdrawal,
			}
		)
	if not columns:
		result["problems"].append(
			"The heading row was not found. The statement needs columns for the receipt number and for money paid in or withdrawn."
		)
	return result
