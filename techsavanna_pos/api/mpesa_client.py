"""
MPESA (Daraja) API Client
Handles all interactions with Safaricom Daraja API
"""

import frappe
from frappe import _
from frappe.utils import now, get_datetime, add_to_date
import base64
import json
import requests
from typing import Dict, Optional, Tuple
from datetime import datetime

from techsavanna_pos.api.mpesa_constants import (
    get_base_url,
    OAUTH_TOKEN_ENDPOINT,
    STK_PUSH_ENDPOINT,
    STK_PUSH_QUERY_ENDPOINT,
    B2C_ENDPOINT,
    B2B_ENDPOINT,
    TRANSACTION_TYPE_PAYBILL,
    TRANSACTION_TYPE_BUYGOODS,
    STATUS_PENDING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELLED,
    MPESA_SUCCESS,
    B2C_COMMAND_BUSINESS_PAYMENT,
    B2B_COMMAND_BUSINESS_PAY_BILL
)


class MpesaClientError(Exception):
    """Base exception for MPESA client errors"""
    pass


class MpesaAuthenticationError(MpesaClientError):
    """Authentication error with Daraja API"""
    pass


class MpesaAPIError(MpesaClientError):
    """Error from Daraja API"""
    pass


def get_access_token(company: str) -> str:
    """
    Get or refresh MPESA access token for a company
    
    Args:
        company: Company name
        
    Returns:
        Access token string
        
    Raises:
        MpesaAuthenticationError: If credentials are invalid
        MpesaClientError: For other errors
    """
    # Get MPESA settings for company
    settings_name = frappe.db.get_value(
        "MPESA Settings",
        {"company": company, "is_active": 1},
        "name"
    )
    
    if not settings_name:
        raise MpesaClientError(_("No active MPESA settings found for company {0}").format(company))
    
    settings = frappe.get_doc("MPESA Settings", settings_name)
    
    # Check if cached token exists and is still valid
    if settings.access_token and settings.token_expiry:
        expiry_time = get_datetime(settings.token_expiry)
        current_time = now()
        
        # Refresh if token expires within 5 minutes
        if expiry_time and expiry_time > add_to_date(current_time, minutes=5):
            return settings.access_token
    
    # Get new token from Daraja
    base_url = get_base_url(settings.environment)
    url = f"{base_url}{OAUTH_TOKEN_ENDPOINT}"
    
    # Create Basic Auth header
    credentials = f"{settings.consumer_key}:{settings.consumer_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {encoded_credentials}"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        if "access_token" not in data:
            raise MpesaAuthenticationError(_("Invalid response from Daraja API: {0}").format(data))
        
        access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))  # Default to 3600 seconds
        
        # Calculate expiry time
        expiry_time = add_to_date(now(), seconds=expires_in)
        
        # Update settings with new token
        settings.access_token = access_token
        settings.token_expiry = expiry_time
        settings.last_token_refresh = now()
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        
        return access_token
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            raise MpesaAuthenticationError(
                _("Invalid MPESA credentials. Please check your Consumer Key and Consumer Secret.")
            )
        raise MpesaAPIError(_("HTTP error from Daraja API: {0}").format(str(e)))
    except requests.exceptions.RequestException as e:
        raise MpesaClientError(_("Network error connecting to Daraja API: {0}").format(str(e)))
    except Exception as e:
        frappe.log_error(f"Error getting MPESA access token: {str(e)}", "MPESA Token Error")
        raise MpesaClientError(_("Error getting access token: {0}").format(str(e)))


def generate_stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
    """
    Generate STK Push password using Daraja formula
    
    Formula: Base64(shortcode + passkey + timestamp)
    
    Args:
        shortcode: Business shortcode
        passkey: STK Push passkey
        timestamp: Timestamp in format YYYYMMDDHHmmss
        
    Returns:
        Base64 encoded password
    """
    password_string = f"{shortcode}{passkey}{timestamp}"
    encoded_password = base64.b64encode(password_string.encode()).decode()
    return encoded_password


def generate_timestamp() -> str:
    """
    Generate timestamp in Daraja format: YYYYMMDDHHmmss
    
    Returns:
        Timestamp string
    """
    return datetime.now().strftime("%Y%m%d%H%M%S")


