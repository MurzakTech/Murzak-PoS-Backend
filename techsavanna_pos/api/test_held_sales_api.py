# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Tests for held sales (bills put on hold at a till) that need no database: who may use the three
calls, the size limits, and that bringing a bill back reports honestly whether this request took it.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import frappe

from techsavanna_pos import hooks
from techsavanna_pos.api import held_sales_api


class FakeDoc:
	"""Just enough of a Frappe Document for the code under test."""

	def __init__(self, owner="amina@shop-a.test", new=True, **fields):
		self.__dict__.update(fields)
		self.owner = owner
		self.modified = "2026-10-10 10:00:01.123456"
		self._new = new
		self.saved = 0
		self.save_kwargs = None
		self.fail_first_save = None

	def update(self, values):
		self.__dict__.update(values)

	def is_new(self):
		return self._new

	def save(self, **kwargs):
		self.saved += 1
		self.save_kwargs = kwargs
		if self.fail_first_save and self.saved == 1:
			raise self.fail_first_save
		self._new = False

	def __getattr__(self, item):
		# Unset fields read as None, like on a real document
		return None


def _stored(**fields):
	"""A bill row as the database hands it back."""
	row = {
		"held_id": "h_1",
		"label": "Table 4",
		"customer_name": "Walk-in Customer",
		"item_count": 3,
		"total": 1500.0,
		"warehouse": "Main Store - SA",
		"held_at": datetime(2026, 10, 10, 10, 0, 0),
		"owner": "amina@shop-a.test",
		"modified": "2026-10-10 10:00:01.123456",
		"payload": json.dumps({"cart": [{"item_code": "TEA"}]}),
	}
	row.update(fields)
	return frappe._dict(row)


class HeldSalesCase(unittest.TestCase):
	def as_user(self, user="amina@shop-a.test", companies=("Shop A",), existing=None, **db_settings):
		"""Run as `user`, who belongs to `companies`; `existing` is the name of a bill already on file."""
		db = MagicMock()
		db.exists.return_value = True

		def get_value(doctype, *args, **kwargs):
			if doctype == "User":
				return "Amina"
			return existing

		db.get_value.side_effect = get_value
		for key, value in db_settings.items():
			setattr(db, key, value)
		self.db = db

		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "db", db, create=True),
			patch(
				"techsavanna_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)
			),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestWhoMayUseHeldSales(HeldSalesCase):
	def calls(self):
		return [
			lambda company: held_sales_api.list_held_sales(company=company),
			lambda company: held_sales_api.save_held_sale(id="h_1", payload={"cart": []}, company=company),
			lambda company: held_sales_api.delete_held_sale(id="h_1", company=company),
		]

	def test_guest_is_refused(self):
		self.as_user("Guest")
		for call in self.calls():
			with self.assertRaises(frappe.AuthenticationError):
				call("Shop A")

	def test_user_from_another_business_is_refused(self):
		self.as_user("boss@shop-b.test", companies=("Shop B",))
		for call in self.calls():
			with self.assertRaises(frappe.PermissionError):
				call("Shop A")

	def test_refused_user_changes_nothing(self):
		self.as_user("boss@shop-b.test", companies=("Shop B",))
		with patch.object(frappe, "new_doc") as new_doc, self.assertRaises(frappe.PermissionError):
			held_sales_api.save_held_sale(id="h_1", payload={"cart": []}, company="Shop A")
		new_doc.assert_not_called()
		with self.assertRaises(frappe.PermissionError):
			held_sales_api.delete_held_sale(id="h_1", company="Shop A")
		self.db.sql.assert_not_called()


