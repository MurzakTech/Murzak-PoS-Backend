# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for saving draft purchase orders, cancelling purchase invoices and the eTIMS status check. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import purchase
from murzak_pos.api import purchase_orders_api as api


def _db(exists=True, stock_item=1, warehouse_company="Acme"):
	db = MagicMock()
	db.exists.return_value = exists

	def get_value(doctype, *args, **kwargs):
		if doctype == "Item":
			return stock_item
		if doctype == "Warehouse":
			return warehouse_company
		return None

	db.get_value.side_effect = get_value
	return db


class OrderLinesCase(unittest.TestCase):
	def lines(self, items, **kwargs):
		with (
			patch.object(api.frappe, "db", _db(**kwargs)),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "today", return_value="2026-03-01"),
		):
			return api._order_lines(items, "Acme", kwargs.get("default"), "2026-03-01")

	def test_a_good_line_keeps_item_quantity_rate_and_warehouse(self):
		rows = self.lines([{"item_code": "A", "qty": "2", "rate": "10", "warehouse": "Stores"}])
		self.assertEqual((rows[0]["qty"], rows[0]["rate"], rows[0]["warehouse"]), (2.0, 10.0, "Stores"))

	def test_no_rate_is_left_for_the_price_list_to_fill(self):
		self.assertNotIn("rate", self.lines([{"item_code": "A", "qty": 1, "warehouse": "S"}])[0])

	def test_a_line_with_no_needed_by_date_is_needed_on_the_day_of_the_order(self):
		rows = self.lines([{"item_code": "A", "qty": 1, "warehouse": "S"}])
		self.assertEqual(rows[0]["schedule_date"], "2026-03-01")

	def test_a_needed_by_date_on_the_line_is_kept(self):
		rows = self.lines([{"item_code": "A", "qty": 1, "warehouse": "S", "schedule_date": "2026-04-01"}])
		self.assertEqual(rows[0]["schedule_date"], "2026-04-01")

	def test_the_default_warehouse_is_used_when_the_line_has_none(self):
		with (
			patch.object(api.frappe, "db", _db()),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "today", return_value="2026-03-01"),
		):
			rows = api._order_lines([{"item_code": "A", "qty": 1}], "Acme", "Main", "2026-03-01")
		self.assertEqual(rows[0]["warehouse"], "Main")

	def test_a_stock_item_with_no_warehouse_anywhere_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.lines([{"item_code": "A", "qty": 1}])

	def test_a_service_item_needs_no_warehouse(self):
		self.assertIsNone(self.lines([{"item_code": "A", "qty": 1}], stock_item=0)[0]["warehouse"])

	def test_an_unknown_item_a_zero_quantity_and_an_empty_list_are_refused(self):
		for items, kwargs in (
			([{"item_code": "A", "qty": 1, "warehouse": "S"}], {"exists": False}),
			([{"item_code": "A", "qty": 0, "warehouse": "S"}], {}),
			([{"qty": 1}], {}),
			([], {}),
		):
			with self.assertRaises(frappe.ValidationError):
				self.lines(items, **kwargs)

	def test_a_warehouse_of_another_business_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.lines([{"item_code": "A", "qty": 1, "warehouse": "Theirs"}], warehouse_company="Other")


