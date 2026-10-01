"""
MPESA API Endpoints
REST API endpoints for MPESA payment operations
"""

import frappe
from frappe import _
from frappe.utils import flt, now
import json
from typing import Dict, Optional

from techsavanna_pos.api.mpesa_client import (
    initiate_stk_push,
    query_stk_status,
    process_b2c_payment,
    process_b2b_payment,
    MpesaClientError,
    MpesaAuthenticationError,
    MpesaAPIError
)
from techsavanna_pos.api.mpesa_constants import (
    MPESA_SUCCESS,
    STATUS_PENDING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELLED,
    get_result_message
)


@frappe.whitelist()
def initiate_stk_push_payment(
    company: str = None,
    phone_number: str = None,
    amount: float = None,
    reference: str = None,
    description: str = None,
    invoice_type: str = None,
    invoice_name: str = None
) -> Dict:
    """
    Initiate STK Push payment request
    
    Args:
        company: Company name (optional, defaults to user's default company)
        phone_number: Customer phone number (MSISDN format: 254712345678)
        amount: Transaction amount
        reference: Internal reference (e.g., invoice number)
        description: Transaction description (optional)
        invoice_type: Invoice type (Sales Invoice or POS Invoice) - optional
        invoice_name: Invoice name - optional
        
    Returns:
        Dictionary with transaction details
        
    Example Request:
        POST /api/method/techsavanna_pos.api.mpesa_api.initiate_stk_push_payment
        {
            "company": "Company Name",
            "phone_number": "254712345678",
            "amount": 100.00,
            "reference": "INV-001",
            "description": "Payment for Invoice INV-001",
            "invoice_type": "Sales Invoice",
            "invoice_name": "INV-001"
        }
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to initiate MPESA payment."),
            frappe.AuthenticationError
        )
    
    # Get company (from parameter or user default)
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(
                _("Company is required. Please provide company parameter or set a default company."),
                frappe.ValidationError
            )
    
    # Validate required fields
    if not phone_number:
        frappe.throw(_("Phone number is required"), frappe.ValidationError)
    
    if not amount or flt(amount) <= 0:
        frappe.throw(_("Valid amount is required (must be greater than 0)"), frappe.ValidationError)
    
    if not reference:
        frappe.throw(_("Reference number is required"), frappe.ValidationError)
    
    # Validate phone number format
    if not phone_number.startswith("254") or len(phone_number) != 12 or not phone_number[3:].isdigit():
        frappe.throw(
            _("Invalid phone number format. Use MSISDN format: 254712345678"),
            frappe.ValidationError
        )
    
    # Validate invoice if provided
    if invoice_type and invoice_name:
        if not frappe.db.exists(invoice_type, invoice_name):
            frappe.throw(
                _("Invoice {0} of type {1} does not exist").format(invoice_name, invoice_type),
                frappe.ValidationError
            )
    
    try:
        # Initiate STK Push
        result = initiate_stk_push(
            company=company,
            phone_number=phone_number,
            amount=flt(amount),
            reference=reference,
            description=description
        )
        
        # Link to invoice if provided
        if invoice_type and invoice_name and result.get("transaction_id"):
            transaction_log = frappe.get_doc("MPESA Transaction Log", result["transaction_id"])
            transaction_log.invoice_type = invoice_type
            transaction_log.invoice = invoice_name
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        
        return {
            "success": True,
            "message": _("STK push initiated successfully"),
            "transaction": {
                "transaction_id": result.get("transaction_id"),
                "merchant_request_id": result.get("merchant_request_id"),
                "checkout_request_id": result.get("checkout_request_id"),
                "status": "Pending",
                "phone_number": phone_number,
                "amount": flt(amount),
                "reference": reference
            }
        }
        
    except MpesaAuthenticationError as e:
        frappe.log_error(f"MPESA Authentication Error: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "MPESA_AUTHENTICATION_ERROR",
            "error_type": "AuthenticationError",
            "message": str(e)
        }
    except MpesaAPIError as e:
        frappe.log_error(f"MPESA API Error: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "MPESA_API_ERROR",
            "error_type": "APIError",
            "message": str(e)
        }
    except MpesaClientError as e:
        frappe.log_error(f"MPESA Client Error: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "MPESA_CLIENT_ERROR",
            "error_type": "ClientError",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"Unexpected error in initiate_stk_push_payment: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "UNEXPECTED_ERROR",
            "error_type": "Exception",
            "message": _("An unexpected error occurred: {0}").format(str(e))
        }


@frappe.whitelist()
def check_payment_status(transaction_id: str = None, checkout_request_id: str = None) -> Dict:
    """
    Check STK Push payment status
    
    Args:
        transaction_id: MPESA Transaction Log ID (optional)
        checkout_request_id: STK Push CheckoutRequestID (optional)
        
    Returns:
        Dictionary with payment status
        
    Example Request:
        GET /api/method/techsavanna_pos.api.mpesa_api.check_payment_status?transaction_id=MPESA-TXN-001
        or
        GET /api/method/techsavanna_pos.api.mpesa_api.check_payment_status?checkout_request_id=ws_CO_...
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to check payment status."),
            frappe.AuthenticationError
        )
    
    # Get transaction log
    if transaction_id:
        try:
            transaction_log = frappe.get_doc("MPESA Transaction Log", transaction_id)
        except frappe.DoesNotExistError:
            frappe.throw(
                _("Transaction not found: {0}").format(transaction_id),
                frappe.ValidationError
            )
    elif checkout_request_id:
        try:
            transaction_log = frappe.get_doc(
                "MPESA Transaction Log",
                {"checkout_request_id": checkout_request_id}
            )
        except frappe.DoesNotExistError:
            frappe.throw(
                _("Transaction not found for CheckoutRequestID: {0}").format(checkout_request_id),
                frappe.ValidationError
            )
    else:
        frappe.throw(
            _("Either transaction_id or checkout_request_id is required"),
            frappe.ValidationError
        )
    
    # Check if transaction is STK Push
    if transaction_log.transaction_type != "STK Push":
        frappe.throw(
            _("This endpoint is only for STK Push transactions. Use appropriate endpoint for {0} transactions.").format(
                transaction_log.transaction_type
            ),
            frappe.ValidationError
        )
    
    # If status is already Success or Failed, return current status
    if transaction_log.status in ["Success", "Failed", "Cancelled"]:
        return {
            "success": True,
            "transaction": {
                "transaction_id": transaction_log.name,
                "checkout_request_id": transaction_log.checkout_request_id,
                "status": transaction_log.status,
                "result_code": transaction_log.result_code,
                "result_description": transaction_log.result_description,
                "mpesa_receipt_number": transaction_log.mpesa_receipt_number,
                "amount": transaction_log.amount,
                "phone_number": transaction_log.phone_number,
                "reference": transaction_log.reference_number
            }
        }
    
    # Query status from Daraja
    try:
        result = query_stk_status(
            company=transaction_log.company,
            checkout_request_id=transaction_log.checkout_request_id
        )
        
        # Reload transaction log to get updated status
        transaction_log.reload()
        
        return {
            "success": True,
            "transaction": {
                "transaction_id": transaction_log.name,
                "checkout_request_id": transaction_log.checkout_request_id,
                "status": transaction_log.status,
                "result_code": transaction_log.result_code,
                "result_description": transaction_log.result_description,
                "mpesa_receipt_number": transaction_log.mpesa_receipt_number,
                "amount": transaction_log.amount,
                "phone_number": transaction_log.phone_number,
                "reference": transaction_log.reference_number
            }
        }
        
    except MpesaClientError as e:
        return {
            "success": False,
            "error_code": "MPESA_CLIENT_ERROR",
            "error_type": "ClientError",
            "message": str(e),
            "transaction": {
                "transaction_id": transaction_log.name,
                "status": transaction_log.status
            }
        }
    except Exception as e:
        frappe.log_error(f"Error checking payment status: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "UNEXPECTED_ERROR",
            "error_type": "Exception",
            "message": _("An unexpected error occurred: {0}").format(str(e))
        }

