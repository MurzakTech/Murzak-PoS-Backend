import frappe
import json
from frappe.utils import nowdate
from frappe import _
from frappe.utils import cint, flt, nowdate, nowtime
from typing import Union, List, Dict

from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from frappe.model.workflow import apply_workflow

from frappe.exceptions import ValidationError, PermissionError
from frappe.model.workflow import get_workflow_name, apply_workflow

from techsavanna_pos.api.api_response import pos_response
from techsavanna_pos.api.error_handler import handle_pos_exception
from techsavanna_pos.api.constants import POSErrorCode

# logger = logging.getLogger("stock_entry_error_log")


# ------------------------------------------------
# Response helpers
# ------------------------------------------------

def validate_payload(data):
    required = [
        "company",
        "from_warehouse",
        "to_warehouse",
        "items"
    ]

    for field in required:
        if not data.get(field):
            frappe.throw(f"Missing required field: {field}")

    if not isinstance(data["items"], list) or not data["items"]:
        frappe.throw("Items must be a non-empty list")


def success_response(message, data=None):
    return {
        "success": True,
        "message": message,
        "data": data or {}
    }


def error_response(message, http_status=400):
    frappe.local.response["http_status_code"] = http_status
    return {
        "success": False,
        "message": message
    }



#Needs login. Temporary override..
# @frappe.whitelist(allow_guest=True, methods=["POST"])  
@frappe.whitelist(allow_guest=False)
def create_stock_transfer():
    """
    Create Stock Entry of type Material Transfer
    Transfers stock between warehouses
    
    """
    try:
        data = frappe.local.form_dict

        # Parse items if sent as string
        if isinstance(data.get("items"), str):
            data["items"] = json.loads(data["items"])

        required_fields = [
            "company",
            "posting_date",
            "posting_time",
            "from_warehouse",
            "to_warehouse",
            "items", 
            "notes"
        ]

        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return {
                "status": "error",
                "message": f"Missing required fields: {', '.join(missing)}"
            }

        if not isinstance(data["items"], list) or not data["items"]:
            return {
                "status": "error",
                "message": "Items must be a non-empty list"
            }

        for idx, item in enumerate(data["items"], start=1):

            for field in ["item_code", "qty"]:
                if not item.get(field):
                    return {
                        "status": "error",
                        "message": f"Item {idx}: Missing required field '{field}'"
                    }

            if item["qty"] <= 0:
                return {
                    "status": "error",
                    "message": f"Item {item['item_code']}: Quantity must be greater than zero"
                }

        items = []
        for item in data["items"]:
            items.append({
                "item_code": item["item_code"],
                "qty": item["qty"],
                "s_warehouse": data["from_warehouse"],
                "t_warehouse": data["to_warehouse"]
            })

        # Create Stock Entry
        stock_entry = frappe.get_doc({
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Transfer",
            "company": data["company"],
            "posting_date": data["posting_date"],
            "posting_time": data["posting_time"],
            "from_warehouse": data["from_warehouse"],
            "to_warehouse": data["to_warehouse"],
            "items": items,
            "remarks": data["notes"]
        })

        stock_entry.insert()
        stock_entry.submit()

        return {
            "status": "success",
            "message": "Stock transfer completed successfully",
            "stock_entry": stock_entry.name
        }

    except frappe.PermissionError:
        return {
            "status": "error",
            "message": "You do not have permission to perform stock transfer"
        }

    except frappe.ValidationError as e:
        return {
            "status": "error",
            "message": str(e)
        }

    # Unexpected Errors
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Stock Transfer API Error"
        )
        return {
            "status": "error",
            "message": "Internal server error. Please contact support."
        }


@frappe.whitelist(methods=["POST"])
def create_stock_adjustment():
    data = frappe.request.get_json()
    validate_stock_adjustment(data)

    entry_type = (
        "Material Issue"
        if data["adjustment_type"] == "issue"
        else "Material Receipt"
    )

    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = entry_type
    stock_entry.company = data["company"]
    stock_entry.posting_date = data.get("posting_date") or nowdate()
    stock_entry.remarks = data.get("remarks")
    stock_entry.custom_pos_reference = data.get("pos_reference")

    for item in data["items"]:
        row = {
            "item_code": item["item_code"],
            "qty": item["qty"],
            "uom": item.get("uom"),
        }

        if entry_type == "Material Issue":
            row["s_warehouse"] = data["warehouse"]
        else:
            row["t_warehouse"] = data["warehouse"]
            row["valuation_rate"] = item.get("valuation_rate")

        stock_entry.append("items", row)

    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()

    return success(stock_entry)


def success(stock_entry):
    return {
        "status": "success",
        "stock_entry": stock_entry.name,
        "posting_date": stock_entry.posting_date
    }

def validate_stock_receipt(data):
    required = ["company", "warehouse", "items"]
    for r in required:
        if not data.get(r):
            frappe.throw(f"Missing field: {r}")

#Validation Helpers
def validate_stock_adjustment(data):
    required = ["company", "warehouse", "adjustment_type", "items"]
    for r in required:
        if not data.get(r):
            frappe.throw(f"Missing field: {r}")

    if data["adjustment_type"] not in ("issue", "receipt"):
        frappe.throw("adjustment_type must be issue or receipt")


#POS Sales → Delivery Note + Stock
# Delivery Note → Stock Ledger → GL


@frappe.whitelist(methods=["POST"])
def create_pos_delivery_note():
    """
    Payload
    {
        "company": "Savanna Ltd",
        "customer": "Walk-in Customer",
        "posting_date": "2025-01-18",
        "warehouse": "POS Store - SV",
        "items": [
        {
            "item_code": "ITEM-0001",
            "qty": 2,
            "rate": 150,
            "batch_no": "BATCH-01"
        }
        ],
        "payments": [
        {
            "mode_of_payment": "Cash",
            "amount": 300
        }
        ],
        "pos_reference": "POS-SALE-9981"
        }


    Returns:
        _type_: _description_
    """
    data = frappe.request.get_json()
    validate_pos_sale(data)
    check_duplicate(data.get("pos_reference"), "Delivery Note")

    dn = frappe.new_doc("Delivery Note")
    dn.company = data["company"]
    dn.customer = data["customer"]
    dn.posting_date = data.get("posting_date") or nowdate()
    dn.set_warehouse = data["warehouse"]
    dn.custom_pos_reference = data.get("pos_reference")

    for item in data["items"]:
        dn.append("items", {
            "item_code": item["item_code"],
            "qty": item["qty"],
            "rate": item.get("rate"),
            "warehouse": data["warehouse"],
            "batch_no": item.get("batch_no"),
            "serial_no": item.get("serial_no")
        })

    dn.insert(ignore_permissions=True)
    dn.submit()

    return {
        "status": "success",
        "delivery_note": dn.name
    }



# @frappe.whitelist(allow_guest=True, methods=["POST"])
@frappe.whitelist()
def create_stock_receipt():
    """
     Creates a Stock Entry of type 'Material Receipt'.
    Args:
        data (str): JSON string containing Stock Entry details.
    Returns:
        dict: success or error message
    
    {
        "doctype": "Stock Entry",
        "stock_entry_type": "Material Receipt",
        "posting_date": "2025-12-21",
        "posting_time": "12:00:00",
        "company": "Techsavanna",
        "to_warehouse": "Stores - WH",
        "items": [
            {
            "item_code": "ITEM-001",
            "qty": 10,
            "s_warehouse": null,
            "t_warehouse": "Stores - WH",
            "rate": 100
            },
            {
            "item_code": "ITEM-002",
            "qty": 5,
            "s_warehouse": null,
            "t_warehouse": "Stores - WH",
            "rate": 50
            }
        ]
    }

    """

    try:
        data = frappe.local.form_dict

        # Parse items if needed
        if isinstance(data.get("items"), str):
            data["items"] = json.loads(data["items"])

        # Basic Validation
        required_fields = ["company", "posting_date", "posting_time", "to_warehouse", "items"]
        missing = [f for f in required_fields if not data.get(f)]

        if missing:
            return {
                "status": "error",
                "message": f"Missing required fields: {', '.join(missing)}"
            }

        if not isinstance(data["items"], list) or not data["items"]:
            return {
                "status": "error",
                "message": "Items must be a non-empty list"
            }

        # Item-level Validation
        for idx, item in enumerate(data["items"], start=1):

            for field in ["item_code", "qty", "t_warehouse"]:
                if not item.get(field):
                    return {
                        "status": "error",
                        "message": f"Item {idx}: Missing required field '{field}'"
                    }

            # Handle valuation rate logic
            rate = item.get("rate")
            allow_zero = item.get("allow_zero_valuation_rate", 0)

            if (rate is None or rate == "") and not allow_zero:
                return {
                    "status": "error",
                    "message": (
                        f"Item {item['item_code']} requires valuation rate. "
                        "Either provide 'rate' or set 'allow_zero_valuation_rate': 1"
                    )
                }

        # Create Stock Entry
        stock_entry = frappe.get_doc({
            "doctype": "Stock Entry",
            "stock_entry_type": "Material Receipt",
            "company": data["company"],
            "posting_date": data["posting_date"],
            "posting_time": data["posting_time"],
            "to_warehouse": data["to_warehouse"],
            "items": data["items"]
        })

        stock_entry.insert()
        stock_entry.submit()

        return {
            "status": "success",
            "message": "Stock Receipt created successfully",
            "stock_entry": stock_entry.name
        }


    except frappe.PermissionError:
        return {
            "status": "error",
            "message": "You do not have permission to create Stock Entry"
        }


    except frappe.ValidationError as e:
        return {
            "status": "error",
            "message": str(e)
        }

    # Unexpected Errors
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Material Receipt API Error"
        )
        return {
            "status": "error",
            "message": "Internal server error. Please contact support."
        }


