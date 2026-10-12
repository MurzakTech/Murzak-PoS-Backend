# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for approvals of large refunds and purchase orders. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import approval_rules as rules
from murzak_pos.api import approvals_api as api


class RulesCase(unittest.TestCase):
	def test_a_limit_of_zero_switches_the_check_off(self):
		self.assertFalse(rules.needs_approval(1_000_000, 0))
		self.assertFalse(rules.needs_approval(1_000_000, None))

	def test_only_an_amount_above_the_limit_needs_approval(self):
		self.assertFalse(rules.needs_approval(5000, 5000))
		self.assertTrue(rules.needs_approval(5000.01, 5000))

	def test_a_refund_is_judged_by_its_size_not_its_sign(self):
		self.assertTrue(rules.needs_approval(-8000, 5000))

	def test_a_cashier_or_sales_manager_cannot_approve(self):
		problem = rules.decision_problem(rules.REFUND, "a@x", "b@x", ["Sales Manager", "All"])
		self.assertIn("approval rights", problem)

	def test_an_accounts_manager_can_approve_someone_elses_refund(self):
		self.assertIsNone(rules.decision_problem(rules.REFUND, "a@x", "b@x", ["Accounts Manager"]))

	def test_a_purchase_manager_can_approve_an_order_but_not_a_refund(self):
		self.assertIsNone(rules.decision_problem(rules.PURCHASE_ORDER, "a@x", "b@x", ["Purchase Manager"]))
		self.assertIsNotNone(rules.decision_problem(rules.REFUND, "a@x", "b@x", ["Purchase Manager"]))

	def test_nobody_but_the_owner_approves_their_own_request(self):
		self.assertIn("own request", rules.decision_problem(rules.REFUND, "a@x", "a@x", ["Accounts Manager"]))
		self.assertIsNone(rules.decision_problem(rules.REFUND, "a@x", "a@x", ["System Manager"]))

	def test_administrator_may_always_decide(self):
		self.assertIsNone(rules.decision_problem(rules.REFUND, "a@x", "Administrator", []))


def _doc(doctype="Sales Invoice", total=9000, is_return=1, company="Acme", name="SINV-1", docstatus=0):
	return frappe._dict(
		doctype=doctype,
		name=name,
		company=company,
		grand_total=-total if is_return else total,
		rounded_total=0,
		is_return=is_return,
		docstatus=docstatus,
	)


class RequirementCase(unittest.TestCase):
	def requires(self, doc, refund=5000, order=100000):
		with patch.object(api, "limits", return_value={rules.REFUND: refund, rules.PURCHASE_ORDER: order}):
			return api.requires_approval(doc)

	def test_a_large_refund_needs_approval(self):
		self.assertTrue(self.requires(_doc(total=9000)))

	def test_a_small_refund_does_not(self):
		self.assertFalse(self.requires(_doc(total=900)))

	def test_a_normal_sale_never_needs_approval(self):
		self.assertFalse(self.requires(_doc(total=900000, is_return=0)))

	def test_a_large_purchase_order_needs_approval(self):
		order = _doc("Purchase Order", total=250000, is_return=0)
		self.assertTrue(self.requires(order))
		self.assertFalse(self.requires(_doc("Purchase Order", total=2500, is_return=0)))

	def test_other_documents_never_need_approval(self):
		self.assertFalse(self.requires(_doc("Stock Entry", total=10**9)))


class SubmitGuardCase(unittest.TestCase):
	def guard(self, doc, approved_amounts, refund=5000):
		rows = [frappe._dict(amount=a) for a in approved_amounts]
		with (
			patch.object(api, "limits", return_value={rules.REFUND: refund, rules.PURCHASE_ORDER: 0}),
			patch.object(api.frappe, "get_all", return_value=rows),
		):
			api.enforce_on_submit(doc)

	def test_a_large_refund_with_no_approval_cannot_be_submitted(self):
		with self.assertRaises(frappe.ValidationError):
			self.guard(_doc(total=9000), [])

	def test_an_approved_refund_can_be_submitted(self):
		self.guard(_doc(total=9000), [9000])

	def test_an_approval_does_not_cover_a_bigger_amount_added_afterwards(self):
		with self.assertRaises(frappe.ValidationError):
			self.guard(_doc(total=12000), [9000])

	def test_a_small_refund_needs_no_request(self):
		self.guard(_doc(total=100), [])


