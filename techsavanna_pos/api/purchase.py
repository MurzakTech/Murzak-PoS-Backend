import frappe
import datetime
import json
import traceback
from frappe import _
from frappe.utils import today, nowdate, getdate, flt
from frappe.exceptions import ValidationError, PermissionError, DoesNotExistError
from json.decoder import JSONDecodeError
from datetime import datetime

from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice as make_pi_from_po
from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice as make_pi_from_pr

from techsavanna_pos.api.stock import create_stock_entry
from techsavanna_pos.api.api_response import pos_response
from techsavanna_pos.api.error_handler import handle_pos_exception
from techsavanna_pos.api.constants import POSErrorCode


from erpnext.accounts.utils import get_account_currency
from erpnext.stock.get_item_details import get_item_details


import frappe
from frappe.model.document import Document
from frappe.exceptions import ValidationError



# Helper Functions
def validate_required(data, fields):
    for f in fields:
        if not data.get(f):
            frappe.throw(f"Missing required field: {f}")

def check_duplicate(ref, doctype):
    if ref and frappe.db.exists(doctype, {"custom_pos_reference": ref}):
        frappe.throw(f"{doctype} already exists for POS reference")

def success(doc, key):
    return {
        "status": "success",
        key: doc.name,
        "posting_date": getattr(doc, "posting_date", None)
    }
    
    
def shorten_error(msg, max_length=120):
    if len(msg) <= max_length:
        return msg
    return msg[:max_length].rsplit(",", 1)[0] + "..."

def extract_frappe_error(e):
    """
    Extract clean error message from Frappe exceptions
    """
    if hasattr(e, "args") and e.args:
        return str(e.args[0])

    return str(e)


def update_stock_valuation(item_code, accepted_qty, warehouse):
    """
    Updates stock valuation (FIFO or Weighted Average) when GRN is applied.
    """
    print(f'\nUpdate Stock Valuation for item: {item_code} with qty: {accepted_qty} at warehouse: {warehouse}')

    # Step 1: Get current stock quantity from Bin (or equivalent table)
    current_stock_qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0
    
    # Step 2: Get current valuation rate from Item (or purchase rate if preferred)
    current_valuation_rate = frappe.db.get_value("Item", item_code, "valuation_rate") or 0
    
    # Step 3: Get current purchase rate (optional, if needed for calculation)
    current_rate = frappe.db.get_value("Purchase Invoice Item", {"item_code": item_code}, "rate") or 0
    
    if accepted_qty > 0:
        # Step 4: Calculate the new weighted average valuation rate
        new_valuation_rate = ((current_valuation_rate * current_stock_qty) + (accepted_qty * current_rate)) / (current_stock_qty + accepted_qty)
        
        # Step 5: Update the item valuation rate
        frappe.db.set_value("Item", item_code, "valuation_rate", new_valuation_rate)
        
        print(f"Updated valuation rate for {item_code} to {new_valuation_rate}")


def update_po_status_if_last_delivery(po_name):
    po = frappe.get_doc("Purchase Order", po_name)
    total_qty_ordered = sum([item.qty for item in po.items])
    total_qty_received = sum([item.received_qty for item in po.items])
    
    if total_qty_received == total_qty_ordered:
        # All items received, mark PO as closed
        po.status = "Closed"
        po.save()
        frappe.logger().info(f"Purchase Order {po_name} marked as closed.")




@frappe.whitelist()
def create_purchase_order():
    """
    Create Purchase Order (Draft)
    POS-safe, normalized, ERPNext v15 compatible
    """

    try:
        data = frappe.local.form_dict

        company = data.get("company")
        supplier = data.get("supplier")

        if not company or not frappe.db.exists("Company", company):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_COMPANY,
                message=f"Invalid or missing company '{company}'",
                data={"company": company}
            )

        if not supplier or not frappe.db.exists("Supplier", supplier):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_SUPPLIER,
                message=f"Invalid or missing supplier '{supplier}'",
                data={"supplier": supplier}
            )

        items = frappe.parse_json(data.get("items"))

        if not items or not isinstance(items, list):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_ITEMS,
                message="Items must be a non-empty list",
                data={"items": items}
            )

        po_items = []

        # Item loop (warehouse resolution, validation)
        for idx, item in enumerate(items, start=1):
            item_code = item.get("item_code")
            qty = item.get("qty")
            material_request = item.get("material_request")
            material_request_item = item.get("material_request_item")
            warehouse = None

            # Item existence
            if not item_code or not frappe.db.exists("Item", item_code):
                return pos_response(
                    status="error",
                    code=POSErrorCode.INVALID_ITEM,
                    message=(
                        f"Row #{idx}: Item '{item_code}' is invalid for "
                        f"supplier '{supplier}' in company '{company}'"
                    ),
                    data={
                        "row": idx,
                        "item_code": item_code,
                        "supplier": supplier,
                        "company": company
                    }
                )

            # Quantity check
            if not qty or float(qty) <= 0:
                return pos_response(
                    status="error",
                    code=POSErrorCode.INVALID_QTY,
                    message=(
                        f"Row #{idx}: Quantity '{qty}' is invalid for item '{item_code}'"
                    ),
                    data={
                        "row": idx,
                        "item_code": item_code,
                        "supplier": supplier,
                        "company": company,
                        "qty": qty
                    }
                )

            is_stock_item = frappe.db.get_value("Item", item_code, "is_stock_item")

            # Material Request warehouse
            if material_request:
                if not frappe.db.exists("Material Request", material_request):
                    return pos_response(
                        status="error",
                        code=POSErrorCode.INVALID_MATERIAL_REQUEST,
                        message=(
                            f"Row #{idx}: Material Request '{material_request}' is invalid"
                        ),
                        data={
                            "row": idx,
                            "item_code": item_code,
                            "supplier": supplier,
                            "company": company,
                            "material_request": material_request
                        }
                    )

                if not material_request_item:
                    return pos_response(
                        status="error",
                        code=POSErrorCode.INVALID_MATERIAL_REQUEST,
                        message=(
                            f"Row #{idx}: Material Request Item is required "
                            f"for Material Request '{material_request}'"
                        ),
                        data={
                            "row": idx,
                            "item_code": item_code,
                            "supplier": supplier,
                            "company": company,
                            "material_request": material_request
                        }
                    )

                warehouse = frappe.db.get_value(
                    "Material Request Item",
                    material_request_item,
                    "warehouse"
                )

            # Warehouse resolution fallback
            if not warehouse:
                warehouse = item.get("warehouse")

            if is_stock_item and not warehouse:
                warehouse = frappe.db.get_value(
                    "Item Default",
                    {"parent": item_code, "company": company},
                    "default_warehouse"
                )

            if is_stock_item and not warehouse:
                warehouse = frappe.db.get_value(
                    "Company", company, "custom_default_warehouse"
                )

            if is_stock_item and not warehouse:
                return pos_response(
                    status="error",
                    code=POSErrorCode.INVALID_WAREHOUSE,
                    message=(
                        f"Row #{idx}: Warehouse is required for stock item '{item_code}'"
                    ),
                    data={
                        "row": idx,
                        "item_code": item_code,
                        "supplier": supplier,
                        "company": company
                    }
                )

            # Warehouse company validation
            if warehouse:
                wh_company = frappe.db.get_value("Warehouse", warehouse, "company")
                if wh_company and wh_company != company:
                    return pos_response(
                        status="error",
                        code=POSErrorCode.INVALID_WAREHOUSE,
                        message=(
                            f"Row #{idx}: Warehouse '{warehouse}' belongs to '{wh_company}', "
                            f"not company '{company}'"
                        ),
                        data={
                            "row": idx,
                            "item_code": item_code,
                            "supplier": supplier,
                            "company": company,
                            "warehouse": warehouse
                        }
                    )

            # Append PO item
            po_items.append({
                "item_code": item_code,
                "qty": qty,
                "rate": item.get("rate"),
                "schedule_date": item.get("schedule_date") or today(),
                "warehouse": warehouse
            })

        # Create Purchase Order
        po = frappe.get_doc({
            "doctype": "Purchase Order",
            "company": company,
            "supplier": supplier,
            "transaction_date": data.get("transaction_date") or today(),
            "items": po_items,
            "idempotency_key": data.get("idempotency_key")
        })

        po.insert()

        # Return normalized success
        return pos_response(
            status="success",
            code="PO_CREATED",
            lpo_no=po.name,
            message="Purchase order created successfully",
            data={
                "docstatus": po.docstatus,
                "grand_total": po.grand_total,
                "company": company,
                "supplier": supplier,
                "items_count": len(po_items)
            }
        )

    except Exception as e:
        return handle_pos_exception(e)