@frappe.whitelist(allow_guest=False)
def get_low_stock_items():
    """
    Returns items below reorder level
    Optional filter:
    - warehouse (specific warehouse name or 'All')
    """

    try:
        data = frappe.local.form_dict
        warehouse = data.get("warehouse")

        conditions = ""
        values = {}

        # Warehouse Validation\
        if warehouse and warehouse.lower() != "all":
            if not frappe.db.exists("Warehouse", warehouse):
                return {
                    "status": "error",
                    "message": f"Warehouse '{warehouse}' does not exist"
                }

            conditions = " AND b.warehouse = %(warehouse)s"
            values["warehouse"] = warehouse

        # Low Stock Query
        query = f"""
            SELECT
                b.item_code,
                i.item_name,
                b.warehouse,
                b.actual_qty,
                ir.warehouse_reorder_level AS reorder_level,
                ir.warehouse_reorder_qty AS reorder_qty
            FROM `tabBin` b
            INNER JOIN `tabItem` i ON i.name = b.item_code
            INNER JOIN `tabItem Reorder` ir ON ir.parent = b.item_code
            WHERE
                i.disabled = 0
                AND b.actual_qty <= ir.warehouse_reorder_level
                {conditions}
            ORDER BY b.actual_qty ASC
        """

        result = frappe.db.sql(query, values, as_dict=True)

        return {
            "status": "success",
            "count": len(result),
            "data": result
        }

    except frappe.PermissionError:
        return {
            "status": "error",
            "message": "You do not have permission to view stock data"
        }

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Low Stock Alert API Error"
        )
        return {
            "status": "error",
            "message": "Internal server error. Please contact support."
        }



# Stock Count Session -	Stock Reconciliation (Draft)
@frappe.whitelist(allow_guest=False)
def start_stock_count():
    data = frappe.local.form_dict

    required = ["company", "warehouse"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return {"status": "error", "message": f"Missing fields: {', '.join(missing)}"}

    if not frappe.db.exists("Warehouse", data["warehouse"]):
        return {"status": "error", "message": "Invalid warehouse"}

    doc = frappe.get_doc({
        "doctype": "Stock Reconciliation",
        "company": data["company"],
        "posting_date": data.get("posting_date") or frappe.utils.today(),
        "posting_time": data.get("posting_time") or frappe.utils.nowtime(),
        "set_warehouse": data["warehouse"],
        "purpose": "Stock Reconciliation"
    })

    doc.insert()

    return {
        "status": "success",
        "count_id": doc.name,
        "warehouse": data["warehouse"]
    }


# List Stock Counts - Add Counted Items - Stock Reconciliation Item
@frappe.whitelist(allow_guest=False)
def list_stock_counts():

    counts = frappe.get_all(
        "Stock Reconciliation",
        fields=["name", "posting_date", "set_warehouse", "docstatus"],
        order_by="creation desc"
    )

    return {
        "status": "success",
        "data": counts
    }

# Add Counted Items- # Complete Count - Submit Stock Reconciliation
@frappe.whitelist(allow_guest=False)
def add_stock_count_items(count_id):
    data = frappe.local.form_dict

    if not frappe.db.exists("Stock Reconciliation", count_id):
        return {"status": "error", "message": "Invalid count session"}

    doc = frappe.get_doc("Stock Reconciliation", count_id)

    if doc.docstatus != 0:
        return {"status": "error", "message": "Count already submitted"}

    items = frappe.parse_json(data.get("items"))
    if not items:
        return {"status": "error", "message": "No items provided"}

    for item in items:
        if not item.get("item_code") or item.get("qty") is None:
            return {"status": "error", "message": "item_code and qty required"}

        doc.append("items", {
            "item_code": item["item_code"],
            "qty": item["qty"],
            "warehouse": doc.set_warehouse
        })

    doc.save()

    return {
        "status": "success",
        "message": "Items added",
        "count_id": doc.name
    }


# Variance Adjustment - Reconcile Stock Count
@frappe.whitelist(allow_guest=False)
def complete_stock_count(count_id):
    if not frappe.db.exists("Stock Reconciliation", count_id):
        return {"status": "error", "message": "Invalid count session"}

    doc = frappe.get_doc("Stock Reconciliation", count_id)

    if doc.docstatus != 0:
        return {"status": "error", "message": "Count already completed"}

    if not doc.items:
        return {"status": "error", "message": "No items counted"}

    doc.submit()

    return {
        "status": "success",
        "message": "Stock count completed and reconciled",
        "count_id": doc.name
    }


# Purchase Receipt (GRN) from Purchase Order
@frappe.whitelist()
def create_pr_from_po(purchase_order, items=None, submit=1, external_ref=None):
    """
    Create Purchase Receipt (GRN) from Purchase Order with full validation
    
    Full Receipt
    {
        "purchase_order": "PO-00045",
        "external_ref": "EXT-GRN-8891"
    }
    
    Partial Receipt
    {
        "purchase_order": "PO-00045",
        "items": [
            { "item_code": "ITEM-001", "qty": 5 },
            { "item_code": "ITEM-002", "qty": 2 }
        ],
        "submit": 1
    }
    
    Draft GRN
    {
        "purchase_order": "PO-00045",
        "submit": 0
    }



    """

    response = {
        "success": False,
        "message": "",
        "data": {},
        "errors": []
    }

    try:
        if not purchase_order:
            raise frappe.ValidationError(_("Purchase Order is required"))

        if not frappe.db.exists("Purchase Order", purchase_order):
            raise frappe.DoesNotExistError(_("Purchase Order not found"))

        po = frappe.get_doc("Purchase Order", purchase_order)

        if po.docstatus != 1:
            raise frappe.ValidationError(_("Purchase Order must be submitted"))

        if po.status in ("Closed", "Completed"):
            raise frappe.ValidationError(_("Purchase Order is already completed or closed"))

        if external_ref:
            existing_pr = frappe.db.get_value(
                "Purchase Receipt",
                {"custom_external_ref": external_ref},
                "name"
            )
            if existing_pr:
                response.update({
                    "success": True,
                    "message": _("Purchase Receipt already exists"),
                    "data": {
                        "purchase_receipt": existing_pr,
                        "idempotent": True
                    }
                })
                return response

        if isinstance(items, str):
            items = frappe.parse_json(items)

        # Create PR using core logic
        pr = make_purchase_receipt(purchase_order)

        # Partial receipt handling
        if items:
            item_map = {}

            for i in items:
                if not i.get("item_code") or not i.get("qty"):
                    raise frappe.ValidationError(
                        _("Each item must have item_code and qty")
                    )
                item_map[i["item_code"]] = i

            for row in pr.items:
                if row.item_code in item_map:
                    requested_qty = item_map[row.item_code]["qty"]

                    if requested_qty <= 0:
                        raise frappe.ValidationError(
                            _(f"Invalid qty for item {row.item_code}")
                        )

                    if requested_qty > row.qty:
                        raise frappe.ValidationError(
                            _(f"Qty exceeds pending amount for item {row.item_code}")
                        )

                    row.qty = requested_qty
                    row.warehouse = item_map[row.item_code].get(
                        "warehouse", row.warehouse
                    )
                else:
                    row.qty = 0

        # Remove zero qty rows
        pr.items = [row for row in pr.items if row.qty > 0]

        if not pr.items:
            raise frappe.ValidationError(_("No valid items to receive"))

        # Optional fields
        if external_ref:
            pr.custom_external_ref = external_ref

        # Insert & Submit
        pr.insert(ignore_permissions=True)

        if cint(submit):
            pr.submit()

        # Success response
        response.update({
            "success": True,
            "message": _("Purchase Receipt created successfully"),
            "data": {
                "purchase_receipt": pr.name,
                "docstatus": pr.docstatus,
                "submitted": bool(pr.docstatus == 1),
                "supplier": pr.supplier,
                "company": pr.company
            }
        })

    # Known ERPNext errors
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

    # Unexpected errors
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Create Purchase Receipt from PO Failed",
            message=frappe.get_traceback()
        )
        response.update({
            "message": _("Unexpected error occurred"),
            "errors": [str(e)]
        })

    return response


