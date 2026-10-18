# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Expenses page calls: the form's choices, adding an expense, the list and the receipt photo. No database
is needed: every Frappe call is mocked, and the order of the steps (what is checked first, what is rolled back, what is
committed) is checked through the mocks.

The list tests run the real SQL of the list, and the real SQL of Home's Running costs (`_get_operating_expenses`), on a
small in-memory SQLite copy of the ledger tables, to show that the two add up to the same number.
"""

import base64
import re
import sqlite3
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import dashboard_api
from murzak_pos.api import expenses_api as api
from murzak_pos.api import expenses_calc as rules

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)  # 12:00 in Nairobi, the 17th
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def photo(content=PNG):
	return "data:image/png;base64," + base64.b64encode(content).decode()


class Line:
	def __init__(self, account, debit=0, credit=0):
		self.account = account
		self.debit_in_account_currency = debit
		self.credit_in_account_currency = credit


class FakeEntry:
	"""A Journal Entry the way the code uses one."""

	def __init__(self, case, values):
		self.case = case
		self.doctype = "Journal Entry"
		self.name = "ACC-JV-2026-00042"
		self.docstatus = 0
		self.owner = "owner@acme.test"
		self.company = values.get("company")
		self.posting_date = values.get("posting_date")
		self.user_remark = values.get("user_remark")
		self.values = values
		self.flags = frappe._dict()
		self.accounts = [
			Line(r["account"], r.get("debit_in_account_currency", 0), r.get("credit_in_account_currency", 0))
			for r in values.get("accounts", [])
		]

	def get(self, key, default=None):
		return getattr(self, key, default)

	def insert(self, **kwargs):
		self.case.calls.append("insert Journal Entry")
		self.case.inserted.append(self)
		if self.case.fail_on == "insert":
			raise frappe.ValidationError("<b>Posting date</b> is outside any fiscal year.")

	def submit(self):
		self.case.calls.append("submit Journal Entry")
		if self.case.fail_on == "submit":
			raise RuntimeError("database exploded: secret internals")
		self.docstatus = 1


class FakeFile:
	def __init__(self, case, values):
		self.case = case
		self.values = values
		self.flags = frappe._dict()
		self.name = "FILE-0001"

	def insert(self, **kwargs):
		self.case.calls.append("insert File")
		self.case.files.append(self.values)
		if self.case.fail_on == "file":
			raise frappe.ValidationError("File type not allowed")

	def get_content(self):
		return self.case.file_content


class ApiCase(unittest.TestCase):
	tier = "owner"

	def setUp(self):
		self.calls = []
		self.inserted = []
		self.files = []
		self.docs_made = []
		self.fail_on = None
		self.file_content = PNG
		self.roles = ["System Manager"]
		self.earlier = []  # names of earlier entries for the request
		self.receipt_files = []  # [{name, attached_to_name}]
		self.credits = []  # [{parent, account}]
		self.entry_head = {"docstatus": 1, "user_remark": "Rent [expense:req-12345]"}
		self.db = MagicMock()
		self.db.commit.side_effect = lambda: self.calls.append("commit")
		self.db.rollback.side_effect = lambda: self.calls.append("rollback")
		self.db.get_value.side_effect = self.get_value
		self.logged = MagicMock()
		self.access = MagicMock()
		self.accounts = [
			self.account("Office Rent - A", "Office Rent"),
			self.account("Airtime - A", "Airtime", account_type=None),
			self.account("Cost of Goods Sold - A", "Cost of Goods Sold", account_type="Cost of Goods Sold"),
			self.account("Stock Cost - A", "Stock Cost"),  # the company's default expense account
			self.account("Stock Adjustment - A", "Stock Adjustment", account_type="Stock Adjustment"),
		]
		self.modes = [
			{"name": "Cash", "type": "Cash"},
			{"name": "M-Pesa", "type": "Bank"},
			{"name": "Bank Draft", "type": "Bank"},
			{"name": "Credit Card", "type": "Bank"},
		]
		self.mode_accounts = {"Cash": "Cash - A", "M-Pesa": "M-Pesa - A", "Bank Draft": "KCB - A"}
		self.metas = {
			a: {
				"name": a,
				"company": "Acme",
				"is_group": 0,
				"disabled": 0,
				"freeze_account": "No",
				"account_currency": "KES",
			}
			for a in ("Cash - A", "M-Pesa - A", "KCB - A")
		}
		stack = [
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "session", frappe._dict(user="owner@acme.test"), create=True),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(self.roles)),
			patch.object(frappe, "log_error", self.logged, create=True),
			patch.object(frappe, "get_doc", create=True, side_effect=self.get_doc),
			patch.object(frappe, "get_all", create=True, side_effect=self.get_all),
			patch.object(frappe, "get_cached_value", create=True, side_effect=self.cached_value),
			patch.object(api.role_tiers, "get_tier", side_effect=lambda: self.tier),
			patch.object(api, "ensure_record_access", self.access),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "_now_utc", return_value=NOW),
		]
		self.patched = [p.start() for p in stack]
		self.addCleanup(lambda: [p.stop() for p in reversed(stack)])

	@staticmethod
	def account(name, label, **extra):
		base = {
			"name": name,
			"account_name": label,
			"account_type": "Expense Account",
			"parent_account": "Indirect Expenses - A",
			"root_type": "Expense",
			"is_group": 0,
			"disabled": 0,
		}
		base.update(extra)
		return base

	# --- the fakes -------------------------------------------------------------------------------

	def cached_value(self, doctype, name, fields):
		# currency, abbr, cost centre, default expense account, stock adjustment account, stock received but not billed
		return [
			"KES",
			"A",
			"Main - A",
			"Stock Cost - A",
			"Stock Adjustment - A",
			"Stock Received But Not Billed - A",
		]

	def get_value(self, doctype, name, field=None, **kwargs):
		if doctype == "Account":
			return {"Office Rent - A": "Office Rent", "Airtime - A": "Airtime"}.get(name)
		if doctype == "Journal Entry":
			return frappe._dict(self.entry_head) if self.entry_head else None
		return name

	def get_all(self, doctype, filters=None, fields=None, **kwargs):
		self.calls.append(f"get_all {doctype}")
		if doctype == "Account":
			if filters and "name" in filters:
				return [self.metas[n] | {"name": n} for n in filters["name"][1] if n in self.metas]
			return list(self.accounts)
		if doctype == "Mode of Payment":
			return list(self.modes)
		if doctype == "Mode of Payment Account":
			return [{"parent": m, "default_account": a} for m, a in self.mode_accounts.items()]
		if doctype == "Journal Entry":
			return list(self.earlier)
		if doctype == "File":
			return list(self.receipt_files)
		if doctype == "Journal Entry Account":
			return list(self.credits)
		if doctype == "User":
			return [{"name": "owner@acme.test", "full_name": "Amina Owner"}]
		return []

	def get_doc(self, *args):
		spec = args[0]
		if isinstance(spec, dict):
			self.docs_made.append(spec["doctype"])
			if spec["doctype"] == "Journal Entry":
				return FakeEntry(self, spec)
			if spec["doctype"] == "File":
				return FakeFile(self, spec)
			raise AssertionError(f"nothing but a Journal Entry and a File may be made, not {spec['doctype']}")
		if spec == "Journal Entry":
			entry = FakeEntry(
				self,
				{
					"company": "Acme",
					"posting_date": date(2026, 10, 16),
					"user_remark": "Rent [expense:req-12345]",
					"accounts": [
						{"account": "Office Rent - A", "debit_in_account_currency": 1500},
						{"account": "Cash - A", "credit_in_account_currency": 1500},
					],
				},
			)
			entry.name = args[1]
			entry.docstatus = 1
			return entry
		if spec == "File":
			return FakeFile(self, {})
		raise AssertionError(spec)

	def untouched(self):
		"""Nothing was read, written, locked or committed."""
		self.db.get_value.assert_not_called()
		self.db.sql.assert_not_called()
		self.db.commit.assert_not_called()
		self.db.rollback.assert_not_called()
		self.assertEqual(self.docs_made, [])
		self.assertEqual([c for c in self.calls if c.startswith("get_all")], [])
		self.patched[6].assert_not_called()  # get_cached_value
		self.patched[9].assert_not_called()  # resolve_company

	def good(self, **extra):
		args = {
			"amount": 1500,
			"category": "Office Rent - A",
			"paid_from": "cash",
			"date": "2026-10-16",
			"note": "Rent for October",
			"request_id": "req-12345",
		}
		args.update(extra)
		return args


# --- who may use these calls -----------------------------------------------------------------------


class TestWhoMayUseTheExpensesCalls(ApiCase):
	CALLS = (
		lambda: api.get_expense_options(),
		lambda: api.create_expense(amount=1, category="X", paid_from="cash", date="2026-10-16"),
		lambda: api.list_expenses(),
		lambda: api.get_receipt("ACC-JV-2026-00042"),
	)

	def test_a_cashier_is_refused_before_anything_is_read(self):
		self.tier = "cashier"
		for call in self.CALLS:
			with self.assertRaises(frappe.PermissionError):
				call()
			self.untouched()
			self.access.assert_not_called()

	def test_a_guest_is_asked_to_sign_in(self):
		self.tier = None
		for call in self.CALLS:
			with self.assertRaises(frappe.AuthenticationError):
				call()
			self.untouched()

	def test_an_owner_and_a_manager_are_let_in(self):
		for tier in ("owner", "manager"):
			self.tier = tier
			self.assertTrue(api.get_expense_options()["success"])


# --- the form's choices ------------------------------------------------------------------------------


class TestExpenseOptions(ApiCase):
	def test_categories_paid_from_and_today(self):
		out = api.get_expense_options()
		self.assertTrue(out["success"])
		data = out["data"]
		self.assertEqual(data["today"], "2026-10-17")
		self.assertEqual(data["company"], "Acme")
		self.assertEqual([c["label"] for c in data["categories"]], ["Airtime", "Office Rent"])
		self.assertEqual(
			data["categories"][1],
			{"id": "Office Rent - A", "label": "Office Rent", "group": "Indirect Expenses"},
		)
		self.assertEqual([p["key"] for p in data["paid_from"]], ["cash", "mpesa", "bank"])
		self.assertTrue(all(p["available"] for p in data["paid_from"]))
		self.assertEqual(data["limits"]["note_length"], 140)
		self.assertEqual(data["limits"]["receipt_bytes"], 5 * 1024 * 1024)

	def test_cost_of_goods_stock_adjustment_and_the_default_expense_account_are_not_offered(self):
		ids = {c["id"] for c in api.get_expense_options()["data"]["categories"]}
		self.assertNotIn("Cost of Goods Sold - A", ids)
		self.assertNotIn("Stock Adjustment - A", ids)
		self.assertNotIn("Stock Cost - A", ids)

	def test_a_missing_account_is_shown_as_not_available_with_a_reason(self):
		del self.mode_accounts["M-Pesa"]
		options = {p["key"]: p for p in api.get_expense_options()["data"]["paid_from"]}
		self.assertFalse(options["mpesa"]["available"])
		self.assertEqual(options["mpesa"]["reason"], "no_account")
		self.assertTrue(options["cash"]["available"])

	def test_no_account_name_is_given_out(self):
		for option in api.get_expense_options()["data"]["paid_from"]:
			self.assertNotIn("account", option)

	def test_the_options_never_write(self):
		api.get_expense_options()
		self.db.commit.assert_not_called()
		self.assertEqual(self.docs_made, [])

	def test_a_failure_gives_a_plain_message(self):
		with patch.object(api, "_categories", side_effect=RuntimeError("boom")):
			out = api.get_expense_options()
		self.assertFalse(out["success"])
		self.assertNotIn("boom", out["message"])
		self.logged.assert_called_once()


# --- adding an expense -------------------------------------------------------------------------------


class TestCreateExpense(ApiCase):
	def test_one_submitted_journal_entry_is_made_and_committed_once(self):
		out = api.create_expense(**self.good())
		self.assertTrue(out["success"], out)
		self.assertEqual(self.docs_made, ["Journal Entry"])
		self.assertEqual(
			[c for c in self.calls if not c.startswith("get_all")],
			["insert Journal Entry", "submit Journal Entry", "commit"],
		)
		self.assertEqual(self.db.commit.call_count, 1)
		self.db.rollback.assert_not_called()

	def test_the_entry_debits_the_category_and_credits_the_cash_account(self):
		api.create_expense(**self.good())
		entry = self.inserted[0].values
		self.assertEqual(entry["company"], "Acme")
		self.assertEqual(entry["voucher_type"], "Journal Entry")
		self.assertEqual(entry["posting_date"], "2026-10-16")
		self.assertEqual(entry["user_remark"], "Rent for October [expense:req-12345]")
		self.assertEqual(
			entry["accounts"],
			[
				{
					"account": "Office Rent - A",
					"debit_in_account_currency": 1500.0,
					"credit_in_account_currency": 0,
					"cost_center": "Main - A",
				},
				{"account": "Cash - A", "debit_in_account_currency": 0, "credit_in_account_currency": 1500.0},
			],
		)
		debit = sum(r["debit_in_account_currency"] for r in entry["accounts"])
		credit = sum(r["credit_in_account_currency"] for r in entry["accounts"])
		self.assertEqual(debit, credit)

	def test_each_paid_from_choice_credits_its_own_account(self):
		for choice, account in (("cash", "Cash - A"), ("mpesa", "M-Pesa - A"), ("bank", "KCB - A")):
			self.inserted.clear()
			api.create_expense(**self.good(paid_from=choice, request_id=None))
			self.assertEqual(self.inserted[0].values["accounts"][1]["account"], account)

	def test_the_labels_a_person_sees_are_read_too(self):
		for label in ("Cash", "M-Pesa", "Bank"):
			self.assertTrue(api.create_expense(**self.good(paid_from=label))["success"])

	def test_the_reply_is_the_row_the_list_shows(self):
		out = api.create_expense(**self.good())
		row = out["data"]["expense"]
		self.assertFalse(out["data"]["repeat"])
		self.assertEqual(row["id"], "ACC-JV-2026-00042")
		self.assertEqual(row["kind"], "expense")
		self.assertEqual(row["amount"], 1500.0)
		self.assertEqual(row["date"], "2026-10-16")
		self.assertEqual(row["note"], "Rent for October")
		self.assertEqual(row["category"], {"id": "Office Rent - A", "label": "Office Rent"})
		self.assertEqual(row["paid_from"], {"key": "cash", "label": "Cash"})
		self.assertFalse(row["has_receipt"])
		self.assertEqual(row["added_by"], "Amina Owner")

	def test_an_amount_typed_with_a_comma_is_read(self):
		api.create_expense(**self.good(amount="1,500.50"))
		self.assertEqual(self.inserted[0].values["accounts"][0]["debit_in_account_currency"], 1500.5)

	def test_today_is_allowed(self):
		self.assertTrue(api.create_expense(**self.good(date="2026-10-17"))["success"])

	def test_the_note_is_optional(self):
		out = api.create_expense(**self.good(note=None))
		self.assertTrue(out["success"])
		self.assertEqual(self.inserted[0].values["user_remark"], "[expense:req-12345]")

	def test_no_account_is_ever_made_or_changed(self):
		api.create_expense(**self.good(receipt=photo()))
		self.assertEqual(set(self.docs_made), {"Journal Entry", "File"})
		self.db.set_value.assert_not_called()
		self.db.delete.assert_not_called()

	def test_the_entry_is_made_with_the_callers_rights_already_checked(self):
		api.create_expense(**self.good())
		self.assertTrue(self.inserted[0].flags.ignore_permissions)

	def test_a_manager_may_add_an_expense(self):
		self.tier = "manager"
		self.assertTrue(api.create_expense(**self.good())["success"])

	def test_the_company_asked_for_is_the_one_used(self):
		api.create_expense(**self.good(company="Acme"))
		self.patched[9].assert_called_once_with("Acme")


class TestReceiptPhoto(ApiCase):
	def test_the_photo_is_attached_to_the_entry_inside_the_same_transaction(self):
		out = api.create_expense(**self.good(receipt=photo()))
		self.assertTrue(out["success"])
		self.assertTrue(out["data"]["expense"]["has_receipt"])
		self.assertEqual(
			[c for c in self.calls if not c.startswith("get_all")],
			["insert Journal Entry", "submit Journal Entry", "insert File", "commit"],
		)
		attached = self.files[0]
		self.assertEqual(attached["attached_to_doctype"], "Journal Entry")
		self.assertEqual(attached["attached_to_name"], "ACC-JV-2026-00042")
		self.assertEqual(attached["is_private"], 1)
		self.assertEqual(attached["content"], PNG)
		self.assertEqual(attached["file_name"], "receipt-ACC-JV-2026-00042.png")

	def test_the_file_name_never_comes_from_the_caller(self):
		api.create_expense(**self.good(receipt={"filename": "../../etc/passwd.png", "content": photo()}))
		self.assertEqual(self.files[0]["file_name"], "receipt-ACC-JV-2026-00042.png")

	def test_a_photo_over_five_megabytes_is_refused_and_nothing_is_made(self):
		big = PNG + b"\x00" * (5 * 1024 * 1024)
		out = api.create_expense(**self.good(receipt=photo(big)))
		self.assertEqual(
			(out["success"], out["error_type"], out["field"]), (False, "receipt_too_big", "receipt")
		)
		self.assertIn("5 MB", out["message"])
		self.untouched()

	def test_a_file_that_is_not_a_picture_is_refused(self):
		out = api.create_expense(**self.good(receipt=photo(b"%PDF-1.4 not a picture")))
		self.assertEqual((out["error_type"], out["field"]), ("receipt_not_image", "receipt"))
		self.untouched()

	def test_a_photo_that_cannot_be_read_is_refused(self):
		out = api.create_expense(**self.good(receipt="data:image/png;base64,@@@@"))
		self.assertEqual(out["error_type"], "bad_receipt")
		self.untouched()

	def test_a_receipt_sent_as_json_text_is_read(self):
		import json

		self.assertTrue(api.create_expense(**self.good(receipt=json.dumps({"content": photo()})))["success"])
		self.assertEqual(len(self.files), 1)

	def test_if_the_photo_cannot_be_saved_the_whole_expense_is_rolled_back(self):
		self.fail_on = "file"
		out = api.create_expense(**self.good(receipt=photo()))
		self.assertFalse(out["success"])
		self.assertIn("rollback", self.calls)
		self.assertNotIn("commit", self.calls)
		self.assertIn("not saved", out["message"])

	def test_the_receipt_can_be_read_back(self):
		self.receipt_files = [{"name": "FILE-0001", "attached_to_name": "ACC-JV-2026-00042"}]
		out = api.get_receipt("ACC-JV-2026-00042")
		self.assertTrue(out["success"], out)
		self.assertEqual(out["data"]["content_type"], "image/png")
		self.assertEqual(out["data"]["data_url"], photo())
		self.access.assert_called_once_with("Journal Entry", "ACC-JV-2026-00042")

	def test_an_expense_without_a_photo(self):
		out = api.get_receipt("ACC-JV-2026-00042")
		self.assertEqual((out["success"], out["error_type"]), (False, "no_receipt"))

	def test_an_entry_that_was_not_made_on_this_page_has_no_receipt_to_show(self):
		self.entry_head = {"docstatus": 1, "user_remark": "Year end adjustment"}
		self.receipt_files = [{"name": "FILE-0001", "attached_to_name": "ACC-JV-2026-00042"}]
		self.assertEqual(api.get_receipt("ACC-JV-2026-00042")["error_type"], "not_found")

	def test_a_missing_entry_and_a_missing_number(self):
		self.entry_head = None
		self.assertEqual(api.get_receipt("NOPE")["error_type"], "not_found")
		self.assertEqual(api.get_receipt("")["error_type"], "not_found")
		self.assertEqual(api.get_receipt(None)["error_type"], "not_found")

	def test_a_stored_file_that_is_not_a_picture_is_not_sent(self):
		self.receipt_files = [{"name": "FILE-0001", "attached_to_name": "ACC-JV-2026-00042"}]
		self.file_content = b"<html>nope</html>"
		self.assertEqual(api.get_receipt("ACC-JV-2026-00042")["error_type"], "no_receipt")


class TestCreateExpenseRefusals(ApiCase):
	def refused(self, code, field=None, **args):
		out = api.create_expense(**self.good(**args))
		self.assertFalse(out["success"], out)
		self.assertEqual(out["error_type"], code)
		if field:
			self.assertEqual(out["field"], field)
		self.assertTrue(out["message"].endswith((".", "?")))
		return out

	def test_the_amount_must_be_above_zero(self):
		for bad in (0, -5, "0"):
			self.refused("not_above_zero", "amount", amount=bad)
		self.untouched()

	def test_the_amount_must_be_given_and_be_a_number(self):
		self.refused("no_amount", "amount", amount="")
		self.refused("bad_amount", "amount", amount="lots")
		self.untouched()

	def test_the_date_must_not_be_in_the_future(self):
		out = self.refused("future_date", "date", date="2026-10-18")
		self.assertEqual(out["message"], "The date cannot be in the future.")
		self.untouched()

	def test_the_date_is_needed_and_must_be_a_date(self):
		self.refused("no_date", "date", date=None)
		self.refused("bad_date", "date", date="yesterday")
		self.untouched()

	def test_the_note_is_at_most_140_characters(self):
		self.assertTrue(api.create_expense(**self.good(note="a" * 140))["success"])
		self.calls.clear()
		self.refused("note_too_long", "note", note="a" * 141)

	def test_a_category_and_a_paid_from_are_needed(self):
		self.refused("no_category", "category", category="")
		self.refused("no_paid_from", "paid_from", paid_from="")
		self.refused("bad_paid_from", "paid_from", paid_from="card")
		self.untouched()

	def test_a_bad_request_id(self):
		self.refused("bad_request_id", request_id="x")
		self.untouched()

	def test_a_category_that_is_not_an_existing_expense_account_is_refused(self):
		for category in (
			"Cost of Goods Sold - A",
			"Stock Adjustment - A",
			"Stock Cost - A",
			"Cash - A",
			"Made Up - A",
		):
			out = self.refused("bad_category", "category", category=category)
			self.assertIn("category", out["message"])
		self.assertEqual(self.inserted, [])
		self.db.commit.assert_not_called()

	def test_a_shop_with_no_mpesa_account_is_told_in_plain_words(self):
		del self.mode_accounts["M-Pesa"]
		out = self.refused("no_account", "paid_from", paid_from="mpesa")
		self.assertEqual(
			out["message"],
			"This shop has no M-Pesa account set up, so an expense cannot be paid from M-Pesa. Choose another way, or ask your accountant to link an account to it.",
		)
		self.assertEqual(self.inserted, [])
		self.db.commit.assert_not_called()

	def test_a_shop_with_no_such_mode_of_payment_at_all(self):
		self.modes = [m for m in self.modes if m["name"] != "Bank Draft"]
		self.refused("no_account", "paid_from", paid_from="bank")
		self.assertEqual(self.inserted, [])

	def test_an_account_that_is_frozen(self):
		self.metas["Cash - A"]["freeze_account"] = "Yes"
		out = self.refused("account_unusable", "paid_from", paid_from="cash")
		self.assertIn("Cash account cannot be used", out["message"])
		self.assertEqual(self.inserted, [])


class TestCreateExpenseRollsBackEverything(ApiCase):
	def test_a_failure_while_submitting(self):
		self.fail_on = "submit"
		out = api.create_expense(**self.good())
		self.assertFalse(out["success"])
		self.assertEqual(out["error_type"], "server_error")
		self.assertIn("rollback", self.calls)
		self.assertNotIn("commit", self.calls)
		self.logged.assert_called_once()

	def test_the_message_does_not_leak_the_internals(self):
		self.fail_on = "submit"
		out = api.create_expense(**self.good())
		self.assertNotIn("exploded", out["message"])
		self.assertEqual(
			out["message"], "The expense was not saved, so nothing was changed. Please try again."
		)

	def test_a_refusal_from_the_books_is_passed_on_in_plain_words(self):
		self.fail_on = "insert"
		out = api.create_expense(**self.good())
		self.assertEqual(
			out["message"],
			"The expense was not saved, so nothing was changed. Posting date is outside any fiscal year.",
		)
		self.assertIn("rollback", self.calls)
		self.assertNotIn("commit", self.calls)


class TestCreateExpenseRepeat(ApiCase):
	def test_a_repeat_returns_the_first_expense_and_changes_nothing(self):
		self.earlier = ["ACC-JV-2026-00042"]
		out = api.create_expense(**self.good())
		self.assertTrue(out["success"])
		self.assertTrue(out["data"]["repeat"])
		self.assertEqual(out["data"]["expense"]["id"], "ACC-JV-2026-00042")
		self.assertEqual(out["data"]["expense"]["amount"], 1500.0)
		self.assertEqual(self.docs_made, [])
		self.assertEqual(self.inserted, [])
		self.db.commit.assert_not_called()

	def test_the_business_is_locked_while_the_request_is_checked(self):
		self.earlier = ["ACC-JV-2026-00042"]
		api.create_expense(**self.good())
		self.db.get_value.assert_any_call("Company", "Acme", "name", for_update=True)

	def test_the_earlier_entry_is_looked_up_by_the_marker_in_the_remark(self):
		with patch.object(api.frappe, "get_all", create=True, wraps=frappe.get_all) as spy:
			api.create_expense(**self.good(request_id="req-12345"))
		first = spy.call_args_list[0]
		self.assertEqual(first.args[0], "Journal Entry")
		self.assertEqual(first.kwargs["filters"]["user_remark"], ["like", "%[expense:req-12345]%"])
		self.assertEqual(first.kwargs["filters"]["docstatus"], 1)
		self.assertEqual(first.kwargs["filters"]["company"], "Acme")

	def test_without_a_request_id_nothing_is_looked_up_or_locked(self):
		api.create_expense(**self.good(request_id=None))
		self.assertNotIn("get_all Journal Entry", self.calls)
		for call in self.db.get_value.call_args_list:
			self.assertNotIn("for_update", call.kwargs)
		self.assertEqual(self.inserted[0].values["user_remark"], "Rent for October [expense]")

	def test_a_new_request_makes_a_new_entry(self):
		api.create_expense(**self.good(request_id="req-aaaaaaaa"))
		api.create_expense(**self.good(request_id="req-bbbbbbbb"))
		self.assertEqual(len(self.inserted), 2)


# --- the list ----------------------------------------------------------------------------------------


class Ledger:
	"""A small SQLite copy of the three tables the list reads, and a way to run the code's SQL on it."""

	def __init__(self):
		self.conn = sqlite3.connect(":memory:")
		self.conn.row_factory = sqlite3.Row
		self.conn.create_function(
			"SUBSTRING_INDEX",
			3,
			lambda text, delimiter, count: (text or "").split(delimiter)[0] if count == 1 else text,
		)
		cur = self.conn.cursor()
		cur.execute(
			"CREATE TABLE `tabAccount` (name TEXT, account_name TEXT, root_type TEXT, account_type TEXT)"
		)
		cur.execute(
			"CREATE TABLE `tabGL Entry` (voucher_type TEXT, voucher_no TEXT, account TEXT, posting_date TEXT, "
			"debit REAL, credit REAL, company TEXT, is_cancelled INTEGER, remarks TEXT, creation TEXT)"
		)
		cur.execute("CREATE TABLE `tabJournal Entry` (name TEXT, user_remark TEXT, owner TEXT)")
		self.sequence = 0

	def account(self, name, label, root, kind=None):
		self.conn.execute("INSERT INTO `tabAccount` VALUES (?, ?, ?, ?)", (name, label, root, kind))

	def entry(
		self,
		voucher_type,
		voucher_no,
		lines,
		day,
		remark=None,
		company="Acme",
		cancelled=0,
		owner="owner@acme.test",
	):
		self.sequence += 1
		if voucher_type == "Journal Entry":
			self.conn.execute("INSERT INTO `tabJournal Entry` VALUES (?, ?, ?)", (voucher_no, remark, owner))
		for account, debit, credit in lines:
			self.conn.execute(
				"INSERT INTO `tabGL Entry` VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
				(
					voucher_type,
					voucher_no,
					account,
					day,
					debit,
					credit,
					company,
					cancelled,
					f"Note: {remark}" if remark else None,
					f"2026-10-17 08:{self.sequence:02d}:00",
				),
			)

	def run(self, sql, values=None, as_dict=False, **_ignored):
		values = values or {}
		args = []

		def place(match):
			value = values[match.group(1)]
			if isinstance(value, tuple | list):
				args.extend(value)
				return "(" + ",".join("?" * len(value)) + ")"
			args.append(value.isoformat() if isinstance(value, date) else value)
			return "?"

		query = re.sub(r"%\((\w+)\)s", place, sql)
		return [dict(row) for row in self.conn.execute(query, args).fetchall()]