@frappe.whitelist(methods=["POST"])
def submit_purchase_order():
    """
    Submit an existing Purchase Order (POS-safe)
    """

    data = frappe.local.form_dict
    lpo_no = data.get("lpo_no") or data.get("purchase_order")

    if not lpo_no:
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_REQUEST,
            message="LPO number is required"
        )

    try:
        if not frappe.db.exists("Purchase Order", lpo_no):
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_NOT_FOUND,
                lpo_no=lpo_no,
                message=f"LPO No. {lpo_no} was not found"
            )

        po = frappe.get_doc("Purchase Order", lpo_no)

        if not frappe.has_permission("Purchase Order", "submit", po):
            return pos_response(
                status="error",
                code=POSErrorCode.PERMISSION_DENIED,
                lpo_no=po.name,
                message="You do not have permission to submit this LPO"
            )

        # Business state rules
        if po.docstatus == 1:
            # LPO already submitted
            return pos_response(
                status="error",
                code=POSErrorCode.PO_ALREADY_SUBMITTED,  
                lpo_no=po.name,
                message="LPO already submitted"
            )

        if po.docstatus == 2:
            # LPO is cancelled, cannot submit
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_CANCELLED,
                lpo_no=po.name,
                message="This LPO has been cancelled and cannot be submitted"
            )

        frappe.db.begin()
        po.submit()
        frappe.db.commit()

        return pos_response(
            status="success",
            code="PO_SUBMITTED",  
            lpo_no=po.name,
            message="LPO submitted successfully",
            data={
                "docstatus": po.docstatus,
                "grand_total": po.grand_total,
                "company": po.company,
                "supplier": po.supplier,
                "items_count": len(po.items)
            }
        )
    except Exception as e:
        return handle_pos_exception(e, lpo_no=lpo_no)



@frappe.whitelist(allow_guest=False)
def get_purchase_order(po_name: str = None):
    """
    Get detailed information for a Purchase Order.
    Args:
        po_name (str): Name / ID of the Purchase Order
    Returns:
        dict: Detailed PO info including items, taxes, supplier, totals, GRN
    """
    try:
        if not po_name:
            return {"status": "error", "message": "Missing required parameter: po_name"}
        
        po = frappe.get_doc("Purchase Order", po_name)

        if not po.has_permission("read"):
            return {"status": "error", "message": "No permission to access this Purchase Order"}

        # Response
        po_data = {
            "name": po.name,
            "supplier": po.supplier,
            "company": po.company,
            "transaction_date": po.transaction_date,
            "status": po.status,
            "docstatus": po.docstatus,
            "grand_total": po.grand_total,
            "total_qty": po.total_qty,
            "currency": po.currency,
            "idempotency_key": getattr(po, "idempotency_key", None),
            "taxes_and_charges": po.taxes_and_charges,
            "items": [],
            "purchase_receipts": []  # Field to hold associated Purchase Receipts
        }

        # Include items
        for item in po.items:
            po_data["items"].append({
                "item_code": item.item_code,
                "description": item.description,
                "qty": item.qty,
                "uom": item.uom,
                "rate": item.rate,
                "amount": item.amount,
                "warehouse": item.warehouse,
                "material_request": item.material_request,
                "material_request_item": item.material_request_item
            })

        # Include taxes
        if po.taxes:
            po_data["taxes"] = []
            for tax in po.taxes:
                po_data["taxes"].append({
                    "charge_type": tax.charge_type,
                    "account_head": tax.account_head,
                    "description": tax.description,
                    "rate": tax.rate,
                    "tax_amount": tax.tax_amount,
                    "total": tax.total
                })

        # Fetch related Purchase Receipts (GRN)
        grns = frappe.get_all(
            "Purchase Receipt", 
            filters={"purchase_order": po_name, "docstatus": 1},  # Only fetch GRNs that are submitted (docstatus = 1)
            fields=["name"]
        )

        if grns:
            # Remove duplicate GRN entries
            unique_grns = list(set([grn.name for grn in grns]))  # Convert to set and back to list to remove duplicates
            po_data["purchase_receipts"] = unique_grns  # Assign the unique GRNs

        return {"status": "success", "purchase_order": po_data}

    except frappe.DoesNotExistError:
        return {"status": "error", "message": f"Purchase Order '{po_name}' does not exist"}

    except frappe.PermissionError:
        return {"status": "error", "message": "No permission to access this Purchase Order"}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Purchase Order API Error")
        return {"status": "error", "message": f"Internal server error: {str(e)}"}





@frappe.whitelist(allow_guest=False)
def get_purchase_order_no_grn(po_name: str = None):
    """
    Get detailed information for a Purchase Order.
    Args:
        po_name (str): Name / ID of the Purchase Order
    Returns:
        dict: Detailed PO info including items, taxes, supplier, totals, GRN
    """
    try:
        if not po_name:
            return {"status": "error", "message": "Missing required parameter: po_name"}
        
        po = frappe.get_doc("Purchase Order", po_name)

        if not po.has_permission("read"):
            return {"status": "error", "message": "No permission to access this Purchase Order"}

        # Response
        po_data = {
            "name": po.name,
            "supplier": po.supplier,
            "company": po.company,
            "transaction_date": po.transaction_date,
            "status": po.status,
            "docstatus": po.docstatus,
            "grand_total": po.grand_total,
            "total_qty": po.total_qty,
            "currency": po.currency,
            "idempotency_key": getattr(po, "idempotency_key", None),
            "taxes_and_charges": po.taxes_and_charges,
            "items": [],
        }

        # Include items
        for item in po.items:
            po_data["items"].append({
                "item_code": item.item_code,
                "description": item.description,
                "qty": item.qty,
                "uom": item.uom,
                "rate": item.rate,
                "amount": item.amount,
                "warehouse": item.warehouse,
                "material_request": item.material_request,
                "material_request_item": item.material_request_item
            })

        # Include taxes
        if po.taxes:
            po_data["taxes"] = []
            for tax in po.taxes:
                po_data["taxes"].append({
                    "charge_type": tax.charge_type,
                    "account_head": tax.account_head,
                    "description": tax.description,
                    "rate": tax.rate,
                    "tax_amount": tax.tax_amount,
                    "total": tax.total
                })

        return {"status": "success", "purchase_order": po_data}

    except frappe.DoesNotExistError:
        return {"status": "error", "message": f"Purchase Order '{po_name}' does not exist"}

    except frappe.PermissionError:
        return {"status": "error", "message": "No permission to access this Purchase Order"}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Purchase Order API Error")
        return {"status": "error", "message": f"Internal server error: {str(e)}"}





@frappe.whitelist(allow_guest=False)
def list_purchase_orders(filters=None, limit_start=0, limit_page_length=20):
    """
    List Purchase Orders with optional filters and pagination.

    Args:
        filters (str, optional): JSON string of filters, e.g. {"supplier": "Supplier A", "status": "Draft"}
        limit_start (int, optional): Starting index for pagination
        limit_page_length (int, optional): Number of records to return (default 20)

    Returns:
        dict: List of POs and total count
    """
    try:
        filters_dict = {}
        if filters:
            try:
                filters_dict = frappe.parse_json(filters)
            except Exception as e:
                return {"status": "error", "message": f"Invalid JSON for filters: {str(e)}"}

        # Permission: Only fetch POs the user can read
        allowed_conditions = "1=1"
        if not frappe.has_permission("Purchase Order", "read"):
            return {"status": "error", "message": "No permission to access Purchase Orders"}

        # Fetch POs with filters and pagination
        po_list = frappe.get_all(
            "Purchase Order",
            filters=filters_dict,
            fields=["name", "supplier", "company", "transaction_date", "status", "docstatus", "grand_total"],
            order_by="transaction_date desc",
            limit_start=limit_start,
            limit_page_length=limit_page_length
        )

        total_count = frappe.db.count("Purchase Order", filters=filters_dict)

        return {
            "status": "success",
            "total_count": total_count,
            "purchase_orders": po_list
        }

    except frappe.PermissionError:
        return {"status": "error", "message": "No permission to access Purchase Orders"}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "List Purchase Orders API Error")
        return {"status": "error", "message": f"Internal server error: {str(e)}"}



# If item does not have default warehouse
def resolve_warehouse(item, company):
    # Use warehouse from item if provided
    if item.get("warehouse"):
        return item["warehouse"]

    # Fetch default company warehouse
    default_warehouse = frappe.db.get_value(
        "Company",
        company,
        "custom_default_warehouse"
    )
    
    print(f'\nDefault WH ... {default_warehouse}')

    if default_warehouse:
        item["warehouse"] = default_warehouse
        return default_warehouse

    frappe.throw(
        f"Warehouse is mandatory for stock item {item.get('item_code')}. "
        f"No warehouse found and no default warehouse set for company {company}."
    )