#Cancel Purchase Order
@frappe.whitelist()
def cancel_purchase_order(purchase_order, force=0, reason=None):
    """
    Cancel a Purchase Order 
    A Purchase Order cannot be cancelled if:
        It is not submitted
        It has linked submitted Purchase Receipts
        It has linked submitted Purchase Invoices
        It is already cancelled
        
    response = {
        "success": False,
        "message": "",
        "data": {},
        "errors": []
    }
    """


    try:
        if not purchase_order:
            raise frappe.ValidationError(_("Purchase Order is required"))

        if not frappe.db.exists("Purchase Order", purchase_order):
            raise frappe.DoesNotExistError(_("Purchase Order not found"))

        po = frappe.get_doc("Purchase Order", purchase_order)

        if po.docstatus == 2:
            response.update({
                "success": True,
                "message": _("Purchase Order is already cancelled"),
                "data": {
                    "purchase_order": po.name,
                    "docstatus": po.docstatus
                }
            })
            return response

        if po.docstatus != 1:
            raise frappe.ValidationError(
                _("Only submitted Purchase Orders can be cancelled")
            )

        # Dependency checks (explicit)
        linked_pr = frappe.db.exists(
            "Purchase Receipt Item",
            {"purchase_order": po.name, "docstatus": 1}
        )

        linked_pi = frappe.db.exists(
            "Purchase Invoice Item",
            {"purchase_order": po.name, "docstatus": 1}
        )

        if linked_pr or linked_pi:
            raise frappe.ValidationError(
                _("Purchase Order has submitted receipts or invoices and cannot be cancelled")
            )

        if reason:
            po.add_comment("Comment", _("Cancellation reason: {0}").format(reason))

        po.cancel()

        response.update({
            "success": True,
            "message": _("Purchase Order cancelled successfully"),
            "data": {
                "purchase_order": po.name,
                "docstatus": po.docstatus,
                "status": po.status
            }
        })

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

    # Unexpected errors
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Cancel Purchase Order Failed",
            message=frappe.get_traceback()
        )
        response.update({
            "message": _("Unexpected error occurred while cancelling Purchase Order"),
            "errors": [str(e)]
        })

    return response


#Approve Stock Transfer - no workflow
@frappe.whitelist(methods=["POST"])
def approve_stock_transfer():
    """
    Approve Stock Transfer Request / Material Request
    """

    try:
        data = frappe.local.form_dict or {}

        request_id = data.get("request_id")
        approved_by = data.get("approved_by")
        approval_notes = data.get("approval_notes")
        
        print(f'\nApprove Stock...data..{data}')

        if not request_id:
            return error_response(
                message="request_id is required",
                http_status=400
            )

        if not approved_by:
            return error_response(
                message="approved_by is required",
                http_status=400
            )

        try:
            doc = frappe.get_doc("Material Request", request_id)
        except frappe.DoesNotExistError:
            return error_response(
                message=f"Stock Transfer Request {request_id} not found",
                http_status=404
            )

        if doc.material_request_type != "Material Transfer":
            return error_response(
                message="Only Material Transfer requests can be approved",
                http_status=400
            )

        if doc.docstatus == 0:
            return error_response(
                message="Request must be submitted before approval. Use submit_stock_transfer_request first.",
                http_status=409
            )

        if doc.docstatus == 2:
            return error_response(
                message="Request is cancelled and cannot be approved",
                http_status=409
            )

        # Check if already approved (has approved_by field set)
        if getattr(doc, "approved_by", None):
            return error_response(
                message="Request is already approved",
                http_status=409
            )

        # Permission check
        if not frappe.has_permission(doc.doctype, "write", doc=doc):
            raise PermissionError(_("You are not permitted to approve this request"))

        doc.approved_by = approved_by
        doc.approval_notes = approval_notes or ""
        doc.approved_on = frappe.utils.now()

        # Recommended Approach: Use custom approval status field if it exists
        # This doesn't interfere with ERPNext's automatic status calculation
        # To enable: Add custom field "custom_approval_status" (Select type) to Material Request
        if frappe.db.has_column("Material Request", "custom_approval_status"):
            doc.custom_approval_status = "Approved"
        
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        # Reload to get updated document
        doc.reload()

        # Build response - use approved_by to determine approval state
        # The status field remains as ERPNext calculates it (Pending/Submitted)
        # Frontend can check approved_by or custom_approval_status to show "Approved"
        response_data = {
            "request_id": doc.name,
            "status": doc.status,  # ERPNext's calculated status (may be "Pending" or "Submitted")
            "approved_by": approved_by,
            "is_approved": True,  # Boolean flag for easy checking
            "approval_status": "Approved"  # Effective status for display
        }
        
        # Include custom_approval_status if field exists
        if frappe.db.has_column("Material Request", "custom_approval_status"):
            response_data["custom_approval_status"] = getattr(doc, "custom_approval_status", None)

        return success_response(
            message="Stock Transfer Request approved successfully",
            data=response_data
        )

    except PermissionError as e:
        return error_response(
            message=str(e),
            http_status=403
        )

    except ValidationError as e:
        return error_response(
            message=str(e),
            http_status=422
        )

    except Exception:
        frappe.log_error(
            title="Approve Stock Transfer Failed",
            message=frappe.get_traceback()
        )
        return error_response(
            message="Internal server error while approving stock transfer",
            http_status=500
        )


#Approve Stock Transfer with workflow
@frappe.whitelist(allow_guest=False)
def approve_stock_transfer_workflow():
    if frappe.request.method != "POST":
        frappe.throw(_("Only POST method is allowed"), PermissionError)

    try:
        data = frappe.local.form_dict or {}

        request_id = data.get("request_id")
        approved_by = data.get("approved_by")
        approval_notes = data.get("approval_notes")

        if not request_id:
            return error_response("request_id is required", 400)

        if not approved_by:
            return error_response("approved_by is required", 400)

        if frappe.session.user == "Guest":
            return error_response("Authentication required", 401)

        if frappe.session.user != approved_by:
            return error_response(
                "API user must match approved_by", 403
            )

        doc = frappe.get_doc("Material Request", request_id)

        if doc.material_request_type != "Material Transfer":
            return error_response(
                "Only Material Transfer requests can be approved", 400
            )

        if doc.docstatus == 2:
            return error_response(
                "Cancelled request cannot be approved", 409
            )

        # Check if workflow is configured
        workflow_name = get_workflow_name(doc.doctype)
        if not workflow_name:
            return error_response(
                "No workflow configured for Material Request. Use approve_stock_transfer endpoint instead.",
                400
            )

        # Get workflow state field name
        state_field = frappe.get_value(
            "Workflow", workflow_name, "workflow_state_field"
        )
        
        if not state_field:
            return error_response(
                "Workflow state field not found in workflow configuration", 500
            )

        # Check current workflow state
        current_state = doc.get(state_field)
        if current_state == "Approved":
            return error_response(
                "Request already approved", 409
            )

        doc.approved_by = approved_by
        doc.approval_notes = approval_notes or ""
        doc.approved_on = frappe.utils.now()

        doc.save()

        # Workflow transition
        apply_workflow(doc, "Approve")

        frappe.db.commit()

        # Get updated workflow state
        doc.reload()
        updated_state = doc.get(state_field)

        return success_response(
            "Stock Transfer Request approved",
            {
                "request_id": doc.name,
                "workflow_state": updated_state,
                "status": doc.status
            }
        )

    except PermissionError as e:
        return error_response(str(e), 403)

    except ValidationError as e:
        return error_response(str(e), 422)

    except Exception as e:
        frappe.log_error(
            "Approve Stock Transfer API Failed",
            frappe.get_traceback()
        )
        return error_response(
            f"Internal error: {str(e)}", 500
        )


