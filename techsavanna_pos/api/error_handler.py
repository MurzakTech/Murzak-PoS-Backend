import traceback
import frappe
import json
from frappe.exceptions import ValidationError  
from techsavanna_pos.api.constants import POSErrorCode
from techsavanna_pos.api.api_response import pos_response


def handle_pos_exception(e, *, lpo_no=None, grn_no=None, data=None, **kwargs):
    """
    Central POS error middleware for handling exceptions and converting them into user-friendly error messages.
    Handles different error types and ensures a consistent structure for responses across all endpoints.
    """
    # Log exception details for easier debugging
    frappe.logger().error(f"Exception caught in POS exception handler: {str(e)}")
    frappe.logger().error(f"Full traceback: {traceback.format_exc()}")

    # Catch JSONDecodeError when payload is malformed or missing required fields
    if isinstance(e, json.decoder.JSONDecodeError):
        error_message = str(e)

        frappe.logger().error(f"Malformed JSON error details: {error_message}")

        # Check for "Expecting value" error, indicating missing or invalid values (e.g., 'qty')
        if "Expecting value" in error_message:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_JSON,
                message="Missing or invalid value in the JSON payload. Ensure all fields (like 'qty') are properly filled.",
                lpo_no=lpo_no,
                grn_no=grn_no,
                data=data
            )

        # Generic JSON decoding error response
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_JSON,
            message="The JSON input is malformed or invalid.",
            lpo_no=lpo_no,
            grn_no=grn_no,
            data=data
        )

    # PermissionError: User doesn't have permission to perform the action
    if isinstance(e, PermissionError):
        frappe.logger().error(f"Permission Denied: {str(e)}")
        return pos_response(
            status="error",
            code=POSErrorCode.PERMISSION_DENIED,
            message="You are not allowed to perform this action.",
            lpo_no=lpo_no,
            grn_no=grn_no,
            data=data
        )

    # ValidationError: Handles business rules validation (e.g., warehouse, qty, etc.)
    if isinstance(e, ValidationError):
        msg = str(e)

        # Check for errors related to warehouse (e.g., invalid or missing warehouse)
        if "Accepted Warehouse" in msg:
            return pos_response(
                status="error",
                code=POSErrorCode.INVALID_WAREHOUSE,
                message="Invalid or missing warehouse.",
                lpo_no=lpo_no,
                grn_no=grn_no,
                data=data
            )

        # Check for errors related to quantity exceeding the ordered quantity
        if "exceeds ordered quantity" in msg:
            return pos_response(
                status="error",
                code=POSErrorCode.QTY_EXCEEDS_ORDERED,
                message="Received quantity exceeds ordered quantity.",
                lpo_no=lpo_no,
                grn_no=grn_no,
                data=data
            )

        # Handle general validation errors with the message from the exception
        return pos_response(
            status="error",
            code=POSErrorCode.INVALID_REQUEST,
            message=msg,
            lpo_no=lpo_no,
            grn_no=grn_no,
            data=data
        )

    # Fallback: If the error is not of type PermissionError or ValidationError, handle it as a generic system error
    frappe.logger().error(f"Unexpected Error: {str(e)}")

    # Check for 'status' attribute in stock entry before accessing it
    if isinstance(e, AttributeError) and "status" in str(e):
        return pos_response(
            status="error",
            code=POSErrorCode.MISSING_STATUS,
            message=f"Missing 'status' attribute in StockEntry {data.get('stock_entry_name')}",
            lpo_no=lpo_no,
            grn_no=grn_no,
            data=data
        )

    # Ensure we have a valid exception string to log
    traceback_info = traceback.format_exc()
    if not traceback_info:
        traceback_info = "No traceback available."

    frappe.log_error(
        title="Unhandled POS API Error",
        message=traceback_info
    )

    # Provide a more user-friendly error message with exception details
    user_friendly_message = (
        "Something went wrong while processing your request. Please try again later. "
        "If the issue persists, contact support with the following error details."
    )

    # Return a clear error message with the exception included
    return pos_response(
        status="error",
        code=POSErrorCode.UNKNOWN_ERROR,
        message=f"{user_friendly_message} Error: {str(e)}",
        lpo_no=lpo_no,
        grn_no=grn_no,
        data=data
    )
