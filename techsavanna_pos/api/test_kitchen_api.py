# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Tests for kitchen and bar tickets that need no database: who may use the five calls, who may change the
settings, that sending twice never doubles an order, that tickets are numbered one send at a time, and the
size limits.
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe

from techsavanna_pos import hooks
from techsavanna_pos.api import kitchen_api


class FakeDoc:
	"""Just enough of a Frappe Document for the code under test."""

	counter = 0

	def __init__(self, owner="amina@shop-a.test", **fields):
		self.__dict__.update(fields)
		self.owner = owner
		self.modified = "2026-10-10 10:00:01.123456"
		self.inserted = 0
		self.saved = 0
		self.save_kwargs = None

	def update(self, values):
		self.__dict__.update(values)

	def insert(self, **kwargs):
		FakeDoc.counter += 1
		self.name = f"k{FakeDoc.counter}"
		self.inserted += 1
		self.save_kwargs = kwargs

	def save(self, **kwargs):
		self.saved += 1
		self.save_kwargs = kwargs

	def __getattr__(self, item):
		# Unset fields read as None, like on a real document
		return None


def _stored(**fields):
	"""A ticket row as the database hands it back."""
	row = {
		"name": "k1",
		"client_id": "c-1",
		"station": "Kitchen",
		"number": 14,
		"round": 2,
		"label": "Table 4",
		"order_type": "table",
		"owner": "amina@shop-a.test",
		"created_at": datetime(2026, 10, 10, 16, 42, 0),
		"status": "New",
		"status_by": None,
		"status_at": None,
		"warehouse": "Main Store - SA",
		"lines": json.dumps({"adds": [{"item_code": "PILAU", "item_name": "Pilau", "qty": 2, "note": ""}]}),
	}
	row.update(fields)
	return frappe._dict(row)


def _ticket(client_id="c-1", station="Kitchen", **fields):
	ticket = {
		"client_id": client_id,
		"station": station,
		"label": "Table 4",
		"order_type": "table",
		"round": 1,
		"created_at": "2026-10-10T16:42:00.000Z",
		"lines": {"adds": [{"item_code": "PILAU", "item_name": "Pilau", "qty": 2, "note": "no pilipili"}]},
	}
	ticket.update(fields)
	return ticket


SETTINGS = {
	"enabled": True,
	"screens": True,
	"stations": [
		{"id": "kitchen", "name": "Kitchen", "groups": ["Food"]},
		{"id": "bar", "name": "Bar", "groups": ["Beer", "Soda"]},
	],
	"defaultStation": "",
	"quickNotes": ["No onions", "Well done"],
}


class KitchenCase(unittest.TestCase):
	def as_user(self, user="amina@shop-a.test", companies=("Shop A",), roles=("Sales User",), **db_settings):
		"""Run as `user`, who belongs to `companies` and has `roles`."""
		db = MagicMock()
		db.exists.return_value = True
		db.get_value.side_effect = lambda doctype, *a, **k: "Amina" if doctype == "User" else None
		for key, value in db_settings.items():
			setattr(db, key, value)
		self.db = db

		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_roles", return_value=list(roles)),
			patch.object(frappe.utils, "today", return_value="2026-10-10"),
			patch(
				"techsavanna_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)
			),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestWhoMayUseKitchenTickets(KitchenCase):
	def calls(self):
		return [
			lambda company: kitchen_api.get_kitchen_settings(company=company),
			lambda company: kitchen_api.save_kitchen_settings(settings=SETTINGS, company=company),
			lambda company: kitchen_api.send_tickets(tickets=[_ticket()], company=company),
			lambda company: kitchen_api.list_tickets(company=company),
			lambda company: kitchen_api.set_ticket_status(id="k1", status="Ready", company=company),
		]

	def test_guest_is_refused(self):
		self.as_user("Guest")
		for call in self.calls():
			with self.assertRaises(frappe.AuthenticationError):
				call("Shop A")

	def test_user_from_another_business_is_refused(self):
		self.as_user("boss@shop-b.test", companies=("Shop B",), roles=("System Manager",))
		for call in self.calls():
			with self.assertRaises(frappe.PermissionError):
				call("Shop A")

	def test_refused_user_changes_nothing(self):
		self.as_user("boss@shop-b.test", companies=("Shop B",), roles=("System Manager",))
		with patch.object(frappe, "new_doc") as new_doc, patch.object(frappe, "get_doc") as get_doc:
			for call in self.calls():
				with self.assertRaises(frappe.PermissionError):
					call("Shop A")
		new_doc.assert_not_called()
		get_doc.assert_not_called()
		self.db.sql.assert_not_called()