# ============================================================================
# Settings Management APIs
# ============================================================================

@frappe.whitelist()
def register_mpesa_settings(
    company: str,
    environment: str = "Sandbox",
    shortcode: str = None,
    shortcode_type: str = None,
    consumer_key: str = None,
    consumer_secret: str = None,
    passkey: str = None,
    payment_account: str = None,
    **kwargs
) -> Dict:
    """
    Register or update MPESA settings for a company
    
    Args:
        company: Company name
        environment: Sandbox or Production
        shortcode: Business shortcode
        shortcode_type: Paybill or BuyGoods
        consumer_key: Daraja consumer key
        consumer_secret: Daraja consumer secret
        passkey: STK Push passkey
        payment_account: Account to receive MPESA payments
        **kwargs: Additional settings (callback URLs, etc.)
        
    Returns:
        Dictionary with settings details
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to register MPESA settings."),
            frappe.AuthenticationError
        )
    
    # Validate company exists
    if not frappe.db.exists("Company", company):
        frappe.throw(_("Company {0} does not exist").format(company), frappe.ValidationError)
    
    # Check if settings already exist
    existing_settings = frappe.db.get_value(
        "MPESA Settings",
        {"company": company},
        "name"
    )
    
    if existing_settings:
        # Update existing settings
        settings = frappe.get_doc("MPESA Settings", existing_settings)
        update_mode = True
    else:
        # Create new settings
        settings = frappe.new_doc("MPESA Settings")
        settings.company = company
        update_mode = False
    
    # Update fields
    if environment:
        settings.environment = environment
    if shortcode:
        settings.shortcode = shortcode
    if shortcode_type:
        settings.shortcode_type = shortcode_type
    if consumer_key:
        settings.consumer_key = consumer_key
    if consumer_secret:
        settings.consumer_secret = consumer_secret
    if passkey:
        settings.passkey = passkey
    if payment_account:
        settings.payment_account = payment_account
    
    # Optional fields
    if "initiator_name" in kwargs:
        settings.initiator_name = kwargs.get("initiator_name")
    if "initiator_password" in kwargs:
        settings.initiator_password = kwargs.get("initiator_password")
    if "stk_callback_url" in kwargs:
        settings.stk_callback_url = kwargs.get("stk_callback_url")
    if "b2c_result_url" in kwargs:
        settings.b2c_result_url = kwargs.get("b2c_result_url")
    if "b2c_timeout_url" in kwargs:
        settings.b2c_timeout_url = kwargs.get("b2c_timeout_url")
    if "b2b_result_url" in kwargs:
        settings.b2b_result_url = kwargs.get("b2b_result_url")
    if "b2b_timeout_url" in kwargs:
        settings.b2b_timeout_url = kwargs.get("b2b_timeout_url")
    if "account_reference_prefix" in kwargs:
        settings.account_reference_prefix = kwargs.get("account_reference_prefix")
    if "transaction_description" in kwargs:
        settings.transaction_description = kwargs.get("transaction_description")
    if "auto_confirm_payments" in kwargs:
        settings.auto_confirm_payments = kwargs.get("auto_confirm_payments", False)
    if "payment_timeout" in kwargs:
        settings.payment_timeout = kwargs.get("payment_timeout", 300)
    if "test_phone_number" in kwargs:
        settings.test_phone_number = kwargs.get("test_phone_number")
    
    # Set as active if not specified
    if "is_active" not in kwargs or kwargs.get("is_active") is None:
        settings.is_active = 1
    
    # Validate and save
    try:
        settings.validate()
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Setup Mode of Payment if not exists
        setup_mpesa_mode_of_payment(company)
        
        return {
            "success": True,
            "message": _("MPESA settings {0} successfully").format("updated" if update_mode else "registered"),
            "settings": {
                "name": settings.name,
                "company": settings.company,
                "is_active": settings.is_active,
                "environment": settings.environment
            }
        }
    except Exception as e:
        frappe.log_error(f"Error registering MPESA settings: {str(e)}", "MPESA Settings Error")
        frappe.throw(_("Error registering MPESA settings: {0}").format(str(e)))


@frappe.whitelist()
def get_mpesa_settings(company: str = None) -> Dict:
    """
    Get MPESA settings for a company
    
    Args:
        company: Company name (optional, defaults to user's default company)
        
    Returns:
        Dictionary with settings (sensitive fields masked)
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to view MPESA settings."),
            frappe.AuthenticationError
        )
    
    # Get company
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(_("Company is required"), frappe.ValidationError)
    
    # Get settings
    settings_name = frappe.db.get_value(
        "MPESA Settings",
        {"company": company},
        "name"
    )
    
    if not settings_name:
        return {
            "success": False,
            "message": _("No MPESA settings found for company {0}").format(company),
            "settings": None
        }
    
    settings = frappe.get_doc("MPESA Settings", settings_name)
    
    # Return settings with sensitive fields masked
    return {
        "success": True,
        "settings": {
            "name": settings.name,
            "company": settings.company,
            "is_active": settings.is_active,
            "environment": settings.environment,
            "shortcode": settings.shortcode,
            "shortcode_type": settings.shortcode_type,
            "payment_account": settings.payment_account,
            "mode_of_payment": settings.mode_of_payment,
            "account_reference_prefix": settings.account_reference_prefix,
            "transaction_description": settings.transaction_description,
            "auto_confirm_payments": settings.auto_confirm_payments,
            "payment_timeout": settings.payment_timeout,
            "test_phone_number": settings.test_phone_number,
            "stk_callback_url": settings.stk_callback_url,
            "b2c_result_url": settings.b2c_result_url,
            "b2c_timeout_url": settings.b2c_timeout_url,
            "b2b_result_url": settings.b2b_result_url,
            "b2b_timeout_url": settings.b2b_timeout_url,
            # Sensitive fields are not returned
            "consumer_key": "***" if settings.consumer_key else None,
            "consumer_secret": "***" if settings.consumer_secret else None,
            "passkey": "***" if settings.passkey else None,
            "initiator_password": "***" if settings.initiator_password else None
        }
    }


