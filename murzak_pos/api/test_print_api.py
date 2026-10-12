# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for print formats and the print call. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe
import jinja2

from murzak_pos.api import print_api as api


class Row:
	"""A stand-in for a document or one of its rows."""

	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def __getattr__(self, key):
		return None

	def get_formatted(self, key, parent=None):
		return f"<{self.get(key)}>"


def sale(**extra):
	values = dict(
		name="SINV-1",
		company="Acme",
		customer="Jane",
		customer_name="Jane",
		is_return=0,
		items=[Row(item_name="Tea", item_code="T1", qty=2, uom="Nos", rate=50, amount=100)],
		taxes=[Row(description="VAT 16%", tax_amount=16)],
		payments=[Row(mode_of_payment="Cash", amount=116)],
		net_total=100,
		grand_total=116,
		posting_date="2026-10-12",
		custom_scu_id="SCU1",
		custom_qr_code="https://etims.example/receipt/1",
		custom_current_receipt_number="1",
		custom_total_receipt_number="9",
		custom_receipt_signature="SIG",
	)
	values.update(extra)
	return Row(**values)


def render(file_name, doc, letter_head="", footer=""):
	env = jinja2.Environment()
	template = env.from_string(api.template_html(file_name))
	return template.render(
		doc=doc,
		_=lambda text: text,
		murzak_company=lambda company: Row(company_name="Acme Ltd", tax_id="P0001", phone_no="0700"),
		murzak_qr=lambda text: "data:image/png;base64,QR",
		letter_head=letter_head,
		footer=footer,
		no_letterhead=0 if letter_head else 1,
	)


class TemplateCase(unittest.TestCase):
	def test_the_tax_invoice_shows_the_customer_the_vat_and_the_etims_qr(self):
		html = render("tax_invoice.html", sale())
		for text in ("Tax Invoice", "Jane", "VAT 16%", "SCU1", "data:image/png;base64,QR", "Acme Ltd"):
			self.assertIn(text, html)

	def test_a_return_prints_as_a_credit_note(self):
		self.assertIn("Credit Note", render("tax_invoice.html", sale(is_return=1, return_against="SINV-0")))

	def test_a_sale_not_sent_to_kra_has_no_etims_block(self):
		html = render("tax_invoice.html", sale(custom_scu_id=None, custom_qr_code=None))
		self.assertNotIn("KRA eTIMS", html)
		self.assertNotIn("data:image", html)

	def test_the_businesss_letter_head_replaces_the_plain_header(self):
		html = render(
			"tax_invoice.html", sale(), letter_head="<div>OUR LETTER HEAD</div>", footer="THE FOOTER"
		)
		self.assertIn("OUR LETTER HEAD", html)
		self.assertIn("THE FOOTER", html)
		self.assertNotIn("Acme Ltd", html)

	def test_the_receipt_fits_80mm_and_carries_the_qr(self):
		html = render("receipt_80mm.html", sale())
		self.assertIn("72mm", html)
		self.assertIn("data:image/png;base64,QR", html)
		self.assertIn("Cash", html)

	def test_the_receipt_of_a_pos_invoice_has_no_etims_block(self):
		html = render("receipt_80mm.html", sale(custom_scu_id=None, custom_qr_code=None))
		self.assertNotIn("KRA eTIMS", html)

	def test_the_delivery_note_has_no_prices_and_has_signature_lines(self):
		html = render("delivery_note.html", sale())
		self.assertIn("Delivery Note", html)
		self.assertIn("Received by", html)
		self.assertNotIn("<50>", html)

	def test_the_purchase_order_renders(self):
		doc = sale(supplier="Sup", supplier_name="Sup", transaction_date="2026-10-12")
		self.assertIn("Purchase Order", render("purchase_order.html", doc))


class ChooseFormatCase(unittest.TestCase):
	def test_each_document_has_its_usual_format(self):
		self.assertEqual(api.choose_format("Sales Invoice", None), ("invoice", "Murzak Tax Invoice"))
		self.assertEqual(
			api.choose_format("POS Invoice", None), ("receipt", "Murzak Receipt 80mm (POS Invoice)")
		)
		self.assertEqual(api.choose_format("Delivery Note", None)[1], "Murzak Delivery Note")
		self.assertEqual(api.choose_format("Purchase Order", None)[1], "Murzak Purchase Order")

	def test_a_receipt_can_be_asked_for_on_a_sales_invoice(self):
		self.assertEqual(
			api.choose_format("Sales Invoice", "receipt")[1], "Murzak Receipt 80mm (Sales Invoice)"
		)

	def test_a_format_made_for_another_document_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.choose_format("Purchase Order", "receipt")

	def test_other_documents_cannot_be_printed(self):
		with self.assertRaises(frappe.ValidationError):
			api.choose_format("Item", None)

	def test_a_print_format_on_the_site_is_allowed_and_an_unknown_one_is_not(self):
		with patch.object(api.frappe, "db", MagicMock(exists=MagicMock(return_value=True))):
			self.assertEqual(api.choose_format("Sales Invoice", "Detailed"), ("", "Detailed"))
		with patch.object(api.frappe, "db", MagicMock(exists=MagicMock(return_value=False))):
			with self.assertRaises(frappe.ValidationError):
				api.choose_format("Sales Invoice", "Nope")


class InstallCase(unittest.TestCase):
	def test_every_template_file_is_there_and_the_style_is_written_in(self):
		for _alias, (_name, file_name, _doctypes) in api.FORMATS.items():
			html = api.template_html(file_name)
			self.assertNotIn("{% include", html)
			self.assertTrue(html.strip())


class LetterheadCase(unittest.TestCase):
	def test_only_a_manager_can_change_the_letter_head(self):
		with (
			patch.object(api.frappe, "get_roles", return_value=["Sales User"]),
			patch.object(api.frappe, "session", MagicMock(user="cashier@x")),
		):
			with self.assertRaises(frappe.PermissionError):
				api.save_letterhead("Acme")

	def test_the_generated_header_uses_the_businesss_details_and_escapes_them(self):
		info = frappe._dict(
			company_name="A & B <Ltd>", tax_id="P1", phone_no="0700", email=None, website=None
		)
		with patch.object(api, "murzak_company", return_value=info):
			html = api.generated_header("Acme")
		self.assertIn("A &amp; B &lt;Ltd&gt;", html)
		self.assertIn("PIN: P1", html)
