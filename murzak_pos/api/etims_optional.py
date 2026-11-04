"""
Saving products for shops that do not send them to KRA eTIMS.

The eTIMS app installed on the server adds tax fields to Item, such as
"custom_item_classification" (the KRA item classification code), and marks
some of them as required. A shop that is not registered for eTIMS, or a
product that should not be sent, has no such code to give. Our own screens
set "custom_prevent_etims_registration" for those products, but the eTIMS
app's required fields were still enforced, so saving failed with
"Some required information is missing: custom item classification".

When a product is marked "do not send to eTIMS", this module lets it save
without the eTIMS fields, but only after checking that the details every
product needs (code, name, group, unit, company) are filled in, so nothing
else that is genuinely missing slips through.

The checks are kept free of Frappe imports so they can be tested on their own.
"""

from __future__ import annotations

from typing import Callable, List

# Details every product must have, whatever its tax status
CORE_ITEM_FIELDS = ("item_code", "item_name", "item_group", "stock_uom", "custom_company")


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def is_etims_prevented(value) -> bool:
    """True when the product's "do not send to eTIMS" box is ticked."""
    try:
        return int(value or 0) == 1
    except (TypeError, ValueError):
        return str(value).strip().lower() in ("true", "yes")


def missing_core_fields(get: Callable[[str], object]) -> List[str]:
    """The core details that are still empty, read through `get(fieldname)`."""
    return [field for field in CORE_ITEM_FIELDS if _is_blank(get(field))]


def company_uses_etims(company) -> bool:
    """True when the business has an active eTIMS settings record, i.e. it really sends its products to KRA."""
    import frappe

    if not company or not frappe.db.exists("DocType", "Navari KRA eTims Settings"):
        return False
    return bool(frappe.db.exists("Navari KRA eTims Settings", {"company": company, "is_active": 1}))


def relax_etims_mandatory(doc, company_uses_etims: Callable[[str], bool] | None = None) -> bool:
    """
    Let an Item that is not sent to eTIMS save without the eTIMS app's required
    tax fields. Call it just before doc.insert() or doc.save().

    A product is "not sent" when its "do not send to eTIMS" box is ticked, or,
    when `company_uses_etims` is given, when its business has no active eTIMS
    settings: such a business has no KRA codes to give, so an older product that
    was saved without the box ticked must still be editable.

    Returns True when the eTIMS fields were made optional for this save. Does
    nothing (returns False) when the product is meant for eTIMS, or when one of
    its core details is missing, so that Frappe still reports that problem.
    """
    if not is_etims_prevented(doc.get("custom_prevent_etims_registration")):
        if company_uses_etims is None or company_uses_etims(doc.get("custom_company")):
            return False
    if missing_core_fields(doc.get):
        return False
    doc.flags.ignore_mandatory = True
    return True


# Details every supplier bill must have, whatever its tax status
CORE_BILL_FIELDS = ("supplier", "company")


def relax_purchase_invoice(
	doc,
	company_uses_etims: Callable[[str], bool],
	item_prevented: Callable[[str], bool],
) -> bool:
	"""
	Let a supplier bill (Purchase Invoice) save without the eTIMS app's required tax fields (purchase type, receipt
	type, payment type, status, and on each line the item classification, packaging unit and unit of quantity).

	Those fields exist only to report the bill to KRA. They are not needed when the business has no active eTIMS
	settings, or when every product on the bill is marked "do not send to eTIMS". Without this, receiving goods
	failed at the bill step and the whole delivery was rolled back.

	Returns True when the eTIMS fields were made optional. Does nothing (returns False) for a business that sends
	its bills to KRA, or when a core detail (supplier, company, or a line's item and quantity) is missing, so that
	Frappe still reports that problem.
	"""
	if missing_bill_core(doc):
		return False
	if company_uses_etims(doc.get("company")):
		rows = doc.get("items") or []
		if not rows or not all(item_prevented(row.get("item_code")) for row in rows):
			return False
	doc.flags.ignore_mandatory = True
	return True


def missing_bill_core(doc) -> List[str]:
	"""The core details of a supplier bill that are still empty."""
	missing = [field for field in CORE_BILL_FIELDS if _is_blank(doc.get(field))]
	for row in doc.get("items") or []:
		if _is_blank(row.get("item_code")) or not (row.get("qty") or 0):
			missing.append("items")
			break
	return missing


def item_is_prevented(item_code) -> bool:
	"""True when this product's "do not send to eTIMS" box is ticked."""
	import frappe

	if not item_code:
		return False
	return is_etims_prevented(frappe.db.get_value("Item", item_code, "custom_prevent_etims_registration"))
