"""
Print formats: an A4 tax invoice, delivery note and purchase order, and an 80 mm till receipt with the eTIMS QR code.

The formats are Jinja templates kept in `murzak_pos/templates/print_formats` and installed as Frappe Print Formats
after every migrate, so a change to a template reaches every site on the next update. Each business has its own letter
head (`save_letterhead`), used on the A4 documents. `get_print` returns a document as HTML or as a PDF.
"""

from __future__ import annotations

import base64
import os

import frappe
from frappe import _
from frappe.utils import cint

from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.payment_gateway_common import resolve_company

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "print_formats")
STYLE_FILE = "_style_a4.html"
INCLUDE_TAG = '{% include "murzak_pos/templates/print_formats/_style_a4.html" %}'

# alias -> (print format name, template file, documents it is made for)
FORMATS = {
	"invoice": ("Murzak Tax Invoice", "tax_invoice.html", ("Sales Invoice",)),
	"receipt": ("Murzak Receipt 80mm", "receipt_80mm.html", ("Sales Invoice", "POS Invoice")),
	"delivery_note": ("Murzak Delivery Note", "delivery_note.html", ("Delivery Note",)),
	"purchase_order": ("Murzak Purchase Order", "purchase_order.html", ("Purchase Order",)),
}
# What each document prints as when no format is asked for
DEFAULT_ALIAS = {
	"Sales Invoice": "invoice",
	"POS Invoice": "receipt",
	"Delivery Note": "delivery_note",
	"Purchase Order": "purchase_order",
}
NO_LETTERHEAD = {"receipt"}  # the till receipt prints its own compact header
LETTERHEAD_ROLES = frozenset({"System Manager", "Accounts Manager"})


# --- helpers for the templates ---------------------------------------------------------------------


def murzak_qr(text: str) -> str:
	"""A QR code for a text as an image address that can sit in an <img> tag."""
	import pyqrcode

	code = pyqrcode.create(str(text), error="M")
	return "data:image/png;base64," + code.png_as_base64_str(scale=4, quiet_zone=2)


def murzak_company(company: str) -> frappe._dict:
	"""The business's name and contact details for the top of a document."""
	row = frappe.db.get_value(
		"Company", company, ["company_name", "tax_id", "phone_no", "email", "website"], as_dict=True
	)
	return row or frappe._dict(company_name=company)


# --- installing the formats --------------------------------------------------------------------------


def _read(name: str) -> str:
	with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as handle:
		return handle.read()


def template_html(file_name: str) -> str:
	"""A template with its shared style written in, so a print format stands on its own."""
	return _read(file_name).replace(INCLUDE_TAG, _read(STYLE_FILE))


def install_print_formats() -> None:
	"""Create or refresh the print formats. Runs after every migrate; safe to run again."""
	ensure_blank_default()
	for name, file_name, doctypes in FORMATS.values():
		html = template_html(file_name)
		for doctype in doctypes:
			label = name if len(doctypes) == 1 else f"{name} ({doctype})"
			values = {
				"doc_type": doctype,
				"module": "Murzak POS",
				"print_format_type": "Jinja",
				"custom_format": 1,
				"standard": "No",
				"disabled": 0,
				"html": html,
				"raw_printing": 0,
				"align_labels_right": 0,
				"show_section_headings": 0,
				"line_breaks": 0,
			}
			if frappe.db.exists("Print Format", label):
				doc = frappe.get_doc("Print Format", label)
				doc.update(values)
				doc.save(ignore_permissions=True)
			else:
				frappe.get_doc({"doctype": "Print Format", "name": label, **values}).insert(
					ignore_permissions=True
				)


def format_name(alias: str, doctype: str) -> str:
	name, _file, doctypes = FORMATS[alias]
	return name if len(doctypes) == 1 else f"{name} ({doctype})"


def choose_format(doctype: str, requested: str | None) -> tuple[str, str]:
	"""(alias, print format name) for a request. A format name already on the site is allowed too."""
	if doctype not in DEFAULT_ALIAS:
		frappe.throw(_("{0} cannot be printed here.").format(_(doctype)), frappe.ValidationError)
	if not requested:
		alias = DEFAULT_ALIAS[doctype]
		return alias, format_name(alias, doctype)
	if requested in FORMATS:
		if doctype not in FORMATS[requested][2]:
			frappe.throw(
				_("The {0} format is not made for {1}.").format(requested, _(doctype)), frappe.ValidationError
			)
		return requested, format_name(requested, doctype)
	if frappe.db.exists("Print Format", {"name": requested, "doc_type": doctype, "disabled": 0}):
		return "", requested
	frappe.throw(
		_("There is no print format called {0} for {1}.").format(requested, _(doctype)),
		frappe.ValidationError,
	)


# --- letter head per business -------------------------------------------------------------------------


BLANK_LETTERHEAD = "No Letter Head"