def seeded_ledger():
	"""
	Made-up books for the shop. What Home counts as running costs in the last 30 days (18 Sep to 17 Oct) is
	1500 + 300 + 4000 + 700 - 200 = 6300; everything else below must be left out.
	"""
	ledger = Ledger()
	for name, label, root, kind in (
		("Office Rent - A", "Office Rent", "Expense", "Expense Account"),
		("Airtime - A", "Airtime", "Expense", None),
		("Electricity - A", "Electricity", "Expense", "Expense Account"),
		("Cost of Goods Sold - A", "Cost of Goods Sold", "Expense", "Cost of Goods Sold"),
		("Stock Cost - A", "Stock Cost", "Expense", "Expense Account"),
		("Stock Adjustment - A", "Stock Adjustment", "Expense", "Stock Adjustment"),
		("Rounding - A", "Rounding", "Expense", "Round Off"),
		("Cash - A", "Cash", "Asset", "Cash"),
		("M-Pesa - A", "M-Pesa", "Asset", "Bank"),
		("Sales - A", "Sales", "Income", None),
		("Rent - B", "Rent", "Expense", "Expense Account"),
	):
		ledger.account(name, label, root, kind)
	ledger.entry(
		"Journal Entry",
		"ACC-JV-1",
		[("Office Rent - A", 1500, 0), ("Cash - A", 0, 1500)],
		"2026-10-16",
		"Rent for October [expense:req-aaaaaaaa]",
	)
	ledger.entry(
		"Journal Entry",
		"ACC-JV-2",
		[("Airtime - A", 300, 0), ("M-Pesa - A", 0, 300)],
		"2026-10-17",
		"[expense:req-bbbbbbbb]",
	)
	ledger.entry("Purchase Invoice", "ACC-PINV-1", [("Electricity - A", 4000, 0)], "2026-10-10", None)
	ledger.entry(
		"Journal Entry",
		"ACC-JV-3",
		[("Office Rent - A", 700, 0), ("Cash - A", 0, 700)],
		"2026-10-12",
		"Rent top up by the accountant",
	)
	ledger.entry(
		"Journal Entry",
		"ACC-JV-4",
		[("Office Rent - A", 0, 200), ("Cash - A", 200, 0)],
		"2026-10-14",
		"Rent refund",
	)
	# left out by Home's rule
	ledger.entry(
		"Sales Invoice",
		"ACC-SINV-1",
		[("Cost of Goods Sold - A", 9000, 0), ("Sales - A", 0, 12000)],
		"2026-10-15",
	)
	ledger.entry(
		"Journal Entry",
		"ACC-JV-5",
		[("Stock Cost - A", 800, 0), ("Cash - A", 0, 800)],
		"2026-10-15",
		"default expense account",
	)
	ledger.entry("Stock Entry", "MAT-STE-1", [("Stock Adjustment - A", 250, 0)], "2026-10-15")
	ledger.entry("Journal Entry", "ACC-JV-6", [("Rounding - A", 1, 0)], "2026-10-15", "rounding")
	ledger.entry(
		"Journal Entry", "ACC-JV-7", [("Office Rent - A", 5000, 0)], "2026-10-15", "cancelled", cancelled=1
	)
	ledger.entry(
		"Journal Entry", "ACC-JV-8", [("Rent - B", 999, 0)], "2026-10-15", "another business", company="Other"
	)
	ledger.entry("Journal Entry", "ACC-JV-9", [("Office Rent - A", 12345, 0)], "2026-08-01", "too early")
	ledger.entry(
		"Journal Entry",
		"ACC-JV-10",
		[("Airtime - A", 100, 0), ("Airtime - A", 0, 100)],
		"2026-10-13",
		"nets to nothing",
	)
	return ledger