@frappe.whitelist(methods=["POST"])
def create_grn():
    frappe.logger().debug("Create GRN. Starting...")
    print(f"\n\nCreate GRN. Starting...")

    try:
        # Try to parse incoming request data
        data = frappe.local.form_dict
        frappe.logger().debug(f"Create GRN: Data received: {data}")

        if not data.get("lpo_no"):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="LPO No. is missing.",
                lpo_no=None,
                grn_no=None
            )

        if not data.get("items"):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="Items data is missing.",
                lpo_no=data.get("lpo_no"),
                grn_no=None
            )

        if not isinstance(data.get("items"), list) or len(data.get("items")) == 0:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="Items list is empty or invalid.",
                lpo_no=data.get("lpo_no"),
                grn_no=None
            )


        # Loop through the items to validate qty is provided
        for idx, item in enumerate(data.get("items"), start=1):
            if "qty" not in item or item["qty"] is None or item["qty"] == "":
                print(f'\nHERE!! Loop thro Itens..{item}....\n')
                return pos_response(
                    status="error",
                    code=POSErrorCode.INVALID_REQUEST,
                    message=f"Item {item['item_code']} in row #{idx} is missing or has an invalid quantity. Please provide a valid quantity.",
                    lpo_no=data.get("lpo_no"),
                    grn_no=None
                )

        # Proceed with the normal logic for creating the GRN
        lpo_no = data.get("lpo_no")
        warehouse = data.get("warehouse")
        items = data.get("items")

        # Log incoming data
        frappe.logger().debug(f"create_grn called with data: {data}")
        frappe.logger().info("Creating GRN for LPO: %s", lpo_no)

        # Validate LPO existence
        frappe.logger().debug(f"Checking if Purchase Order {lpo_no} exists...")
        if not frappe.db.exists("Purchase Order", lpo_no):
            frappe.logger().error(f"LPO No. {lpo_no} does not exist.")
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_NOT_FOUND,
                message=f"LPO No. {lpo_no} does not exist",
                lpo_no=lpo_no
            )

        po = frappe.get_doc("Purchase Order", lpo_no)

        # Ensure PO is submitted
        if po.docstatus != 1:
            frappe.logger().error(f"LPO No. {lpo_no} is not in 'Submitted' state.")
            return pos_response(
                status="error",
                code=POSErrorCode.PO_NOT_SUBMITTED,
                message="Purchase Order must be submitted",
                lpo_no=lpo_no
            )

        # Validate warehouse belongs to the same company as PO
        if warehouse:
            warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
            if warehouse_company != po.company:
                frappe.logger().error(f"Warehouse '{warehouse}' does not belong to company '{po.company}'.")
                return pos_response(
                    status="error",
                    code=POSErrorCode.INVALID_WAREHOUSE,
                    message=f"Warehouse '{warehouse}' does not belong to the company '{po.company}'",
                    lpo_no=lpo_no,
                    data={
                        "warehouse": warehouse,
                        "po_company": po.company
                    }
                )
        else:
            frappe.logger().error(f"Warehouse is missing for LPO {lpo_no}.")
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_WAREHOUSE,
                message="Invalid or missing warehouse",
                lpo_no=lpo_no,
                data={}
            )

        # Create Purchase Receipt (GRN)
        frappe.logger().debug(f"Creating Purchase Receipt for LPO: {lpo_no}...")
        frappe.db.begin()

        pr = frappe.get_doc({
            "doctype": "Purchase Receipt",
            "supplier": po.supplier,
            "company": po.company,
            "set_warehouse": warehouse,
            "purchase_order": po.name,
            "items": []
        })

        po_items = {i.item_code: i for i in po.items}

        # Process each item
        for idx, row in enumerate(items, start=1):
            item_code = row["item_code"]
            item = po_items.get(item_code)

            if not item:
                frappe.logger().error(f"Row #{idx}: Item '{item_code}' not found in Purchase Order.")
                return pos_response(
                    status="error",
                    code=POSErrorCode.INVALID_ITEM,
                    message=f"Row #{idx}: Item '{item_code}' not found in Purchase Order",
                    data={
                        "row": idx,
                        "item_code": item_code,
                        "lpo_no": lpo_no
                    }
                )

            pending_qty = item.qty - item.received_qty
            frappe.logger().debug(f"Row #{idx}: Checking if received qty exceeds ordered quantity...")

            if row["qty"] > pending_qty:
                frappe.logger().error(f"Row #{idx}: Received qty exceeds ordered qty for item '{item_code}'.")
                return pos_response(
                    status="error",
                    code=POSErrorCode.QTY_EXCEEDS_ORDERED,
                    message=f"Row #{idx}: Received qty exceeds ordered quantity for item '{item_code}'",
                    data={
                        "row": idx,
                        "item_code": item_code,
                        "lpo_no": lpo_no
                    }
                )

            # Append item to Purchase Receipt
            pr.append("items", {
                "item_code": item_code,
                "qty": row["qty"],
                "rate": item.rate,
                "warehouse": warehouse,
                "purchase_order": po.name,
                "purchase_order_item": item.name
            })

        # Log creation of Purchase Receipt
        frappe.logger().debug(f"Created Purchase Receipt with items: {pr.items}")

        # Insert and submit the GRN
        pr.insert(ignore_permissions=True)
        pr.submit()

        frappe.db.commit()

        # Log success
        frappe.logger().info(f"GRN created successfully for LPO {lpo_no} with GRN No: {pr.name}")

        return pos_response(
            status="success",
            code="GRN_CREATED",
            message="Goods received successfully",
            lpo_no=lpo_no,
            grn_no=pr.name,
            data={
                "docstatus": pr.docstatus,
                "received_items": len(pr.items)
            }
        )

    except JSONDecodeError as e:
        # Log the error for malformed JSON
        frappe.logger().error(f"Create GRN: JSONDecodeError: {str(e)}")
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_JSON,
            message="Malformed JSON. Ensure the payload is correctly formatted.",
            lpo_no=None,
            grn_no=None
        )

    except Exception as e:
        # Catch all other exceptions
        frappe.logger().error(f"Create GRN: Unexpected error: {str(e)}")
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message="An unexpected error occurred while processing the GRN request.",
            lpo_no=None,
            grn_no=None
        )




@frappe.whitelist(methods=["POST"])
def create_stock_receipt(grn_no):
    """
    Apply GRN to inventory by creating a Stock Entry for the received items.
    """
    try:
        # Fetch the GRN document
        grn = frappe.get_doc("Purchase Receipt", grn_no)

        if not grn:
            return pos_response(
                status="error", 
                code=POSErrorCode.LPO_NOT_FOUND, 
                message=f"GRN {grn_no} not found. Please check the GRN number and try again.",
                grn_no=grn_no
            )

        # Ensure GRN status is 'RECEIVED' or 'To Bill'
        if grn.docstatus != 1 or grn.status not in ["Received", "To Bill"]:
            return pos_response(
                status="error", 
                code=POSErrorCode.GRN_NOT_RECEIVED,
                message=(
                    f"GRN {grn_no} is in an invalid status. GRN must be either 'Received' or 'To Bill' "
                    "to be applied to inventory."
                ),
                grn_no=grn_no
            )

        # Fetch the global warehouse (set_warehouse) from the GRN
        accepted_warehouse = grn.set_warehouse
        if not accepted_warehouse:
            return pos_response(
                status="error", 
                code=POSErrorCode.INVALID_WAREHOUSE, 
                message=f"GRN {grn_no} does not have an accepted warehouse set. Please set a valid warehouse for the GRN.",
                grn_no=grn_no
            )

        # Prepare the items for stock entry
        items = []
        for item in grn.items:
            if not item.qty or item.qty <= 0:
                return pos_response(
                    status="error", 
                    code=POSErrorCode.INVALID_QTY,
                    message=f"Invalid quantity received for item {item.item_code}. Please ensure the quantity is correctly entered.",
                    grn_no=grn_no
                )

            # Ensure item_code is present
            if not item.item_code:
                return pos_response(
                    status="error", 
                    code=POSErrorCode.INVALID_ITEM,
                    message=f"Missing item code for item in GRN {grn_no}. Please provide a valid item code.",
                    grn_no=grn_no
                )

            # Use item-level warehouse if it exists, otherwise use the global accepted warehouse (set_warehouse)
            item_warehouse = item.warehouse or accepted_warehouse

            # Add item to the list of items to be used for stock entry
            items.append({
                "item_code": item.item_code,
                "qty": item.qty,  
                "basic_rate": item.rate,  
                "conversion_factor": item.conversion_factor,
                "t_warehouse": item_warehouse,  # Use warehouse from item or global accepted warehouse
                "serial_no": item.serial_no,
                "batch_no": item.batch_no
            })

        # If no valid items found, return error
        if not items:
            return pos_response(
                status="error", 
                code=POSErrorCode.INVALID_ITEMS, 
                message="No valid items found to apply for stock entry. Please check the items in the GRN.",
                grn_no=grn_no
            )

        # Convert posting_date to string (ensure it's in 'yyyy-mm-dd' format)
        posting_date_str = str(grn.posting_date) if isinstance(grn.posting_date, datetime.date) else grn.posting_date

        # Call the stock entry creation method
        stock_entry_response = create_stock_entry(
            stock_entry_type="Material Receipt",  # Type of stock entry
            items=items,
            posting_date=posting_date_str,  
            company=grn.company,
            from_warehouse=None,  
            to_warehouse=accepted_warehouse,  
            do_not_save=False,  # Save the stock entry
            do_not_submit=False  # Submit the stock entry
        )

        # Check the response from stock entry creation
        if stock_entry_response.get("success"):
            # If stock entry creation is successful, mark the GRN as applied to stock
            grn.db_set("status", "APPLIED_TO_STOCK")
            return pos_response(
                status="success", 
                message="GRN applied to inventory successfully.", 
                data=stock_entry_response["data"],  # Assuming 'data' contains stock entry details
                grn_no=grn_no
            )

        else:
            return pos_response(
                status="error", 
                code=POSErrorCode.STOCK_ERROR,
                message=f"Failed to create stock entry: {stock_entry_response['message']}",
                grn_no=grn_no
            )

    except frappe.exceptions.DoesNotExistError:
        # GRN not found in database
        return pos_response(
            status="error", 
            code=POSErrorCode.LPO_NOT_FOUND,
            message=f"GRN {grn_no} does not exist in the system. Please verify the GRN number.",
            grn_no=grn_no
        )
    
    except frappe.exceptions.ValidationError as e:
        # ValidationError raised by ERPNext during document creation or validation
        return pos_response(
            status="error", 
            code=POSErrorCode.INVALID_REQUEST, 
            message=f"Validation error: {str(e)}",
            grn_no=grn_no
        )

    except Exception as e:
        # Log the exception for debugging
        frappe.logger().error(f"Unexpected error occurred while processing GRN {grn_no}: {str(e)}")
        frappe.logger().error(f"Full traceback: {traceback.format_exc()}")

        # Return a user-friendly error message with the exception message included
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message=f"An unexpected error occurred while processing your request. Error: {str(e)}. Please try again later. If the problem persists, contact support.",
            grn_no=grn_no
        )
        
        
        
    