#Dispatch Stock - Optional Workflow
@frappe.whitelist(allow_guest=False)
def dispatch_stock():
    """
    Dispatch stock from origin warehouse for a Material Request.

    Payload:
    {
        "request_id": "MAT-MR-2025-00001",
        "origin_warehouse": "Stores - HO",
        "items": [
            {"item_code": "ITEM-001", "dispatched_qty": 5}
        ],
        "dispatched_by": "steve@steve.com",
        "dispatch_notes": "Partial dispatch"
    }
    """

    if frappe.request.method != "POST":
        frappe.throw(_("Only POST allowed"), PermissionError)

    try:
        data = frappe.local.form_dict or {}

        request_id = data.get("request_id")
        origin_warehouse = data.get("origin_warehouse")
        items = data.get("items")
        dispatched_by = data.get("dispatched_by")
        dispatch_notes = data.get("dispatch_notes")
        
        if not request_id or not origin_warehouse or not items:
            return error_response("Invalid payload", 400)

        if frappe.session.user != dispatched_by:
            return error_response("API user mismatch", 403)

        doc = frappe.get_doc("Material Request", request_id)

        # ------------------- Workflow check -------------------
        workflow_name = get_workflow_name(doc.doctype)
        if workflow_name:
            state_field = frappe.get_value(
                "Workflow", workflow_name, "workflow_state_field"
            )
            current_state = doc.get(state_field)
            if current_state != "Approved":
                return error_response(
                    f"Request not approved (current state: {current_state})", 409
                )
        else:
            if doc.docstatus != 1:
                return error_response("Request not submitted", 409)

        if doc.get("status") in ("In Transit", "Partially In Transit"):
            return error_response("Request already dispatched", 409)

        # ------------------- Expense Account -------------------
        expense_account = frappe.get_cached_value(
            "Company", doc.company, "stock_adjustment_account"
        )
        if not expense_account:
            raise ValidationError(
                "Stock Adjustment Account not set for company"
            )

        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Issue"
        se.company = doc.company
        se.set_posting_time = 1
        se.posting_date = frappe.utils.today()
        se.posting_time = frappe.utils.nowtime()

        fully_dispatched = True

        for row in items:
            item_code = row["item_code"]
            qty = row["dispatched_qty"]

            mr_row = next(
                (i for i in doc.items if i.item_code == item_code), None
            )
            if not mr_row:
                raise ValidationError(f"{item_code} not in Material Request")

            # Check if custom dispatched_qty field exists, otherwise use ordered_qty as fallback
            has_dispatched_qty_field = frappe.db.has_column("Material Request Item", "custom_dispatched_qty")
            
            if has_dispatched_qty_field:
                already_dispatched = getattr(mr_row, "custom_dispatched_qty", 0) or 0
            else:
                # Fallback: use ordered_qty if custom field doesn't exist
                # For Material Transfer, ordered_qty might track dispatched quantity
                already_dispatched = getattr(mr_row, "ordered_qty", 0) or 0
            
            remaining_qty = mr_row.qty - already_dispatched

            if qty > remaining_qty:
                raise ValidationError(
                    f"Dispatch qty {qty} exceeds remaining {remaining_qty} for {item_code}"
                )

            actual_qty = frappe.db.get_value(
                "Bin",
                {"item_code": item_code, "warehouse": origin_warehouse},
                "actual_qty"
            ) or 0

            if actual_qty < qty:
                raise ValidationError(f"Insufficient stock for {item_code}")

            se.append("items", {
                "item_code": item_code,
                "qty": qty,
                "s_warehouse": origin_warehouse,
                "expense_account": expense_account
            })

            # Update dispatched_qty in MR item
            new_dispatched_qty = already_dispatched + qty
            if has_dispatched_qty_field:
                mr_row.db_set(
                    "custom_dispatched_qty",
                    new_dispatched_qty,
                    update_modified=False
                )
            else:
                # Fallback: update ordered_qty if custom field doesn't exist
                # Note: This is not ideal but works as temporary solution
                mr_row.db_set(
                    "ordered_qty",
                    new_dispatched_qty,
                    update_modified=False
                )
                # Log warning about missing custom field
                frappe.log_error(
                    "custom_dispatched_qty field not found. Using ordered_qty as fallback. "
                    "Please add custom_dispatched_qty field to Material Request Item.",
                    "Material Request Item Custom Field Missing"
                )

            if new_dispatched_qty < mr_row.qty:
                fully_dispatched = False

        se.insert()
        se.submit()

        # Update Material Request Status 
        status = "In Transit" if fully_dispatched else "Partially In Transit"
        doc.db_set("status", status, update_modified=False)
        
        # Update custom fields if they exist
        if frappe.db.has_column("Material Request", "custom_dispatched_by"):
            doc.db_set("custom_dispatched_by", dispatched_by)
        elif frappe.db.has_column("Material Request", "dispatched_by"):
            doc.db_set("dispatched_by", dispatched_by)
        
        if frappe.db.has_column("Material Request", "custom_dispatch_notes"):
            doc.db_set("custom_dispatch_notes", dispatch_notes)
        elif frappe.db.has_column("Material Request", "dispatch_notes"):
            doc.db_set("dispatch_notes", dispatch_notes)
        
        if frappe.db.has_column("Material Request", "custom_last_dispatch_on"):
            doc.db_set("custom_last_dispatch_on", frappe.utils.now())
        elif frappe.db.has_column("Material Request", "last_dispatch_on"):
            doc.db_set("last_dispatch_on", frappe.utils.now())

        if workflow_name:
            try:
                apply_workflow(doc, "Dispatch")
            except Exception:
                pass  # Ignore if workflow action not configured

        frappe.db.commit()

        return success_response(
            "Stock dispatched successfully",
            {
                "status": status,
                "stock_entry": se.name
            }
        )

    except ValidationError as e:
        frappe.db.rollback()
        return error_response(str(e), 422)

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Dispatch Stock Failed", frappe.get_traceback())
        return error_response(f"Dispatch failed: {str(e)}", 500)



#Receive Stock at Destination Store Endpoint
@frappe.whitelist(allow_guest=False)
def receive_stock_destination():
    if frappe.request.method != "POST":
        frappe.throw(_("Only POST allowed"), PermissionError)

    try:
        try:
            data = json.loads(frappe.request.data)
        except Exception:
            return error_response("Invalid payload: JSON expected", 400)

        request_id = data.get("request_id")
        destination_warehouse = data.get("destination_warehouse")
        items = data.get("items")
        received_by = data.get("received_by")
        receive_notes = data.get("receive_notes")
        goods_received_note = data.get("goods_received_note") 

        if not request_id or not destination_warehouse or not items or not received_by:
            return error_response("Invalid payload: missing required fields", 400)

        doc = frappe.get_doc("Material Request", request_id)

        if doc.status not in ("In Transit", "Partially In Transit"):
            return error_response(
                "Only In Transit requests can be received", 409
            )

        #  Create Stock Entry
        se = frappe.new_doc("Stock Entry")
        se.stock_entry_type = "Material Receipt"
        se.company = doc.company
        se.set_posting_time = 1
        se.posting_date = frappe.utils.today()
        se.posting_time = frappe.utils.nowtime()

        fully_received = True

        for row in items:
            item_code = row.get("item_code")
            qty = row.get("received_qty")

            if not item_code or qty is None:
                return error_response("Invalid payload: item_code or received_qty missing", 400)

            mr_row = next(
                (i for i in doc.items if i.item_code == item_code), None
            )
            if not mr_row:
                raise ValidationError(f"{item_code} not in Material Request")

            # Check if custom fields exist
            has_dispatched_qty_field = frappe.db.has_column("Material Request Item", "custom_dispatched_qty")
            has_received_qty_field = frappe.db.has_column("Material Request Item", "custom_received_qty")
            
            # Get received quantity
            if has_received_qty_field:
                already_received = getattr(mr_row, "custom_received_qty", 0) or 0
            else:
                already_received = getattr(mr_row, "received_qty", 0) or 0
            
            # Get dispatched quantity
            if has_dispatched_qty_field:
                dispatched_qty = getattr(mr_row, "custom_dispatched_qty", 0) or 0
            else:
                # Fallback: use ordered_qty if custom field doesn't exist
                dispatched_qty = getattr(mr_row, "ordered_qty", 0) or 0
            
            remaining_qty = dispatched_qty - already_received

            if qty > remaining_qty:
                raise ValidationError(
                    f"Receive qty {qty} exceeds dispatched {dispatched_qty} for {item_code}"
                )

            # Add item to Stock Entry
            se.append("items", {
                "item_code": item_code,
                "qty": qty,
                "t_warehouse": destination_warehouse
            })

            # Update received_qty in MR item
            new_received_qty = already_received + qty
            if has_received_qty_field:
                mr_row.db_set(
                    "custom_received_qty",
                    new_received_qty,
                    update_modified=False
                )
            else:
                # Use standard received_qty field
                mr_row.db_set(
                    "received_qty",
                    new_received_qty,
                    update_modified=False
                )

            if already_received + qty < dispatched_qty:
                fully_received = False

        se.insert()
        se.submit()

        # ------------------- Update Material Request -------------------
        status = "Completed" if fully_received else "Partially Received"
        doc.db_set("status", status, update_modified=False)
        
        # Update custom fields if they exist
        if frappe.db.has_column("Material Request", "custom_received_by"):
            doc.db_set("custom_received_by", received_by)
        elif frappe.db.has_column("Material Request", "received_by"):
            doc.db_set("received_by", received_by)
        
        if frappe.db.has_column("Material Request", "custom_receive_notes"):
            doc.db_set("custom_receive_notes", receive_notes)
        elif frappe.db.has_column("Material Request", "receive_notes"):
            doc.db_set("receive_notes", receive_notes)
        
        if frappe.db.has_column("Material Request", "custom_received_on"):
            doc.db_set("custom_received_on", frappe.utils.now())
        elif frappe.db.has_column("Material Request", "received_on"):
            doc.db_set("received_on", frappe.utils.now())

        if goods_received_note:
            if frappe.db.has_column("Material Request", "custom_goods_received_note"):
                doc.db_set("custom_goods_received_note", goods_received_note)
            elif frappe.db.has_column("Material Request", "goods_received_note"):
                doc.db_set("goods_received_note", goods_received_note)

        # Workflow transition (optional) -------------------
        workflow_name = get_workflow_name(doc.doctype)
        if workflow_name:
            try:
                apply_workflow(doc, "Receive")
            except Exception:
                pass

        frappe.db.commit()

        return success_response(
            "Stock received successfully",
            {
                "status": status,
                "stock_entry": se.name,
                "goods_received_note": goods_received_note
            }
        )

    except ValidationError as e:
        frappe.db.rollback()
        return error_response(str(e), 422)

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Receive Stock Failed", frappe.get_traceback())
        return error_response(f"Receive failed: {str(e)}", 500)