class DecisionCase(unittest.TestCase):
	def request(self, status="Pending", requested_by="cashier@x", kind="Refund"):
		request = MagicMock()
		for key, value in dict(
			name="APR-00001",
			company="Acme",
			kind=kind,
			status=status,
			requested_by=requested_by,
			reference_doctype="Sales Invoice",
			reference_name="SINV-1",
			amount=9000,
		).items():
			setattr(request, key, value)
		return request

	def decide(self, call, request, user, roles, invoice=None):
		invoice = invoice or MagicMock(docstatus=0, name="SINV-1")
		invoice.get.side_effect = lambda key, default=None: {"grand_total": -9000}.get(key, default)
		invoice.name = "SINV-1"

		def get_doc(doctype, name=None):
			return request if doctype == api.REQUEST else invoice

		with (
			patch.object(api.frappe, "db", MagicMock()),
			patch.object(api.frappe, "get_doc", side_effect=get_doc),
			patch.object(api.frappe, "get_roles", return_value=roles),
			patch.object(api.frappe, "session", MagicMock(user=user)),
			patch.object(api, "resolve_company"),
			patch.object(api, "now_datetime", return_value="2026-10-12 10:00:00"),
		):
			return call(), invoice

	def test_a_manager_approves_and_the_refund_is_submitted(self):
		request = self.request()
		result, invoice = self.decide(
			lambda: api.approve_request("APR-00001"), request, "boss@x", ["Accounts Manager"]
		)
		self.assertEqual(request.status, "Approved")
		self.assertEqual(request.decided_by, "boss@x")
		invoice.submit.assert_called_once()
		self.assertTrue(result["success"])

	def test_the_person_who_asked_cannot_approve_it(self):
		request = self.request(requested_by="boss@x")
		with self.assertRaises(frappe.PermissionError):
			self.decide(lambda: api.approve_request("APR-00001"), request, "boss@x", ["Accounts Manager"])

	def test_a_sales_manager_cannot_approve(self):
		with self.assertRaises(frappe.PermissionError):
			self.decide(lambda: api.approve_request("APR-00001"), self.request(), "s@x", ["Sales Manager"])

	def test_a_request_already_decided_cannot_be_decided_again(self):
		with self.assertRaises(frappe.ValidationError):
			self.decide(
				lambda: api.approve_request("APR-00001"),
				self.request(status="Rejected"),
				"boss@x",
				["Accounts Manager"],
			)

	def test_rejecting_keeps_the_draft_and_records_the_reason(self):
		request = self.request()
		_result, invoice = self.decide(
			lambda: api.reject_request("APR-00001", "Wrong customer"), request, "boss@x", ["Accounts Manager"]
		)
		self.assertEqual(request.status, "Rejected")
		self.assertEqual(request.reason, "Wrong customer")
		invoice.submit.assert_not_called()


class SettingsCase(unittest.TestCase):
	def test_only_a_manager_can_read_or_change_limits(self):
		with (
			patch.object(api.frappe, "get_roles", return_value=["Sales User"]),
			patch.object(api.frappe, "session", MagicMock(user="cashier@x")),
		):
			with self.assertRaises(frappe.PermissionError):
				api.get_approval_settings("Acme")
			with self.assertRaises(frappe.PermissionError):
				api.save_approval_settings(refund_limit=100, company="Acme")

	def test_a_bad_status_filter_is_refused(self):
		with patch.object(api, "resolve_company", return_value="Acme"):
			with self.assertRaises(frappe.ValidationError):
				api.list_approvals("Acme", status="Maybe")