@frappe.whitelist()
def update_mpesa_settings(company: str, **kwargs) -> Dict:
    """
    Update MPESA settings for a company
    
    This is an alias for register_mpesa_settings which handles both create and update
    """
    return register_mpesa_settings(company=company, **kwargs)


@frappe.whitelist()
def deactivate_mpesa_settings(company: str) -> Dict:
    """
    Deactivate MPESA settings for a company
    
    Args:
        company: Company name
        
    Returns:
        Dictionary with result
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to deactivate MPESA settings."),
            frappe.AuthenticationError
        )
    
    # Get settings
    settings_name = frappe.db.get_value(
        "MPESA Settings",
        {"company": company},
        "name"
    )
    
    if not settings_name:
        frappe.throw(
            _("No MPESA settings found for company {0}").format(company),
            frappe.ValidationError
        )
    
    settings = frappe.get_doc("MPESA Settings", settings_name)
    settings.is_active = 0
    settings.save(ignore_permissions=True)
    frappe.db.commit()
    
    return {
        "success": True,
        "message": _("MPESA settings deactivated successfully"),
        "settings": {
            "name": settings.name,
            "company": settings.company,
            "is_active": settings.is_active
        }
    }


# ============================================================================
# B2C and B2B Payment APIs
# ============================================================================

@frappe.whitelist()
def initiate_b2c_payment(
    company: str = None,
    phone_number: str = None,
    amount: float = None,
    reference: str = None,
    remarks: str = None
) -> Dict:
    """
    Initiate B2C payment (Business to Customer payout)
    
    Args:
        company: Company name
        phone_number: Customer phone number
        amount: Transaction amount
        reference: Internal reference
        remarks: Payment remarks
        
    Returns:
        Dictionary with transaction details
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to initiate B2C payment."),
            frappe.AuthenticationError
        )
    
    # Get company
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(_("Company is required"), frappe.ValidationError)
    
    # Validate required fields
    if not phone_number:
        frappe.throw(_("Phone number is required"), frappe.ValidationError)
    
    if not amount or flt(amount) <= 0:
        frappe.throw(_("Valid amount is required"), frappe.ValidationError)
    
    if not reference:
        frappe.throw(_("Reference number is required"), frappe.ValidationError)
    
    try:
        result = process_b2c_payment(
            company=company,
            phone_number=phone_number,
            amount=flt(amount),
            reference=reference,
            remarks=remarks
        )
        
        return {
            "success": True,
            "message": _("B2C payment initiated successfully"),
            "transaction": {
                "transaction_id": result.get("transaction_id"),
                "conversation_id": result.get("conversation_id"),
                "status": "Pending",
                "phone_number": phone_number,
                "amount": flt(amount),
                "reference": reference
            }
        }
    except MpesaClientError as e:
        return {
            "success": False,
            "error_code": "MPESA_CLIENT_ERROR",
            "error_type": "ClientError",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"Error initiating B2C payment: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "UNEXPECTED_ERROR",
            "error_type": "Exception",
            "message": _("An unexpected error occurred: {0}").format(str(e))
        }


