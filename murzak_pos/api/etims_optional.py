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


def relax_etims_mandatory(doc) -> bool:
    """
    Let an Item that is not sent to eTIMS save without the eTIMS app's required
    tax fields. Call it just before doc.insert() or doc.save().

    Returns True when the eTIMS fields were made optional for this save. Does
    nothing (returns False) when the product is meant for eTIMS, or when one of
    its core details is missing, so that Frappe still reports that problem.
    """
    if not is_etims_prevented(doc.get("custom_prevent_etims_registration")):
        return False
    if missing_core_fields(doc.get):
        return False
    doc.flags.ignore_mandatory = True
    return True