#Create Stock Transfer Request (Material Request)
@frappe.whitelist(allow_guest=False)
def create_stock_transfer_request():
    """
    Create a Material Request for stock transfer
    
    Payload:
    {
        "company": "Savanna Ltd",
        "transaction_date": "2025-01-20",
        "from_warehouse": "Stores - HO",
        "to_warehouse": "Stores - Branch",
        "items": [
            {"item_code": "ITEM-001", "qty": 10, "uom": "Nos"},
            {"item_code": "ITEM-002", "qty": 5, "uom": "Nos"}
        ],
        "schedule_date": "2025-01-25",  // Optional
        "submit": false  // Optional: submit immediately (default: false)
    }
    """
    try:
        data = frappe.request.get_json() or frappe.local.form_dict or {}
        
        # Parse items if sent as string
        if isinstance(data.get("items"), str):
            data["items"] = json.loads(data["items"])
        
        # Validation
        required_fields = ["company", "from_warehouse", "to_warehouse", "items"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return error_response(f"Missing required fields: {', '.join(missing)}", 400)
        
        if not isinstance(data.get("items"), list) or not data["items"]:
            return error_response("Items must be a non-empty list", 400)
        
        # Validate items
        for idx, item in enumerate(data["items"], start=1):
            if not item.get("item_code") or not item.get("qty"):
                return error_response(f"Item {idx}: Missing required field 'item_code' or 'qty'", 400)
            if item.get("qty", 0) <= 0:
                return error_response(f"Item {idx}: Quantity must be greater than zero", 400)
        
        # Create Material Request
        mr = frappe.new_doc("Material Request")
        mr.company = data["company"]
        mr.transaction_date = data.get("transaction_date") or frappe.utils.today()
        mr.material_request_type = "Material Transfer"
        mr.set_from_warehouse = data["from_warehouse"]
        mr.set_warehouse = data["to_warehouse"]
        mr.schedule_date = data.get("schedule_date") or mr.transaction_date
        
        # Add items
        for item in data["items"]:
            mr.append("items", {
                "item_code": item["item_code"],
                "qty": item["qty"],
                "uom": item.get("uom"),
                "s_warehouse": data["from_warehouse"],
                "warehouse": data["to_warehouse"]
            })
        
        # Insert
        mr.insert(ignore_permissions=True)
        
        # Submit if requested
        should_submit = data.get("submit", False)
        if should_submit:
            mr.submit()
            frappe.db.commit()
        
        return success_response(
            "Stock transfer request created successfully",
            {
                "material_request": mr.name,
                "status": mr.status,
                "docstatus": mr.docstatus,
                "submitted": bool(mr.docstatus == 1)
            }
        )
    
    except ValidationError as e:
        frappe.db.rollback()
        return error_response(str(e), 422)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Create Stock Transfer Request Failed", frappe.get_traceback())
        return error_response(f"Failed to create request: {str(e)}", 500)


#List all Material Requests for stock transfer
@frappe.whitelist(allow_guest=False)
def list_stock_transfer_requests():
    """
    List Material Requests (Stock Transfer Requests) 
    
    Automatically filters by the logged-in user's default company.
    Only shows stock transfer requests relative to the user's company.

    Optional filters (POST JSON):
    {
        "company": "Company Name",  # Optional, defaults to user's default company
        "status": "In Transit",
        "origin_warehouse": "Stores - HO",
        "destination_warehouse": "Stores - Branch",
        "from_date": "2025-12-01",
        "to_date": "2025-12-22"
    }

    Returns key fields: name, requested_by, requested_on, status, 
    origin_warehouse, destination_warehouse, dispatched_by, received_by, goods_received_note
    """
    if frappe.request.method != "POST":
        frappe.throw(_("Only POST allowed"))

    try:
        try:
            data = json.loads(frappe.request.data)
        except Exception:
            data = {}

        # Get logged-in user's company
        company = data.get("company") or frappe.defaults.get_user_default("Company")
        if not company:
            return {
                "success": False,
                "message": "Company is required. Please set a default company or provide a company parameter.",
                "data": {"requests": []}
            }

        filters = [["material_request_type", "=", "Material Transfer"], ["company", "=", company]]

        # Optional filters
        if status := data.get("status"):
            filters.append(["status", "=", status])
        if from_date := data.get("from_date"):
            filters.append(["creation", ">=", from_date])
        if to_date := data.get("to_date"):
            filters.append(["creation", "<=", to_date])

        # Fetch Material Requests
        mrs = frappe.get_all(
            "Material Request",
            filters=filters,
            fields=["name", "owner", "creation", "status"],
            order_by="creation desc"
        )

        results = []

        for mr in mrs:
            mr_doc = frappe.get_doc("Material Request", mr.name)
            
            # Get origin warehouse from item or document level
            origin_wh = None
            if mr_doc.items:
                origin_wh = mr_doc.items[0].get("s_warehouse")
            if not origin_wh:
                origin_wh = mr_doc.get("set_from_warehouse")
            
            # Get destination warehouse from item or document level
            dest_wh = None
            if mr_doc.items:
                dest_wh = mr_doc.items[0].get("warehouse")  # Material Request uses 'warehouse', not 't_warehouse'
            if not dest_wh:
                dest_wh = mr_doc.get("set_warehouse")
            
            # Get custom fields with fallback to standard fields
            dispatched_by = (
                getattr(mr_doc, "custom_dispatched_by", None) or 
                getattr(mr_doc, "dispatched_by", None) or 
                ""
            )
            received_by = (
                getattr(mr_doc, "custom_received_by", None) or 
                getattr(mr_doc, "received_by", None) or 
                ""
            )
            grn = (
                getattr(mr_doc, "custom_goods_received_note", None) or 
                getattr(mr_doc, "goods_received_note", None) or 
                ""
            )
            
            # Determine approval status - check if approved_by exists
            approved_by = getattr(mr_doc, "approved_by", "") or ""
            is_approved = bool(approved_by)
            approval_status = "Approved" if is_approved else "Pending Approval"
            
            # Get custom_approval_status if field exists
            custom_approval_status = None
            if frappe.db.has_column("Material Request", "custom_approval_status"):
                custom_approval_status = getattr(mr_doc, "custom_approval_status", None)

            results.append({
                "name": mr.name,
                "requested_by": mr.owner,
                "requested_on": mr.creation,
                "status": mr.status,  # ERPNext's calculated status
                "approval_status": approval_status,  # Effective approval status for display
                "approved_by": approved_by,
                "is_approved": is_approved,
                "origin_warehouse": origin_wh or "",
                "destination_warehouse": dest_wh or "",
                "dispatched_by": dispatched_by,
                "received_by": received_by,
                "goods_received_note": grn
            })
            
            # Add custom_approval_status if field exists
            if custom_approval_status is not None:
                results[-1]["custom_approval_status"] = custom_approval_status

        return {
            "success": True,
            "message": f"{len(results)} requests found",
            "data": {"requests": results}
        }

    except Exception as e:
        frappe.log_error("List Stock Transfer Requests Failed", frappe.get_traceback())
        return {
            "success": False,
            "message": f"Failed to list stock transfer requests: {str(e)}"
        }


#Get Single Stock Transfer 
@frappe.whitelist(allow_guest=False)
def get_stock_transfer_request(request_id: str = None):
    if not request_id:
        return error_response("Missing request_id")

    request_id = request_id.strip()  

    try:
        mr_doc = frappe.get_doc("Material Request", request_id)
        if mr_doc.material_request_type != "Material Transfer":
            return error_response("Not a Stock Transfer Request")

        origin_wh = mr_doc.items[0].get("s_warehouse") if mr_doc.items else ""
        dest_wh = mr_doc.items[0].get("t_warehouse") if mr_doc.items else ""
        # Get custom fields with fallback to standard fields
        dispatched_by = (
            getattr(mr_doc, "custom_dispatched_by", None) or 
            getattr(mr_doc, "dispatched_by", None) or 
            ""
        )
        received_by = (
            getattr(mr_doc, "custom_received_by", None) or 
            getattr(mr_doc, "received_by", None) or 
            ""
        )
        grn = (
            getattr(mr_doc, "custom_goods_received_note", None) or 
            getattr(mr_doc, "goods_received_note", None) or 
            ""
        )
        
        # Determine approval status
        approved_by = getattr(mr_doc, "approved_by", "") or ""
        is_approved = bool(approved_by)
        approval_status = "Approved" if is_approved else "Pending Approval"
        
        # Get custom_approval_status if field exists
        custom_approval_status = None
        if frappe.db.has_column("Material Request", "custom_approval_status"):
            custom_approval_status = getattr(mr_doc, "custom_approval_status", None)

        items = []
        for row in mr_doc.items:
            items.append({
                "item_code": row.item_code,
                "requested_qty": row.qty,
                "dispatched_qty": getattr(row, "dispatched_qty", 0),
                "received_qty": getattr(row, "received_qty", 0)
            })

        response = {
            "name": mr_doc.name,
            "status": mr_doc.status,  # ERPNext's calculated status
            "approval_status": approval_status,  # Effective approval status for display
            "approved_by": approved_by,
            "is_approved": is_approved,
            "requested_by": mr_doc.owner,
            "requested_on": mr_doc.creation,
            "origin_warehouse": origin_wh or mr_doc.get("set_from_warehouse") or "",
            "destination_warehouse": dest_wh or mr_doc.get("set_warehouse") or "",
            "dispatched_by": dispatched_by or "",
            "received_by": received_by or "",
            "goods_received_note": grn or "",
            "items": items
        }
        
        # Add custom_approval_status if field exists
        if custom_approval_status is not None:
            response["custom_approval_status"] = custom_approval_status

        return success_response("Stock transfer request fetched successfully", response)

    except frappe.DoesNotExistError:
        return error_response(f"Material Request {request_id} not found", 404)
    except Exception as e:
        frappe.log_error(f"Get Stock Transfer Request Failed for {request_id}", frappe.get_traceback())
        return error_response(f"Failed to fetch stock transfer request: {str(e)}", 500)


#Submit Stock Transfer Request (Draft -> Submitted)
@frappe.whitelist(allow_guest=False)
def submit_stock_transfer_request():
    """
    Submit a Material Request (Stock Transfer Request) from Draft to Submitted status.
    
    Payload:
    {
        "request_id": "MAT-MR-2025-00001"
    }
    """
    if frappe.request.method != "POST":
        frappe.throw(_("Only POST allowed"))
    
    try:
        try:
            data = json.loads(frappe.request.data)
        except Exception:
            data = frappe.local.form_dict or {}
        
        request_id = data.get("request_id")
        
        if not request_id:
            return error_response("request_id is required", 400)
        
        try:
            doc = frappe.get_doc("Material Request", request_id)
        except frappe.DoesNotExistError:
            return error_response(f"Stock Transfer Request {request_id} not found", 404)
        
        if doc.material_request_type != "Material Transfer":
            return error_response("Only Material Transfer requests can be submitted", 400)
        
        if doc.docstatus == 1:
            return error_response("Request is already submitted", 409)
        
        if doc.docstatus == 2:
            return error_response("Cancelled request cannot be submitted", 409)
        
        # Permission check
        if not frappe.has_permission(doc.doctype, "submit", doc=doc):
            return error_response("You do not have permission to submit this request", 403)
        
        # Submit the document
        doc.submit()
        frappe.db.commit()
        
        return success_response(
            "Stock Transfer Request submitted successfully",
            {
                "request_id": doc.name,
                "status": doc.status,
                "docstatus": doc.docstatus
            }
        )
    
    except ValidationError as e:
        frappe.db.rollback()
        return error_response(str(e), 422)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Submit Stock Transfer Request Failed", frappe.get_traceback())
        return error_response(f"Failed to submit request: {str(e)}", 500)


#Cancel Stock Transfer Request (any status -> Cancelled)
@frappe.whitelist(allow_guest=False)
def cancel_stock_transfer_request():
    """
    Cancel a Material Request (Stock Transfer Request).
    
    Payload:
    {
        "request_id": "MAT-MR-2025-00001",
        "reason": "No longer needed"  // Optional
    }
    """
    if frappe.request.method != "POST":
        frappe.throw(_("Only POST allowed"))
    
    try:
        try:
            data = json.loads(frappe.request.data)
        except Exception:
            data = frappe.local.form_dict or {}
        
        request_id = data.get("request_id")
        reason = data.get("reason")
        
        if not request_id:
            return error_response("request_id is required", 400)
        
        try:
            doc = frappe.get_doc("Material Request", request_id)
        except frappe.DoesNotExistError:
            return error_response(f"Stock Transfer Request {request_id} not found", 404)
        
        if doc.material_request_type != "Material Transfer":
            return error_response("Only Material Transfer requests can be cancelled", 400)
        
        if doc.docstatus == 2:
            return error_response("Request is already cancelled", 409)
        
        # Check if request has been dispatched (cannot cancel if already in transit)
        if doc.status in ("In Transit", "Partially In Transit"):
            return error_response(
                "Cannot cancel request that is already in transit. Please receive the stock first.",
                409
            )
        
        # Permission check
        if not frappe.has_permission(doc.doctype, "cancel", doc=doc):
            return error_response("You do not have permission to cancel this request", 403)
        
        # Add cancellation reason if provided
        if reason:
            doc.add_comment("Comment", _("Cancellation reason: {0}").format(reason))
        
        # Cancel the document
        doc.cancel()
        frappe.db.commit()
        
        return success_response(
            "Stock Transfer Request cancelled successfully",
            {
                "request_id": doc.name,
                "status": doc.status,
                "docstatus": doc.docstatus
            }
        )
    
    except ValidationError as e:
        frappe.db.rollback()
        return error_response(str(e), 422)
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error("Cancel Stock Transfer Request Failed", frappe.get_traceback())
        return error_response(f"Failed to cancel request: {str(e)}", 500)
    

@frappe.whitelist()
def create_stock_entry_old(
    stock_entry_type: str,
    items: Union[str, List[Dict]],
    posting_date: str = None,
    posting_time: str = None,
    company: str = None,
    purpose: str = None,
    from_warehouse: str = None,
    to_warehouse: str = None,
    do_not_save: bool = False,
    do_not_submit: bool = False,
) -> Dict:
    """
    Create a stock entry for material receipt, issue, or transfer.
    Returns success only if stock balance is updated successfully.
    """
    try:
        # Parse items if they are provided as a string
        if isinstance(items, str):
            items = json.loads(items)
        
        # Check if items list is not empty
        if not isinstance(items, list) or len(items) == 0:
            return {"success": False, "message": "Items must be a non-empty list."}

        # Get default company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {"success": False, "message": "Company is required. Please set a default company or provide a company parameter."}

        # Default posting_date and posting_time if not provided
        posting_date = posting_date or frappe.utils.nowdate()
        posting_time = posting_time or frappe.utils.nowtime()

        # Create stock entry document
        stock_entry = frappe.new_doc("Stock Entry")
        stock_entry.stock_entry_type = stock_entry_type
        stock_entry.company = company
        stock_entry.posting_date = posting_date
        stock_entry.posting_time = posting_time
        stock_entry.purpose = purpose or "Material Receipt"  # default purpose if not provided

        # Set document-level warehouses (used as defaults for items)
        stock_entry.from_warehouse = from_warehouse or None
        stock_entry.to_warehouse = to_warehouse or None

        # Add items to the stock entry
        for item in items:
            item_code = item.get("item_code")
            if not item_code:
                return {"success": False, "message": "item_code is required for all items"}
            
            # Validate item existence
            if not frappe.db.exists("Item", item_code):
                return {"success": False, "message": f"Item '{item_code}' does not exist"}

            qty = flt(item.get("qty", 0))
            if qty <= 0:
                return {"success": False, "message": f"Quantity must be greater than 0 for item '{item_code}'"}

            t_warehouse = item.get("t_warehouse") or to_warehouse

            # Validate warehouse existence
            if t_warehouse and not frappe.db.exists("Warehouse", t_warehouse):
                return {"success": False, "message": f"Target warehouse '{t_warehouse}' does not exist"}

            # Append item to stock entry item table
            stock_entry.append("items", {
                "item_code": item_code,
                "qty": qty,
                "t_warehouse": t_warehouse,
                "basic_rate": item.get("basic_rate"),
                "conversion_factor": item.get("conversion_factor", 1.0),
                "serial_no": item.get("serial_no"),
                "batch_no": item.get("batch_no"),
                "expense_account": item.get("expense_account"),
                "cost_center": item.get("cost_center"),
            })

        # Validate and save stock entry
        stock_entry.validate()

        if not do_not_save:
            stock_entry.insert(ignore_permissions=True)

            if not do_not_submit:
                stock_entry.submit()

        # After submission, check if stock balance was updated
        # Check if stock in the target warehouse has been updated
        updated = False
        for item in items:
            item_code = item.get("item_code")
            t_warehouse = item.get("t_warehouse") or to_warehouse

            # Query the Bin table to check stock level after receipt
            bin_entry = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": t_warehouse}, "actual_qty")

            if bin_entry and flt(bin_entry) >= flt(item.get("qty", 0)):
                updated = True
            else:
                updated = False
                break
        
        # Return response based on stock update check
        if updated:
            return {
                "success": True,
                "message": "Stock entry created and stock updated successfully",
                "data": {
                    "name": stock_entry.name,
                    "stock_entry_type": stock_entry.stock_entry_type,
                    "company": stock_entry.company,
                    "posting_date": str(stock_entry.posting_date),
                    "docstatus": stock_entry.docstatus,
                    "items_count": len(stock_entry.items),
                }
            }
        else:
            # If stock was not updated, return failure response
            frappe.log_error(f"Stock update failed for Stock Entry {stock_entry.name}", "Stock Entry Error")
            return {
                "success": False,
                "message": "Stock entry created, but stock balance was not updated",
                "data": {
                    "name": stock_entry.name,
                    "stock_entry_type": stock_entry.stock_entry_type,
                    "company": stock_entry.company,
                    "posting_date": str(stock_entry.posting_date),
                    "docstatus": stock_entry.docstatus,
                    "items_count": len(stock_entry.items),
                }
            }

    except frappe.ValidationError as e:
        frappe.log_error(f"Validation error creating stock entry: {str(e)}", "Create Stock Entry Validation Error")
        return {"success": False, "message": f"Validation error: {str(e)}", "error_type": "validation_error"}

    except Exception as e:
        frappe.log_error(f"Error creating stock entry: {str(e)}", "Create Stock Entry Error")
        return {"success": False, "message": f"Error creating stock entry: {str(e)}"}



# //////////////////////////////////////////REPORTS/////////////////////////////////////////


@frappe.whitelist()
def inventory_summary_report():
    """
    Returns an inventory summary report.
    """
    # Query to get total stock in each warehouse
    data = frappe.db.sql("""
        SELECT
            warehouse,
            SUM(actual_qty) AS total_qty,
            SUM(valuation_rate * actual_qty) AS total_value
        FROM
            `tabBin`
        WHERE
            actual_qty > 0
        GROUP BY
            warehouse
    """, as_dict=True)

    return {
        "success": True,
        "data": data
    }


@frappe.whitelist()
def inventory_movement_report(start_date: str, end_date: str):
    """
    Returns a movement report based on the start and end date.
    """
    data = frappe.db.sql("""
        SELECT
            item_code,
            SUM(CASE WHEN stock_entry_type = 'Material Receipt' THEN qty ELSE 0 END) AS received_qty,
            SUM(CASE WHEN stock_entry_type = 'Material Issue' THEN qty ELSE 0 END) AS issued_qty,
            SUM(CASE WHEN stock_entry_type = 'Material Transfer' THEN qty ELSE 0 END) AS transferred_qty
        FROM
            `tabStock Entry`
        WHERE
            posting_date BETWEEN %s AND %s
        GROUP BY
            item_code
    """, (start_date, end_date), as_dict=True)

    return {
        "success": True,
        "data": data
    }

@frappe.whitelist()
def inventory_value_report():
    """
    Returns the total inventory value by warehouse.
    """
    data = frappe.db.sql("""
        SELECT
            warehouse,
            SUM(actual_qty * valuation_rate) AS total_value
        FROM
            `tabBin`
        WHERE
            actual_qty > 0
        GROUP BY
            warehouse
    """, as_dict=True)

    return {
        "success": True,
        "data": data
    }



@frappe.whitelist()
def stock_aging_report():
    """
    Returns a stock aging report based on the stock's age.
    """
    data = frappe.db.sql("""
        SELECT
            item_code,
            warehouse,
            actual_qty,
            DATEDIFF(CURDATE(), creation) AS age_days
        FROM
            `tabBin`
        WHERE
            actual_qty > 0
    """, as_dict=True)

    # Categorize the aging of stock
    categorized_data = {
        "0-30": [],
        "31-60": [],
        "61-90": [],
        "90+": []
    }

    for entry in data:
        age_days = entry["age_days"]
        if age_days <= 30:
            categorized_data["0-30"].append(entry)
        elif age_days <= 60:
            categorized_data["31-60"].append(entry)
        elif age_days <= 90:
            categorized_data["61-90"].append(entry)
        else:
            categorized_data["90+"].append(entry)

    return {
        "success": True,
        "data": categorized_data
    }


@frappe.whitelist()
def create_stock_entry(
    stock_entry_type: str,
    items: Union[str, List[Dict]],
    posting_date: str = None,
    posting_time: str = None,
    company: str = None,
    purpose: str = None,
    from_warehouse: str = None,
    to_warehouse: str = None,
    do_not_save: bool = False,
    do_not_submit: bool = False,
) -> Dict:
    """
    Create a stock entry for material receipt, issue, or transfer.
    Returns success only if stock balance is updated successfully.
    """
    
    print(f'|Creating a Stock entry.....\n\n')
    try:
        # Parse items if they are provided as a string
        if isinstance(items, str):
            items = json.loads(items)
        
        # Check if items list is not empty
        if not isinstance(items, list) or len(items) == 0:
            return {"success": False, "message": "Items must be a non-empty list."}

        # Get default company if not provided
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                return {"success": False, "message": "Company is required. Please set a default company or provide a company parameter."}

        # Default posting_date and posting_time if not provided
        posting_date = posting_date or frappe.utils.nowdate()
        posting_time = posting_time or frappe.utils.nowtime()

        # Create stock entry document
        stock_entry = frappe.new_doc("Stock Entry")
        stock_entry.stock_entry_type = stock_entry_type
        stock_entry.company = company
        stock_entry.posting_date = posting_date
        stock_entry.posting_time = posting_time
        stock_entry.purpose = purpose or "Material Receipt"  # default purpose if not provided

        # Set document-level warehouses (used as defaults for items)
        stock_entry.from_warehouse = from_warehouse or None
        stock_entry.to_warehouse = to_warehouse or None

        # Validate and Add Items to the stock entry
        item_errors = validate_and_add_items(items, stock_entry, from_warehouse, to_warehouse)

        if item_errors:
            return {"success": False, "message": "Item validation failed", "errors": item_errors}

        # Validate and save stock entry
        stock_entry.validate()

        if not do_not_save:
            stock_entry.insert(ignore_permissions=True)

            if not do_not_submit:
                stock_entry.submit()

        # After submission, check if stock balance was updated
        updated = check_stock_update(stock_entry, items, to_warehouse)

        if updated:
            return {
                "success": True,
                "message": "Stock entry created and stock updated successfully",
                "data": {
                    "name": stock_entry.name,
                    "stock_entry_type": stock_entry.stock_entry_type,
                    "company": stock_entry.company,
                    "posting_date": str(stock_entry.posting_date),
                    "docstatus": stock_entry.docstatus,
                    "items_count": len(stock_entry.items),
                }
            }
        else:
            # If stock was not updated, return failure response
            frappe.log_error(f"Stock update failed for Stock Entry {stock_entry.name}", "Stock Entry Error")
            return {
                "success": False,
                "message": "Stock entry created, but stock balance was not updated",
                "data": {
                    "name": stock_entry.name,
                    "stock_entry_type": stock_entry.stock_entry_type,
                    "company": stock_entry.company,
                    "posting_date": str(stock_entry.posting_date),
                    "docstatus": stock_entry.docstatus,
                    "items_count": len(stock_entry.items),
                }
            }

    except frappe.ValidationError as e:
        frappe.log_error(f"Validation error creating stock entry: {str(e)}", "Create Stock Entry Validation Error")
        return {"success": False, "message": f"Validation error: {str(e)}", "error_type": "validation_error"}

    except Exception as e:
        frappe.log_error(f"Error creating stock entry: {str(e)}", "Create Stock Entry Error")
        return {"success": False, "message": f"Error creating stock entry: {str(e)}"}


def validate_and_add_items(items, stock_entry, from_warehouse, to_warehouse):
    item_errors = []

    for item in items:
        item_code = item.get("item_code")
        if not item_code:
            item_errors.append("item_code is required for all items")
            continue
        
        # Validate item existence
        if not frappe.db.exists("Item", item_code):
            item_errors.append(f"Item '{item_code}' does not exist")
            continue

        qty = flt(item.get("qty", 0))
        if qty <= 0:
            item_errors.append(f"Quantity must be greater than 0 for item '{item_code}'")
            continue

        t_warehouse = item.get("t_warehouse") or to_warehouse

        # Validate warehouse existence
        if t_warehouse and not frappe.db.exists("Warehouse", t_warehouse):
            item_errors.append(f"Target warehouse '{t_warehouse}' does not exist")
            continue

        # Append item to stock entry item table
        stock_entry.append("items", {
            "item_code": item_code,
            "qty": qty,
            "t_warehouse": t_warehouse,
            "basic_rate": item.get("basic_rate"),
            "conversion_factor": item.get("conversion_factor", 1.0),
            "serial_no": item.get("serial_no"),
            "batch_no": item.get("batch_no"),
            "expense_account": item.get("expense_account"),
            "cost_center": item.get("cost_center"),
        })

    return item_errors


def check_stock_update(stock_entry, items, to_warehouse):
    updated = False
    for item in items:
        item_code = item.get("item_code")
        t_warehouse = item.get("t_warehouse") or to_warehouse

        # Query the Bin table to check stock level after receipt
        bin_entry = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": t_warehouse}, "actual_qty")

        if bin_entry and flt(bin_entry) >= flt(item.get("qty", 0)):
            updated = True
        else:
            updated = False
            break

    return updated




def check_user_permissions():
    user = "steve@steve.com"
    doc_type = "Stock Ledger Entry"

    # Check the permissions for the user
    permissions = frappe.get_all("User Permission", filters={"user": user, "allow": doc_type})
    
    # Log the permissions for debugging
    frappe.logger().error(f"Permissions for {user}: {permissions}")
    
    
    


@frappe.whitelist(methods=["POST"])
def confirm_receive_transfer(stock_entry_name):
    """
    Exposes the stock transfer process as an API.
    """
    try:
        # Fetch the Stock Entry Document
        stock_entry = frappe.get_doc('Stock Entry', stock_entry_name)
        
        # Check if the stock entry is a Material Transfer (optional)
        if stock_entry.purpose != 'Material Transfer':
            return {
                "status": "fail",
                "message": "This is not a material transfer type."
            }

        # Initialize counters for requested, dispatched, and received quantities
        requested_qty = 0
        dispatched_qty = 0

        for item in stock_entry.items:
            requested_qty += item.qty
            dispatched_qty += item.transferred_qty

            # Log stock movement for each item (from store → out, to store → in)
            # if item.qty > 0:
            #     log_stock_movement(item, stock_entry)

            # Record variance (Requested vs Dispatched vs Received)
            # record_variance(item)

        # Determine if the stock entry is fully received or partially received
        if requested_qty == dispatched_qty:
            transfer_status = "Received"
        else:
            transfer_status = "Partially Received"
        
        # Update stock in destination warehouse (i.e., increase stock)
        for item in stock_entry.items:
            if item.transferred_qty > 0:
                add_stock_to_destination(item)

        # Return a custom response
        return {
            "status": "success",
            "message": f"Stock transfer processed successfully. Transfer Status: {transfer_status}",
            "data": {
                "stock_entry_name": stock_entry_name,
                "transfer_status": transfer_status
            }
        }

    except frappe.ValidationError as e:
        # Parse the _server_messages and check for the Actual Qty is mandatory error
        if hasattr(e, '_server_messages'):
            server_messages = json.loads(e._server_messages)
            for msg in server_messages:
                if "Actual Qty is mandatory" in msg.get('message', ''):
                    return {
                        "status": "error",
                        "message": f"Actual Qty is mandatory in Stock Entry {stock_entry_name}",
                        "data": {
                            "stock_entry_name": stock_entry_name
                        }
                    }

        # Generic validation error handling
        return {
            "status": "error",
            "message": str(e),
            "data": {
                "stock_entry_name": stock_entry_name
            }
        }

    except Exception as e:
        # General exception handling (logging and responding)
        return {
            "status": "error",
            "message": f"An error occurred: {str(e)}",
            "data": {
                "stock_entry_name": stock_entry_name
            }
        }

def process_stock_transfer(stock_entry_name):
    """
    Processes the stock transfer by adding stock to the destination store,
    logging the stock movement, recording variances, and updating the status.
    """
    try:
        # Step 1: Fetch the Stock Entry Document (Material Transfer)
        stock_entry = frappe.get_doc('Stock Entry', stock_entry_name)
        
        if stock_entry.purpose != 'Material Transfer':
            return {
                "status": "fail",
                "message": _("This is not a material transfer type.")
            }

        if stock_entry.status == "Received":
            return {
                "status": "fail",
                "message": _("Stock transfer has already been received.")
            }

        # Initialize counters for requested, dispatched, and received quantities
        requested_qty = 0
        dispatched_qty = 0

        for item in stock_entry.items:
            requested_qty += item.qty
            dispatched_qty += item.transferred_qty

            # Log stock movement for each item (from store → out, to store → in)
            # if item.qty > 0:
            #     log_stock_movement(item, stock_entry)

            # Record variance (Requested vs Dispatched vs Received)
            # record_variance(item)

        # Update stock in destination warehouse (i.e., increase stock)
        for item in stock_entry.items:
            if item.transferred_qty > 0:
                add_stock_to_destination(item)

        # Update the status of the stock entry (Material Transfer)
        if requested_qty == dispatched_qty:
            stock_entry.status = "Received"
        else:
            stock_entry.status = "Partially Received"
        
        stock_entry.save()

        return {
            "status": "success",
            "message": _("Stock transfer processed successfully."),
            "data": {
                "stock_entry_name": stock_entry_name,
                "status": stock_entry.status
            }
        }
    
    except frappe.DoesNotExistError:
        return {
            "status": "fail",
            "message": _("Stock Entry not found.")
        }
    
    except ValidationError as e:
        # Check if the error is about 'Actual Qty is mandatory'
        if "Actual Qty is mandatory" in str(e):
            return {
                "status": "fail",
                "message": f"Actual Qty is mandatory in Stock Entry {stock_entry_name}"
            }
        return {
            "status": "fail",
            "message": str(e)
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"An error occurred: {str(e)}"
        }

def log_stock_movement(item, stock_entry):
    """
    Logs the movement of stock between warehouses (from store → out, to store → in)
    """
    # Log Out movement (from the source warehouse)
    frappe.get_doc({
        "doctype": "Stock Ledger Entry",
        "item_code": item.item_code,
        "warehouse": stock_entry.from_warehouse,
        "qty": -item.transferred_qty,
        "stock_entry": stock_entry.name,
        "posting_date": stock_entry.posting_date,
        "voucher_type": "Stock Entry",
        "voucher_no": stock_entry.name,
        "transaction_type": "Out"
    }).insert()

    # Log In movement (to the destination warehouse)
    frappe.get_doc({
        "doctype": "Stock Ledger Entry",
        "item_code": item.item_code,
        "warehouse": stock_entry.to_warehouse,
        "qty": item.transferred_qty,
        "stock_entry": stock_entry.name,
        "posting_date": stock_entry.posting_date,
        "voucher_type": "Stock Entry",
        "voucher_no": stock_entry.name,
        "transaction_type": "In"
    }).insert()

def record_variance(item):
    """
    Records variance between requested, dispatched, and received quantities.
    """
    requested_qty = item.qty
    dispatched_qty = item.transferred_qty

    variance = requested_qty - dispatched_qty
    if variance != 0:
        frappe.get_doc({
            "doctype": "Stock Transfer Variance",
            "item_code": item.item_code,
            "requested_qty": requested_qty,
            "dispatched_qty": dispatched_qty,
            "variance": variance,
            "stock_entry_item": item.name
        }).insert()

def add_stock_to_destination(item):
    """
    Adds the stock to the destination warehouse (final reception).
    """
    stock_entry = frappe.get_doc({
        "doctype": "Stock Entry",
        "purpose": "Material Receipt",
        "items": [{
            "item_code": item.item_code,
            "qty": item.transferred_qty,
            "warehouse": item.to_warehouse,
            "stock_uom": item.uom
        }]
    })

    stock_entry.insert()
    stock_entry.submit()



