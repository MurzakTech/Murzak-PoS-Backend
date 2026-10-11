"""
VAT (value added tax) set-up for a business.

For each business this makes, once and safely repeatable:

* a VAT output account (where the tax collected on sales is recorded),
* a "Kenya VAT 16%" sales tax template, which the till adds to a sale,
* three item tax templates that say how a product is taxed: standard-rated (16%), zero-rated, exempt.
  Each one is linked to the matching KRA eTIMS taxation type (B, C and A), when the Kenya app is installed.

Prices are VAT-inclusive: the price on the shelf is what the customer pays, with the VAT shown inside it.
A product with no item tax template simply pays the standard 16%, so only zero-rated and exempt goods
need marking. The product classes offered to a business are suggestions to confirm with its tax adviser,
not legal claims.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

VAT_RATE = 16
SALES_TEMPLATE_TITLE = "Kenya VAT 16%"
VAT_ACCOUNT_NAME = "VAT Output"

# vat_class -> (template title, VAT rate, eTIMS taxation type code)
VAT_CLASSES = {
	"standard": ("Kenya Standard-rated 16%", VAT_RATE, "B"),
	"zero_rated": ("Kenya Zero-rated", 0, "C"),
	"exempt": ("Kenya Exempt", 0, "A"),
}


def clean_vat_class(vat_class):
	"""The class name in canonical form, None for 'not given'; anything else is refused."""
	if vat_class is None or (isinstance(vat_class, str) and not vat_class.strip()):
		return None
	key = str(vat_class).strip().lower().replace("-", "_").replace(" ", "_")
	if key not in VAT_CLASSES:
		frappe.throw(
			_("VAT class must be one of: {0}.").format(", ".join(VAT_CLASSES)), frappe.ValidationError
		)
	return key


def etims_code_for(vat_class) -> str:
	return VAT_CLASSES[clean_vat_class(vat_class)][2]


def _abbr(company: str) -> str:
	return frappe.get_cached_value("Company", company, "abbr")


def _find_or_make_vat_account(company: str) -> str:
	abbr = _abbr(company)
	name = f"{VAT_ACCOUNT_NAME} - {abbr}"
	if frappe.db.exists("Account", name):
		return name

	parent = (
		frappe.db.get_value(
			"Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 1}, "name"
		)
		or frappe.db.get_value("Account", {"company": company, "account_type": "Tax", "is_group": 1}, "name")
		or frappe.db.get_value(
			"Account", {"company": company, "account_name": "Current Liabilities", "is_group": 1}, "name"
		)
	)
	if not parent:
		frappe.throw(
			_("{0} has no chart of accounts yet, so the VAT account cannot be created.").format(company),
			frappe.ValidationError,
		)
	account = frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": VAT_ACCOUNT_NAME,
			"company": company,
			"parent_account": parent,
			"is_group": 0,
			"account_type": "Tax",
		}
	)
	account.insert(ignore_permissions=True)
	return account.name


def _ensure_sales_template(company: str, vat_account: str) -> str:
	name = f"{SALES_TEMPLATE_TITLE} - {_abbr(company)}"
	if frappe.db.exists("Sales Taxes and Charges Template", name):
		# Shelf prices already include VAT; bring a template made earlier in line
		for row in frappe.get_all(
			"Sales Taxes and Charges",
			filters={
				"parent": name,
				"parenttype": "Sales Taxes and Charges Template",
				"included_in_print_rate": 0,
			},
			pluck="name",
		):
			frappe.db.set_value("Sales Taxes and Charges", row, "included_in_print_rate", 1)
		return name
	template = frappe.get_doc(
		{
			"doctype": "Sales Taxes and Charges Template",
			"title": SALES_TEMPLATE_TITLE,
			"company": company,
			"taxes": [
				{
					"charge_type": "On Net Total",
					"account_head": vat_account,
					"description": f"VAT {VAT_RATE}%",
					"rate": VAT_RATE,
					# Shelf prices already include VAT: the customer pays the price shown
					"included_in_print_rate": 1,
				}
			],
		}
	)
	template.insert(ignore_permissions=True)
	return template.name


def _ensure_item_template(company: str, vat_account: str, vat_class: str) -> str:
	title, rate, etims_code = VAT_CLASSES[vat_class]
	name = f"{title} - {_abbr(company)}"
	if frappe.db.exists("Item Tax Template", name):
		return name
	values = {
		"doctype": "Item Tax Template",
		"title": title,
		"company": company,
		"taxes": [{"tax_type": vat_account, "tax_rate": rate}],
	}
	# Added to Item Tax Template by the Kenya app; absent when it is not installed
	if frappe.get_meta("Item Tax Template").has_field("custom_etims_taxation_type") and frappe.db.exists(
		"Navari KRA eTims Taxation Type", etims_code
	):
		values["custom_etims_taxation_type"] = etims_code
	template = frappe.get_doc(values)
	template.insert(ignore_permissions=True)
	return template.name


def ensure_vat_setup(company: str) -> dict:
	"""Create whatever VAT pieces the business is missing. Safe to run again."""
	account = _find_or_make_vat_account(company)
	return {
		"company": company,
		"vat_account": account,
		"sales_taxes_template": _ensure_sales_template(company, account),
		"item_tax_templates": {key: _ensure_item_template(company, account, key) for key in VAT_CLASSES},
	}


def item_tax_template_for(company: str, vat_class) -> str | None:
	"""The business's item tax template for a VAT class, made if need be. None when no class was given."""
	vat_class = clean_vat_class(vat_class)
	if not vat_class:
		return None
	return ensure_vat_setup(company)["item_tax_templates"][vat_class]