class TestKitchenSettings(KitchenCase):
	def test_a_business_with_no_settings_gets_null(self):
		self.as_user()
		self.assertEqual(
			kitchen_api.get_kitchen_settings(company="Shop A"), {"success": True, "data": {"settings": None}}
		)

	def test_saved_settings_come_back_as_they_were_saved(self):
		self.as_user()
		self.db.get_value.side_effect = lambda doctype, *a, **k: json.dumps(SETTINGS)
		reply = kitchen_api.get_kitchen_settings(company="Shop A")
		self.assertEqual(reply["data"]["settings"], SETTINGS)

	def test_unreadable_stored_settings_read_as_none(self):
		self.as_user()
		self.db.get_value.side_effect = lambda doctype, *a, **k: "{not json"
		self.assertIsNone(kitchen_api.get_kitchen_settings(company="Shop A")["data"]["settings"])

	def save(self, settings=None, existing=None, **as_user):
		self.as_user(**as_user)
		self.doc = FakeDoc()
		self.db.get_value.side_effect = lambda doctype, *a, **k: existing
		with (
			patch.object(frappe, "new_doc", return_value=self.doc) as new_doc,
			patch.object(frappe, "get_doc", return_value=self.doc) as get_doc,
		):
			reply = kitchen_api.save_kitchen_settings(
				settings=SETTINGS if settings is None else settings, company="Shop A"
			)
		self.new_doc, self.get_doc = new_doc, get_doc
		return reply

	def test_a_manager_can_save_and_each_manager_role_counts(self):
		for role in ("System Manager", "Accounts Manager", "Sales Manager"):
			reply = self.save(roles=(role,))
			self.assertEqual(reply, {"success": True, "data": {}}, role)
			self.assertEqual(self.doc.saved, 1)

	def test_the_administrator_can_save(self):
		self.assertTrue(self.save(user="Administrator", roles=())["success"])

	def test_a_waiter_cannot_change_the_settings(self):
		for roles in (("Sales User",), ("Desk User",), ()):
			reply = self.save(roles=roles)
			self.assertFalse(reply["success"])
			self.assertIn("manager", reply["message"])
			self.assertEqual(self.doc.saved, 0)

	def test_settings_are_kept_per_business_and_replaced_when_they_exist(self):
		self.save(roles=("Sales Manager",))
		self.new_doc.assert_called_once_with("POS Kitchen Settings")
		self.assertEqual(self.doc.company, "Shop A")
		self.assertEqual(self.doc.save_kwargs, {"ignore_permissions": True})
		self.save(roles=("Sales Manager",), existing="Shop A")
		self.get_doc.assert_called_once_with("POS Kitchen Settings", "Shop A")

	def test_only_the_five_shared_keys_are_kept(self):
		# what belongs to one device (printing, the ticket counter) must never be stored for everyone
		self.save(
			settings={**SETTINGS, "printNow": False, "nextTicket": 9, "ticketDate": "2026-10-10", "extra": 1},
			roles=("Sales Manager",),
		)
		self.assertEqual(json.loads(self.doc.settings), SETTINGS)

	def test_names_and_notes_are_tidied(self):
		self.save(
			settings={
				**SETTINGS,
				"stations": [{"id": "k", "name": "  Grill   Room ", "groups": [" Food ", ""]}],
				"quickNotes": ["  No   onions ", ""],
			},
			roles=("Sales Manager",),
		)
		stored = json.loads(self.doc.settings)
		self.assertEqual(stored["stations"], [{"id": "k", "name": "Grill Room", "groups": ["Food"]}])
		self.assertEqual(stored["quickNotes"], ["No onions"])

	def test_a_station_with_no_name_is_refused(self):
		reply = self.save(
			settings={**SETTINGS, "stations": [{"id": "k", "name": " ", "groups": []}]},
			roles=("Sales Manager",),
		)
		self.assertFalse(reply["success"])
		self.assertEqual(self.doc.saved, 0)

	def test_two_stations_with_the_same_name_are_refused(self):
		stations = [{"id": "a", "name": "Bar", "groups": []}, {"id": "b", "name": "bar", "groups": []}]
		reply = self.save(settings={**SETTINGS, "stations": stations}, roles=("Sales Manager",))
		self.assertFalse(reply["success"])
		self.assertIn("same name", reply["message"])

	def test_too_many_stations_or_notes_are_refused(self):
		many = [{"id": f"s{n}", "name": f"Station {n}", "groups": []} for n in range(13)]
		self.assertFalse(
			self.save(settings={**SETTINGS, "stations": many}, roles=("Sales Manager",))["success"]
		)
		self.assertFalse(
			self.save(
				settings={**SETTINGS, "quickNotes": [f"n{n}" for n in range(13)]}, roles=("Sales Manager",)
			)["success"]
		)

	def test_settings_that_are_not_settings_are_refused(self):
		for bad in ("{not json", "[1]", "null", 5, {"stations": "Kitchen"}, {"stations": ["Kitchen"]}):
			reply = self.save(settings=bad, roles=("Sales Manager",))
			self.assertFalse(reply["success"], bad)
			self.assertEqual(self.doc.saved, 0)

	def test_settings_may_be_sent_as_a_json_string(self):
		self.assertTrue(self.save(settings=json.dumps(SETTINGS), roles=("Sales Manager",))["success"])
		self.assertEqual(json.loads(self.doc.settings), SETTINGS)

	def test_the_screens_switch_is_only_on_when_it_is_exactly_true(self):
		self.save(settings={**SETTINGS, "screens": "yes", "enabled": 1}, roles=("Sales Manager",))
		stored = json.loads(self.doc.settings)
		self.assertFalse(stored["screens"])
		self.assertFalse(stored["enabled"])