@frappe.whitelist()
def initiate_b2b_payment(
    company: str = None,
    receiver_shortcode: str = None,
    amount: float = None,
    reference: str = None,
    remarks: str = None
) -> Dict:
    """
    Initiate B2B payment (Business to Business transfer)
    
    Args:
        company: Company name
        receiver_shortcode: Receiver business shortcode
        amount: Transaction amount
        reference: Internal reference
        remarks: Payment remarks
        
    Returns:
        Dictionary with transaction details
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to initiate B2B payment."),
            frappe.AuthenticationError
        )
    
    # Get company
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(_("Company is required"), frappe.ValidationError)
    
    # Validate required fields
    if not receiver_shortcode:
        frappe.throw(_("Receiver shortcode is required"), frappe.ValidationError)
    
    if not amount or flt(amount) <= 0:
        frappe.throw(_("Valid amount is required"), frappe.ValidationError)
    
    if not reference:
        frappe.throw(_("Reference number is required"), frappe.ValidationError)
    
    try:
        result = process_b2b_payment(
            company=company,
            receiver_shortcode=receiver_shortcode,
            amount=flt(amount),
            reference=reference,
            remarks=remarks
        )
        
        return {
            "success": True,
            "message": _("B2B payment initiated successfully"),
            "transaction": {
                "transaction_id": result.get("transaction_id"),
                "conversation_id": result.get("conversation_id"),
                "status": "Pending",
                "receiver_shortcode": receiver_shortcode,
                "amount": flt(amount),
                "reference": reference
            }
        }
    except MpesaClientError as e:
        return {
            "success": False,
            "error_code": "MPESA_CLIENT_ERROR",
            "error_type": "ClientError",
            "message": str(e)
        }
    except Exception as e:
        frappe.log_error(f"Error initiating B2B payment: {str(e)}", "MPESA API Error")
        return {
            "success": False,
            "error_code": "UNEXPECTED_ERROR",
            "error_type": "Exception",
            "message": _("An unexpected error occurred: {0}").format(str(e))
        }


# ============================================================================
# Transaction Management APIs
# ============================================================================

@frappe.whitelist()
def get_transactions(
    company: str = None,
    status: str = None,
    transaction_type: str = None,
    limit: int = 50,
    offset: int = 0
) -> Dict:
    """
    Get transaction history with filters
    
    Args:
        company: Company name (optional, defaults to user's default)
        status: Filter by status (Pending, Success, Failed, Cancelled)
        transaction_type: Filter by type (STK Push, B2C, B2B)
        limit: Number of records to return (default: 50)
        offset: Offset for pagination (default: 0)
        
    Returns:
        Dictionary with transaction list
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to view transactions."),
            frappe.AuthenticationError
        )
    
    # Get company
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(_("Company is required"), frappe.ValidationError)
    
    # Build filters
    filters = {"company": company}
    
    if status:
        filters["status"] = status
    if transaction_type:
        filters["transaction_type"] = transaction_type
    
    # Get transactions
    transactions = frappe.get_all(
        "MPESA Transaction Log",
        filters=filters,
        fields=[
            "name",
            "transaction_type",
            "status",
            "phone_number",
            "amount",
            "reference_number",
            "mpesa_receipt_number",
            "result_code",
            "result_description",
            "created_at",
            "completed_at",
            "payment_entry",
            "invoice_type",
            "invoice"
        ],
        order_by="created_at desc",
        limit=limit,
        start=offset
    )
    
    # Get total count
    total_count = frappe.db.count("MPESA Transaction Log", filters)
    
    return {
        "success": True,
        "transactions": transactions,
        "total": total_count,
        "limit": limit,
        "offset": offset
    }