class ListCase(ApiCase):
	def setUp(self):
		super().setUp()
		self.ledger = seeded_ledger()
		self.db.sql.side_effect = self.ledger.run
		self.credits = [
			{"parent": "ACC-JV-1", "account": "Cash - A"},
			{"parent": "ACC-JV-2", "account": "M-Pesa - A"},
		]
		self.receipt_files = [{"name": "FILE-0001", "attached_to_name": "ACC-JV-1"}]
		self.cached_value = lambda *a, **k: [
			"KES",
			"A",
			"Main - A",
			"Stock Cost - A",
			"Stock Adjustment - A",
			None,
		]
		self.patched[6].side_effect = self.cached_value

	def rows(self, **args):
		out = api.list_expenses(**args)
		self.assertTrue(out["success"], out)
		return out["data"]


class TestTheListMatchesHomesRunningCosts(ListCase):
	def home(self, start, end):
		"""Home's own number, from its own query (dashboard_api._get_operating_expenses), run on the same books."""
		with patch.object(dashboard_api.frappe, "db", create=True) as db:
			db.sql.side_effect = lambda query, values=None, as_dict=False, **k: self.ledger.run(
				re.sub(r"%\(param(\d+)\)s", r"%(param\1)s", query), values, as_dict
			)
			filters = {"company": "Acme", "posting_date": ["between", [start, end]]}
			return dashboard_api._get_operating_expenses(filters, "Acme")

	def test_the_total_of_the_list_is_the_number_home_shows(self):
		data = self.rows(period="30d")
		self.assertEqual(data["summary"]["total"], 6300.0)
		self.assertEqual(self.home("2026-09-18", "2026-10-17"), 6300.0)
		self.assertEqual(data["summary"]["total"], self.home(data["period"]["from"], data["period"]["to"]))

	def test_the_rows_add_up_to_the_same_number(self):
		data = self.rows(period="30d", page_size=100)
		self.assertEqual(round(sum(r["amount"] for r in data["rows"]), 2), 6300.0)
		self.assertEqual(data["spent_in_list"], 6300.0)

	def test_other_periods_agree_too(self):
		for key, start in (
			("today", "2026-10-17"),
			("7d", "2026-10-11"),
			("week", "2026-10-12"),
			("month", "2026-10-01"),
		):
			data = self.rows(period=key, page_size=100)
			self.assertEqual(data["summary"]["total"], self.home(start, "2026-10-17"), key)
			self.assertEqual(round(sum(r["amount"] for r in data["rows"]), 2), data["summary"]["total"], key)

	def test_a_custom_period(self):
		data = self.rows(period="custom", from_date="2026-08-01", to_date="2026-08-31")
		self.assertEqual(data["summary"]["total"], 12345.0)
		self.assertEqual(data["summary"]["total"], self.home("2026-08-01", "2026-08-31"))

	def test_home_and_the_list_use_the_same_conditions(self):
		"""The SQL of the list names the same account types, tables and tests as Home's query."""
		captured = []
		with patch.object(dashboard_api.frappe, "db", create=True) as db:
			db.sql.side_effect = lambda query, values=None, **k: captured.append((query, values)) or []
			dashboard_api._get_operating_expenses(
				{"posting_date": ["between", ["2026-10-01", "2026-10-17"]]}, "Acme"
			)
		home_sql, home_values = captured[0]
		mine_sql, mine_values = api._ledger(
			"Acme", {"from": date(2026, 10, 1), "to": date(2026, 10, 17)}, ["Stock Cost - A"]
		)
		self.assertEqual(
			set(mine_values["types"]), {v for v in home_values.values() if v in rules.HOME_EXCLUDED_TYPES}
		)
		self.assertIn("Expense", home_values.values())
		for text in (
			"is_cancelled",
			"root_type",
			"account_type",
			"posting_date",
			"NOT IN",
			"IS NULL",
			"company",
		):
			self.assertIn(text.lower(), home_sql.lower())
			self.assertIn(text.lower(), mine_sql.lower())
		self.assertIn("a.root_type = 'Expense'", mine_sql)
		self.assertIn("gl.is_cancelled = 0", mine_sql)
		self.assertIn("gl.account NOT IN", mine_sql)