class TestListHeldSales(HeldSalesCase):
	def list(self, rows, **request):
		self.as_user()
		with patch.object(frappe, "get_all", return_value=rows) as get_all:
			reply = held_sales_api.list_held_sales(company="Shop A", **request)
		self.get_all = get_all
		return reply

	def test_only_the_users_company_is_asked_for(self):
		self.list([])
		self.assertEqual(self.get_all.call_args.kwargs["filters"], {"company": "Shop A"})

	def test_a_bill_is_returned_in_the_shape_the_till_expects(self):
		reply = self.list([_stored(held_at=datetime(2026, 10, 10, 10, 0, 0, 123000))])
		self.assertTrue(reply["success"])
		self.assertEqual(
			reply["data"]["held_sales"],
			[
				{
					"id": "h_1",
					"label": "Table 4",
					"customer": "Walk-in Customer",
					"item_count": 3,
					"total": 1500.0,
					"warehouse": "Main Store - SA",
					"held_at": "2026-10-10T10:00:00.123Z",
					"held_by": "amina@shop-a.test",
					"held_by_name": "Amina",
					"modified": "2026-10-10 10:00:01.123456",
					"payload": {"cart": [{"item_code": "TEA"}]},
				}
			],
		)

	def test_a_store_sees_its_own_bills_and_bills_with_no_store(self):
		rows = [
			_stored(held_id="here", warehouse="Main Store - SA"),
			_stored(held_id="elsewhere", warehouse="Branch - SA"),
			_stored(held_id="nowhere", warehouse=None),
		]
		reply = self.list(rows, warehouse="Main Store - SA")
		self.assertEqual([b["id"] for b in reply["data"]["held_sales"]], ["here", "nowhere"])

	def test_without_a_store_every_bill_of_the_business_is_listed(self):
		rows = [
			_stored(held_id="a", warehouse="Main Store - SA"),
			_stored(held_id="b", warehouse="Branch - SA"),
		]
		reply = self.list(rows)
		self.assertEqual([b["id"] for b in reply["data"]["held_sales"]], ["a", "b"])

	def test_an_unreadable_payload_does_not_hide_the_other_bills(self):
		reply = self.list([_stored(held_id="bad", payload="{not json"), _stored(held_id="good")])
		bills = {b["id"]: b for b in reply["data"]["held_sales"]}
		self.assertEqual(bills["bad"]["payload"], {})
		self.assertEqual(bills["good"]["payload"], {"cart": [{"item_code": "TEA"}]})


