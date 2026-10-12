# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for matching M-Pesa payments against the statement. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import payment_matching_api as api

STATEMENT = (
	"Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"
	"QAA1,2026-10-05 10:00:00,Pay Bill from Jane,Completed,500.00,,500.00\n"
	"QAA2,2026-10-05 11:00:00,Pay Bill from John,Completed,300.00,,800.00\n"
	"QAA2,2026-10-05 11:00:00,Pay Bill from John,Completed,300.00,,800.00\n"
	"QAA3,2026-10-05 12:00:00,Failed payment,Failed,100.00,,800.00\n"
)


class ClassifyLineCase(unittest.TestCase):
	def test_one_payment_for_the_same_amount_is_reconciled(self):
		self.assertEqual(api.classify_line(500, [{"amount": 500}], True), ("reconcile", None))

	def test_a_different_amount_is_a_problem(self):
		self.assertEqual(api.classify_line(500, [{"amount": 450}], True), ("problem", api.AMOUNT_DIFFERS))

	def test_two_sales_for_one_receipt_are_a_problem(self):
		self.assertEqual(
			api.classify_line(500, [{"amount": 250}, {"amount": 250}], True), ("problem", api.SEVERAL_SALES)
		)

	def test_a_known_receipt_with_no_sale_is_told_apart_from_an_unknown_one(self):
		self.assertEqual(api.classify_line(500, [], True), ("problem", api.NOT_IN_BOOKS))
		self.assertEqual(api.classify_line(500, [], False), ("problem", api.NO_RECEIPT))

	def test_cents_do_not_break_a_match(self):
		self.assertEqual(api.classify_line(500.001, [{"amount": 500}], True), ("reconcile", None))


class ImportCase(unittest.TestCase):
	def run_import(self, csv_text=STATEMENT, known=()):
		db = MagicMock()
		db.get_value.return_value = "KES"
		created = []

		def get_doc(values):
			doc = MagicMock()
			created.append(values)
			return doc

		with (
			patch.object(api, "require_gateway_admin"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "statement_account", return_value="Acme M-Pesa"),
			patch.object(api, "_match", return_value={"reconciled": 1, "left": 0, "failed": 0}) as match,
			patch.object(api.frappe, "db", db),
			patch.object(api.frappe, "get_all", return_value=list(known)),
			patch.object(api.frappe, "get_doc", side_effect=get_doc),
		):
			result = api.import_mpesa_statement(csv_text, "Acme")
		return result, created, match

	def test_each_payment_becomes_one_bank_line_and_a_repeat_in_the_file_is_ignored(self):
		result, created, match = self.run_import()
		self.assertEqual([c["transaction_id"] for c in created], ["QAA1", "QAA2"])
		self.assertEqual(result["data"]["imported"], 2)
		self.assertEqual(result["data"]["repeated_in_file"], 1)
		self.assertEqual(result["data"]["not_completed"], 1)
		match.assert_called_once()

	def test_a_receipt_imported_before_is_not_added_again(self):
		result, created, _match = self.run_import(known=["QAA1", "QAA2"])
		self.assertEqual(created, [])
		self.assertEqual(result["data"]["already_imported"], 2)

	def test_deposits_and_withdrawals_are_kept_apart(self):
		_result, created, _match = self.run_import()
		self.assertEqual(created[0]["deposit"], 500)
		self.assertEqual(created[0]["withdrawal"], 0)

	def test_a_file_with_no_payments_is_refused_with_the_reason(self):
		with self.assertRaises(frappe.ValidationError):
			self.run_import("Just a title\nnothing else\n")


class ExceptionCase(unittest.TestCase):
	def rows(self, lines, receipts, unreconciled, vouchers=None):
		def get_all(doctype, **kwargs):
			return lines

		with (
			patch.object(api, "statement_account", return_value="Acme M-Pesa"),
			patch.object(api, "nowdate", return_value="2026-10-12"),
			patch.object(api, "_receipts", return_value=receipts),
			patch.object(api, "_unreconciled_deposits", return_value=unreconciled),
			patch.object(api, "_vouchers", return_value=vouchers or []),
			patch.object(api.frappe, "get_all", side_effect=get_all),
			patch.object(api.frappe, "db", MagicMock()),
		):
			return api.exception_rows("Acme", 3)

	def test_a_business_that_never_imported_a_statement_has_no_exceptions(self):
		with patch.object(api, "statement_account", return_value=None):
			self.assertEqual(api.exception_rows("Acme"), [])

	def test_a_receipt_missing_from_the_statement_is_listed(self):
		lines = [
			frappe._dict(date="2026-10-01", reference_number="QAA1", transaction_id="QAA1"),
			frappe._dict(date="2026-10-08", reference_number="QAA9", transaction_id="QAA9"),
		]
		receipts = {"QBB1": frappe._dict(completed_at="2026-10-05 09:00:00", amount=250)}
		rows = self.rows(lines, receipts, [])
		self.assertEqual([(r["reference"], r["problem"]) for r in rows], [("QBB1", api.NO_STATEMENT_LINE)])

	def test_a_receipt_after_the_last_statement_line_is_not_missing_yet(self):
		lines = [frappe._dict(date="2026-10-01", reference_number="QAA1", transaction_id="QAA1")]
		receipts = {"QBB1": frappe._dict(completed_at="2026-10-05 09:00:00", amount=250)}
		self.assertEqual(self.rows(lines, receipts, []), [])

	def test_a_statement_line_with_no_receipt_on_record_is_listed(self):
		lines = [frappe._dict(date="2026-10-01", reference_number="QAA1", transaction_id="QAA1")]
		waiting = [
			frappe._dict(
				date="2026-10-01",
				deposit=500,
				reference_number="QAA1",
				transaction_id="QAA1",
				description="x",
			)
		]
		rows = self.rows(lines, {}, waiting)
		self.assertEqual([r["problem"] for r in rows], [api.NO_RECEIPT])

	def test_a_line_that_would_reconcile_is_not_an_exception(self):
		lines = [frappe._dict(date="2026-10-01", reference_number="QAA1", transaction_id="QAA1")]
		waiting = [
			frappe._dict(
				date="2026-10-01",
				deposit=500,
				reference_number="QAA1",
				transaction_id="QAA1",
				description="x",
			)
		]
		receipts = {"QAA1": frappe._dict(completed_at="2026-10-01 09:00:00", amount=500)}
		self.assertEqual(self.rows(lines, receipts, waiting, vouchers=[{"amount": 500}]), [])


class SummaryCase(unittest.TestCase):
	def test_exceptions_are_counted_by_kind(self):
		rows = [{"problem": "A"}, {"problem": "B"}, {"problem": "A"}]
		with patch.object(api, "exception_rows", return_value=rows):
			self.assertEqual(api.summary_counts("Acme"), {"A": 2, "B": 1})


class ScopeCase(unittest.TestCase):
	def test_only_an_administrator_can_import_match_or_list(self):
		with patch.object(api, "require_gateway_admin", side_effect=frappe.PermissionError):
			for call in (
				lambda: api.import_mpesa_statement("x"),
				lambda: api.match_mpesa_statement(),
				lambda: api.get_payment_exceptions(),
			):
				with self.assertRaises(frappe.PermissionError):
					call()
