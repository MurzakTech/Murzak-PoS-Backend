import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import insurance_api as api
from murzak_pos.api import insurance_rules as rules

AAR = {"name": "AAR", "copay_percent": 10, "copay_amount": 200, "customer": "AAR Insurance"}
JUBILEE = {"name": "Jubilee", "copay_percent": 0, "copay_amount": 0, "customer": "Jubilee"}
CARD = {"insurer": "aar", "patient": "Wanjiru Kamau", "member_no": "AAR-12345", "scheme": "Safaricom staff"}


def plain(fn):
	return getattr(fn, "__wrapped__", fn)


class TestInsurers(unittest.TestCase):
	def test_insurers_are_tidied_and_kept(self):
		clean = rules.clean_insurers(
			'[{"name":" AAR ","copay_percent":"10","copay_amount":200},{"name":"aar"},{"name":""}]'
		)
		self.assertEqual(
			clean, [{"name": "AAR", "copay_percent": 10.0, "copay_amount": 200.0, "customer": None}]
		)

	def test_insurers_that_cannot_be_kept_are_refused_in_plain_words(self):
		for raw in (
			"{oops",
			{"name": "AAR"},
			[1],
			[{"name": "AAR", "copay_percent": 120}],
			[{"name": "AAR", "copay_amount": "ten"}],
		):
			with self.assertRaises(rules.InsuranceError, msg=repr(raw)):
				rules.clean_insurers(raw)

	def test_the_copay_is_the_larger_of_percent_and_amount_and_never_more_than_the_bill(self):
		self.assertEqual(rules.copay_for(AAR, 5000), 500.0)  # 10% beats 200
		self.assertEqual(rules.copay_for(AAR, 1000), 200.0)  # 200 beats 10%
		self.assertEqual(rules.copay_for(AAR, 150), 150.0)  # never more than the bill
		self.assertEqual(rules.copay_for(JUBILEE, 900), 0.0)


class TestInsuranceOnASale(unittest.TestCase):
	def test_nothing_usable_means_an_ordinary_sale(self):
		for raw in (None, "", "{oops", [], {"insurer": " "}):
			self.assertIsNone(rules.clean_insurance(raw))

	def test_a_covered_sale_is_checked_and_gives_its_claim_fields(self):
		record = rules.clean_insurance({**CARD, "copay": "500"})
		self.assertEqual(rules.check_insurance(record, [AAR, JUBILEE], "AAR Insurance")["name"], "AAR")
		fields = rules.claim_fields(record)
		self.assertEqual((fields["custom_member_no"], fields["custom_copay"]), ("AAR-12345", 500.0))
		self.assertEqual(fields["custom_claim_status"], rules.TO_CLAIM)

	def test_a_sale_that_cannot_be_claimed_is_refused(self):
		for card, customer in (
			({**CARD, "insurer": "Madison"}, "AAR Insurance"),  # not one of the pharmacy's insurers
			(CARD, "Walk-in Customer"),  # billed to someone else
			({**CARD, "member_no": ""}, "AAR Insurance"),
			({**CARD, "patient": ""}, "AAR Insurance"),
		):
			with self.assertRaises(rules.InsuranceError, msg=repr(card)):
				rules.check_insurance(rules.clean_insurance(card), [AAR], customer)


class TestAllocate(unittest.TestCase):
	CLAIMS = ({"invoice": "A", "owed": 1000}, {"invoice": "B", "owed": 600}, {"invoice": "C", "owed": 400})

	def test_a_payment_settles_claims_in_order_and_part_pays_the_last(self):
		plan = rules.allocate(1300, list(self.CLAIMS))
		self.assertEqual(
			plan,
			[
				{"invoice": "A", "amount": 1000.0, "status": rules.PAID},
				{"invoice": "B", "amount": 300.0, "status": rules.PART_PAID},
			],
		)

	def test_a_payment_of_nothing_or_more_than_owed_is_refused(self):
		for amount in (0, -5, 2000.5):
			with self.assertRaises(rules.InsuranceError, msg=amount):
				rules.allocate(amount, list(self.CLAIMS))
		self.assertEqual(len(rules.allocate(2000, list(self.CLAIMS))), 3)