class TestSaveHeldSale(HeldSalesCase):
	def save(self, existing=None, doc=None, **request):
		self.as_user(existing=existing)
		request.setdefault("id", "h_1")
		request.setdefault("payload", {"cart": [{"item_code": "TEA"}]})
		request.setdefault("company", "Shop A")
		self.doc = doc or FakeDoc()
		with (
			patch.object(frappe, "new_doc", return_value=self.doc) as new_doc,
			patch.object(frappe, "get_doc", return_value=self.doc) as get_doc,
		):
			reply = held_sales_api.save_held_sale(**request)
		self.new_doc, self.get_doc = new_doc, get_doc
		return reply

	def test_a_new_bill_is_saved_and_who_held_it_is_the_signed_in_user(self):
		reply = self.save(label="Table 4", item_count=3, total=1500)
		self.assertEqual(
			reply,
			{
				"success": True,
				"data": {
					"id": "h_1",
					"held_by": "amina@shop-a.test",
					"held_by_name": "Amina",
					"modified": "2026-10-10 10:00:01.123456",
				},
			},
		)
		self.new_doc.assert_called_once_with("POS Held Sale")
		self.assertEqual(self.doc.saved, 1)
		self.assertEqual((self.doc.company, self.doc.item_count, self.doc.total), ("Shop A", 3, 1500.0))

	def test_the_request_cannot_choose_who_held_the_bill(self):
		# held_by is not a parameter at all, so a request that sends one is refused rather than believed
		with self.assertRaises(TypeError):
			held_sales_api.save_held_sale(
				id="h_1", payload={}, company="Shop A", held_by="someone.else@shop-a.test"
			)

	def test_saving_the_same_bill_again_replaces_it_rather_than_adding_another(self):
		existing = FakeDoc(new=False)
		reply = self.save(existing="abc123", doc=existing, total=2000)
		self.assertTrue(reply["success"])
		self.new_doc.assert_not_called()
		self.get_doc.assert_called_once_with("POS Held Sale", "abc123")
		self.assertEqual(existing.total, 2000.0)

	def test_a_retry_that_races_the_first_request_replaces_that_bill(self):
		racing = FakeDoc(new=True)
		racing.fail_first_save = frappe.DuplicateEntryError("POS Held Sale", "x")
		winner = FakeDoc(new=False)
		self.as_user()
		finds = iter([None, "abc123"])  # not there when we looked, there by the time the save failed
		self.db.get_value.side_effect = lambda doctype, *a, **k: "Amina" if doctype == "User" else next(finds)
		with (
			patch.object(frappe, "new_doc", return_value=racing),
			patch.object(frappe, "get_doc", return_value=winner) as get_doc,
		):
			reply = held_sales_api.save_held_sale(id="h_1", payload={"cart": []}, company="Shop A", total=5)
		self.assertTrue(reply["success"])
		get_doc.assert_called_once_with("POS Held Sale", "abc123")
		self.assertEqual((winner.saved, winner.total), (1, 5.0))

	def test_a_bill_over_300_lines_is_refused_and_nothing_is_saved(self):
		reply = self.save(payload={"cart": [{"item_code": f"I{n}"} for n in range(301)]})
		self.assertFalse(reply["success"])
		self.assertIn("300", reply["message"])
		self.new_doc.assert_not_called()
		self.assertEqual(self.doc.saved, 0)

	def test_a_bill_of_exactly_300_lines_is_accepted(self):
		reply = self.save(payload={"cart": [{"item_code": f"I{n}"} for n in range(300)]})
		self.assertTrue(reply["success"])

	def test_a_payload_over_256_kb_is_refused(self):
		reply = self.save(payload={"cart": [], "note": "x" * (256 * 1024)})
		self.assertFalse(reply["success"])
		self.assertIn("too large", reply["message"])
		self.assertEqual(self.doc.saved, 0)

	def test_an_oversize_payload_sent_as_text_is_refused_before_it_is_read(self):
		reply = self.save(payload="[" * (256 * 1024 + 1))
		self.assertFalse(reply["success"])
		self.assertIn("too large", reply["message"])

	def test_the_payload_may_be_sent_as_a_json_string(self):
		reply = self.save(payload=json.dumps({"cart": [{"item_code": "TEA"}]}))
		self.assertTrue(reply["success"])
		self.assertEqual(json.loads(self.doc.payload), {"cart": [{"item_code": "TEA"}]})

	def test_the_payload_is_stored_as_given(self):
		payload = {"cart": [{"item_code": "Maziwa Lala"}], "customerId": None, "manualDiscountValue": 0}
		self.save(payload=payload)
		self.assertEqual(json.loads(self.doc.payload), payload)

	def test_a_payload_that_is_not_a_bill_is_refused(self):
		for payload in ("{not json", "[1, 2]", "null", 5, {"cart": "TEA"}):
			reply = self.save(payload=payload)
			self.assertFalse(reply["success"], payload)
			self.assertEqual(self.doc.saved, 0)

	def test_the_label_is_trimmed_to_40_characters(self):
		self.save(label="  " + "T" * 60)
		self.assertEqual(self.doc.label, "T" * 40)

	def test_an_id_sent_as_a_number_is_kept_as_text(self):
		reply = self.save(id=1696934400000)
		self.assertEqual(self.doc.held_id, "1696934400000")
		self.assertEqual(reply["data"]["id"], "1696934400000")

	def test_a_missing_id_is_refused(self):
		for bad_id in ("", "   ", None):
			reply = self.save(id=bad_id)
			self.assertFalse(reply["success"])

	def test_the_time_held_is_kept_in_utc(self):
		self.save(held_at="2026-10-10T13:00:00.250+03:00")
		self.assertEqual(self.doc.held_at, datetime(2026, 10, 10, 10, 0, 0, 250000))
		self.save(held_at="2026-10-10T10:00:00.000Z")
		self.assertEqual(self.doc.held_at, datetime(2026, 10, 10, 10, 0, 0))

	def test_a_bill_with_no_time_is_stamped_now(self):
		self.save()
		self.assertLess(
			abs(datetime.now(timezone.utc).replace(tzinfo=None) - self.doc.held_at), timedelta(minutes=1)
		)

	def test_a_time_that_cannot_be_read_is_refused(self):
		reply = self.save(held_at="last tuesday")
		self.assertFalse(reply["success"])
		self.assertEqual(self.doc.saved, 0)

	def test_no_store_is_saved_as_empty(self):
		self.save(warehouse="")
		self.assertIsNone(self.doc.warehouse)

	def test_the_save_skips_desk_permissions_because_the_company_was_already_checked(self):
		self.save()
		self.assertEqual(self.doc.save_kwargs, {"ignore_permissions": True})