@frappe.whitelist(methods=["GET"])
def get_grn_details(grn_no):
    """
    Fetch the GRN details along with the PO linkage.
    """
    try:
        # Using reference_id as grn_no and doctype as "Purchase Receipt"
        reference_id = grn_no
        doctype = "Purchase Receipt"

        grn = frappe.get_doc(doctype, reference_id)

        if not grn:
            return pos_response(
                status="error", 
                code=POSErrorCode.LPO_NOT_FOUND, 
                message=f"GRN {reference_id} not found. Please check the GRN number and try again.",
                lpo_no=None,
                grn_no=None
            )
        
        # Fetch related Purchase Order (PO) information, if available
        po_data = None
        if hasattr(grn, 'purchase_order') and grn.purchase_order:
            # If the 'purchase_order' attribute exists and is populated
            po = frappe.get_doc("Purchase Order", grn.purchase_order)
            po_data = {
                "po_no": po.name,
                "supplier": po.supplier,
                "order_date": po.transaction_date,
                "total_amount": po.grand_total
            }
        else:
            # Handle case when no purchase order is linked to the GRN
            po_data = {"message": "No purchase order linked to this GRN"}

        grn_details = {
            "grn_no": grn.name,
            "posting_date": grn.posting_date,
            "status": grn.status,
            "items": []
        }

        # Adding items details to the response
        for item in grn.items:
            grn_details["items"].append({
                "item_code": item.item_code,
                "item_name": item.item_name,
                "qty_received": item.qty,
                "rate": item.rate,
                "warehouse": item.warehouse
            })

        # Return GRN details along with related PO data (if available)
        return pos_response(
            status="success",
            message="GRN details fetched successfully.",
            data={"grn_details": grn_details, "purchase_order": po_data},
            lpo_no=None,  # Optional: If LPO, include it here
            grn_no=grn_no  
        )

    except Exception as e:
        # Log the exception message for debugging
        exception_message = str(e)

        # Return the error response with exception message included
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message=f"Something went wrong while processing your request. Error: {exception_message}. Please try again later. If the issue persists, contact support.",
            data={},
            grn_no=grn_no
        )




def normalize_grn_filters(payload: dict) -> dict:
    """
    Normalize API payload filters into ERPNext-safe filters
    """
    filters = {}

    docstatus = payload.get("docstatus")

    if docstatus is None:
        filters["docstatus"] = ["!=", 2]

    elif docstatus in (0, 1):
        filters["docstatus"] = docstatus

    elif docstatus == "all":
        pass  

    else:
        filters["docstatus"] = ["!=", 2]

    # Supplier
    if payload.get("supplier"):
        filters["supplier"] = payload["supplier"]

    if payload.get("status"):
        filters["status"] = payload["status"]

    # Date range
    from_date = payload.get("from_date")
    to_date = payload.get("to_date")

    if from_date and to_date:
        filters["posting_date"] = [
            "between",
            [getdate(from_date), getdate(to_date)]
        ]

    return filters


def normalize_grn_filters(payload: dict) -> dict:
    print("🔹 [STEP 1] normalize_grn_filters called")
    print("Payload received:", payload)

    frappe.logger().info(f"[GRN] Payload received: {payload}")

    filters = {}

    # ---------------------------
    # docstatus normalization
    # ---------------------------
    raw_docstatus = payload.get("docstatus")
    print("raw_docstatus:", raw_docstatus, type(raw_docstatus))

    if raw_docstatus is None or raw_docstatus == "":
        filters["docstatus"] = ["!=", 2]
        print("docstatus defaulted to != 2")

    elif str(raw_docstatus).lower() == "all":
        print("docstatus = all (no filter applied)")

    else:
        try:
            docstatus = int(raw_docstatus)
            if docstatus in (0, 1):
                filters["docstatus"] = docstatus
                print(f"docstatus set to {docstatus}")
            else:
                filters["docstatus"] = ["!=", 2]
                print("docstatus invalid, fallback to != 2")
        except Exception as e:
            print("docstatus conversion failed:", str(e))
            filters["docstatus"] = ["!=", 2]

    # ---------------------------
    # Supplier
    # ---------------------------
    if payload.get("supplier"):
        filters["supplier"] = payload["supplier"]
        print("supplier filter:", payload["supplier"])

    if payload.get("status"):
        filters["status"] = payload["status"]
        print("status filter:", payload["status"])

    # Date range
    from_date = payload.get("from_date")
    to_date = payload.get("to_date")

    print("from_date:", from_date, "to_date:", to_date)

    if from_date and to_date:
        try:
            filters["posting_date"] = [
                "between",
                [getdate(from_date), getdate(to_date)]
            ]
            print("posting_date filter applied:", filters["posting_date"])
        except Exception as e:
            print("Date parsing failed:", str(e))

    frappe.logger().info(f"[GRN] Normalized filters: {filters}")

    return filters


@frappe.whitelist(allow_guest=False)
def get_grn_list(
    page: int = 1,
    page_size: int = 20,
    supplier: str = None,
    purchase_order: str = None,
    from_date: str = None,
    to_date: str = None,
    docstatus=None,
    status: str = None
):

    try:
        page = int(page) if page else 1
        page_size = min(int(page_size) if page_size else 20, 100)
        offset = (page - 1) * page_size
        
        payload = {
            "supplier": supplier,
            "purchase_order": purchase_order,
            "from_date": from_date,
            "to_date": to_date,
            "docstatus": docstatus,
            "status": status
        }
        
        filters = normalize_grn_filters(payload)

        print("Filters after normalization:", filters)

        # Permission check
        has_perm = frappe.has_permission("Purchase Receipt", "read")
        print("Has permission:", has_perm)

        if not has_perm:
            print("Permission denied")
            return {
                "status": "error",
                "message": "No permission to access Purchase Receipts"
            }

        receipts = frappe.get_all(
            "Purchase Receipt",
            filters=filters,
            fields=[
                "name",
                "supplier",
                "posting_date",
                "grand_total",
                "status",
                "docstatus"
            ],
            order_by="posting_date desc",
            limit_start=offset,
            limit_page_length=page_size
        )

        if purchase_order:

            receipts = [
                r for r in receipts
                if frappe.db.exists(
                    "Purchase Receipt Item",
                    {
                        "parent": r["name"],
                        "purchase_order": purchase_order
                    }
                )
            ]

            print("Receipts after PO filter:", len(receipts))

        total = frappe.db.count("Purchase Receipt", filters)

        response = {
            "status": "success",
            "message": "Purchase Receipts fetched",
            "data": receipts,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": total
            }
        }

        print("RESPONSE:", response)
        return response

    except Exception as e:
        print(str(e))
        frappe.log_error(frappe.get_traceback(), "get_grn_list ERROR")

        return {
            "status": "error",
            "message": str(e)
        }