@frappe.whitelist()
def get_transaction(transaction_id: str) -> Dict:
    """
    Get single transaction details
    
    Args:
        transaction_id: MPESA Transaction Log ID
        
    Returns:
        Dictionary with complete transaction details
    """
    # Validate authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Authentication required. Please log in to view transaction details."),
            frappe.AuthenticationError
        )
    
    try:
        transaction_log = frappe.get_doc("MPESA Transaction Log", transaction_id)
        
        # Get full transaction data
        transaction_data = transaction_log.as_dict()
        
        # Parse JSON payloads if they exist
        if transaction_data.get("request_payload"):
            try:
                transaction_data["request_payload"] = json.loads(transaction_data["request_payload"])
            except:
                pass
        
        if transaction_data.get("response_payload"):
            try:
                transaction_data["response_payload"] = json.loads(transaction_data["response_payload"])
            except:
                pass
        
        if transaction_data.get("callback_payload"):
            try:
                transaction_data["callback_payload"] = json.loads(transaction_data["callback_payload"])
            except:
                pass
        
        return {
            "success": True,
            "transaction": transaction_data
        }
        
    except frappe.DoesNotExistError:
        frappe.throw(
            _("Transaction not found: {0}").format(transaction_id),
            frappe.ValidationError
        )
    except Exception as e:
        frappe.log_error(f"Error getting transaction: {str(e)}", "MPESA API Error")
        frappe.throw(_("Error getting transaction: {0}").format(str(e)))


