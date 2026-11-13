import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import pharmacy, product_api
from murzak_pos.api.pharmacy_rules import controlled_register, movement_kind

ITEMS = {"DIAZ": "Diazepam 5mg", "PETH": "Pethidine 50mg"}
ENTRIES = [
	{
		"item_code": "DIAZ",
		"date": "2026-10-20",
		"qty": 100,
		"voucher_type": "Purchase Receipt",
		"reference": "PR-1",
	},
	{
		"item_code": "DIAZ",
		"date": "2026-10-25",
		"qty": -10,
		"voucher_type": "POS Invoice",
		"reference": "POS-1",
	},
	{
		"item_code": "DIAZ",
		"date": "2026-11-02",
		"time": "09:30:00",
		"qty": -20,
		"voucher_type": "POS Invoice",
		"reference": "POS-2",
		"patient": "Jane",
		"prescriber": "Dr O",
		"dispensed_by": "Amina",
	},
	{
		"item_code": "DIAZ",
		"date": "2026-11-05",
		"qty": 2,
		"voucher_type": "POS Invoice",
		"reference": "POS-3",
	},
	{
		"item_code": "DIAZ",
		"date": "2026-11-06",
		"qty": -1,
		"voucher_type": "Stock Reconciliation",
		"reference": "SR-1",
	},
	{
		"item_code": "OTHER",
		"date": "2026-11-06",
		"qty": -1,
		"voucher_type": "POS Invoice",
		"reference": "POS-9",
	},
]


class TestControlledRegister(unittest.TestCase):
	def test_the_opening_balance_movements_and_running_balance_add_up(self):
		register = controlled_register(ITEMS, ENTRIES, "2026-11-01")
		diaz = register[0]
		self.assertEqual(diaz["item_name"], "Diazepam 5mg")
		self.assertEqual(diaz["opening"], 90)  # 100 received, 10 dispensed before the period
		self.assertEqual([line["balance"] for line in diaz["lines"]], [70, 72, 71])
		self.assertEqual((diaz["received"], diaz["issued"], diaz["closing"]), (2, 21, 71))

	def test_each_movement_says_what_it_was_and_a_sale_names_the_patient(self):
		lines = controlled_register(ITEMS, ENTRIES, "2026-11-01")[0]["lines"]
		self.assertEqual(
			[line["kind"] for line in lines], ["Dispensed", "Returned by patient", "Count adjustment (less)"]
		)
		self.assertEqual(
			(lines[0]["patient"], lines[0]["prescriber"], lines[0]["dispensed_by"]), ("Jane", "Dr O", "Amina")
		)
		self.assertEqual((lines[0]["qty_out"], lines[0]["time"]), (20, "09:30"))

	def test_a_controlled_medicine_with_no_movement_still_shows_its_balance(self):
		pethidine = controlled_register(ITEMS, ENTRIES, "2026-11-01")[1]
		self.assertEqual((pethidine["opening"], pethidine["closing"], pethidine["lines"]), (0, 0, []))

	def test_unknown_documents_are_still_named(self):
		self.assertEqual(movement_kind("Something Else", 5), "Stock in")
		self.assertEqual(movement_kind("Purchase Receipt", -5), "Returned to supplier")


class TestGetControlledRegister(unittest.TestCase):
	def test_the_merged_shift_invoice_is_left_out_and_pos_sales_are_read_one_by_one(self):
		ledger = [
			frappe._dict(
				item_code="DIAZ",
				warehouse="Main",
				actual_qty=50,
				posting_date="2026-11-01",
				posting_time="08:00",
				voucher_type="Purchase Receipt",
				voucher_no="PR-1",
			),
			frappe._dict(
				item_code="DIAZ",
				warehouse="Main",
				actual_qty=-5,
				posting_date="2026-11-02",
				posting_time="20:00",
				voucher_type="Sales Invoice",
				voucher_no="SINV-MERGED",
			),
		]

		def get_all(doctype, **kwargs):
			if doctype == "Item":
				return [frappe._dict(name="DIAZ", item_name="Diazepam 5mg")]
			if doctype == "Stock Ledger Entry":
				return ledger
			if doctype == "Sales Invoice":
				return ["SINV-MERGED"]
			return []

		meta = MagicMock()
		meta.has_field.return_value = True
		pos = [
			{
				"item_code": "DIAZ",
				"warehouse": "Main",
				"qty": -5,
				"date": "2026-11-02",
				"time": "10:00",
				"voucher_type": "POS Invoice",
				"reference": "POS-7",
				"patient": "Jane",
			}
		]
		with (
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(pharmacy.role_tiers, "require_access") as require_access,
			patch.object(pharmacy, "resolve_company", return_value="Duka"),
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "get_all", side_effect=get_all),
			patch.object(frappe, "db", MagicMock(sql=MagicMock(return_value=pos)), create=True),
		):
			data = pharmacy.get_controlled_register(from_date="2026-11-01", to_date="2026-11-13")["data"]
		require_access.assert_called_once_with("all_sales")
		lines = data["medicines"][0]["lines"]
		self.assertEqual([line["reference"] for line in lines], ["PR-1", "POS-7"])
		self.assertEqual(data["medicines"][0]["closing"], 45)


class TestPharmacyFields(unittest.TestCase):
	def test_a_controlled_medicine_is_always_prescription_only_and_the_ppb_number_is_tidied(self):
		item = MagicMock()
		item.meta.has_field.return_value = True
		product_api._set_pharmacy_fields(item, "1", " h2026/123 <x>")
		item.set.assert_any_call("custom_controlled_drug", 1)
		item.set.assert_any_call("custom_prescription_only", 1)
		item.set.assert_any_call("custom_ppb_registration_no", "H2026/123 X")

	def test_nothing_sent_changes_nothing(self):
		item = MagicMock()
		item.meta.has_field.return_value = True
		product_api._set_pharmacy_fields(item, None, None)
		item.set.assert_not_called()


if __name__ == "__main__":
	unittest.main()