@frappe.whitelist()
def create_purchase_invoice(reference_type, reference_name):
    """
    Creates a Purchase Invoice from a Purchase Order or Purchase Receipt.
    :param reference_type: 'Purchase Order' or 'Purchase Receipt'
    :param reference_name: The name of the Purchase Order or Purchase Receipt
    :return: A dict containing the status and the name of the created Purchase Invoice
    """
    try:
        if reference_type not in ['Purchase Order', 'Purchase Receipt']:
            return {"status": "error", "message": "Invalid reference type. Must be 'Purchase Order' or 'Purchase Receipt'"}

        # Fetch the document based on reference type
        doc = None
        if reference_type == 'Purchase Order':
            doc = frappe.get_doc('Purchase Order', reference_name)
        elif reference_type == 'Purchase Receipt':
            doc = frappe.get_doc('Purchase Receipt', reference_name)

        if not doc:
            return {"status": "error", "message": f"{reference_type} {reference_name} not found."}

        # Handle the status of Purchase Order
        if reference_type == 'Purchase Order':
            if doc.status != 'To Receive and Bill':
                return {"status": "error", "message": f"Purchase Order {reference_name} is not ready to be invoiced. Current status: {doc.status}."}

        # Handle the status of Purchase Receipt
        if reference_type == 'Purchase Receipt':
            # GRN status should be "To Bill" (per_billed == 0) or "Partly Billed" (0 < per_billed < 100)
            # This is the correct status after GRN is submitted and ready for invoicing
            if doc.status not in ['To Bill', 'Partly Billed']:
                return {"status": "error", "message": f"Purchase Receipt {reference_name} is not ready to be invoiced. Current status: {doc.status}. Expected status: 'To Bill' or 'Partly Billed'."}

        # Prepare the Purchase Invoice
        invoice = frappe.new_doc('Purchase Invoice')
        invoice.supplier = doc.supplier
        invoice.company = doc.company
        invoice.posting_date = frappe.utils.nowdate()
        invoice.due_date = frappe.utils.add_days(frappe.utils.nowdate(), 30)  # Default 30 days due date

        # Add items to the Purchase Invoice based on the PO or PR
        for item in doc.items:
            # For Purchase Receipt, use qty (received quantity) instead of received_qty
            # received_qty is a Purchase Order field, not Purchase Receipt
            item_qty = item.received_qty if reference_type == 'Purchase Order' else item.qty
            
            # Ensure that the item data is valid and non-zero
            if item_qty <= 0:
                continue  # Skip items with no received quantity
            
            invoice_item = {
                'item_code': item.item_code,
                'item_name': item.item_name,
                'qty': item_qty,
                'rate': item.rate,
                'amount': item.amount,
            }
            
            # Link to Purchase Receipt (GRN) if creating from GRN
            if reference_type == 'Purchase Receipt':
                invoice_item['purchase_receipt'] = doc.name
                # pr_detail is the ERPNext field that links to Purchase Receipt Item
                invoice_item['pr_detail'] = item.name
            
            invoice.append('items', invoice_item)

        if not invoice.items:
            return {"status": "error", "message": f"No valid items to invoice for {reference_type} {reference_name}."}

        # Save and submit the Purchase Invoice
        invoice.insert(ignore_permissions=True)
        invoice.submit()

        return {
            "status": "success",
            "message": f"Purchase Invoice {invoice.name} has been created successfully.",
            "invoice_name": invoice.name
        }

    except frappe.exceptions.DoesNotExistError as e:
        # Specific error if the document is not found
        return {"status": "error", "message": f"{reference_type} {reference_name} does not exist."}

    except frappe.exceptions.ValidationError as e:
        # Handle validation issues more specifically
        return {"status": "error", "message": f"Validation error: {str(e)}"}

    except Exception as e:
        # General catch-all for unexpected errors
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}"
        }


@frappe.whitelist(methods=["POST"])
def create_purchase_invoice_from_grn(
    grn_no, 
    do_not_submit=False,
    bill_no=None,
    bill_date=None,
    supplier_invoice_file=None,
    supplier_invoice_filename=None
):
    """
    Create Purchase Invoice from GRN (Purchase Receipt) using ERPNext's built-in function.
    
    This is the RECOMMENDED way to create Purchase Invoice from GRN as it:
    - Handles pending quantities correctly (excludes already invoiced items)
    - Handles returned quantities
    - Properly links items to Purchase Receipt
    - Handles taxes and charges
    - Calculates totals correctly
    - Supports supplier invoice details and file attachments
    
    Args:
        grn_no (str): Purchase Receipt (GRN) name/number
        do_not_submit (bool): If True, invoice will be saved as draft only (default: False)
        bill_no (str, optional): Supplier invoice number
        bill_date (str, optional): Supplier invoice date (YYYY-MM-DD format)
        supplier_invoice_file (str, optional): Base64 encoded file content or file URL for supplier invoice attachment
        supplier_invoice_filename (str, optional): Filename for the supplier invoice attachment (required if supplier_invoice_file is provided)
    
    Returns:
        dict: Response with status and invoice details
    
    Example:
        POST /api/method/techsavanna_pos.api.purchase.create_purchase_invoice_from_grn
        {
            "grn_no": "MAT-PRE-2026-00001",
            "do_not_submit": false,
            "bill_no": "SUP-INV-2026-001",
            "bill_date": "2026-01-15",
            "supplier_invoice_file": "base64_encoded_content_or_url",
            "supplier_invoice_filename": "supplier_invoice.pdf"
        }
    """
    try:
        # Validate GRN exists
        if not grn_no:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="GRN number is required",
                grn_no=None
            )
        
        if not frappe.db.exists("Purchase Receipt", grn_no):
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_NOT_FOUND,
                message=f"GRN {grn_no} not found",
                grn_no=grn_no
            )
        
        # Get GRN document
        grn = frappe.get_doc("Purchase Receipt", grn_no)
        
        # Validate GRN is submitted
        if grn.docstatus != 1:
            return pos_response(
                status="error",
                code=POSErrorCode.PO_NOT_SUBMITTED,
                message=f"GRN {grn_no} must be submitted before creating Purchase Invoice",
                grn_no=grn_no
            )
        
        # Validate GRN status is ready for invoicing
        if grn.status not in ['To Bill', 'Partly Billed']:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message=f"GRN {grn_no} is not ready for invoicing. Current status: {grn.status}. Expected: 'To Bill' or 'Partly Billed'",
                grn_no=grn_no,
                data={"current_status": grn.status}
            )
        
        # Use ERPNext's built-in function to create Purchase Invoice
        # This handles all the complex logic like pending quantities, returned quantities, etc.
        frappe.db.begin()
        
        invoice = make_pi_from_pr(grn_no)
        
        # Set posting date if needed (defaults to today)
        if not invoice.posting_date:
            invoice.posting_date = frappe.utils.nowdate()
        
        # Set supplier invoice details if provided
        if bill_no:
            invoice.bill_no = bill_no
        
        if bill_date:
            try:
                invoice.bill_date = getdate(bill_date)
            except Exception as e:
                frappe.logger().error(f"Invalid bill_date format: {bill_date}. Error: {str(e)}")
                # Continue without bill_date if invalid format
        
        # Save the invoice
        invoice.insert(ignore_permissions=True)
        
        # Attach supplier invoice file if provided
        file_attached = False
        if supplier_invoice_file and supplier_invoice_filename:
            try:
                # Check if it's a URL or base64 content
                if supplier_invoice_file.startswith(('http://', 'https://', '/files/', '/private/files/')):
                    # It's a URL - create file reference
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": supplier_invoice_filename,
                        "file_url": supplier_invoice_file,
                        "attached_to_doctype": "Purchase Invoice",
                        "attached_to_name": invoice.name,
                        "folder": "Home/Attachments",
                        "is_private": 0
                    })
                    file_doc.insert(ignore_permissions=True)
                    file_attached = True
                else:
                    # Assume it's base64 encoded content
                    # Create File document directly
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": supplier_invoice_filename,
                        "attached_to_doctype": "Purchase Invoice",
                        "attached_to_name": invoice.name,
                        "folder": "Home/Attachments",
                        "is_private": 0,
                        "content": supplier_invoice_file,
                        "decode": True  # Decode base64
                    })
                    file_doc.insert(ignore_permissions=True)
                    file_attached = True
            except Exception as e:
                frappe.logger().error(f"Error attaching supplier invoice file: {str(e)}")
                # Continue even if file attachment fails - don't fail the whole operation
                # The invoice is still created successfully
        
        # Submit if requested
        if not do_not_submit:
            invoice.submit()
            invoice.reload()
        
        frappe.db.commit()
        
        response_data = {
            "invoice_name": invoice.name,
            "docstatus": invoice.docstatus,
            "submitted": bool(invoice.docstatus == 1),
            "grand_total": invoice.grand_total,
            "items_count": len(invoice.items),
            "supplier": invoice.supplier,
            "company": invoice.company,
            "bill_no": invoice.bill_no,
            "bill_date": str(invoice.bill_date) if invoice.bill_date else None,
            "supplier_invoice_attached": file_attached
        }
        
        return pos_response(
            status="success",
            code="PI_CREATED",
            message="Purchase Invoice created successfully from GRN" + (" with supplier invoice attached" if file_attached else ""),
            grn_no=grn_no,
            data=response_data
        )
    
    except frappe.exceptions.ValidationError as e:
        frappe.db.rollback()
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_REQUEST,
            message=f"Validation error: {str(e)}",
            grn_no=grn_no
        )
    
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Create Purchase Invoice from GRN Failed",
            message=frappe.get_traceback()
        )
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message=f"An unexpected error occurred: {str(e)}",
            grn_no=grn_no
        )