# ============================================================================
# Mode of Payment Setup
# ============================================================================

def setup_mpesa_mode_of_payment(company: str) -> Optional[str]:
    """
    Setup MPESA Mode of Payment for a company
    
    Args:
        company: Company name
        
    Returns:
        Mode of Payment name if created/updated, None otherwise
    """
    try:
        # Check if MPESA Mode of Payment exists
        mpesa_mop = frappe.db.get_value("Mode of Payment", {"name": "MPESA"}, "name")
        
        if not mpesa_mop:
            # Create MPESA Mode of Payment
            mop = frappe.new_doc("Mode of Payment")
            mop.mode_of_payment = "MPESA"
            mop.type = "Phone"
            mop.enabled = 1
            mop.insert(ignore_permissions=True)
            mpesa_mop = mop.name
        
        # Get MPESA settings to get payment account
        settings_name = frappe.db.get_value(
            "MPESA Settings",
            {"company": company, "is_active": 1},
            "name"
        )
        
        if settings_name:
            settings = frappe.get_doc("MPESA Settings", settings_name)
            
            # Check if account mapping exists for this company
            mop = frappe.get_doc("Mode of Payment", mpesa_mop)
            existing_account = None
            
            for account in mop.accounts:
                if account.company == company:
                    existing_account = account
                    break
            
            if existing_account:
                # Update existing account
                if settings.payment_account:
                    existing_account.default_account = settings.payment_account
                    mop.save(ignore_permissions=True)
            else:
                # Add new account mapping
                if settings.payment_account:
                    mop.append("accounts", {
                        "company": company,
                        "default_account": settings.payment_account
                    })
                    mop.save(ignore_permissions=True)
            
            # Update settings with mode of payment if not set
            if not settings.mode_of_payment:
                settings.mode_of_payment = mpesa_mop
                settings.save(ignore_permissions=True)
            
            frappe.db.commit()
        
        return mpesa_mop
        
    except Exception as e:
        frappe.log_error(f"Error setting up MPESA Mode of Payment: {str(e)}", "MPESA Setup Error")
        return None