def is_vat_registered(company: str) -> bool:
	if not frappe.get_meta("Company").has_field("custom_vat_registered"):
		return False
	return bool(cint(frappe.db.get_value("Company", company, "custom_vat_registered")))


def pos_profile_needs_vat_template(registered: bool, template) -> bool:
	"""True when a VAT-registered business's POS Profile has no sales tax template."""
	return bool(registered) and not template


def validate_pos_profile(doc, method=None) -> None:
	"""A VAT-registered business gets the VAT template by default and cannot save a profile without one."""
	if not doc.get("company") or not is_vat_registered(doc.company):
		return
	if pos_profile_needs_vat_template(True, doc.get("taxes_and_charges")):
		doc.taxes_and_charges = ensure_vat_setup(doc.company)["sales_taxes_template"]
	if pos_profile_needs_vat_template(True, doc.get("taxes_and_charges")):
		frappe.throw(
			_("{0} is registered for VAT, so its POS Profile needs a sales tax template.").format(
				doc.company
			),
			frappe.ValidationError,
		)


@frappe.whitelist()
def setup_vat(company: str, vat_registered: int | None = None) -> dict:
	"""
	Create the VAT accounts and templates for a business and, when asked, mark it VAT-registered.

	vat_registered: 1 or 0 to change the business's flag; leave out to keep it. Marking it 1 also puts
	the VAT template on the business's POS Profiles that have none.
	"""
	from murzak_pos.api.payment_gateway_common import require_gateway_admin, resolve_company

	require_gateway_admin()
	company = resolve_company(company)
	result = ensure_vat_setup(company)

	if vat_registered is not None:
		flag = 1 if cint(vat_registered) else 0
		frappe.db.set_value("Company", company, "custom_vat_registered", flag)
		if flag:
			for profile in frappe.get_all(
				"POS Profile",
				filters={"company": company, "taxes_and_charges": ["in", ["", None]]},
				pluck="name",
			):
				frappe.db.set_value(
					"POS Profile", profile, "taxes_and_charges", result["sales_taxes_template"]
				)
	result["vat_registered"] = is_vat_registered(company)
	return {"success": True, "data": result}
