import frappe
from frappe import _
from frappe.utils import nowdate
from erpnext.stock.utils import get_incoming_rate
from erpnext.stock.doctype.stock_entry.stock_entry import make_stock_entry
from erpnext.buying.doctype.purchase_receipt.purchase_receipt import PurchaseReceipt

@frappe.whitelist()
def reject_return_items(purchase_receipt, items, reason=None):
    """
    Reject or return items from Purchase Receipt (GRN)
    
    response = {
        "success": False,
        "message": "",
        "data": {},
        "errors": []
    }
    """  

    try:
        if not purchase_receipt:
            raise frappe.ValidationError(_("Purchase Receipt is required"))
        
        if not frappe.db.exists("Purchase Receipt", purchase_receipt):
            raise frappe.DoesNotExistError(_("Purchase Receipt not found"))

        pr = frappe.get_doc("Purchase Receipt", purchase_receipt)

        if pr.docstatus == 2:
            raise frappe.ValidationError(_("Purchase Receipt is already cancelled"))

        if pr.status == "Closed":
            raise frappe.ValidationError(_("Purchase Receipt is closed and cannot be updated"))

        if isinstance(items, str):
            items = frappe.parse_json(items)

        return_items = []
        for item in items:
            if item["qty"] <= 0:
                raise frappe.ValidationError(_("Return quantity must be greater than zero"))

            # Check if item exists in Purchase Receipt and validate quantity
            pr_item = next((x for x in pr.items if x.item_code == item["item_code"]), None)
            if not pr_item:
                raise frappe.ValidationError(_("Item {0} not found in Purchase Receipt".format(item["item_code"])))

            if pr_item.qty_received <= pr_item.qty_returned + item["qty"]:
                raise frappe.ValidationError(_("Cannot return more than the received quantity for item {0}".format(item["item_code"])))

            return_items.append({
                "item_code": item["item_code"],
                "qty": item["qty"],
                "warehouse": item.get("warehouse", pr_item.warehouse)
            })

        # Reduce Stock & Create Stock Entry
        stock_entry = make_stock_entry(
            purpose="Material Receipt",
            items=return_items,
            target_warehouse="Stores - MC",
            posting_date=nowdate(),
            reason=reason
        )

        stock_entry.insert(ignore_permissions=True)
        stock_entry.submit()

        # Update Purchase Receipt line items
        for item in return_items:
            for pr_item in pr.items:
                if pr_item.item_code == item["item_code"]:
                    pr_item.qty_returned += item["qty"]

        pr.save(ignore_permissions=True)

        # Optionally Update Purchase Order
        if pr.purchase_order:
            po = frappe.get_doc("Purchase Order", pr.purchase_order)
            for item in return_items:
                po_item = next((x for x in po.items if x.item_code == item["item_code"]), None)
                if po_item:
                    po_item.received_qty -= item["qty"]

            po.save(ignore_permissions=True)

        # Response - success
        response.update({
            "success": True,
            "message": _("Items returned successfully"),
            "data": {
                "purchase_receipt": pr.name,
                "returned_items": return_items
            }
        })

    # Handle known errors
    except (
        frappe.ValidationError,
        frappe.DoesNotExistError,
        frappe.PermissionError
    ) as e:
        frappe.db.rollback()
        response.update({
            "message": str(e),
            "errors": [str(e)]
        })

    # Handle unexpected errors
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Reject/Return Items Failed",
            message=frappe.get_traceback()
        )
        response.update({
            "message": _("Unexpected error occurred while rejecting/returning items"),
            "errors": [str(e)]
        })

    return response
