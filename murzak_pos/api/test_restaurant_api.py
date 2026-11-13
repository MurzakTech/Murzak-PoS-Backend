import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import restaurant_api as api

approve_void = getattr(api.approve_void, "__wrapped__", api.approve_void)

TIERS = {
	"till@bar.test": "cashier",
	"boss@bar.test": "owner",
	"mgr@bar.test": "manager",
	"other@bar.test": "cashier",
}


class TestApproveVoid(unittest.TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.db.exists.return_value = True
		self.db.get_value.side_effect = self.get_value
		self.get_doc = MagicMock()
		self.get_doc.return_value.name = "VOID-1"
		self.check_password = MagicMock()
		self.user = "till@bar.test"
		for p in (
			patch.object(frappe, "db", self.db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api, "resolve_company", return_value="Bar Ltd"),
			patch.object(api, "_has_void_doctype", return_value=True),
			patch.object(api, "ensure_record_access"),
			patch.object(
				api,
				"get_user_companies",
				side_effect=lambda u: {"Bar Ltd"} if u != "outsider@x.test" else {"X"},
			),
			patch.object(
				api.role_tiers, "get_tier", side_effect=lambda u=None: TIERS.get(u or self.user, "manager")
			),
			patch.object(api, "check_password", self.check_password),
			patch.object(api.frappe, "get_doc", self.get_doc),
			patch.object(api, "now_datetime", return_value="2026-11-13 20:00:00"),
		):
			p.start()
		self.addCleanup(patch.stopall)

	def get_value(self, doctype, name, field=None, *args, **kwargs):
		if doctype == "User" and field == "enabled":
			return 1
		if doctype == "User":
			return name.split("@")[0].title()
		return "Beer"

	def void(self, **kw):
		args = {
			"item_code": "BEER",
			"qty": 2,
			"reason": "Guest changed their mind",
			"rate": 350,
			"bill_label": "Table 4",
		}
		args.update(kw)
		with patch.object(frappe, "session", frappe._dict(user=self.user), create=True):
			return approve_void(**args)

	def test_a_waiter_needs_a_manager_with_their_password(self):
		result = self.void(approver="mgr@bar.test", password="secret")
		self.check_password.assert_called_once_with("mgr@bar.test", "secret")
		record = self.get_doc.call_args.args[0]
		self.assertEqual((record["requested_by"], record["approved_by"]), ("till@bar.test", "mgr@bar.test"))
		self.assertEqual((record["qty"], record["amount"], record["bill_label"]), (2.0, 700.0, "Table 4"))
		self.get_doc.return_value.insert.assert_called_once()
		self.assertEqual(result["data"]["approved_by"], "mgr@bar.test")

	def test_without_an_approver_a_waiters_void_is_refused_and_nothing_is_kept(self):
		with self.assertRaises(frappe.ValidationError):
			self.void()
		self.get_doc.assert_not_called()

	def test_a_wrong_password_is_refused(self):
		self.check_password.side_effect = frappe.AuthenticationError
		with self.assertRaises(frappe.AuthenticationError):
			self.void(approver="mgr@bar.test", password="wrong")
		self.get_doc.assert_not_called()

	def test_another_cashier_cannot_approve(self):
		with self.assertRaises(frappe.PermissionError):
			self.void(approver="other@bar.test", password="x")
		self.check_password.assert_not_called()

	def test_a_manager_from_another_business_cannot_approve(self):
		with self.assertRaises(frappe.PermissionError):
			self.void(approver="outsider@x.test", password="x")

	def test_nobody_approves_their_own_void_unless_they_are_a_manager(self):
		with self.assertRaises(frappe.ValidationError):
			self.void(approver="till@bar.test", password="x")

	def test_a_manager_approves_their_own_without_a_password(self):
		self.user = "mgr@bar.test"
		self.void()
		self.check_password.assert_not_called()
		self.assertEqual(self.get_doc.call_args.args[0]["approved_by"], "mgr@bar.test")

	def test_a_reason_and_a_real_quantity_are_needed(self):
		self.user = "boss@bar.test"
		for bad in ({"reason": "  "}, {"qty": 0}, {"qty": "lots"}):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				self.void(**bad)
		self.get_doc.assert_not_called()

	def test_a_site_not_yet_updated_says_so(self):
		with patch.object(api, "_has_void_doctype", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				self.void(approver="mgr@bar.test", password="secret")


if __name__ == "__main__":
	unittest.main()


class TestRecordTip(unittest.TestCase):
	def tip(self, **kw):
		db = MagicMock()
		db.table_exists.return_value = True
		db.exists.return_value = True
		db.get_value.return_value = kw.pop("owner", "Bar Ltd")
		doc = MagicMock()
		doc.name = "TIP-1"
		args = {"amount": "150", "mode_of_payment": "M-Pesa", "sales_person": "Brian"}
		args.update(kw)
		with (
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "session", frappe._dict(user="till@bar.test"), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api, "resolve_company", return_value="Bar Ltd"),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "now_datetime", return_value="2026-11-13 21:00:00"),
			patch.object(api.frappe, "get_doc", return_value=doc) as get_doc,
		):
			record = getattr(api.record_tip, "__wrapped__", api.record_tip)
			return record(**args), get_doc

	def test_a_tip_is_kept_with_its_waiter_and_how_it_was_paid(self):
		result, get_doc = self.tip()
		kept = get_doc.call_args.args[0]
		self.assertEqual(
			(kept["amount"], kept["mode_of_payment"], kept["sales_person"]), (150.0, "M-Pesa", "Brian")
		)
		self.assertEqual(result["data"]["amount"], 150.0)

	def test_a_bad_amount_or_another_business_waiter_is_refused(self):
		for kw, error in (
			({"amount": "0"}, frappe.ValidationError),
			({"owner": "Other Ltd"}, frappe.PermissionError),
		):
			with self.assertRaises(error, msg=kw):
				self.tip(**kw)