class TestDeleteHeldSale(HeldSalesCase):
	def delete(self, rows_removed, **request):
		self.as_user(sql=MagicMock(side_effect=[None, [(rows_removed,)]]))
		request.setdefault("id", "h_1")
		return held_sales_api.delete_held_sale(company="Shop A", **request)

	def test_a_bill_that_was_there_reports_deleted(self):
		self.assertEqual(self.delete(1), {"success": True, "data": {"id": "h_1", "deleted": True}})

	def test_a_bill_already_gone_reports_not_deleted_and_is_not_an_error(self):
		self.assertEqual(self.delete(0), {"success": True, "data": {"id": "h_1", "deleted": False}})

	def test_the_delete_is_one_statement_for_this_company_and_id_only(self):
		self.delete(1)
		query, values = self.db.sql.call_args_list[0].args
		self.assertTrue(query.startswith("DELETE FROM `tabPOS Held Sale`"))
		self.assertEqual(values, ("Shop A", "h_1"))
		self.assertEqual(self.db.sql.call_args_list[1].args, ("SELECT ROW_COUNT()",))

	def test_an_id_sent_as_a_number_is_looked_up_as_text(self):
		reply = self.delete(1, id=42)
		self.assertEqual(reply["data"]["id"], "42")
		self.assertEqual(self.db.sql.call_args_list[0].args[1], ("Shop A", "42"))

	def test_a_missing_id_is_refused_without_touching_the_database(self):
		self.as_user()
		reply = held_sales_api.delete_held_sale(id="", company="Shop A")
		self.assertFalse(reply["success"])
		self.db.sql.assert_not_called()


class TestPurgeOldHeldSales(HeldSalesCase):
	def test_only_bills_held_and_last_saved_more_than_30_days_ago_are_removed(self):
		self.as_user()
		now = datetime(2026, 10, 10, 12, 0, 0)
		with (
			patch.object(held_sales_api, "_utc_now", return_value=now),
			patch.object(frappe, "utils", MagicMock(), create=True) as utils,
		):
			utils.now_datetime.return_value = now
			utils.add_days.side_effect = lambda moment, days: moment + timedelta(days=days)
			held_sales_api.purge_old_held_sales()
		query, values = self.db.sql.call_args.args
		self.assertTrue(query.startswith("DELETE FROM `tabPOS Held Sale`"))
		self.assertIn("held_at < %s", query)
		self.assertIn("modified < %s", query)
		self.assertEqual(values, (datetime(2026, 9, 10, 12, 0, 0), datetime(2026, 9, 10, 12, 0, 0)))

	def test_the_daily_job_is_switched_on_and_points_at_a_real_function(self):
		jobs = hooks.scheduler_events["daily"]
		self.assertIn("techsavanna_pos.api.held_sales_api.purge_old_held_sales", jobs)
		self.assertTrue(callable(held_sales_api.purge_old_held_sales))


if __name__ == "__main__":
	unittest.main()
