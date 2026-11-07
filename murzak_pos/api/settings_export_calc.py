"""
A plain, secret-free picture of how a shop is set up, as one document.

Support compares two shops with it, and an owner keeps it as a record of how the till was set before a change. It holds only
choices (the till switches, the ways of paying that are switched on, the cashier discount limit, the defaults for new
customers). It never holds a key, a secret, a password, a phone number or a name of a person.

Kept free of Frappe imports so it can be tested on its own. The facts are gathered in settings_export_api.py.
"""

from __future__ import annotations

VERSION = 1

# Words that mark a value as secret; a key containing one is dropped, whatever put it there
SECRET_WORDS = (
	"secret",
	"password",
	"passkey",
	"token",
	"key",
	"pin",
	"credential",
	"phone",
	"mobile",
	"email",
)


def _clean(value):
	"""The value with any secret-looking key removed, however deeply it is nested."""
	if isinstance(value, dict):
		return {
			k: _clean(v) for k, v in value.items() if not any(word in str(k).lower() for word in SECRET_WORDS)
		}
	if isinstance(value, list):
		return [_clean(v) for v in value]
	return value


def build_export(company: str, facts: dict, exported_on: str) -> dict:
	"""
	The export for a business.

	facts: till (the four switches as booleans), payment_methods (names switched on), cashier_discount_limit (percent),
	customer_defaults ({group, territory}), shop_type (text or None), currency, time_zone
	"""
	document = {
		"format": "murzak-pos-settings",
		"version": VERSION,
		"exported_on": exported_on,
		"company": company,
		"till": facts.get("till") or {},
		"payment_methods": sorted(facts.get("payment_methods") or []),
		"cashier_discount_limit_percent": facts.get("cashier_discount_limit"),
		"new_customer_defaults": facts.get("customer_defaults") or {},
		"shop_type": facts.get("shop_type"),
		"currency": facts.get("currency"),
		"time_zone": facts.get("time_zone"),
	}
	return _clean(document)