@frappe.whitelist(allow_guest=False)
def list_purchase_invoices(
    page: int = 1,
    page_size: int = 20,
    supplier: str = None,
    company: str = None,
    purchase_order: str = None,
    purchase_receipt: str = None,
    from_date: str = None,
    to_date: str = None,
    docstatus=None,
    status: str = None,
    bill_no: str = None
):
    """
    List Purchase Invoices with optional filters and pagination.
    
    Args:
        page (int): Page number (default: 1)
        page_size (int): Number of records per page (default: 20, max: 100)
        supplier (str, optional): Filter by supplier
        company (str, optional): Filter by company
        purchase_order (str, optional): Filter by Purchase Order
        purchase_receipt (str, optional): Filter by Purchase Receipt (GRN)
        from_date (str, optional): Filter from date (YYYY-MM-DD)
        to_date (str, optional): Filter to date (YYYY-MM-DD)
        docstatus (int, optional): Filter by document status (0=Draft, 1=Submitted, 2=Cancelled, "all"=All)
        status (str, optional): Filter by invoice status (Draft, Unpaid, Paid, Overdue, etc.)
        bill_no (str, optional): Filter by supplier invoice number
    
    Returns:
        dict: List of purchase invoices with pagination metadata
    
    Example:
        GET /api/method/techsavanna_pos.api.purchase.list_purchase_invoices?page=1&page_size=20&supplier=Supplier%20ABC&status=Unpaid
    """
    try:
        page = int(page) if page else 1
        page_size = min(int(page_size) if page_size else 20, 100)
        offset = (page - 1) * page_size
        
        # Build filters
        filters = {}
        
        # Document status filter
        if docstatus is None or docstatus == "":
            filters["docstatus"] = ["!=", 2]  # Exclude cancelled by default
        elif str(docstatus).lower() == "all":
            pass  # No filter
        else:
            try:
                docstatus_int = int(docstatus)
                if docstatus_int in (0, 1, 2):
                    filters["docstatus"] = docstatus_int
                else:
                    filters["docstatus"] = ["!=", 2]
            except (ValueError, TypeError):
                filters["docstatus"] = ["!=", 2]
        
        # Supplier filter
        if supplier:
            filters["supplier"] = supplier
        
        # Company filter
        if company:
            filters["company"] = company
        
        # Status filter
        if status:
            filters["status"] = status
        
        # Bill number filter
        if bill_no:
            filters["bill_no"] = ["like", f"%{bill_no}%"]
        
        # Date range filter
        if from_date and to_date:
            filters["posting_date"] = ["between", [getdate(from_date), getdate(to_date)]]
        elif from_date:
            filters["posting_date"] = [">=", getdate(from_date)]
        elif to_date:
            filters["posting_date"] = ["<=", getdate(to_date)]
        
        # Permission check
        if not frappe.has_permission("Purchase Invoice", "read"):
            return {
                "status": "error",
                "message": "No permission to access Purchase Invoices"
            }
        
        # Fetch purchase invoices
        invoices = frappe.get_all(
            "Purchase Invoice",
            filters=filters,
            fields=[
                "name",
                "supplier",
                "supplier_name",
                "company",
                "posting_date",
                "due_date",
                "bill_no",
                "bill_date",
                "grand_total",
                "outstanding_amount",
                "status",
                "docstatus",
                "currency"
            ],
            order_by="posting_date desc, name desc",
            limit_start=offset,
            limit_page_length=page_size
        )
        
        # Filter by Purchase Order if provided
        if purchase_order:
            filtered_invoices = []
            for inv in invoices:
                # Check if invoice has items linked to this PO
                if frappe.db.exists(
                    "Purchase Invoice Item",
                    {
                        "parent": inv["name"],
                        "purchase_order": purchase_order
                    }
                ):
                    filtered_invoices.append(inv)
            invoices = filtered_invoices
        
        # Filter by Purchase Receipt (GRN) if provided
        if purchase_receipt:
            filtered_invoices = []
            for inv in invoices:
                # Check if invoice has items linked to this GRN
                if frappe.db.exists(
                    "Purchase Invoice Item",
                    {
                        "parent": inv["name"],
                        "purchase_receipt": purchase_receipt
                    }
                ):
                    filtered_invoices.append(inv)
            invoices = filtered_invoices
        
        # Format dates for JSON serialization
        for inv in invoices:
            if inv.get("posting_date"):
                inv["posting_date"] = str(inv["posting_date"])
            if inv.get("due_date"):
                inv["due_date"] = str(inv["due_date"])
            if inv.get("bill_date"):
                inv["bill_date"] = str(inv["bill_date"])
        
        # Get total count
        total = frappe.db.count("Purchase Invoice", filters)
        
        return {
            "status": "success",
            "message": "Purchase Invoices fetched successfully",
            "data": invoices,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0
            }
        }
    
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "List Purchase Invoices Error")
        return {
            "status": "error",
            "message": f"Error fetching purchase invoices: {str(e)}"
        }


@frappe.whitelist(methods=["GET"])
def get_purchase_invoice_details(invoice_no):
    """
    Get detailed information for a specific Purchase Invoice.
    
    Args:
        invoice_no (str): Purchase Invoice name/number
    
    Returns:
        dict: Detailed Purchase Invoice information including items, taxes, linked documents, and attachments
    
    Example:
        GET /api/method/techsavanna_pos.api.purchase.get_purchase_invoice_details?invoice_no=ACC-PINV-2026-00001
    """
    try:
        if not invoice_no:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="Purchase Invoice number is required",
                data={}
            )
        
        # Check if invoice exists
        if not frappe.db.exists("Purchase Invoice", invoice_no):
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_NOT_FOUND,
                message=f"Purchase Invoice {invoice_no} not found",
                data={}
            )
        
        # Get Purchase Invoice document
        invoice = frappe.get_doc("Purchase Invoice", invoice_no)
        
        # Check permissions
        if not invoice.has_permission("read"):
            return pos_response(
                status="error",
                code=POSErrorCode.PERMISSION_DENIED,
                message="No permission to access this Purchase Invoice",
                data={}
            )
        
        # Basic invoice information
        invoice_data = {
            "invoice_no": invoice.name,
            "supplier": invoice.supplier,
            "supplier_name": invoice.supplier_name,
            "company": invoice.company,
            "posting_date": str(invoice.posting_date) if invoice.posting_date else None,
            "posting_time": str(invoice.posting_time) if invoice.posting_time else None,
            "due_date": str(invoice.due_date) if invoice.due_date else None,
            "bill_no": invoice.bill_no,
            "bill_date": str(invoice.bill_date) if invoice.bill_date else None,
            "status": invoice.status,
            "docstatus": invoice.docstatus,
            "is_return": invoice.is_return,
            "is_paid": invoice.is_paid,
            "currency": invoice.currency,
            "conversion_rate": invoice.conversion_rate,
            "grand_total": flt(invoice.grand_total, 2),
            "net_total": flt(invoice.net_total, 2),
            "total_taxes_and_charges": flt(invoice.total_taxes_and_charges, 2),
            "outstanding_amount": flt(invoice.outstanding_amount, 2),
            "paid_amount": flt(invoice.paid_amount, 2),
            "write_off_amount": flt(invoice.write_off_amount, 2),
            "items": [],
            "taxes": [],
            "purchase_orders": [],
            "purchase_receipts": [],
            "payment_entries": [],
            "attachments": []
        }
        
        # Include items
        for item in invoice.items:
            item_data = {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "description": item.description,
                "qty": flt(item.qty, 2),
                "rate": flt(item.rate, 2),
                "amount": flt(item.amount, 2),
                "warehouse": item.warehouse,
                "uom": item.uom,
                "stock_qty": flt(item.stock_qty, 2) if item.stock_qty else None,
            }
            
            # Link to Purchase Receipt (GRN) if available
            if item.purchase_receipt:
                item_data["purchase_receipt"] = item.purchase_receipt
                # pr_detail is the field that links to Purchase Receipt Item
                if hasattr(item, 'pr_detail') and item.pr_detail:
                    item_data["purchase_receipt_item"] = item.pr_detail
            
            # Link to Purchase Order if available
            if item.purchase_order:
                item_data["purchase_order"] = item.purchase_order
                item_data["purchase_order_item"] = item.po_detail
            
            invoice_data["items"].append(item_data)
        
        # Include taxes
        if invoice.taxes:
            for tax in invoice.taxes:
                invoice_data["taxes"].append({
                    "charge_type": tax.charge_type,
                    "account_head": tax.account_head,
                    "description": tax.description,
                    "rate": flt(tax.rate, 2) if tax.rate else None,
                    "tax_amount": flt(tax.tax_amount, 2),
                    "total": flt(tax.total, 2)
                })
        
        # Get linked Purchase Orders
        purchase_orders = frappe.get_all(
            "Purchase Invoice Item",
            filters={"parent": invoice_no, "purchase_order": ["!=", ""]},
            fields=["purchase_order"],
            distinct=True
        )
        invoice_data["purchase_orders"] = [po.purchase_order for po in purchase_orders if po.purchase_order]
        
        # Get linked Purchase Receipts (GRNs)
        purchase_receipts = frappe.get_all(
            "Purchase Invoice Item",
            filters={"parent": invoice_no, "purchase_receipt": ["!=", ""]},
            fields=["purchase_receipt"],
            distinct=True
        )
        invoice_data["purchase_receipts"] = [pr.purchase_receipt for pr in purchase_receipts if pr.purchase_receipt]
        
        # Get linked Payment Entries
        payment_entry_refs = frappe.get_all(
            "Payment Entry Reference",
            filters={
                "reference_doctype": "Purchase Invoice",
                "reference_name": invoice_no
            },
            fields=["parent", "allocated_amount"],
            order_by="creation desc"
        )
        
        if payment_entry_refs:
            payment_entry_names = list(set([ref.parent for ref in payment_entry_refs]))
            payment_entries = frappe.get_all(
                "Payment Entry",
                filters={"name": ["in", payment_entry_names]},
                fields=[
                    "name", "payment_type", "posting_date", "mode_of_payment",
                    "paid_amount", "received_amount", "reference_no", "reference_date",
                    "remarks", "docstatus", "status"
                ],
                order_by="posting_date desc"
            )
            
            for pe in payment_entries:
                # Find the allocated amount for this invoice
                allocated_amount = next(
                    (ref.allocated_amount for ref in payment_entry_refs if ref.parent == pe.name),
                    None
                )
                
                invoice_data["payment_entries"].append({
                    "name": pe.name,
                    "payment_type": pe.payment_type,
                    "posting_date": str(pe.posting_date) if pe.posting_date else None,
                    "mode_of_payment": pe.mode_of_payment,
                    "paid_amount": flt(pe.paid_amount, 2),
                    "received_amount": flt(pe.received_amount, 2),
                    "allocated_amount": flt(allocated_amount, 2) if allocated_amount else None,
                    "reference_no": pe.reference_no,
                    "reference_date": str(pe.reference_date) if pe.reference_date else None,
                    "remarks": pe.remarks,
                    "docstatus": pe.docstatus,
                    "status": pe.status,
                    "submitted": pe.docstatus == 1
                })
        
        # Get attached files
        attachments = frappe.get_all(
            "File",
            filters={
                "attached_to_doctype": "Purchase Invoice",
                "attached_to_name": invoice_no
            },
            fields=["name", "file_name", "file_url", "is_private", "file_size"]
        )
        invoice_data["attachments"] = [
            {
                "name": att.name,
                "file_name": att.file_name,
                "file_url": att.file_url,
                "is_private": att.is_private,
                "file_size": att.file_size
            }
            for att in attachments
        ]
        
        return pos_response(
            status="success",
            message="Purchase Invoice details fetched successfully",
            data=invoice_data
        )
    
    except frappe.exceptions.DoesNotExistError:
        return pos_response(
            status="error",
            code=POSErrorCode.LPO_NOT_FOUND,
            message=f"Purchase Invoice {invoice_no} does not exist",
            data={}
        )
    
    except frappe.exceptions.PermissionError:
        return pos_response(
            status="error",
            code=POSErrorCode.PERMISSION_DENIED,
            message="No permission to access this Purchase Invoice",
            data={}
        )
    
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Get Purchase Invoice Details Error")
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message=f"Error fetching purchase invoice details: {str(e)}",
            data={}
        )