class UpsertCase(unittest.TestCase):
	def upsert(self, name=None, docstatus=0, company="Acme", **kwargs):
		existing = MagicMock(docstatus=docstatus, company=company, grand_total=50, items=[1])
		existing.name = name
		fresh = MagicMock(docstatus=0, grand_total=50, items=[1], company="Acme", supplier="Sup")
		fresh.name = "PO-NEW"
		with (
			patch.object(api, "require_stock_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "today", return_value="2026-03-01"),
			patch.object(api.frappe, "db", _db()),
			patch.object(api.frappe, "get_meta") as meta,
			patch.object(api.frappe, "get_doc", return_value=existing if name else fresh) as get_doc,
		):
			meta.return_value.has_field.return_value = False
			result = api.upsert_purchase_order(
				"Sup", [{"item_code": "A", "qty": 1, "warehouse": "S"}], "Acme", name=name, **kwargs
			)
		return result, existing if name else fresh, get_doc

	def test_without_a_name_a_new_draft_is_inserted(self):
		result, doc, get_doc = self.upsert()
		doc.insert.assert_called_once()
		doc.submit.assert_not_called()
		self.assertEqual(result["purchase_order"], "PO-NEW")
		self.assertEqual(get_doc.call_args.args[0]["doctype"], "Purchase Order")

	def test_the_reply_has_what_the_screen_reads(self):
		result, _doc, _get = self.upsert()
		self.assertEqual(result["status"], "success")
		self.assertIn("docstatus", result)
		self.assertIn("grand_total", result)

	def test_with_a_name_the_draft_is_changed_and_saved(self):
		result, doc, _get = self.upsert(name="PO-1", transaction_date="2026-03-01")
		doc.set.assert_called_once()
		doc.save.assert_called_once()
		doc.insert.assert_not_called()
		self.assertEqual(result["purchase_order"], "PO-1")

	def test_submit_submits_after_saving(self):
		_result, doc, _get = self.upsert(name="PO-1", submit=1)
		doc.save.assert_called_once()
		doc.submit.assert_called_once()

	def test_submit_zero_as_text_does_not_submit(self):
		_result, doc, _get = self.upsert(name="PO-1", submit="0")
		doc.submit.assert_not_called()

	def test_an_order_already_submitted_is_not_changed(self):
		with self.assertRaises(frappe.ValidationError):
			self.upsert(name="PO-1", docstatus=1)

	def test_an_order_of_another_business_is_not_found(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.upsert(name="PO-1", company="Other")

	def test_a_new_order_sent_twice_with_the_same_key_is_saved_once(self):
		earlier = MagicMock(docstatus=0, grand_total=50, items=[1], company="Acme", supplier="Sup")
		earlier.name = "PO-OLD"
		db = _db()
		db.get_value.side_effect = lambda doctype, *a, **k: {
			"Purchase Order": "PO-OLD",
			"Warehouse": "Acme",
		}.get(doctype, 1)
		with (
			patch.object(api, "require_stock_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "today", return_value="2026-03-01"),
			patch.object(api.frappe, "db", db),
			patch.object(api.frappe, "get_meta") as meta,
			patch.object(api.frappe, "get_doc", return_value=earlier),
		):
			meta.return_value.has_field.return_value = True
			result = api.upsert_purchase_order(
				"Sup", [{"item_code": "A", "qty": 1, "warehouse": "S"}], "Acme", idempotency_key="k1"
			)
		self.assertEqual(result["purchase_order"], "PO-OLD")
		earlier.insert.assert_not_called()

	def test_an_unknown_supplier_is_refused(self):
		with (
			patch.object(api, "require_stock_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "db", _db(exists=False)),
		):
			with self.assertRaises(frappe.ValidationError):
				api.upsert_purchase_order("Nobody", [{"item_code": "A", "qty": 1}], "Acme")


class CancelCase(unittest.TestCase):
	def cancel(self, docstatus=1, exists=True):
		doc = MagicMock(docstatus=docstatus)
		doc.name = "PI-1"
		with (
			patch.object(api, "require_any_role"),
			patch.object(api, "ensure_record_access"),
			patch.object(api.frappe, "db", _db(exists=exists)),
			patch.object(api.frappe, "get_doc", return_value=doc),
		):
			result = api.cancel_purchase_invoice("PI-1")
		return result, doc

	def test_a_submitted_invoice_is_cancelled(self):
		result, doc = self.cancel()
		doc.cancel.assert_called_once()
		self.assertTrue(result["success"])

	def test_a_draft_or_cancelled_invoice_is_refused(self):
		for docstatus in (0, 2):
			with self.assertRaises(frappe.ValidationError):
				self.cancel(docstatus=docstatus)

	def test_an_unknown_invoice_is_not_found(self):
		with self.assertRaises(frappe.DoesNotExistError):
			self.cancel(exists=False)

	def test_the_roles_that_may_cancel_are_checked(self):
		with patch.object(api, "require_any_role", side_effect=frappe.PermissionError) as check:
			with self.assertRaises(frappe.PermissionError):
				api.cancel_purchase_invoice("PI-1")
		self.assertIn("Accounts Manager", check.call_args.args[0])


class EtimsStatusCase(unittest.TestCase):
	def status(self, settings, doctype_exists=True):
		db = MagicMock()
		db.exists.return_value = doctype_exists
		db.get_value.return_value = settings
		with (
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "db", db),
		):
			return api.check_etims_registration_status("Acme")["data"]

	def test_an_active_record_means_the_business_sends_to_kra(self):
		settings = MagicMock(is_active=1)
		settings.name = "Acme-eTIMS"
		data = self.status(settings)
		self.assertEqual(
			(data["has_etims"], data["is_active"], data["settings_name"]), (True, True, "Acme-eTIMS")
		)

	def test_a_record_that_is_switched_off_is_registered_but_not_active(self):
		settings = MagicMock(is_active=0)
		settings.name = "Acme-eTIMS"
		data = self.status(settings)
		self.assertEqual((data["has_etims"], data["is_active"]), (True, False))

	def test_no_record_means_no_etims(self):
		data = self.status(None)
		self.assertEqual((data["has_etims"], data["is_active"], data["settings_name"]), (False, False, None))

	def test_a_server_without_the_kenya_app_has_no_etims(self):
		self.assertFalse(self.status(None, doctype_exists=False)["has_etims"])


class ReachabilityCase(unittest.TestCase):
	def test_the_screens_find_the_calls_where_they_look(self):
		for name in ("upsert_purchase_order", "cancel_purchase_invoice", "check_etims_registration_status"):
			self.assertIs(getattr(purchase, name), getattr(api, name))


if __name__ == "__main__":
	unittest.main()