class TestSendTickets(KitchenCase):
	def send(self, tickets=None, found=(), number=0, lock=1, warehouse="Main Store - SA", **as_user):
		"""Send `tickets`; `found` are tickets the server already has; `number` is the day's highest so far."""
		self.as_user(**as_user)
		self.order = []

		def sql(query, values=None):
			self.order.append(query.split("(")[0].split(" `")[0].strip())
			if "GET_LOCK" in query:
				return ((lock,),)
			if "MAX(number)" in query:
				self.max_args = values
				return ((number,),)
			return ()

		self.db.sql.side_effect = sql
		self.db.commit.side_effect = lambda: self.order.append("COMMIT")
		self.made = []

		def new_doc(doctype):
			doc = FakeDoc(owner=frappe.session.user)
			self.made.append(doc)
			return doc

		with (
			patch.object(frappe, "new_doc", side_effect=new_doc) as self.new_doc,
			patch.object(frappe, "get_all", return_value=list(found)) as self.get_all,
		):
			return kitchen_api.send_tickets(
				tickets=[_ticket()] if tickets is None else tickets, company="Shop A", warehouse=warehouse
			)

	def test_a_new_ticket_is_saved_numbered_and_returned_in_the_shape_the_till_expects(self):
		reply = self.send()
		self.assertTrue(reply["success"])
		(ticket,) = reply["data"]["tickets"]
		self.assertEqual(
			(ticket["client_id"], ticket["station"], ticket["number"], ticket["round"], ticket["status"]),
			("c-1", "Kitchen", 1, 1, "New"),
		)
		self.assertEqual(
			(ticket["label"], ticket["order_type"], ticket["sent_by_name"]), ("Table 4", "table", "Amina")
		)
		self.assertEqual(ticket["created_at"], "2026-10-10T16:42:00.000Z")
		self.assertEqual(ticket["lines"]["adds"][0]["note"], "no pilipili")
		self.assertEqual(ticket["lines"]["changes"], [])
		self.assertTrue(ticket["id"])

	def test_the_ticket_is_stored_for_the_users_company_store_and_day_without_desk_permissions(self):
		self.send()
		(doc,) = self.made
		self.assertEqual(
			(doc.company, doc.warehouse, doc.ticket_date, doc.status),
			("Shop A", "Main Store - SA", "2026-10-10", "New"),
		)
		self.assertEqual(doc.save_kwargs, {"ignore_permissions": True})
		self.assertEqual(doc.inserted, 1)

	def test_the_number_comes_after_the_days_highest_for_this_business_and_store(self):
		reply = self.send(number=13)
		self.assertEqual(reply["data"]["tickets"][0]["number"], 14)
		self.assertEqual(self.max_args, ("Shop A", "Main Store - SA", "2026-10-10"))

	def test_numbers_start_again_each_day(self):
		# the highest number is asked for per day, so a new day finds none and starts at 1
		self.assertEqual(self.send(number=0)["data"]["tickets"][0]["number"], 1)

	def test_the_number_is_never_taken_from_the_request(self):
		reply = self.send(tickets=[_ticket(number=99)], number=4)
		self.assertEqual(reply["data"]["tickets"][0]["number"], 5)

	def test_every_ticket_of_one_send_shares_one_number(self):
		reply = self.send(tickets=[_ticket("c-1", "Kitchen"), _ticket("c-2", "Bar")], number=6)
		self.assertEqual([t["number"] for t in reply["data"]["tickets"]], [7, 7])
		self.assertEqual([t["station"] for t in reply["data"]["tickets"]], ["Kitchen", "Bar"])

	def test_the_replies_follow_the_order_they_were_sent_in(self):
		reply = self.send(tickets=[_ticket("c-2", "Bar"), _ticket("c-1", "Kitchen")])
		self.assertEqual([t["client_id"] for t in reply["data"]["tickets"]], ["c-2", "c-1"])

	def test_the_request_cannot_choose_who_sent_it(self):
		reply = self.send(tickets=[_ticket(sent_by="someone.else@shop-a.test")], user="amina@shop-a.test")
		self.assertEqual(reply["data"]["tickets"][0]["sent_by"], "amina@shop-a.test")

	def test_sending_a_ticket_the_server_already_has_creates_nothing_and_keeps_its_number(self):
		reply = self.send(found=[_stored(client_id="c-1", number=14, name="k9")], number=20)
		self.assertTrue(reply["success"])
		self.assertEqual(self.made, [])
		self.assertEqual(reply["data"]["tickets"][0]["number"], 14)
		self.assertEqual(reply["data"]["tickets"][0]["id"], "k9")

	def test_a_retry_that_finds_only_some_of_its_tickets_gives_the_rest_the_same_number(self):
		reply = self.send(
			tickets=[_ticket("c-1", "Kitchen"), _ticket("c-2", "Bar")],
			found=[_stored(client_id="c-1", number=14)],
			number=20,
		)
		self.assertEqual([t["number"] for t in reply["data"]["tickets"]], [14, 14])
		self.assertEqual(len(self.made), 1)

	def test_the_lookup_for_tickets_already_saved_is_limited_to_this_company(self):
		self.send()
		filters = self.get_all.call_args.kwargs["filters"]
		self.assertEqual(filters, {"company": "Shop A", "client_id": ["in", ["c-1"]]})

	def test_sends_take_turns_the_tickets_are_committed_before_the_next_sender_is_let_in(self):
		self.send()
		self.assertEqual(self.order, ["SELECT GET_LOCK", "SELECT COALESCE", "COMMIT", "SELECT RELEASE_LOCK"])
		key, seconds = self.db.sql.call_args_list[0].args[1]
		self.assertTrue(key.startswith("kt:"))
		self.assertLessEqual(len(key), 64)  # the longest name MariaDB accepts for a lock
		self.assertGreater(seconds, 0)

	def test_the_lock_is_for_this_business_store_and_day_only(self):
		self.send(warehouse="Main Store - SA")
		first = self.db.sql.call_args_list[0].args[1][0]
		self.send(warehouse="Branch - SA")
		second = self.db.sql.call_args_list[0].args[1][0]
		self.assertNotEqual(first, second)

	def test_the_lock_is_let_go_even_when_saving_fails(self):
		self.as_user()
		self.db.sql.side_effect = lambda q, v=None: (
			((1,),) if "GET_LOCK" in q else ((0,),) if "MAX" in q else ()
		)
		failing = FakeDoc()
		failing.insert = MagicMock(side_effect=RuntimeError("database went away"))
		with (
			patch.object(frappe, "new_doc", return_value=failing),
			patch.object(frappe, "get_all", return_value=[]),
			self.assertRaises(RuntimeError),
		):
			kitchen_api.send_tickets(tickets=[_ticket()], company="Shop A")
		self.assertIn("RELEASE_LOCK", self.db.sql.call_args.args[0])
		self.db.commit.assert_not_called()

	def test_when_the_kitchen_is_busy_the_waiter_is_told_and_nothing_is_saved(self):
		reply = self.send(lock=0)
		self.assertFalse(reply["success"])
		self.assertIn("busy", reply["message"])
		self.assertEqual(self.made, [])
		self.db.commit.assert_not_called()

	def test_a_store_less_send_is_numbered_with_the_stores_that_have_none(self):
		self.send(warehouse=None)
		self.assertEqual(self.max_args[1], "")
		self.assertIsNone(self.made[0].warehouse)

	def assert_refused(self, tickets, text=None, **kwargs):
		reply = self.send(tickets=tickets, **kwargs)
		self.assertFalse(reply["success"], tickets)
		if text:
			self.assertIn(text, reply["message"])
		self.assertEqual(self.made, [])
		self.db.sql.assert_not_called()  # refused before the lock was even asked for

	def test_nothing_to_send_is_refused(self):
		for empty in ([], "[]", "{not json", {"client_id": "c-1"}, 5, "null"):
			self.assert_refused(empty)

	def test_more_than_20_tickets_in_one_send_are_refused(self):
		self.assert_refused([_ticket(f"c-{n}") for n in range(21)], "too many")
		self.assertTrue(self.send(tickets=[_ticket(f"c-{n}", f"S{n}") for n in range(20)])["success"])

	def test_a_ticket_without_an_id_or_a_station_is_refused(self):
		self.assert_refused([_ticket(client_id="")])
		self.assert_refused([_ticket(client_id="   ")])
		self.assert_refused([_ticket(station="")])
		self.assert_refused(["not a ticket"])

	def test_two_tickets_with_the_same_id_in_one_send_are_refused(self):
		self.assert_refused([_ticket("c-1", "Kitchen"), _ticket("c-1", "Bar")], "same id")

	def test_a_ticket_with_nothing_on_it_is_refused(self):
		self.assert_refused([_ticket(lines={"adds": [], "changes": [], "voids": []})], "at least one")
		self.assert_refused([_ticket(lines={})])

	def test_a_ticket_with_only_a_cancellation_is_accepted(self):
		voids = {"voids": [{"item_code": "PILAU", "item_name": "Pilau", "qty": 1, "note": ""}]}
		reply = self.send(tickets=[_ticket(lines=voids)])
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"]["tickets"][0]["lines"]["voids"][0]["qty"], 1)

	def test_more_than_100_lines_on_one_ticket_are_refused_and_exactly_100_are_accepted(self):
		many = lambda n: {"adds": [{"item_code": f"I{i}", "qty": 1} for i in range(n)]}  # noqa: E731
		self.assert_refused([_ticket(lines=many(101))], "100")
		self.assertTrue(self.send(tickets=[_ticket(lines=many(100))])["success"])

	def test_lines_that_are_not_lists_of_items_are_refused(self):
		self.assert_refused([_ticket(lines={"adds": "Pilau"})])
		self.assert_refused([_ticket(lines={"adds": ["Pilau"]})])
		self.assert_refused([_ticket(lines="{not json")])

	def test_a_ticket_over_64_kb_is_refused(self):
		self.assert_refused(
			[_ticket(lines={"adds": [{"item_code": "X", "note": "n" * (64 * 1024)}]})], "too large"
		)

	def test_lines_may_be_sent_as_a_json_string(self):
		reply = self.send(tickets=[_ticket(lines=json.dumps({"adds": [{"item_code": "PILAU", "qty": 2}]}))])
		self.assertTrue(reply["success"])

	def test_the_label_is_trimmed_to_40_characters_and_tidied(self):
		self.send(tickets=[_ticket(label="  " + "T" * 60)])
		self.assertEqual(self.made[0].label, "T" * 40)
		self.send(tickets=[_ticket(label="Table    4")])
		self.assertEqual(self.made[0].label, "Table 4")

	def test_a_counter_order_is_kept_as_one_and_anything_else_is_a_table(self):
		self.send(tickets=[_ticket(order_type="counter", label="")])
		self.assertEqual((self.made[0].order_type, self.made[0].label), ("counter", ""))
		for odd in ("takeaway", None, 5, ""):
			self.send(tickets=[_ticket(order_type=odd)])
			self.assertEqual(self.made[0].order_type, "table")

	def test_the_round_is_at_least_one(self):
		for odd, expected in ((0, 1), (-3, 1), (None, 1), ("2", 2), ("two", 1), (3, 3)):
			self.send(tickets=[_ticket(round=odd)])
			self.assertEqual(self.made[0].round, expected, odd)

	def test_the_time_sent_is_kept_in_utc_and_a_missing_one_is_stamped_now(self):
		self.send(tickets=[_ticket(created_at="2026-10-10T19:42:00.250+03:00")])
		self.assertEqual(self.made[0].created_at, datetime(2026, 10, 10, 16, 42, 0, 250000))
		self.send(tickets=[_ticket(created_at=None)])
		self.assertLess(abs(kitchen_api._utc_now() - self.made[0].created_at), timedelta(minutes=1))
		self.send(tickets=[_ticket(created_at="last tuesday")])
		self.assertLess(abs(kitchen_api._utc_now() - self.made[0].created_at), timedelta(minutes=1))

	def test_a_ticket_never_carries_a_customer_name(self):
		self.send(tickets=[_ticket(customer="Jane Wanjiru", customer_name="Jane Wanjiru")])
		self.assertNotIn("Jane", json.dumps(self.made[0].__dict__, default=str))