@frappe.whitelist(methods=["POST"])
def update_purchase_invoice(
    invoice_no: str,
    bill_no: str = None,
    bill_date: str = None,
    posting_date: str = None,
    supplier_invoice_file: str = None,
    supplier_invoice_filename: str = None,
    items: list = None,
    taxes: list = None
):
    """
    Update an existing draft Purchase Invoice.
    
    Only draft invoices (docstatus = 0) can be updated. Submitted invoices must be cancelled first.
    
    Args:
        invoice_no (str): Purchase Invoice name/number to update
        bill_no (str, optional): Updated supplier invoice number
        bill_date (str, optional): Updated supplier invoice date (YYYY-MM-DD format)
        posting_date (str, optional): Updated posting date (YYYY-MM-DD format)
        supplier_invoice_file (str, optional): Base64 encoded file content or file URL for supplier invoice attachment
        supplier_invoice_filename (str, optional): Filename for the supplier invoice attachment
        items (list, optional): Updated list of items (replaces all existing items if provided)
        taxes (list, optional): Updated list of taxes (replaces all existing taxes if provided)
    
    Returns:
        dict: Response with status and updated invoice details
    
    Example:
        POST /api/method/techsavanna_pos.api.purchase.update_purchase_invoice
        {
            "invoice_no": "ACC-PINV-2026-00001",
            "bill_no": "SUP-INV-2026-002",
            "bill_date": "2026-01-16",
            "supplier_invoice_file": "base64_encoded_content",
            "supplier_invoice_filename": "updated_invoice.pdf"
        }
    """
    try:
        # Validate invoice number
        if not invoice_no:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="Purchase Invoice number is required",
                data={}
            )
        
        # Check if invoice exists
        if not frappe.db.exists("Purchase Invoice", invoice_no):
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_NOT_FOUND,
                message=f"Purchase Invoice {invoice_no} not found",
                data={}
            )
        
        # Get Purchase Invoice document
        invoice = frappe.get_doc("Purchase Invoice", invoice_no)
        
        # Check permissions
        if not invoice.has_permission("write"):
            return pos_response(
                status="error",
                code=POSErrorCode.PERMISSION_DENIED,
                message="No permission to update this Purchase Invoice",
                data={}
            )
        
        # Validate invoice is in draft state (can only update drafts)
        if invoice.docstatus != 0:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message=f"Cannot update Purchase Invoice {invoice_no}. Only draft invoices (docstatus = 0) can be updated. Current status: {'Submitted' if invoice.docstatus == 1 else 'Cancelled'}",
                data={
                    "current_docstatus": invoice.docstatus,
                    "current_status": invoice.status
                }
            )
        
        frappe.db.begin()
        
        # Update basic fields
        if bill_no is not None:
            invoice.bill_no = bill_no
        
        if bill_date is not None:
            try:
                invoice.bill_date = getdate(bill_date) if bill_date else None
            except Exception as e:
                frappe.logger().error(f"Invalid bill_date format: {bill_date}. Error: {str(e)}")
                # Continue without bill_date if invalid format
        
        if posting_date is not None:
            try:
                invoice.posting_date = getdate(posting_date) if posting_date else None
            except Exception as e:
                frappe.logger().error(f"Invalid posting_date format: {posting_date}. Error: {str(e)}")
                # Continue without posting_date if invalid format
        
        # Update items if provided
        if items is not None and isinstance(items, list):
            # Clear existing items
            invoice.set("items", [])
            
            # Add new items
            for item_data in items:
                item_row = {
                    "item_code": item_data.get("item_code"),
                    "qty": flt(item_data.get("qty", 1)),
                    "rate": flt(item_data.get("rate", 0)),
                    "warehouse": item_data.get("warehouse"),
                }
                
                # Preserve Purchase Receipt (GRN) linking if updating existing items
                if item_data.get("purchase_receipt"):
                    item_row["purchase_receipt"] = item_data.get("purchase_receipt")
                # Map purchase_receipt_item from API to pr_detail (ERPNext field name)
                if item_data.get("purchase_receipt_item"):
                    item_row["pr_detail"] = item_data.get("purchase_receipt_item")
                if item_data.get("purchase_order"):
                    item_row["purchase_order"] = item_data.get("purchase_order")
                if item_data.get("purchase_order_item"):
                    item_row["purchase_order_item"] = item_data.get("purchase_order_item")
                
                invoice.append("items", item_row)
        
        # Update taxes if provided
        if taxes is not None and isinstance(taxes, list):
            # Clear existing taxes
            invoice.set("taxes", [])
            
            # Add new taxes
            for tax_data in taxes:
                invoice.append("taxes", tax_data)
        
        # Save the invoice
        invoice.save(ignore_permissions=True)
        
        # Handle supplier invoice file attachment if provided
        file_attached = False
        if supplier_invoice_file and supplier_invoice_filename:
            try:
                # Remove existing attachments with the same filename (optional - you may want to keep all)
                # For now, we'll just add the new one
                
                # Check if it's a URL or base64 content
                if supplier_invoice_file.startswith(('http://', 'https://', '/files/', '/private/files/')):
                    # It's a URL - create file reference
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": supplier_invoice_filename,
                        "file_url": supplier_invoice_file,
                        "attached_to_doctype": "Purchase Invoice",
                        "attached_to_name": invoice.name,
                        "folder": "Home/Attachments",
                        "is_private": 0
                    })
                    file_doc.insert(ignore_permissions=True)
                    file_attached = True
                else:
                    # Assume it's base64 encoded content
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": supplier_invoice_filename,
                        "attached_to_doctype": "Purchase Invoice",
                        "attached_to_name": invoice.name,
                        "folder": "Home/Attachments",
                        "is_private": 0,
                        "content": supplier_invoice_file,
                        "decode": True  # Decode base64
                    })
                    file_doc.insert(ignore_permissions=True)
                    file_attached = True
            except Exception as e:
                frappe.logger().error(f"Error attaching supplier invoice file: {str(e)}")
                # Continue even if file attachment fails
        
        # Reload to get updated values
        invoice.reload()
        
        frappe.db.commit()
        
        response_data = {
            "invoice_name": invoice.name,
            "docstatus": invoice.docstatus,
            "submitted": bool(invoice.docstatus == 1),
            "grand_total": flt(invoice.grand_total, 2),
            "items_count": len(invoice.items),
            "supplier": invoice.supplier,
            "company": invoice.company,
            "bill_no": invoice.bill_no,
            "bill_date": str(invoice.bill_date) if invoice.bill_date else None,
            "posting_date": str(invoice.posting_date) if invoice.posting_date else None,
            "supplier_invoice_attached": file_attached
        }
        
        return pos_response(
            status="success",
            code="PI_UPDATED",
            message="Purchase Invoice updated successfully" + (" with supplier invoice attached" if file_attached else ""),
            data=response_data
        )
    
    except frappe.exceptions.ValidationError as e:
        frappe.db.rollback()
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_REQUEST,
            message=f"Validation error: {str(e)}",
            data={}
        )
    
    except frappe.exceptions.DoesNotExistError:
        frappe.db.rollback()
        return pos_response(
            status="error",
            code=POSErrorCode.LPO_NOT_FOUND,
            message=f"Purchase Invoice {invoice_no} does not exist",
            data={}
        )
    
    except frappe.exceptions.PermissionError:
        frappe.db.rollback()
        return pos_response(
            status="error",
            code=POSErrorCode.PERMISSION_DENIED,
            message="No permission to update this Purchase Invoice",
            data={}
        )
    
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Update Purchase Invoice Failed",
            message=frappe.get_traceback()
        )
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message=f"An unexpected error occurred: {str(e)}",
            data={}
        )