class TestTheList(ListCase):
	def test_newest_first_with_the_exact_total(self):
		data = self.rows(period="30d")
		self.assertEqual(data["total"], 5)
		self.assertEqual(
			[r["date"] for r in data["rows"]], sorted((r["date"] for r in data["rows"]), reverse=True)
		)
		self.assertEqual(
			[r["id"] for r in data["rows"]], ["ACC-JV-2", "ACC-JV-1", "ACC-JV-4", "ACC-JV-3", "ACC-PINV-1"]
		)

	def test_rows_made_here_carry_their_details(self):
		rows = {r["id"]: r for r in self.rows(period="30d")["rows"]}
		mine = rows["ACC-JV-1"]
		self.assertEqual(mine["kind"], "expense")
		self.assertEqual(mine["amount"], 1500.0)
		self.assertEqual(mine["note"], "Rent for October")
		self.assertEqual(mine["category"], {"id": "Office Rent - A", "label": "Office Rent"})
		self.assertEqual(mine["paid_from"], {"key": "cash", "label": "Cash"})
		self.assertTrue(mine["has_receipt"])
		self.assertEqual(mine["added_by"], "Amina Owner")
		self.assertEqual(rows["ACC-JV-2"]["paid_from"], {"key": "mpesa", "label": "M-Pesa"})
		self.assertEqual(rows["ACC-JV-2"]["note"], "")
		self.assertFalse(rows["ACC-JV-2"]["has_receipt"])

	def test_other_entries_home_counts_are_listed_with_their_source(self):
		rows = {r["id"]: r for r in self.rows(period="30d")["rows"]}
		bill = rows["ACC-PINV-1"]
		self.assertEqual((bill["kind"], bill["source"], bill["amount"]), ("other", "Supplier bill", 4000.0))
		self.assertEqual(rows["ACC-JV-3"]["kind"], "other")
		self.assertEqual(rows["ACC-JV-3"]["source"], "Journal entry")
		self.assertEqual(rows["ACC-JV-3"]["note"], "Rent top up by the accountant")
		self.assertEqual(rows["ACC-JV-4"]["amount"], -200.0)

	def test_what_home_leaves_out_is_not_listed(self):
		ids = {r["id"] for r in self.rows(period="30d", page_size=100)["rows"]}
		for left_out in (
			"ACC-SINV-1",
			"ACC-JV-5",
			"MAT-STE-1",
			"ACC-JV-6",
			"ACC-JV-7",
			"ACC-JV-8",
			"ACC-JV-9",
			"ACC-JV-10",
		):
			self.assertNotIn(left_out, ids)

	def test_the_three_numbers(self):
		summary = self.rows(period="30d")["summary"]
		self.assertEqual(summary["total"], 6300.0)
		self.assertEqual(summary["count"], 5)
		self.assertEqual(
			summary["top_category"], {"id": "Electricity - A", "label": "Electricity", "amount": 4000.0}
		)

	def test_the_numbers_for_an_empty_period_are_zero_not_missing(self):
		data = self.rows(period="today", search="no such thing")
		self.assertEqual(data["rows"], [])
		self.assertEqual((data["total"], data["page"], data["pages"]), (0, 1, 1))
		empty = seeded_ledger()
		empty.conn.execute("DELETE FROM `tabGL Entry`")
		self.db.sql.side_effect = empty.run
		data = self.rows(period="30d")
		self.assertEqual(data["summary"], {"total": 0.0, "count": 0, "top_category": None})

	def test_paging(self):
		first = self.rows(period="30d", page=1, page_size=2)
		self.assertEqual((first["total"], first["pages"], first["page_size"]), (5, 3, 2))
		self.assertEqual([r["id"] for r in first["rows"]], ["ACC-JV-2", "ACC-JV-1"])
		last = self.rows(period="30d", page=3, page_size=2)
		self.assertEqual([r["id"] for r in last["rows"]], ["ACC-PINV-1"])
		past = self.rows(period="30d", page=99, page_size=2)
		self.assertEqual(past["page"], 3)
		self.assertEqual(self.rows(period="30d", page_size=0)["page_size"], 20)
		self.assertEqual(self.rows(period="30d", page_size=9999)["page_size"], 100)

	def test_a_category_narrows_the_list_but_not_the_three_numbers(self):
		data = self.rows(period="30d", category="Office Rent - A")
		self.assertEqual(data["total"], 3)
		self.assertEqual({r["category"]["id"] for r in data["rows"]}, {"Office Rent - A"})
		self.assertEqual(data["spent_in_list"], 2000.0)
		self.assertEqual(data["summary"]["total"], 6300.0)

	def test_search_looks_in_the_category_the_note_and_the_reference(self):
		self.assertEqual([r["id"] for r in self.rows(period="30d", search="airtime")["rows"]], ["ACC-JV-2"])
		self.assertEqual([r["id"] for r in self.rows(period="30d", search="top up")["rows"]], ["ACC-JV-3"])
		self.assertEqual([r["id"] for r in self.rows(period="30d", search="PINV-1")["rows"]], ["ACC-PINV-1"])
		self.assertEqual([r["id"] for r in self.rows(period="30d", search="October")["rows"]], ["ACC-JV-1"])

	def test_the_marker_is_not_searchable(self):
		self.assertEqual(self.rows(period="30d", search="expense")["rows"], [])
		self.assertEqual(self.rows(period="30d", search="req-aaaa")["rows"], [])

	def test_the_period_is_echoed(self):
		self.assertEqual(
			self.rows(period="7d")["period"],
			{"key": "7d", "from": "2026-10-11", "to": "2026-10-17", "days": 7},
		)

	def test_a_bad_period_is_refused_in_plain_words(self):
		for args in (
			{"period": "decade"},
			{"period": "custom"},
			{"period": "custom", "from_date": "x", "to_date": "y"},
			{"period": "custom", "from_date": "2026-10-10", "to_date": "2026-10-01"},
		):
			with self.assertRaises(frappe.ValidationError):
				api.list_expenses(**args)

	def test_the_list_only_reads(self):
		self.rows(period="30d")
		self.db.commit.assert_not_called()
		self.db.rollback.assert_not_called()
		self.assertEqual(self.docs_made, [])

	def test_a_failed_read_gives_a_plain_message(self):
		self.db.sql.side_effect = RuntimeError("table missing")
		out = api.list_expenses()
		self.assertFalse(out["success"])
		self.assertNotIn("table missing", out["message"])
		self.logged.assert_called_once()

	def test_the_company_is_the_one_asked_for(self):
		self.rows(company="Acme")
		self.patched[9].assert_called_once_with("Acme")
		values = self.db.sql.call_args_list[0].args[1]
		self.assertEqual(values["company"], "Acme")

	def test_a_special_character_in_a_search_is_taken_literally(self):
		self.rows(period="30d", search="50%_off")
		values = next(c.args[1] for c in self.db.sql.call_args_list if "like" in c.args[1])
		self.assertEqual(values["like"], "%50\\%\\_off%")


if __name__ == "__main__":
	unittest.main()