class TestListTickets(KitchenCase):
	def list(self, rows=(), **request):
		self.as_user()
		with patch.object(frappe, "get_all", return_value=list(rows)) as get_all:
			reply = kitchen_api.list_tickets(company="Shop A", **request)
		self.get_all = get_all
		return reply

	def test_only_the_users_company_is_asked_for(self):
		self.list()
		self.assertEqual(self.get_all.call_args.kwargs["filters"], {"company": "Shop A"})

	def test_a_ticket_is_returned_in_the_shape_the_till_expects(self):
		reply = self.list(
			[
				_stored(
					status="Ready", status_by="cook@shop-a.test", status_at=datetime(2026, 10, 10, 16, 50, 0)
				)
			]
		)
		self.assertTrue(reply["success"])
		self.assertEqual(
			reply["data"]["tickets"],
			[
				{
					"id": "k1",
					"client_id": "c-1",
					"station": "Kitchen",
					"number": 14,
					"round": 2,
					"label": "Table 4",
					"order_type": "table",
					"sent_by": "amina@shop-a.test",
					"sent_by_name": "Amina",
					"created_at": "2026-10-10T16:42:00.000Z",
					"status": "Ready",
					"status_by": "cook@shop-a.test",
					"status_by_name": "Amina",
					"status_at": "2026-10-10T16:50:00.000Z",
					"lines": {
						"adds": [{"item_code": "PILAU", "item_name": "Pilau", "qty": 2, "note": ""}],
						"changes": [],
						"voids": [],
					},
				}
			],
		)

	def test_a_ticket_not_yet_worked_on_has_empty_status_fields(self):
		(ticket,) = self.list([_stored()])["data"]["tickets"]
		self.assertEqual((ticket["status_by"], ticket["status_at"]), ("", ""))

	def test_the_list_is_oldest_first(self):
		# the newest are fetched, so that a long list never hides what just came in, then put in order
		rows = [_stored(name="new", number=3), _stored(name="old", number=1)]
		reply = self.list(rows)
		self.assertEqual(self.get_all.call_args.kwargs["order_by"], "created_at desc")
		self.assertEqual([t["id"] for t in reply["data"]["tickets"]], ["old", "new"])

	def test_the_list_is_capped(self):
		self.list()
		self.assertEqual(self.get_all.call_args.kwargs["limit_page_length"], 500)

	def test_a_station_is_asked_for_by_name(self):
		self.list(station="Bar")
		self.assertEqual(self.get_all.call_args.kwargs["filters"]["station"], "Bar")

	def test_statuses_limit_the_list_and_unknown_ones_are_ignored(self):
		self.list(statuses=["New", "Ready", "Nonsense"])
		self.assertEqual(self.get_all.call_args.kwargs["filters"]["status"], ["in", ["New", "Ready"]])

	def test_asking_only_for_statuses_that_do_not_exist_returns_nothing_and_asks_nothing(self):
		reply = self.list(statuses=["Nonsense"])
		self.assertEqual(reply, {"success": True, "data": {"tickets": []}})
		self.get_all.assert_not_called()

	def test_since_limits_the_list_to_tickets_sent_from_that_time_in_utc(self):
		self.list(since="2026-10-09T19:42:00.000+03:00")
		self.assertEqual(
			self.get_all.call_args.kwargs["filters"]["created_at"], [">=", datetime(2026, 10, 9, 16, 42)]
		)

	def test_a_time_that_cannot_be_read_is_refused(self):
		reply = self.list(since="yesterday-ish")
		self.assertFalse(reply["success"])
		self.get_all.assert_not_called()

	def test_a_store_sees_its_own_tickets_and_tickets_sent_with_no_store(self):
		self.list(warehouse="Main Store - SA")
		self.assertEqual(
			self.get_all.call_args.kwargs["or_filters"],
			[["warehouse", "is", "not set"], ["warehouse", "=", "Main Store - SA"]],
		)

	def test_without_a_store_every_ticket_of_the_business_is_listed(self):
		self.list()
		self.assertNotIn("or_filters", self.get_all.call_args.kwargs)

	def test_an_unreadable_ticket_does_not_hide_the_others(self):
		reply = self.list([_stored(name="bad", lines="{not json"), _stored(name="good")])
		tickets = {t["id"]: t for t in reply["data"]["tickets"]}
		self.assertEqual(tickets["bad"]["lines"], {"adds": [], "changes": [], "voids": []})
		self.assertEqual(len(tickets["good"]["lines"]["adds"]), 1)


