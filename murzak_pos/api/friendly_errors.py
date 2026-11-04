"""
Plain sentences for the errors ERPNext raises, so shop staff see something they can act on.

ERPNext's own messages name internal fields ("custom_purchase_type"), document numbers ("ACC-PINV-2026-00001") and item
codes ("ITEM084"), and run on into a long list. The functions here turn the ones we know into a short sentence, and
leave every other message whole (never cut off in the middle). The original text should still be kept for support,
in the Error Log or in the reply's "error_detail".

Kept free of Frappe imports so it can be tested on its own.
"""

from __future__ import annotations

import re
from collections.abc import Callable

MISSING_TAX_DETAILS = "This bill is missing tax details. Ask your administrator to check the eTIMS settings."
MISSING_DETAILS = "Some required information is missing. Please check the form and try again."

_TAGS = re.compile(r"<[^>]*>")
_SPACES = re.compile(r"\s+")
_NO_STOCK = re.compile(r"Item\s+(?P<item>.+?)\s+has no stock in warehouse\s+(?P<warehouse>.+?)\.?\s*$", re.I)
_ETIMS_FIELD = re.compile(r"\bcustom_(?:purchase|receipt|payment|item|packaging|unit)\w*", re.I)


def clean(text) -> str:
	"""The words of a message without tags or line breaks. Nothing is cut."""
	return _SPACES.sub(" ", _TAGS.sub(" ", str(text or ""))).strip()


def friendly(error, item_name: Callable[[str], str] | None = None) -> str:
	"""
	One plain sentence for a known error, or the whole cleaned message for an unknown one.

	`item_name` turns an item code into the product's name (e.g. "ITEM084" into "Beef Samosa (Pair)"); without it
	the code is used.
	"""
	text = clean(error)
	if not text:
		return ""
	m = _NO_STOCK.search(text)
	if m:
		code = m.group("item")
		name = (item_name(code) if item_name else None) or code
		return f"{name} is out of stock. Remove it from the sale, or add stock first."
	if _ETIMS_FIELD.search(text) or "etims" in text.lower():
		return MISSING_TAX_DETAILS
	if re.search(r"\b(mandatory|required information|value missing|missing fields?)\b", text, re.I):
		return MISSING_DETAILS
	return text
