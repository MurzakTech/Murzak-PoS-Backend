"""Purchase Invoice class: loads the guard that keeps the Kenya tax app (eTIMS) out of a business that does not use it."""

from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import (
	PurchaseInvoice as ERPNextPurchaseInvoice,
)

from murzak_pos.api import etims_optional
from murzak_pos.overrides import etims_guard

# Frappe imports this class before it runs any step of a Purchase Invoice, so the tax app's steps are guarded in time
etims_guard.install()


class PurchaseInvoice(ERPNextPurchaseInvoice):
	"""
	ERPNext's Purchase Invoice. It loads the guard above, and lets a bill save without the eTIMS app's required
	tax fields when the business (or every product on the bill) is not sent to eTIMS.
	"""

	def validate(self):
		# Must come first: Frappe checks required fields right after validate, and a bill made by receiving goods
		# has no way to fill in KRA codes
		etims_optional.relax_purchase_invoice(
			self, etims_optional.company_uses_etims, etims_optional.item_is_prevented
		)
		super().validate()