class TestSetTicketStatus(KitchenCase):
	def set_status(self, status="Ready", found="k1", **request):
		self.as_user()
		self.db.get_value.side_effect = lambda doctype, *a, **k: "Cook" if doctype == "User" else found
		self.doc = FakeDoc(**_stored())
		with patch.object(frappe, "get_doc", return_value=self.doc) as self.get_doc:
			return kitchen_api.set_ticket_status(id=request.pop("id", "k1"), status=status, company="Shop A")

	def test_a_station_marks_a_ticket_ready_and_who_and_when_is_recorded(self):
		reply = self.set_status("Ready")
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"]["status"], "Ready")
		self.assertEqual(self.doc.status_by, "amina@shop-a.test")
		self.assertLess(abs(kitchen_api._utc_now() - self.doc.status_at), timedelta(minutes=1))
		self.assertEqual((self.doc.saved, self.doc.save_kwargs), (1, {"ignore_permissions": True}))

	def test_every_status_is_allowed_so_a_mistaken_tap_can_be_undone(self):
		for status in ("New", "Preparing", "Ready", "Served"):
			self.assertEqual(self.set_status(status)["data"]["status"], status)

	def test_a_status_that_does_not_exist_is_refused(self):
		for status in ("Cancelled", "ready", "", None):
			reply = self.set_status(status)
			self.assertFalse(reply["success"], status)
			self.assertEqual(self.doc.saved, 0)

	def test_a_ticket_is_looked_up_within_the_users_company_only(self):
		self.set_status()
		self.assertEqual(
			self.db.get_value.call_args_list[0].args, ("Kitchen Ticket", {"name": "k1", "company": "Shop A"})
		)

	def test_a_ticket_that_is_not_there_or_belongs_to_another_business_is_not_found(self):
		reply = self.set_status(found=None)
		self.assertFalse(reply["success"])
		self.assertIn("not found", reply["message"])
		self.get_doc.assert_not_called()
		self.assertEqual(self.doc.saved, 0)

	def test_an_id_sent_as_a_number_is_looked_up_as_text(self):
		self.set_status(id=42)
		self.assertEqual(self.db.get_value.call_args_list[0].args[1]["name"], "42")


class TestPurgeOldTickets(KitchenCase):
	def test_only_tickets_from_more_than_90_days_ago_are_removed(self):
		self.as_user()
		now = datetime(2026, 10, 10, 12, 0, 0)
		with patch.object(kitchen_api, "_utc_now", return_value=now):
			kitchen_api.purge_old_tickets()
		query, values = self.db.sql.call_args.args
		self.assertTrue(query.startswith("DELETE FROM `tabKitchen Ticket`"))
		self.assertIn("created_at < %s", query)
		self.assertEqual(values, (datetime(2026, 7, 12, 12, 0, 0),))

	def test_the_daily_job_is_switched_on_next_to_the_held_sales_one(self):
		jobs = hooks.scheduler_events["daily"]
		self.assertIn("techsavanna_pos.api.kitchen_api.purge_old_tickets", jobs)
		self.assertIn("techsavanna_pos.api.held_sales_api.purge_old_held_sales", jobs)
		self.assertTrue(callable(kitchen_api.purge_old_tickets))


if __name__ == "__main__":
	unittest.main()