def ensure_blank_default() -> None:
	"""
	Frappe makes the first letter head on a site the site-wide default, which would print one business's header on every
	other business's documents. A blank default, made first, keeps each business's own letter head to itself.
	"""
	if frappe.db.exists("Letter Head", {"is_default": 1}):
		return
	frappe.get_doc(
		{
			"doctype": "Letter Head",
			"letter_head_name": BLANK_LETTERHEAD,
			"source": "HTML",
			"content": "",
			"is_default": 1,
		}
	).insert(ignore_permissions=True)


def default_letterhead(company: str) -> str | None:
	return frappe.db.get_value("Company", company, "default_letter_head")


def generated_header(company: str, logo_url: str | None = None) -> str:
	"""A plain header made from the business's own details."""
	info = murzak_company(company)
	lines = [f"<b style='font-size:18px'>{frappe.utils.escape_html(info.company_name or company)}</b>"]
	if info.get("tax_id"):
		lines.append(f"PIN: {frappe.utils.escape_html(info.tax_id)}")
	contact = " | ".join(
		frappe.utils.escape_html(v)
		for v in (info.get("phone_no"), info.get("email"), info.get("website"))
		if v
	)
	if contact:
		lines.append(contact)
	logo = (
		f"<img src='{frappe.utils.escape_html(logo_url)}' style='max-height:60px;float:left;margin-right:12px'>"
		if logo_url
		else ""
	)
	return f"<div style='border-bottom:2px solid #333;padding-bottom:6px'>{logo}{'<br>'.join(lines)}<div style='clear:both'></div></div>"


@frappe.whitelist(methods=["POST"])
def save_letterhead(
	company: str | None = None,
	header_html: str | None = None,
	footer_html: str | None = None,
	logo_url: str | None = None,
) -> dict:
	"""
	Set the business's letter head, used at the top of its A4 documents.

	With no `header_html` one is made from the business's name, PIN and contact details (and `logo_url`, if given).
	"""
	if frappe.session.user != "Administrator" and not LETTERHEAD_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("Only a manager can change the letter head."), frappe.PermissionError)
	company = resolve_company(company)
	ensure_blank_default()
	name = f"{company} Letter Head"
	values = {
		"source": "HTML",
		"content": header_html or generated_header(company, logo_url),
		"footer_source": "HTML",
		"footer": footer_html or "",
		"disabled": 0,
		# never the site-wide default: every business has its own, and a business without one prints with none
		"is_default": 0,
	}
	if frappe.db.exists("Letter Head", name):
		doc = frappe.get_doc("Letter Head", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc({"doctype": "Letter Head", "letter_head_name": name, **values}).insert(
			ignore_permissions=True
		)
	frappe.db.set_value("Company", company, "default_letter_head", name)
	return {
		"success": True,
		"message": _("Letter head saved"),
		"data": {"letter_head": name, "company": company},
	}


# --- printing -----------------------------------------------------------------------------------------


@frappe.whitelist()
def get_print(doctype: str, name: str, format: str | None = None, as_pdf: int = 0) -> dict:
	"""
	A document ready to print. `format` is "invoice", "receipt", "delivery_note", "purchase_order" or the name of a print
	format on the site; left out, each document uses its usual one (an invoice is an A4 tax invoice, a POS invoice a
	receipt). With `as_pdf` the reply holds the PDF as base64 text, otherwise the HTML.
	"""
	if doctype not in DEFAULT_ALIAS:
		frappe.throw(_("{0} cannot be printed here.").format(_(doctype)), frappe.ValidationError)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} does not exist.").format(_(doctype), name), frappe.DoesNotExistError)
	ensure_record_access(doctype, name)
	if not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(_("You do not have access to this {0}.").format(_(doctype)), frappe.PermissionError)
	alias, print_format = choose_format(doctype, format)
	company = frappe.db.get_value(doctype, name, "company")
	letterhead = None if alias in NO_LETTERHEAD else default_letterhead(company)
	options = {"print_format": print_format, "no_letterhead": 0 if letterhead else 1}
	if letterhead:
		options["letterhead"] = letterhead
	if cint(as_pdf):
		try:
			pdf = frappe.get_print(doctype, name, as_pdf=True, **options)
		except OSError:
			frappe.log_error("Print to PDF failed", frappe.get_traceback())
			frappe.throw(
				_("The PDF could not be made on this server. Ask for the HTML and print from the browser."),
				frappe.ValidationError,
			)
		return {
			"success": True,
			"data": {
				"format": print_format,
				"filename": f"{name}.pdf",
				"content_type": "application/pdf",
				"pdf_base64": base64.b64encode(pdf).decode("ascii"),
			},
		}
	html = frappe.get_print(doctype, name, **options)
	return {"success": True, "data": {"format": print_format, "filename": f"{name}.html", "html": html}}
