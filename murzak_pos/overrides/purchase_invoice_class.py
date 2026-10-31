"""Purchase Invoice class: loads the guard that keeps the Kenya tax app (eTIMS) out of a business that does not use it."""

from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import (
	PurchaseInvoice as ERPNextPurchaseInvoice,
)

from murzak_pos.overrides import etims_guard

# Frappe imports this class before it runs any step of a Purchase Invoice, so the tax app's steps are guarded in time
etims_guard.install()


class PurchaseInvoice(ERPNextPurchaseInvoice):
	"""ERPNext's Purchase Invoice, unchanged. It exists so the guard above is loaded before the document's steps run."""
