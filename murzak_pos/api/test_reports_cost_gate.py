"""
Stock value, stock analysis and profit reports show what things cost, so they are for owners and managers only.

The newer Reports screens already check this (reports_api.run_report). The older report calls underneath them did not,
so a cashier who called one directly could read stock value or the profit and loss. Each call below now starts with the
check. No database is used; the list is read from the source, so a new report call that skips the check, or a check
that is removed, fails here.
"""

import ast
import os
import unittest
from unittest.mock import patch

import frappe

from murzak_pos.api import role_tiers

API = os.path.dirname(os.path.abspath(__file__))

# Sales analysis is for everyone who sells (a cashier sees their own sales), so it is not gated here.
OPEN_TO_SELLERS = {"sales_analytics_report", "export_sales_analytics_report"}
PROFIT_REPORTS = {"get_profit_and_loss"}


def whitelisted_reports():
	tree = ast.parse(open(os.path.join(API, "reports.py")).read())
	return [
		node
		for node in tree.body
		if isinstance(node, ast.FunctionDef)
		and any("whitelist" in ast.unparse(d) for d in node.decorator_list)
	]


def first_statement(node):
	body = node.body
	if isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
		body = body[1:]  # skip the docstring
	return body[0]


class TestEveryCostReportChecksTheRole(unittest.TestCase):
	def test_each_report_starts_with_its_check(self):
		reports = whitelisted_reports()
		self.assertGreater(len(reports), 15, "expected the report calls to be found")
		for node in reports:
			if node.name in OPEN_TO_SELLERS:
				continue
			wanted = "profit" if node.name in PROFIT_REPORTS else "cost_of_goods"
			stmt = first_statement(node)
			expected = f'role_tiers.require_access("{wanted}")'
			actual = ast.unparse(stmt.value) if isinstance(stmt, ast.Expr) else ast.unparse(stmt)
			with self.subTest(report=node.name):
				self.assertEqual(actual.replace("'", '"'), expected)

	def test_the_sales_analysis_stays_open_to_sellers(self):
		names = {n.name for n in whitelisted_reports()}
		self.assertTrue(OPEN_TO_SELLERS <= names)
		for node in whitelisted_reports():
			if node.name in OPEN_TO_SELLERS:
				self.assertNotIn("require_access", ast.unparse(first_statement(node)))


class TestWhoPasses(unittest.TestCase):
	def as_roles(self, roles, capability):
		with (
			patch.object(role_tiers.frappe, "session", frappe._dict(user="someone@shop.test"), create=True),
			patch.object(role_tiers.frappe, "get_roles", return_value=roles),
		):
			role_tiers.require_access(capability)

	def test_a_cashier_is_refused_both(self):
		for capability in ("cost_of_goods", "profit"):
			with self.assertRaises(frappe.PermissionError):
				self.as_roles(["Sales User"], capability)

	def test_owners_and_managers_pass(self):
		for roles in (["Business Owner"], ["System Manager"], ["Stock Manager"], ["Accounts Manager"]):
			for capability in ("cost_of_goods", "profit"):
				self.as_roles(roles, capability)