@frappe.whitelist()
def create_delivery_note_from_po(purchase_order_name):
    """
    Creates a Delivery Note from a Purchase Order
    """
    try:
        # Fetch the Purchase Order document
        purchase_order = frappe.get_doc('Purchase Order', purchase_order_name)

        # Check if the Purchase Order status is "To Deliver"
        if purchase_order.status != "To Deliver":
            return {
                "status": "fail",
                "message": f"Cannot create Delivery Note. PO status must be 'To Deliver', but the current status is {purchase_order.status}."
            }
        
        # Create a new Delivery Note document
        delivery_note = frappe.new_doc('Delivery Note')
        delivery_note.supplier = purchase_order.supplier
        delivery_note.posting_date = frappe.utils.today()

        # Add items from the Purchase Order to the Delivery Note
        for item in purchase_order.items:
            delivery_note.append('items', {
                'item_code': item.item_code,
                'item_name': item.item_name,
                'qty': item.qty,  # You can also use item.received_qty to send already received quantities
                'warehouse': item.warehouse,
                'rate': item.rate,
                'uom': item.uom,
            })

        # Save and submit the Delivery Note
        delivery_note.insert()
        delivery_note.submit()

        return {
            "status": "success",
            "message": f"Delivery Note {delivery_note.name} has been created from Purchase Order {purchase_order_name}.",
            "data": {
                "delivery_note_name": delivery_note.name
            }
        }

    except frappe.DoesNotExistError:
        return {
            "status": "fail",
            "message": f"Purchase Order {purchase_order_name} not found."
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"An error occurred: {str(e)}"
        }


@frappe.whitelist()
def pay_purchase_invoice(
    invoice_no: str,
    paid_amount: float = None,
    mode_of_payment: str = None,
    bank_account: str = None,
    posting_date: str = None,
    reference_no: str = None,
    reference_date: str = None,
    remarks: str = None,
    submit: bool = True,
) -> dict:
    """
    Create a Payment Entry against a Purchase Invoice to record payment made to supplier.
    This moves the invoice from "Partly Paid" or "Unpaid" to "Paid" status when fully paid.
    
    Args:
        invoice_no: Purchase Invoice name/ID (e.g., "PI-00001")
        paid_amount: Amount to pay (optional, defaults to outstanding_amount if not provided)
        mode_of_payment: Mode of payment name (e.g., "Cash", "Bank Transfer", "Cheque")
        bank_account: Bank account name (optional, required for bank payments)
        posting_date: Posting date (optional, defaults to today)
        reference_no: Payment reference number (optional, e.g., cheque number, transaction ID)
        reference_date: Reference date (optional)
        remarks: Additional remarks/notes (optional)
        submit: Whether to submit the payment entry (default: True)
    
    Returns:
        dict: Payment Entry details and updated invoice status
    """
    try:
        # Validate purchase invoice exists
        if not frappe.db.exists("Purchase Invoice", invoice_no):
            return pos_response(
                status="error",
                code=POSErrorCode.LPO_NOT_FOUND,
                message=f"Purchase Invoice {invoice_no} not found",
                data={}
            )
        
        # Get purchase invoice
        pi = frappe.get_doc("Purchase Invoice", invoice_no)
        
        # Validate invoice is submitted
        if pi.docstatus != 1:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message=f"Purchase Invoice {invoice_no} must be submitted before making payment",
                data={}
            )
        
        # Check if already fully paid
        if flt(pi.outstanding_amount) <= 0:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message=f"Purchase Invoice {invoice_no} is already fully paid",
                data={}
            )
        
        # Use outstanding amount if paid_amount not provided
        if paid_amount is None:
            paid_amount = flt(pi.outstanding_amount)
        else:
            paid_amount = flt(paid_amount)
        
        # Validate paid amount
        if paid_amount <= 0:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message="Paid amount must be greater than zero",
                data={}
            )
        
        if paid_amount > flt(pi.outstanding_amount):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message=f"Paid amount ({paid_amount}) cannot be greater than outstanding amount ({pi.outstanding_amount})",
                data={}
            )
        
        # Get default mode of payment if not provided
        if not mode_of_payment:
            # Try to get from company defaults
            mode_of_payment = frappe.db.get_value("Company", pi.company, "default_mode_of_payment")
            if not mode_of_payment:
                # Get first available mode of payment
                mop = frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name", order_by="name")
                if not mop:
                    return pos_response(
                        status="error",
                        code=POSErrorCode.INVALID_REQUEST,
                        message="No mode of payment found. Please configure at least one Mode of Payment.",
                        data={}
                    )
                mode_of_payment = mop
        
        # Validate mode of payment exists
        if not frappe.db.exists("Mode of Payment", mode_of_payment):
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_REQUEST,
                message=f"Mode of Payment {mode_of_payment} not found",
                data={}
            )
        
        # Import payment entry utilities
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        from frappe.utils import nowdate, getdate
        
        # Create payment entry using ERPNext utility
        # For Purchase Invoice, payment_type will be "Pay" automatically
        pe = get_payment_entry(
            dt="Purchase Invoice",
            dn=invoice_no,
            party_amount=paid_amount,
            bank_account=bank_account,
        )
        
        # Override mode of payment if provided
        if mode_of_payment:
            pe.mode_of_payment = mode_of_payment
        
        # Set posting date
        if posting_date:
            pe.posting_date = getdate(posting_date)
        else:
            pe.posting_date = getdate(nowdate())
        
        # Set reference details
        if reference_no:
            pe.reference_no = reference_no
        if reference_date:
            pe.reference_date = getdate(reference_date)
        if remarks:
            pe.remarks = remarks
        
        # Adjust allocated amount if partial payment
        if paid_amount < flt(pi.outstanding_amount):
            # Update the reference allocated amount
            if pe.references:
                pe.references[0].allocated_amount = paid_amount
                pe.references[0].outstanding_amount = flt(pi.outstanding_amount)
        
        # Save payment entry
        pe.insert(ignore_permissions=True)
        
        # Submit if requested
        if submit:
            pe.submit()
            frappe.db.commit()
        
        # Reload purchase invoice to get updated status
        pi.reload()
        
        # Prepare response data
        response_data = {
            "payment_entry": {
                "name": pe.name,
                "payment_type": pe.payment_type,
                "party": pe.party,
                "party_type": pe.party_type,
                "paid_amount": flt(pe.paid_amount),
                "received_amount": flt(pe.received_amount),
                "posting_date": str(pe.posting_date),
                "mode_of_payment": pe.mode_of_payment,
                "docstatus": pe.docstatus,
                "submitted": pe.docstatus == 1,
            },
            "purchase_invoice": {
                "name": pi.name,
                "outstanding_amount": flt(pi.outstanding_amount),
                "status": pi.status,
                "paid_amount": flt(pi.paid_amount) if hasattr(pi, "paid_amount") else None,
                "grand_total": flt(pi.grand_total),
            },
        }
        
        return pos_response(
            status="success",
            message="Payment Entry created successfully",
            data=response_data
        )
    
    except frappe.ValidationError as e:
        frappe.db.rollback()
        frappe.log_error(
            f"Validation error creating Payment Entry for Purchase Invoice {invoice_no}: {str(e)}",
            "Create Payment Entry Validation Error",
        )
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_REQUEST,
            message=f"Validation error: {str(e)}",
            data={}
        )
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            f"Error creating Payment Entry for Purchase Invoice {invoice_no}: {str(e)}",
            "Create Payment Entry Error",
        )
        return pos_response(
            status="error",
            code=POSErrorCode.UNKNOWN_ERROR,
            message=f"Error creating Payment Entry: {str(e)}",
            data={}
        )
