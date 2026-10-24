import frappe
from erpnext.controllers.sales_and_purchase_return import make_return_doc
from frappe import _
from frappe.utils import flt

from murzak_pos.api.access_control import ensure_record_access
from murzak_pos.api.error_handler import safe_error_message


def _requested_quantities(items) -> dict[str, float]:
	"""The quantity asked for per item code, after checking each line is sensible."""
	wanted: dict[str, float] = {}
	for item in items:
		if not item.get("item_code"):
			raise frappe.ValidationError(_("Every returned item needs an item code"))
		if flt(item.get("qty")) <= 0:
			raise frappe.ValidationError(_("Return quantity must be greater than zero"))
		wanted[item["item_code"]] = wanted.get(item["item_code"], 0) + flt(item["qty"])
	return wanted


@frappe.whitelist()
def reject_return_items(purchase_receipt, items, reason=None):
	"""
	Send items from a Purchase Receipt (GRN) back to the supplier.

	This creates and submits a standard ERPNext Purchase Return (a Purchase Receipt marked as a return).
	ERPNext then lowers the stock in the warehouse the goods were received into and updates the Purchase
	Order, so neither is adjusted by hand here.

	Args:
		purchase_receipt (str): Name of the submitted Purchase Receipt.
		items (list | str): Lines to return, each like {"item_code": "ITEM-001", "qty": 2}. An optional
			"warehouse" takes the goods from a different warehouse than the one they were received into.
		reason (str, optional): Why the goods are going back; saved in the return's remarks.

	Returns:
		dict: {"success": bool, "message": str, "data": {...}, "errors": [...]}
			On success, data holds "purchase_receipt", "return_document" and "returned_items".
	"""
	ensure_record_access("Purchase Receipt", purchase_receipt)

	response = {"success": False, "message": "", "data": {}, "errors": []}

	try:
		if not purchase_receipt:
			raise frappe.ValidationError(_("Purchase Receipt is required"))

		if not frappe.db.exists("Purchase Receipt", purchase_receipt):
			raise frappe.DoesNotExistError(_("Purchase Receipt not found"))

		pr = frappe.get_doc("Purchase Receipt", purchase_receipt)

		if pr.docstatus == 2:
			raise frappe.ValidationError(_("Purchase Receipt is already cancelled"))

		if pr.docstatus == 0:
			raise frappe.ValidationError(_("Purchase Receipt must be submitted before items can be returned"))

		if pr.status == "Closed":
			raise frappe.ValidationError(_("Purchase Receipt is closed and cannot be updated"))

		if pr.is_return:
			raise frappe.ValidationError(_("Items cannot be returned from a return"))

		if isinstance(items, str):
			items = frappe.parse_json(items)

		if not items:
			raise frappe.ValidationError(_("Select at least one item to return"))

		wanted = _requested_quantities(items)
		warehouses = {item["item_code"]: item.get("warehouse") for item in items if item.get("warehouse")}

		on_receipt = {line.item_code for line in pr.items}
		for item_code in wanted:
			if item_code not in on_receipt:
				raise frappe.ValidationError(_("Item {0} not found in Purchase Receipt").format(item_code))

		# ERPNext builds the return with every line at the quantity that can still go back (as a negative
		# number, 0 once a line has been fully returned). That is the one reliable source for what is left,
		# because it counts every earlier return.
		return_doc = make_return_doc("Purchase Receipt", pr.name)

		# Share each item's quantity across its lines, in order, up to what each line can still give back
		to_return: dict[str, float] = {}  # name of the receipt line -> quantity going back
		for item_code, qty in wanted.items():
			remaining = qty
			available = 0.0
			for line in return_doc.items:
				if line.item_code != item_code:
					continue
				can_return = -flt(line.qty)
				available += can_return
				take = min(remaining, can_return)
				if take > 0:
					to_return[line.purchase_receipt_item] = take
					remaining -= take

			if remaining > 0:
				raise frappe.ValidationError(
					_(
						"Cannot return more than the received quantity for item {0}. Quantity left to return: {1}"
					).format(item_code, available)
				)

		# Keep only the lines asked for, at the quantities asked for
		return_doc.set(
			"items",
			[line for line in return_doc.items if line.purchase_receipt_item in to_return],
		)

		returned_items = []
		for line in return_doc.items:
			qty = to_return[line.purchase_receipt_item]
			line.qty = -qty
			line.received_qty = -qty
			line.rejected_qty = 0
			if warehouses.get(line.item_code):
				line.warehouse = warehouses[line.item_code]
			returned_items.append({"item_code": line.item_code, "qty": qty, "warehouse": line.warehouse})

		if reason:
			return_doc.remarks = reason

		return_doc.insert(ignore_permissions=True)
		return_doc.submit()

		response.update(
			{
				"success": True,
				"message": _("Items returned successfully"),
				"data": {
					"purchase_receipt": pr.name,
					"return_document": return_doc.name,
					"returned_items": returned_items,
				},
			}
		)

	# Handle known errors
	except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError) as e:
		frappe.db.rollback()
		response.update({"message": safe_error_message(e), "errors": [safe_error_message(e)]})

	# Handle unexpected errors
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title="Reject/Return Items Failed", message=frappe.get_traceback())
		response.update(
			{
				"message": _("Unexpected error occurred while rejecting/returning items"),
				"errors": [str(e)],
			}
		)

	return response
