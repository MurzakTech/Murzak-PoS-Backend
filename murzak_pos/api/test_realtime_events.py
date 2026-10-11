# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the live screen messages. No database is needed.

The rules that matter: a message names only the record, status, reference and amount (nothing secret or
personal); a payment goes to the user who started it, never to another user; and a message that cannot
be sent never breaks the payment or the ticket.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from murzak_pos.api import realtime_events as rt


class Doc(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def get_doc_before_save(self):
		return getattr(self, "_before", None)


def mpesa_log(**changes):
	values = {
		"doctype": "MPESA Transaction Log",
		"name": "MPL-0001",
		"status": "Success",
		"mpesa_receipt_number": "ABC123XYZ",
		"amount": 250.0,
		"company": "Acme",
		"owner": "cashier@acme.test",
		"phone_number": "254712345678",
		"callback_payload": "{secret}",
		"payer_name": "Jane Doe",
		"_before": Doc(status="Pending"),
	}
	values.update(changes)
	return Doc(**values)


class MessageCase(unittest.TestCase):
	def test_payment_message_carries_no_personal_or_secret_data(self):
		message = rt.payment_message(mpesa_log())
		self.assertEqual(
			set(message),
			{"kind", "doctype", "name", "status", "reference", "amount", "gateway", "company"},
		)
		text = repr(message)
		for private in ("254712345678", "{secret}", "Jane Doe"):
			self.assertNotIn(private, text)

	def test_payment_message_content(self):
		message = rt.payment_message(mpesa_log())
		self.assertEqual(message["reference"], "ABC123XYZ")
		self.assertEqual(message["amount"], 250.0)
		self.assertEqual(message["gateway"], "M-Pesa")

	def test_gateway_payment_names_its_gateway_and_code(self):
		doc = Doc(
			doctype="POS Gateway Transaction",
			name="PGT-1",
			status="Success",
			gateway="Pesapal",
			confirmation_code="PP-99",
			amount=10,
			company="Acme",
		)
		message = rt.payment_message(doc)
		self.assertEqual((message["gateway"], message["reference"]), ("Pesapal", "PP-99"))

	def test_kitchen_message_says_only_what_changed(self):
		doc = Doc(
			doctype="Kitchen Ticket", name="KT-1", status="Ready", station="Bar", number=7, company="Acme"
		)
		self.assertEqual(
			set(rt.kitchen_message(doc)),
			{"kind", "doctype", "name", "status", "station", "number", "company"},
		)


class RecipientCase(unittest.TestCase):
	def test_a_payment_goes_to_the_user_who_started_it_alone(self):
		self.assertEqual(rt.payment_recipients(mpesa_log()), ["cashier@acme.test"])

	def test_a_payment_nobody_started_goes_to_the_business(self):
		with patch.object(rt, "users_of_company", return_value=["a@acme.test", "b@acme.test"]) as users:
			self.assertEqual(rt.payment_recipients(mpesa_log(owner="Guest")), ["a@acme.test", "b@acme.test"])
		users.assert_called_once_with("Acme")

	def test_administrator_and_guest_never_receive(self):
		with patch.object(rt, "users_of_company", return_value=[]):
			self.assertEqual(rt.payment_recipients(mpesa_log(owner="Administrator")), [])

	def test_no_company_means_no_recipients(self):
		self.assertEqual(rt.users_of_company(""), [])


class SendCase(unittest.TestCase):
	def run_update(self, handler, doc, recipients=("cashier@acme.test",)):
		with (
			patch.object(rt.frappe, "publish_realtime") as publish,
			patch.object(rt, "users_of_company", return_value=list(recipients)),
		):
			handler(doc)
		return publish

	def test_final_payment_status_is_sent_after_commit_to_its_owner(self):
		publish = self.run_update(rt.on_payment_update, mpesa_log())
		publish.assert_called_once()
		args, kwargs = publish.call_args
		self.assertEqual(args[0], rt.EVENT)
		self.assertEqual(kwargs["user"], "cashier@acme.test")
		self.assertTrue(kwargs["after_commit"])
		self.assertEqual(kwargs["message"]["status"], "Success")

	def test_pending_payment_sends_nothing(self):
		publish = self.run_update(rt.on_payment_update, mpesa_log(status="Pending", _before=None))
		publish.assert_not_called()

	def test_unchanged_status_sends_nothing(self):
		publish = self.run_update(rt.on_payment_update, mpesa_log(_before=Doc(status="Success")))
		publish.assert_not_called()

	def test_new_final_record_is_sent(self):
		publish = self.run_update(
			rt.on_payment_update, mpesa_log(_before=None, owner="Guest"), ("a@x", "b@x")
		)
		self.assertEqual(publish.call_count, 2)

	def test_each_kitchen_staff_member_gets_one_message(self):
		doc = Doc(doctype="Kitchen Ticket", name="KT-1", status="New", company="Acme", _before=None)
		publish = self.run_update(rt.on_kitchen_ticket_update, doc, ("a@x", "b@x", "c@x"))
		self.assertEqual(
			sorted(call.kwargs["user"] for call in publish.call_args_list), ["a@x", "b@x", "c@x"]
		)

	def test_ticket_edit_without_status_change_sends_nothing(self):
		doc = Doc(
			doctype="Kitchen Ticket", name="KT-1", status="New", company="Acme", _before=Doc(status="New")
		)
		self.run_update(rt.on_kitchen_ticket_update, doc).assert_not_called()

	def test_a_failure_to_send_never_breaks_the_payment(self):
		with (
			patch.object(rt.frappe, "publish_realtime", side_effect=RuntimeError("redis down")),
			patch.object(rt.frappe, "log_error", MagicMock()) as log_error,
		):
			rt.on_payment_update(mpesa_log())
		log_error.assert_called_once()


if __name__ == "__main__":
	unittest.main()