class TestClaimForSale(unittest.TestCase):
	def test_the_claim_is_checked_against_the_saved_insurers(self):
		with (
			patch.object(api, "read_insurers", return_value=[AAR]),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			self.assertEqual(api.claim_for_sale("Chemist Ltd", "Walk-in", None), {})
			self.assertEqual(
				api.claim_for_sale("Chemist Ltd", "AAR Insurance", CARD)["custom_insurer"], "AAR"
			)
			with self.assertRaises(frappe.ValidationError):
				api.claim_for_sale("Chemist Ltd", "Walk-in", CARD)


class TestCopayOnTheSale(unittest.TestCase):
	def test_the_copay_is_what_the_patient_paid_now_not_what_the_till_said(self):
		from murzak_pos.api import sales_api

		payments = [
			{"mode_of_payment": "Cash", "amount": 300},
			{"mode_of_payment": "M-Pesa", "amount": 200},
			{"mode_of_payment": "Credit", "amount": 4500, "use_receivable_account": 1},
		]
		with patch.object(api, "claim_for_sale", return_value={"custom_insurer": "AAR", "custom_copay": 9}):
			claim = sales_api._insurance_claim("Chemist Ltd", "AAR Insurance", CARD, payments)
		self.assertEqual(claim["custom_copay"], 500.0)
		self.assertEqual(sales_api._insurance_claim("Chemist Ltd", "Walk-in", None, payments), {})


class TestRecordInsurerPayment(unittest.TestCase):
	def run_payment(self, invoices, amount, rows):
		"""rows: {invoice: (doctype, row)}; Sales Invoice outstanding is 10,000 each."""
		db = MagicMock()
		db.get_value.return_value = 10000
		entries = []

		def make_entry(*_a, **kw):
			entry = MagicMock()
			entry.references = [MagicMock()]
			entry.name = f"PE-{len(entries) + 1}"
			entries.append((_a, kw, entry))
			return entry

		with (
			patch.object(frappe, "db", db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api.role_tiers, "require_access"),
			patch.object(api, "resolve_company", return_value="Chemist Ltd"),
			patch.object(api, "_own_claim", side_effect=lambda _c, name: rows[name]),
			patch.object(api, "_set_status") as set_status,
			patch(
				"murzak_pos.api.sales_list_api._mode_account",
				return_value={"account": "Bank - CL", "account_currency": "KES"},
			),
			patch(
				"erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
				side_effect=make_entry,
			),
		):
			result = plain(api.record_insurer_payment)(invoices, amount, "Bank Transfer", reference="EFT-889")
		return result, entries, set_status

	def test_one_payment_is_shared_over_claims_and_each_is_paid_on_its_invoice(self):
		rows = {
			"POS-1": (
				"POS Invoice",
				frappe._dict(grand_total=1500, custom_copay=500, consolidated_invoice="SI-9"),
			),
			"SI-2": ("Sales Invoice", frappe._dict(grand_total=800, custom_copay=0)),
		}
		result, entries, set_status = self.run_payment(["POS-1", "SI-2"], 1400, rows)
		self.assertEqual([e[0] for e in entries], [("Sales Invoice", "SI-9"), ("Sales Invoice", "SI-2")])
		self.assertEqual([p["amount"] for p in result["data"]["payments"]], [1000.0, 400.0])
		self.assertEqual(entries[0][2].reference_no, "EFT-889")
		self.assertEqual(
			set_status.call_args_list[1].args,
			("Sales Invoice", "SI-2", {"custom_claim_status": rules.PART_PAID}),
		)

	def test_a_sale_on_an_open_shift_cannot_take_a_payment_yet(self):
		rows = {
			"POS-1": (
				"POS Invoice",
				frappe._dict(grand_total=1500, custom_copay=500, consolidated_invoice=None),
			)
		}
		with self.assertRaises(frappe.ValidationError):
			self.run_payment(["POS-1"], 1000, rows)


if __name__ == "__main__":
	unittest.main()
