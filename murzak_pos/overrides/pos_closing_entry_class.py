"""Shift close that never counts a sale twice"""

import frappe
from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
	POSClosingEntry as ERPNextPOSClosingEntry,
)
from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
	consolidate_pos_invoices,
	create_merge_logs,
)


def split_already_posted(rows, posted_names):
	"""
	Divide a shift's POS Invoice rows into (to_merge, already_posted).

	This app posts a POS Invoice's stock and accounting entries at the moment of sale. ERPNext's shift
	close then merges the shift's POS Invoices into one Sales Invoice, which posts them a second time:
	stock, revenue, cash and cost of goods all doubled. A POS Invoice that is already posted is therefore
	left out of the merge.
	"""
	posted = set(posted_names)
	to_merge = [row for row in rows if row.pos_invoice not in posted]
	already_posted = [row for row in rows if row.pos_invoice in posted]
	return to_merge, already_posted


def posted_pos_invoices(names):
	"""Which of these POS Invoices already have accounting or stock entries of their own."""
	if not names:
		return set()
	filters = {"voucher_type": "POS Invoice", "voucher_no": ["in", list(names)]}
	posted = set(frappe.get_all("GL Entry", filters={**filters, "is_cancelled": 0}, pluck="voucher_no"))
	posted.update(
		frappe.get_all("Stock Ledger Entry", filters={**filters, "is_cancelled": 0}, pluck="voucher_no")
	)
	return posted


class POSClosingEntry(ERPNextPOSClosingEntry):
	def on_submit(self):
		self.merge_unposted_invoices()
		frappe.publish_realtime(
			f"poe_{self.pos_opening_entry}_closed",
			self,
			docname=f"POS Opening Entry/{self.pos_opening_entry}",
		)

	@frappe.whitelist()
	def retry(self):
		self.merge_unposted_invoices()

	def merge_unposted_invoices(self):
		rows = list(self.get("pos_transactions") or [])
		to_merge, _already_posted = split_already_posted(
			rows, posted_pos_invoices([row.pos_invoice for row in rows])
		)
		if to_merge:
			consolidate_pos_invoices(pos_invoices=to_merge, closing_entry=self)
		else:
			# Nothing to merge: ERPNext's own finishing steps (status "Submitted", shift marked closed)
			create_merge_logs({}, self)