def initiate_stk_push(
    company: str,
    phone_number: str,
    amount: float,
    reference: str,
    description: str = None
) -> Dict:
    """
    Initiate STK Push payment request
    
    Args:
        company: Company name
        phone_number: Customer phone number (MSISDN format: 254712345678)
        amount: Transaction amount
        reference: Internal reference (e.g., invoice number)
        description: Transaction description (optional)
        
    Returns:
        Dictionary with transaction details:
        {
            "merchant_request_id": "...",
            "checkout_request_id": "...",
            "response_code": 0,
            "response_description": "...",
            "transaction_id": "..."  # MPESA Transaction Log name
        }
        
    Raises:
        MpesaClientError: For errors
    """
    # Get MPESA settings
    settings = frappe.get_doc("MPESA Settings", {"company": company, "is_active": 1})
    
    if not settings:
        raise MpesaClientError(_("No active MPESA settings found for company {0}").format(company))
    
    # Validate phone number format
    if not phone_number.startswith("254") or len(phone_number) != 12:
        raise MpesaClientError(_("Invalid phone number format. Use MSISDN format: 254712345678"))
    
    # Get access token
    access_token = get_access_token(company)
    
    # Generate timestamp and password
    timestamp = generate_timestamp()
    password = generate_stk_password(settings.shortcode, settings.passkey, timestamp)
    
    # Determine transaction type based on shortcode type
    if settings.shortcode_type == "Paybill":
        transaction_type = TRANSACTION_TYPE_PAYBILL
    else:
        transaction_type = TRANSACTION_TYPE_BUYGOODS
    
    # Build request payload
    request_payload = {
        "BusinessShortCode": settings.shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": transaction_type,
        "Amount": str(int(amount)),  # Amount must be string and integer
        "PartyA": phone_number,
        "PartyB": settings.shortcode,
        "PhoneNumber": phone_number,
        "CallBackURL": settings.stk_callback_url,
        "AccountReference": reference,
        "TransactionDesc": description or settings.transaction_description or "Payment"
    }
    
    # Prepare API request
    base_url = get_base_url(settings.environment)
    url = f"{base_url}{STK_PUSH_ENDPOINT}"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        # Create Transaction Log entry first
        transaction_log = frappe.new_doc("MPESA Transaction Log")
        transaction_log.company = company
        transaction_log.transaction_type = "STK Push"
        transaction_log.phone_number = phone_number
        transaction_log.amount = amount
        transaction_log.reference_number = reference
        transaction_log.status = STATUS_PENDING
        transaction_log.request_payload = json.dumps(request_payload)
        transaction_log.created_at = now()
        transaction_log.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Send request to Daraja
        response = requests.post(url, json=request_payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        response_data = response.json()
        
        # Update Transaction Log with response
        transaction_log.response_payload = json.dumps(response_data)
        transaction_log.merchant_request_id = response_data.get("MerchantRequestID")
        transaction_log.checkout_request_id = response_data.get("CheckoutRequestID")
        
        # Check response code
        response_code = int(response_data.get("ResponseCode", -1))
        if response_code != 0:
            transaction_log.status = STATUS_FAILED
            transaction_log.result_code = response_code
            transaction_log.result_description = response_data.get("ResponseDescription", "")
            transaction_log.error_message = response_data.get("ResponseDescription", "")
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
            
            raise MpesaAPIError(
                _("Daraja API error: {0}").format(response_data.get("ResponseDescription", "Unknown error"))
            )
        
        # Success - update transaction log
        transaction_log.result_code = response_code
        transaction_log.result_description = response_data.get("ResponseDescription", "")
        transaction_log.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "merchant_request_id": response_data.get("MerchantRequestID"),
            "checkout_request_id": response_data.get("CheckoutRequestID"),
            "response_code": response_code,
            "response_description": response_data.get("ResponseDescription", ""),
            "customer_message": response_data.get("CustomerMessage", ""),
            "transaction_id": transaction_log.name
        }
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error from Daraja API: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("errorMessage", error_msg)
            except:
                pass
        
        # Update transaction log with error
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = error_msg
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        
        raise MpesaAPIError(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"Network error: {str(e)}"
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = error_msg
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        raise MpesaClientError(error_msg)
    except Exception as e:
        frappe.log_error(f"Error initiating STK push: {str(e)}", "MPESA STK Push Error")
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = str(e)
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        raise MpesaClientError(_("Error initiating STK push: {0}").format(str(e)))


def query_stk_status(company: str, checkout_request_id: str) -> Dict:
    """
    Query STK Push payment status
    
    Args:
        company: Company name
        checkout_request_id: STK Push CheckoutRequestID
        
    Returns:
        Dictionary with query result:
        {
            "result_code": 0,
            "result_description": "...",
            "merchant_request_id": "...",
            "checkout_request_id": "...",
            "result_desc": "..."
        }
        
    Raises:
        MpesaClientError: For errors
    """
    # Get MPESA settings
    settings = frappe.get_doc("MPESA Settings", {"company": company, "is_active": 1})
    
    if not settings:
        raise MpesaClientError(_("No active MPESA settings found for company {0}").format(company))
    
    # Get access token
    access_token = get_access_token(company)
    
    # Generate timestamp and password
    timestamp = generate_timestamp()
    password = generate_stk_password(settings.shortcode, settings.passkey, timestamp)
    
    # Build request payload
    request_payload = {
        "BusinessShortCode": settings.shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id
    }
    
    # Prepare API request
    base_url = get_base_url(settings.environment)
    url = f"{base_url}{STK_PUSH_QUERY_ENDPOINT}"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        # Find existing transaction log
        transaction_log = frappe.get_doc(
            "MPESA Transaction Log",
            {"checkout_request_id": checkout_request_id}
        )
        
        # Send request to Daraja
        response = requests.post(url, json=request_payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        response_data = response.json()
        
        # Update transaction log with query response
        result_code = int(response_data.get("ResultCode", -1))
        result_desc = response_data.get("ResultDesc", "")
        
        transaction_log.result_code = result_code
        transaction_log.result_description = result_desc
        
        if result_code == MPESA_SUCCESS:
            transaction_log.status = STATUS_SUCCESS
            transaction_log.mpesa_receipt_number = response_data.get("MpesaReceiptNumber", "")
        else:
            transaction_log.status = STATUS_FAILED
        
        transaction_log.completed_at = now()
        transaction_log.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "result_code": result_code,
            "result_description": result_desc,
            "merchant_request_id": response_data.get("MerchantRequestID", ""),
            "checkout_request_id": response_data.get("CheckoutRequestID", ""),
            "result_desc": result_desc,
            "mpesa_receipt_number": response_data.get("MpesaReceiptNumber", ""),
            "transaction_id": transaction_log.name
        }
        
    except frappe.DoesNotExistError:
        raise MpesaClientError(_("Transaction not found for CheckoutRequestID: {0}").format(checkout_request_id))
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error from Daraja API: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("errorMessage", error_msg)
            except:
                pass
        raise MpesaAPIError(error_msg)
    except requests.exceptions.RequestException as e:
        raise MpesaClientError(_("Network error: {0}").format(str(e)))
    except Exception as e:
        frappe.log_error(f"Error querying STK status: {str(e)}", "MPESA STK Query Error")
        raise MpesaClientError(_("Error querying STK status: {0}").format(str(e)))


def process_b2c_payment(
    company: str,
    phone_number: str,
    amount: float,
    reference: str,
    remarks: str = None
) -> Dict:
    """
    Process B2C payment (Business to Customer payout)
    
    Args:
        company: Company name
        phone_number: Customer phone number (MSISDN format: 254712345678)
        amount: Transaction amount
        reference: Internal reference
        remarks: Payment remarks (optional)
        
    Returns:
        Dictionary with transaction details:
        {
            "conversation_id": "...",
            "originator_conversation_id": "...",
            "response_code": 0,
            "response_description": "...",
            "transaction_id": "..."  # MPESA Transaction Log name
        }
        
    Raises:
        MpesaClientError: For errors
    """
    # Get MPESA settings
    settings_name = frappe.db.get_value(
        "MPESA Settings",
        {"company": company, "is_active": 1},
        "name"
    )
    
    if not settings_name:
        raise MpesaClientError(_("No active MPESA settings found for company {0}").format(company))
    
    settings = frappe.get_doc("MPESA Settings", settings_name)
    
    # Validate required fields for B2C
    if not settings.initiator_name or not settings.initiator_password:
        raise MpesaClientError(_("Initiator name and password are required for B2C payments"))
    
    if not settings.b2c_result_url or not settings.b2c_timeout_url:
        raise MpesaClientError(_("B2C callback URLs are required"))
    
    # Validate phone number format
    if not phone_number.startswith("254") or len(phone_number) != 12:
        raise MpesaClientError(_("Invalid phone number format. Use MSISDN format: 254712345678"))
    
    # Get access token
    access_token = get_access_token(company)
    
    # Encrypt initiator password (Daraja requires RSA encryption, but for now we'll use as-is)
    # Note: In production, you may need to implement RSA encryption based on Daraja requirements
    security_credential = settings.initiator_password
    
    # Build request payload
    request_payload = {
        "InitiatorName": settings.initiator_name,
        "SecurityCredential": security_credential,
        "CommandID": B2C_COMMAND_BUSINESS_PAYMENT,
        "Amount": str(int(amount)),  # Amount must be string and integer
        "PartyA": settings.shortcode,
        "PartyB": phone_number,
        "Remarks": remarks or f"Payment for {reference}",
        "QueueTimeOutURL": settings.b2c_timeout_url,
        "ResultURL": settings.b2c_result_url,
        "Occasion": ""
    }
    
    # Prepare API request
    base_url = get_base_url(settings.environment)
    url = f"{base_url}{B2C_ENDPOINT}"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        # Create Transaction Log entry first
        transaction_log = frappe.new_doc("MPESA Transaction Log")
        transaction_log.company = company
        transaction_log.transaction_type = "B2C"
        transaction_log.phone_number = phone_number
        transaction_log.amount = amount
        transaction_log.reference_number = reference
        transaction_log.status = STATUS_PENDING
        transaction_log.request_payload = json.dumps(request_payload)
        transaction_log.created_at = now()
        transaction_log.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Send request to Daraja
        response = requests.post(url, json=request_payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        response_data = response.json()
        
        # Update Transaction Log with response
        transaction_log.response_payload = json.dumps(response_data)
        transaction_log.conversation_id = response_data.get("ConversationID")
        
        # Check response code
        response_code = int(response_data.get("ResponseCode", -1))
        if response_code != 0:
            transaction_log.status = STATUS_FAILED
            transaction_log.result_code = response_code
            transaction_log.result_description = response_data.get("ResponseDescription", "")
            transaction_log.error_message = response_data.get("ResponseDescription", "")
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
            
            raise MpesaAPIError(
                _("Daraja API error: {0}").format(response_data.get("ResponseDescription", "Unknown error"))
            )
        
        # Success - update transaction log
        transaction_log.result_code = response_code
        transaction_log.result_description = response_data.get("ResponseDescription", "")
        transaction_log.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "conversation_id": response_data.get("ConversationID"),
            "originator_conversation_id": response_data.get("OriginatorConversationID"),
            "response_code": response_code,
            "response_description": response_data.get("ResponseDescription", ""),
            "transaction_id": transaction_log.name
        }
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error from Daraja API: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("errorMessage", error_msg)
            except:
                pass
        
        # Update transaction log with error
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = error_msg
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        
        raise MpesaAPIError(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"Network error: {str(e)}"
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = error_msg
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        raise MpesaClientError(error_msg)
    except Exception as e:
        frappe.log_error(f"Error processing B2C payment: {str(e)}", "MPESA B2C Error")
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = str(e)
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        raise MpesaClientError(_("Error processing B2C payment: {0}").format(str(e)))


def process_b2b_payment(
    company: str,
    receiver_shortcode: str,
    amount: float,
    reference: str,
    remarks: str = None
) -> Dict:
    """
    Process B2B payment (Business to Business transfer)
    
    Args:
        company: Company name
        receiver_shortcode: Receiver business shortcode
        amount: Transaction amount
        reference: Internal reference
        remarks: Payment remarks (optional)
        
    Returns:
        Dictionary with transaction details:
        {
            "conversation_id": "...",
            "originator_conversation_id": "...",
            "response_code": 0,
            "response_description": "...",
            "transaction_id": "..."  # MPESA Transaction Log name
        }
        
    Raises:
        MpesaClientError: For errors
    """
    # Get MPESA settings
    settings_name = frappe.db.get_value(
        "MPESA Settings",
        {"company": company, "is_active": 1},
        "name"
    )
    
    if not settings_name:
        raise MpesaClientError(_("No active MPESA settings found for company {0}").format(company))
    
    settings = frappe.get_doc("MPESA Settings", settings_name)
    
    # Validate required fields for B2B
    if not settings.initiator_name or not settings.initiator_password:
        raise MpesaClientError(_("Initiator name and password are required for B2B payments"))
    
    if not settings.b2b_result_url or not settings.b2b_timeout_url:
        raise MpesaClientError(_("B2B callback URLs are required"))
    
    # Get access token
    access_token = get_access_token(company)
    
    # Encrypt initiator password
    security_credential = settings.initiator_password
    
    # Build request payload
    request_payload = {
        "Initiator": settings.initiator_name,
        "SecurityCredential": security_credential,
        "CommandID": B2B_COMMAND_BUSINESS_PAY_BILL,
        "SenderIdentifierType": "4",  # Shortcode
        "RecieverIdentifierType": "4",  # Shortcode
        "Amount": str(int(amount)),  # Amount must be string and integer
        "PartyA": settings.shortcode,
        "PartyB": receiver_shortcode,
        "AccountReference": reference,
        "Remarks": remarks or f"Payment for {reference}",
        "QueueTimeOutURL": settings.b2b_timeout_url,
        "ResultURL": settings.b2b_result_url
    }
    
    # Prepare API request
    base_url = get_base_url(settings.environment)
    url = f"{base_url}{B2B_ENDPOINT}"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        # Create Transaction Log entry first
        transaction_log = frappe.new_doc("MPESA Transaction Log")
        transaction_log.company = company
        transaction_log.transaction_type = "B2B"
        transaction_log.phone_number = None  # B2B doesn't use phone number
        transaction_log.amount = amount
        transaction_log.reference_number = reference
        transaction_log.status = STATUS_PENDING
        transaction_log.request_payload = json.dumps(request_payload)
        transaction_log.created_at = now()
        transaction_log.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Send request to Daraja
        response = requests.post(url, json=request_payload, headers=headers, timeout=30)
        response.raise_for_status()
        
        response_data = response.json()
        
        # Update Transaction Log with response
        transaction_log.response_payload = json.dumps(response_data)
        transaction_log.conversation_id = response_data.get("ConversationID")
        
        # Check response code
        response_code = int(response_data.get("ResponseCode", -1))
        if response_code != 0:
            transaction_log.status = STATUS_FAILED
            transaction_log.result_code = response_code
            transaction_log.result_description = response_data.get("ResponseDescription", "")
            transaction_log.error_message = response_data.get("ResponseDescription", "")
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
            
            raise MpesaAPIError(
                _("Daraja API error: {0}").format(response_data.get("ResponseDescription", "Unknown error"))
            )
        
        # Success - update transaction log
        transaction_log.result_code = response_code
        transaction_log.result_description = response_data.get("ResponseDescription", "")
        transaction_log.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "conversation_id": response_data.get("ConversationID"),
            "originator_conversation_id": response_data.get("OriginatorConversationID"),
            "response_code": response_code,
            "response_description": response_data.get("ResponseDescription", ""),
            "transaction_id": transaction_log.name
        }
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error from Daraja API: {str(e)}"
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_data = e.response.json()
                error_msg = error_data.get("errorMessage", error_msg)
            except:
                pass
        
        # Update transaction log with error
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = error_msg
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        
        raise MpesaAPIError(error_msg)
    except requests.exceptions.RequestException as e:
        error_msg = f"Network error: {str(e)}"
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = error_msg
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        raise MpesaClientError(error_msg)
    except Exception as e:
        frappe.log_error(f"Error processing B2B payment: {str(e)}", "MPESA B2B Error")
        if 'transaction_log' in locals():
            transaction_log.status = STATUS_FAILED
            transaction_log.error_message = str(e)
            transaction_log.save(ignore_permissions=True)
            frappe.db.commit()
        raise MpesaClientError(_("Error processing B2B payment: {0}").format(str(e)))

