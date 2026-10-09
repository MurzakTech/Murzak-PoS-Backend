"""
Authentication and User Management API
Handles user registration, login, and OAuth Bearer Token generation
"""

import re
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional

import frappe
from frappe import _
from frappe.core.doctype.user.user import generate_keys
from frappe.utils import (
    add_to_date,
    get_site_name,
    now_datetime,
)


# OAuth Configuration
OAUTH_TOKEN_EXPIRATION_HOURS = 24 * 7  # 7 days
OAUTH_CLIENT_NAME = "SavvyPOS API Client"
OAUTH_SCOPES = "all"

# Standard roles to assign to registered business owners for full access
STANDARD_BUSINESS_ROLES = [
    "System Manager",  # Full system access
    "Item Manager",    # For Warehouse, Item creation and management
    "Stock Manager",   # For stock/inventory management
    "Stock User",      # Basic stock operations
    "Sales Manager",   # For sales operations
    "Sales User",      # Basic sales operations
    "Purchase Manager", # For purchase operations
    "Purchase User",   # Basic purchase operations
    "Accounts Manager", # For accounts and financial operations
    "Accounts User",   # Basic accounts operations
]


def assign_all_business_roles(user_email: str) -> None:
    """Assign all standard business roles to a user for full access
    
    This function ensures that all standard business roles are assigned to a user.
    It's idempotent - it won't duplicate roles that are already assigned.
    
    Args:
        user_email: User email to assign roles to
    """
    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        
        user_doc = frappe.get_doc("User", user_email)
        existing_roles = [r.role for r in user_doc.roles]
        
        # Track if any roles were added
        roles_added = False
        
        # Add all standard roles that don't already exist
        for role in STANDARD_BUSINESS_ROLES:
            if role not in existing_roles:
                # Check if role exists in the system before adding
                if frappe.db.exists("Role", role):
                    user_doc.append("roles", {"role": role})
                    roles_added = True
                else:
                    # Log warning if role doesn't exist (shouldn't happen for standard roles)
                    frappe.log_error(
                        "Role Assignment Warning",
                        f"Role '{role}' does not exist in the system. Skipping assignment."
                    )
        
        # Only save if roles were added to avoid unnecessary database writes
        if roles_added:
            user_doc.save(ignore_permissions=True)
            frappe.db.commit()
            
    except Exception as e:
        frappe.log_error(
            "Assign Business Roles Error",
            f"Error assigning roles to user {user_email}: {str(e)}"
        )
        # Re-raise to ensure the error is visible
        raise
    finally:
        frappe.set_user(original_user)


def get_or_create_oauth_client() -> str:
    """Get or create OAuth Client for SavvyPOS API
    
    Returns:
        OAuth Client name
    """
    # Check if client already exists
    client_name = frappe.db.get_value("OAuth Client", {"app_name": OAUTH_CLIENT_NAME}, "name")
    
    if client_name:
        return client_name
    
    # Create new OAuth Client
    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        
        client = frappe.new_doc("OAuth Client")
        client.app_name = OAUTH_CLIENT_NAME
        client.client_id = secrets.token_urlsafe(32)
        client.client_secret = secrets.token_urlsafe(32)
        client.default_redirect_uri = "http://localhost:3000"  # Default redirect URI
        client.grant_type = "Authorization Code"
        client.response_type = "Code"
        client.scopes = OAUTH_SCOPES
        client.insert(ignore_permissions=True)
        client.save(ignore_permissions=True)
        
        return client.name
    finally:
        frappe.set_user(original_user)


def generate_oauth_bearer_token(user: str) -> Dict[str, str]:
    """Generate OAuth Bearer Token for user authentication
    
    Args:
        user: Username
        
    Returns:
        Dictionary with access_token, token_type, expires_in, and refresh_token
    """
    from oauthlib.common import generate_token
    
    # Get or create OAuth Client
    client_name = get_or_create_oauth_client()
    
    # Generate tokens
    access_token = generate_token()
    refresh_token = generate_token()
    expires_in = OAUTH_TOKEN_EXPIRATION_HOURS * 3600  # seconds
    
    # Create OAuth Bearer Token document
    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        
        # Revoke any existing active tokens for this user and client
        existing_tokens = frappe.get_all(
            "OAuth Bearer Token",
            filters={
                "user": user,
                "client": client_name,
                "status": "Active"
            },
            fields=["name"]
        )
        for token in existing_tokens:
            frappe.db.set_value("OAuth Bearer Token", token.name, "status", "Revoked")
        
        # Create new bearer token
        bearer_token = frappe.new_doc("OAuth Bearer Token")
        bearer_token.client = client_name
        bearer_token.user = user
        bearer_token.scopes = OAUTH_SCOPES
        bearer_token.access_token = access_token
        bearer_token.refresh_token = refresh_token
        bearer_token.expires_in = expires_in
        bearer_token.status = "Active"
        bearer_token.insert(ignore_permissions=True)
        bearer_token.save(ignore_permissions=True)
        
        frappe.db.commit()
        
        # Calculate expiration time
        expiration_time = add_to_date(None, seconds=expires_in, as_datetime=True)
        
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "expires_at": expiration_time.isoformat() if expiration_time else None,
            "refresh_token": refresh_token
        }
    finally:
        frappe.set_user(original_user)


def validate_password_strength(password):
    """
    Validate password against our policy.
    """

    errors = []

    if len(password) < 8:
        errors.append("at least 8 characters")

    if not re.search(r"[A-Z]", password):
        errors.append("one uppercase letter")

    if not re.search(r"[a-z]", password):
        errors.append("one lowercase letter")

    if not re.search(r"\d", password):
        errors.append("one number")

    if not re.search(r"[^A-Za-z0-9\s]", password):
        errors.append("one symbol, such as ! @ # - or _")

    return errors


EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)


@frappe.whitelist(allow_guest=True)
def check_email(email):

    return {
        "exists":
            frappe.db.exists("User", email)
    }


@frappe.whitelist(allow_guest=True)
def register_user(
    email: str,
    first_name: str,
    last_name: str,
    password: str,
    phone: Optional[str] = None,
    pos_industry: Optional[str] = None,
    send_welcome_email: bool = False,
    email_verification_code: Optional[str] = None,
    phone_verification_code: Optional[str] = None,
    require_email_verification: bool = False,
    require_phone_verification: bool = False
) -> Dict:
    """Register a new user
    
    Args:
        email: User email (used as username)
        first_name: User's first name
        last_name: User's last name
        password: User password
        phone: Optional phone number
        pos_industry: Optional POS Industry code or name (required for product filtering)
        send_welcome_email: Whether to send welcome email
        email_verification_code: Optional email verification code (if require_email_verification is True)
        phone_verification_code: Optional phone verification code (if require_phone_verification is True)
        require_email_verification: Whether to require email verification before registration
        require_phone_verification: Whether to require phone verification before registration (if phone provided)
        
    Returns:
        User details and API credentials
    """
    try:
        # Validate required fields
        if not email or not email.strip():
            frappe.throw(_("Email address is required. Please provide a valid email address to create your account."), frappe.ValidationError)
        
        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            frappe.throw(_("Please provide a valid email address. The email '{0}' is not in the correct format (e.g., name@example.com).").format(email), frappe.ValidationError)

        if not EMAIL_PATTERN.match(email):

            frappe.local.response["http_status_code"] = 400

            return {
                "success": False,
                "code": "INVALID_EMAIL",
                "message": "Please enter a valid email address."
            }

        # Check if email already exists
        if frappe.db.exists("User", email):
            frappe.local.response["http_status_code"] = 409

            return {
                "success": False,
                "code": "EMAIL_EXISTS",
                "message": "An account already exists with this email address."
            }
        
        if not first_name or not first_name.strip():
            frappe.throw(_("First name is required. Please provide your first name to create your account."), frappe.ValidationError)
        
        if not last_name or not last_name.strip():
            frappe.throw(_("Last name is required. Please provide your last name to create your account."), frappe.ValidationError)
        
        if not password or not password.strip():
            frappe.throw(_("Password is required. Please provide a secure password for your account."), frappe.ValidationError)
        
        # Validate email
        if frappe.db.exists("User", email):
            frappe.throw(_("An account with the email '{0}' already exists. Please use a different email address or try logging in instead.").format(email), frappe.ValidationError)
        
        # Validate password strength
        if len(password) < 8:
            frappe.throw(_("Password must be at least 8 characters long. Please choose a stronger password to secure your account."), frappe.ValidationError)

        password_errors = validate_password_strength(password)

        if password_errors:

            frappe.local.response["http_status_code"] = 400

            return {
                "success": False,
                "code": "WEAK_PASSWORD",
                "message": "Password does not meet security requirements.",
                "requirements": password_errors
            }
        
        # Validate phone number format if provided
        if phone:
            phone_cleaned = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if not phone_cleaned.isdigit() or len(phone_cleaned) < 10:
                frappe.throw(_("Please provide a valid phone number. The phone number should contain at least 10 digits."), frappe.ValidationError)
        
        # Validate POS industry if provided
        industry = None
        industry_name = None
        if pos_industry:
            # Try to find industry by code or name
            industry = frappe.db.get_value(
                "POS Industry",
                {
                    "is_active": 1,
                    "industry_code": pos_industry
                },
                "name"
            )
            
            if not industry:
                # Try by name
                industry = frappe.db.get_value(
                    "POS Industry",
                    {
                        "is_active": 1,
                        "industry_name": pos_industry
                    },
                    "name"
                )
            
            if not industry:
                # Try by name (case-insensitive)
                industries = frappe.get_all(
                    "POS Industry",
                    filters={"is_active": 1},
                    fields=["name", "industry_code", "industry_name"]
                )
                for ind in industries:
                    if ind.industry_name.lower() == pos_industry.lower() or ind.industry_code.lower() == pos_industry.lower():
                        industry = ind.name
                        break
            
            if not industry:
                frappe.throw(_("The industry '{0}' is not valid. Please select a valid industry from the available options.").format(pos_industry), frappe.ValidationError)
            
            # Get industry name for response
            industry_doc = frappe.get_doc("POS Industry", industry)
            industry_name = industry_doc.industry_name
        
        # Verify email if required
        if require_email_verification:
            if not email_verification_code:
                frappe.throw(_("Email verification code is required. Please enter the verification code sent to your email address."), frappe.ValidationError)
            
            try:
                from techsavanna_pos.api.verification_api import verify_email_code
                verify_result = verify_email_code(email, email_verification_code)
                if not verify_result.get("verified"):
                    frappe.throw(_("Email verification failed. The verification code is incorrect or has expired. Please request a new verification code."), frappe.ValidationError)
            except Exception as e:
                error_msg = str(e)
                if "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                    frappe.throw(_("Email verification failed. The verification code is incorrect or has expired. Please request a new verification code."), frappe.ValidationError)
                else:
                    frappe.throw(_("Email verification failed: {0}. Please try again or contact support if the problem persists.").format(error_msg), frappe.ValidationError)
        
        # Verify phone if required and provided
        if require_phone_verification and phone:
            if not phone_verification_code:
                frappe.throw(_("Phone verification code is required. Please enter the verification code sent to your phone number."), frappe.ValidationError)
            
            try:
                from techsavanna_pos.api.verification_api import verify_phone_code
                verify_result = verify_phone_code(phone, phone_verification_code)
                if not verify_result.get("verified"):
                    frappe.throw(_("Phone verification failed. The verification code is incorrect or has expired. Please request a new verification code."), frappe.ValidationError)
            except Exception as e:
                error_msg = str(e)
                if "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                    frappe.throw(_("Phone verification failed. The verification code is incorrect or has expired. Please request a new verification code."), frappe.ValidationError)
                else:
                    frappe.throw(_("Phone verification failed: {0}. Please try again or contact support if the problem persists.").format(error_msg), frappe.ValidationError)
        
        # Temporarily switch to Administrator to bypass permission checks
        original_user = frappe.session.user
        try:
            frappe.set_user("Administrator")
            
            # Create user
            user = frappe.new_doc("User")
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.mobile_no = phone
            user.enabled = 1
            user.send_welcome_email = 1 if send_welcome_email else 0
            
            # Set POS industry if provided
            if pos_industry and industry:
                user.custom_pos_industry = industry
            
            # Set user type
            user.user_type = "System User"
            
            # Assign all standard business roles BEFORE insert to ensure full permissions
            # This ensures registered users have all necessary permissions from the start
            for role in STANDARD_BUSINESS_ROLES:
                # Check if role exists before adding
                if frappe.db.exists("Role", role):
                    user.append("roles", {"role": role})
            
            # Insert user with all roles assigned
            user.insert(ignore_permissions=True)
            
            # Set password
            user.new_password = password
            user.save(ignore_permissions=True)
            
            # Double-check: Ensure all roles are assigned (in case any were missed)
            # This is a safety measure to ensure all permissions are granted
            assign_all_business_roles(user.name)
            
            # Generate API keys
            api_keys = generate_keys(user.name)
            
            # Generate OAuth Bearer Token
            oauth_token = generate_oauth_bearer_token(user.name)
        finally:
            # Restore original user
            frappe.set_user(original_user)
        
        # Set HTTP status code for successful creation
        frappe.local.response["http_status_code"] = 201
        
        # Check verification status
        email_verified = False
        phone_verified = False
        
        if require_email_verification:
            try:
                from techsavanna_pos.api.verification_api import check_verification_status
                email_status = check_verification_status(email, "email")
                email_verified = email_status.get("verified", False)
            except Exception:
                pass
        
        if require_phone_verification and phone:
            try:
                from techsavanna_pos.api.verification_api import check_verification_status
                phone_status = check_verification_status(phone, "phone")
                phone_verified = phone_status.get("verified", False)
            except Exception:
                pass
        
        response_data = {
            "user": {
                "name": user.name,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "full_name": user.full_name,
            },
            "api_key": api_keys.get("api_key"),
            "api_secret": api_keys.get("api_secret"),
            "access_token": oauth_token["access_token"],
            "token_type": oauth_token["token_type"],
            "expires_in": oauth_token["expires_in"],
            "expires_at": oauth_token["expires_at"],
            "refresh_token": oauth_token["refresh_token"],
            "email_verified": email_verified,
            "phone_verified": phone_verified if phone else None,
            "message": _("User registered successfully")
        }
        
        # Add industry information if set
        if pos_industry and industry_name:
            response_data["pos_industry"] = {
                "name": industry,
                "industry_name": industry_name
            }
        
        return response_data
    
    except frappe.AuthenticationError:
        raise

    except frappe.ValidationError as e:
        error_message = str(e)
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "code": "VALIDATION_ERROR",
            "message": error_message
        }
    except frappe.DuplicateEntryError as e:
        error_message = _("An account with this email address already exists. Please use a different email or sign in.")
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 409
        return {
            "success": False,
            "code": "EMAIL_EXISTS",
            "message": error_message
        }
    except frappe.MandatoryError as e:
        error_msg = str(e)
        if "email" in error_msg.lower():
            error_message = _("Email address is required. Please provide a valid email address to create your account.")
        elif "first_name" in error_msg.lower():
            error_message = _("First name is required. Please provide your first name to create your account.")
        elif "last_name" in error_msg.lower():
            error_message = _("Last name is required. Please provide your last name to create your account.")
        elif "password" in error_msg.lower():
            error_message = _("Password is required. Please provide a secure password for your account.")
        else:
            error_message = _("Some required information is missing. Please fill in all required fields.")
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "code": "MANDATORY_FIELD_MISSING",
            "message": error_message
        }
    except frappe.PermissionError as e:
        frappe.throw(
            _("An error occurred during registration. Please contact support if this problem persists."),
            frappe.PermissionError
        )
    except Exception as e:
        frappe.log_error(
            "User Registration Error",
            frappe.get_traceback()
        )

        error = str(e).lower()

        if "duplicate" in error or "already exists" in error:
            message = _(
                "An account with this email already exists. Please sign in instead."
            )

        # Weak password from Frappe password policy
        elif "password" in error:
            message = _(
                "Your password is too weak. Use at least 8 characters with uppercase, lowercase, numbers and a special character."
            )

        # Invalid email
        elif "email" in error:
            message = _(
                "Please enter a valid email address."
            )

        else:
            message = _(
                "We couldn't create your account right now. Please try again in a few minutes."
            )

        # Prevent Frappe popup messages from leaking to frontend
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []

        frappe.local.response["http_status_code"] = 500

        return {
            "success": False,
            "code": "SERVER_ERROR",
            "message": message
        }


@frappe.whitelist(allow_guest=True)
def login_user(email: str, password: str) -> Dict:
    """Login user and generate OAuth Bearer Token
    
    Args:
        email: User email
        password: User password
        
    Returns:
        User details and OAuth Bearer Token
    """
    # Authenticate user
    user = frappe.auth.LoginManager()
    user.authenticate(user=email, pwd=password)
    
    print(f'\nUser : {user}\n')
    
    if not user.user:
        frappe.throw(_("Invalid email or password"), frappe.AuthenticationError)
    
    # Check if user is enabled
    if not frappe.db.get_value("User", user.user, "enabled"):
        frappe.throw(_("User account is disabled"), frappe.AuthenticationError)
    
    # Get or generate API keys
    api_key = frappe.db.get_value("User", user.user, "api_key")
    if not api_key:
        api_keys = generate_keys(user.user)
        api_key = api_keys.get("api_key")
    
    # Generate OAuth Bearer Token
    oauth_token = generate_oauth_bearer_token(user.user)
    
    # Get user details
    user_doc = frappe.get_doc("User", user.user)
    
    # Set HTTP status code for successful login
    frappe.local.response["http_status_code"] = 200
    
    return {
        "user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "first_name": user_doc.first_name,
            "last_name": user_doc.last_name,
            "full_name": user_doc.full_name,
        },
        "api_key": api_key,
        "access_token": oauth_token["access_token"],
        "token_type": oauth_token["token_type"],
        "expires_in": oauth_token["expires_in"],
        "expires_at": oauth_token["expires_at"],
        "refresh_token": oauth_token["refresh_token"],
        "message": _("Login successful")
    }


@frappe.whitelist()
def get_current_user() -> Dict:
    """Get current authenticated user details with company/business information, roles, and permissions
    
    Returns:
        dict: Current user details including:
            - user: User basic information (name, email, first_name, last_name, full_name, mobile_no)
            - company: Company information if available
            - pos_profile: POS Profile information if available
            - pos_industry: POS Industry information if available
            - roles: List of user roles
            - permissions: Dictionary of user permissions organized by doctype
            - business_capabilities: Object containing RBAC structure for frontend:
                - list: Array of capabilities with actions (for iteration/menu rendering)
                - map: Object mapping capability -> actions array (for quick lookups)
                - permissions: Flattened object with "capability:action" keys (for direct permission checks)
            - default_warehouse: Default warehouse if available
    """
    user = frappe.session.user
    
    if user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    user_doc = frappe.get_doc("User", user)
    
    # Get user's default company
    company = frappe.defaults.get_user_default("Company")
    
    # If no default company, check if user is a staff user with custom_company
    if not company:
        company = frappe.db.get_value("User", user, "custom_company")
    
    company_info = None
    default_warehouse = None
    # Only assigned inside `if company:` below, but always returned — so a
    # user with no company yet (every new owner, before onboarding) got
    # UnboundLocalError and a 500 instead of their profile.
    pos_profile = None
    
    if company:
        # Get company details
        company_doc = frappe.get_doc("Company", company)
        
        # Get company address (primary or first available)
        company_address = None
        try:
            # Try to get primary address first
            from erpnext.setup.doctype.company.company import get_default_company_address
            address_name = get_default_company_address(company, sort_key="is_primary_address")
            
            if not address_name:
                # Fallback to any address linked to company
                addresses = frappe.get_all(
                    "Dynamic Link",
                    filters={
                        "link_doctype": "Company",
                        "link_name": company,
                        "parenttype": "Address"
                    },
                    fields=["parent"],
                    limit=1
                )
                if addresses:
                    address_name = addresses[0].parent
            
            if address_name:
                address_doc = frappe.get_doc("Address", address_name)
                company_address = {
                    "address_line1": address_doc.address_line1,
                    "address_line2": address_doc.address_line2,
                    "city": address_doc.city,
                    "state": address_doc.state,
                    "country": address_doc.country,
                    "pincode": address_doc.pincode,
                    "phone": address_doc.phone,
                    "email_id": address_doc.email_id
                }
        except Exception:
            # If address retrieval fails, continue without address
            pass
        
        # Get default warehouse for the company
        try:
            from techsavanna_pos.api.warehouse_api import get_default_warehouse_for_company
            default_warehouse = get_default_warehouse_for_company(company)
        except Exception:
            # If default warehouse retrieval fails, continue without it
            pass
        
        company_info = {
            "name": company_doc.name,
            "company_name": company_doc.company_name,
            "abbr": company_doc.abbr,
            "default_currency": company_doc.default_currency,
            "country": company_doc.country,
            "tax_id": company_doc.tax_id,
            "phone_no": company_doc.phone_no,
            "email": company_doc.email,
            "website": company_doc.website,
            "address": company_address
        }
        
        # Get POS Profile for the company
        pos_profile = None
        try:
            # Try to get existing POS Profile for the company
            pos_profile_name = frappe.db.get_value(
                "POS Profile",
                {"company": company, "disabled": 0},
                "name",
                order_by="creation desc"
            )
            
            if pos_profile_name:
                pos_profile_doc = frappe.get_doc("POS Profile", pos_profile_name)
                pos_profile = {
                    "name": pos_profile_doc.name,
                    "company": pos_profile_doc.company,
                    "warehouse": pos_profile_doc.warehouse,
                    "currency": pos_profile_doc.currency,
                    "customer": pos_profile_doc.customer,
                }
            else:
                # If no POS Profile exists, create one dynamically
                from techsavanna_pos.api.sales_api import _get_or_create_pos_profile
                pos_profile_name = _get_or_create_pos_profile(company)
                if pos_profile_name:
                    pos_profile_doc = frappe.get_doc("POS Profile", pos_profile_name)
                    pos_profile = {
                        "name": pos_profile_doc.name,
                        "company": pos_profile_doc.company,
                        "warehouse": pos_profile_doc.warehouse,
                        "currency": pos_profile_doc.currency,
                        "customer": pos_profile_doc.customer,
                    }
        except Exception as e:
            # If POS Profile creation fails, log but don't fail the request
            frappe.log_error("Get User Profile", f"Error getting POS Profile for user profile: {str(e)}")
    
    # Set HTTP status code for successful retrieval
    frappe.local.response["http_status_code"] = 200
    
    # Get user's POS industry
    pos_industry_info = None
    try:
        user_industry = frappe.db.get_value("User", user, "custom_pos_industry")
        if user_industry:
            try:
                industry_doc = frappe.get_doc("POS Industry", user_industry)
                pos_industry_info = {
                    "name": industry_doc.name,
                    "industry_code": industry_doc.industry_code,
                    "industry_name": industry_doc.industry_name,
                    "description": industry_doc.description,
                    "serving_location": industry_doc.serving_location
                }
            except Exception:
                pass
    except Exception as e:
        # Handle case where custom_pos_industry column doesn't exist in database
        # This can happen if custom fields haven't been migrated yet
        # Log short error message to avoid CharacterLengthExceededError
        error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
        frappe.log_error(
            "Get User Profile - POS Industry",
            f"POS industry field missing: {error_msg}"
        )
        # Continue without industry info - field will be None
    
    # Get user roles
    roles = []
    try:
        roles = frappe.get_roles(user)
        # Filter out automatic/system roles if needed (optional - you can keep them)
        # from frappe.permissions import AUTOMATIC_ROLES
        # roles = [r for r in roles if r not in AUTOMATIC_ROLES]
    except Exception as e:
        frappe.log_error("Get User Roles", f"Error getting user roles: {str(e)}")
    
    # Get user permissions
    user_permissions = {}
    try:
        from frappe.core.doctype.user_permission.user_permission import get_user_permissions
        user_permissions = get_user_permissions(user)
        # Convert to a more frontend-friendly format
        # user_permissions is a dict like: {"Customer": [{"doc": "Customer A", ...}], ...}
        permissions_dict = {}
        for doctype, permission_list in user_permissions.items():
            permissions_dict[doctype] = [
                {
                    "doc": perm.get("doc"),
                    "applicable_for": perm.get("applicable_for"),
                    "is_default": perm.get("is_default"),
                    "hide_descendants": perm.get("hide_descendants"),
                }
                for perm in permission_list
            ]
        user_permissions = permissions_dict
    except Exception as e:
        frappe.log_error("Get User Permissions", f"Error getting user permissions: {str(e)}")
    
    # Get business capabilities based on doctype permissions
    business_capabilities = {}
    try:
        from frappe.permissions import get_valid_perms
        
        # Mapping of doctypes to business capabilities
        # Each capability can have multiple doctypes, and actions are derived from doctype permissions
        DOCTYPE_TO_CAPABILITY_MAP = {
            # Customer Management
            "Customer": "manage_customers",
            "Customer Group": "manage_customers",
            "Contact": "manage_customers",
            "Address": "manage_customers",
            
            # Product/Item Management
            "Item": "manage_products",
            "Item Group": "manage_products",
            "Item Price": "manage_products",
            "Item Attribute": "manage_products",
            "Item Attribute Value": "manage_products",
            "Brand": "manage_products",
            
            # Sales Management
            "Sales Invoice": "process_sales",
            "Sales Order": "process_sales",
            "Quotation": "process_sales",
            "POS Invoice": "process_sales",
            "POS Profile": "process_sales",
            "Delivery Note": "process_sales",
            
            # Purchase Management
            "Purchase Invoice": "manage_purchases",
            "Purchase Order": "manage_purchases",
            "Purchase Receipt": "manage_purchases",
            "Supplier": "manage_purchases",
            "Supplier Group": "manage_purchases",
            
            # Inventory/Stock Management
            "Stock Entry": "manage_inventory",
            "Stock Ledger Entry": "manage_inventory",
            "Bin": "manage_inventory",
            "Warehouse": "manage_inventory",
            "Material Request": "manage_inventory",
            "Delivery Note": "manage_inventory",
            
            # Financial Management
            "Payment Entry": "manage_finances",
            "Journal Entry": "manage_finances",
            "Account": "manage_finances",
            "Cost Center": "manage_finances",
            "Fiscal Year": "manage_finances",
            "GL Entry": "manage_finances",
            
            # Reports & Analytics
            "Report": "view_reports",
            "Dashboard": "view_reports",
            
            # User & Staff Management
            "User": "manage_users",
            "Role": "manage_users",
            "Role Profile": "manage_users",
            "Employee": "manage_staff",
            
            # Company & Setup
            "Company": "manage_company",
            "Branch": "manage_company",
            "Department": "manage_company",
            
            # Settings & Configuration
            "Print Format": "manage_settings",
            "Custom Field": "manage_settings",
            "Property Setter": "manage_settings",
        }
        
        # Get all valid permissions for the user
        valid_perms = get_valid_perms(user=user)
        
        # List of standard permission rights mapped to actions
        # These are the actions that can be performed based on doctype permissions
        permission_to_action_map = {
            "read": "view",
            "write": "edit",
            "create": "create",
            "delete": "delete",
            "submit": "submit",
            "cancel": "cancel",
            "amend": "amend",
            "print": "print",
            "export": "export",
            "import": "import",
            "email": "email",
            "report": "report",
        }
        
        # Group permissions by capability
        for perm in valid_perms:
            doctype = perm.get("parent")
            if not doctype:
                continue
            
            # Get the capability for this doctype
            capability = DOCTYPE_TO_CAPABILITY_MAP.get(doctype)
            if not capability:
                # Skip doctypes that don't map to a business capability
                continue
            
            # Initialize capability if not exists
            if capability not in business_capabilities:
                business_capabilities[capability] = {
                    "capability": capability,
                    "actions": set()  # Use set to avoid duplicates
                }
            
            # Map permission rights to actions
            for perm_right, action in permission_to_action_map.items():
                if perm.get(perm_right):
                    business_capabilities[capability]["actions"].add(action)
        
        # Build frontend-friendly RBAC structure
        # 1. List format for iteration (e.g., menu rendering)
        business_capabilities_list = []
        # 2. Object format for O(1) lookups (e.g., permission checks)
        business_capabilities_map = {}
        # 3. Flattened permissions for easy checking (e.g., "manage_customers:create" -> true)
        business_capabilities_permissions = {}
        
        for capability, cap_data in business_capabilities.items():
            # Sort actions for consistent output
            actions = sorted(list(cap_data["actions"]))
            if actions:  # Only include capabilities with at least one action
                # List format
                business_capabilities_list.append({
                    "capability": capability,
                    "actions": actions
                })
                
                # Map format (capability -> actions array)
                business_capabilities_map[capability] = actions
                
                # Flattened permissions format (capability:action -> true)
                for action in actions:
                    permission_key = f"{capability}:{action}"
                    business_capabilities_permissions[permission_key] = True
        
        # Sort capabilities alphabetically for consistent output
        business_capabilities_list.sort(key=lambda x: x["capability"])
        
        # Combine into final structure optimized for frontend RBAC
        business_capabilities = {
            # List format - use for: menu rendering, capability listing, iteration
            "list": business_capabilities_list,
            # Map format - use for: quick capability lookup with O(1) access
            # Example: permissions.map.manage_customers?.includes('create')
            "map": business_capabilities_map,
            # Flattened format - use for: direct permission checks, route guards
            # Example: permissions.check('manage_customers:create') or permissions['manage_customers:create']
            "permissions": business_capabilities_permissions
        }
    except Exception as e:
        frappe.log_error("Get Business Capabilities", f"Error getting business capabilities: {str(e)}")
        business_capabilities = {
            "list": [],
            "map": {},
            "permissions": {}
        }
    
    response = {
        "user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "first_name": user_doc.first_name,
            "last_name": user_doc.last_name,
            "full_name": user_doc.full_name,
            "mobile_no": user_doc.mobile_no,
        },
        "company": company_info,
        "pos_profile": pos_profile,
        "pos_industry": pos_industry_info,
        "roles": roles,
        "permissions": user_permissions,
        "business_capabilities": business_capabilities
    }
    
    # Add default_warehouse if available
    if default_warehouse:
        response["default_warehouse"] = default_warehouse
    
    return response


@frappe.whitelist()
def refresh_token() -> Dict:
    """Refresh OAuth Bearer Token for current user
    
    Returns:
        New OAuth Bearer Token
    """
    user = frappe.session.user
    
    if user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Generate new OAuth Bearer Token
    oauth_token = generate_oauth_bearer_token(user)
    
    # Set HTTP status code for successful token refresh
    frappe.local.response["http_status_code"] = 200
    
    return {
        "access_token": oauth_token["access_token"],
        "token_type": oauth_token["token_type"],
        "expires_in": oauth_token["expires_in"],
        "expires_at": oauth_token["expires_at"],
        "refresh_token": oauth_token["refresh_token"],
        "message": _("Token refreshed successfully")
    }


@frappe.whitelist()
def update_user_profile(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None
) -> Dict:
    """Update user profile
    
    Args:
        first_name: Updated first name
        last_name: Updated last name
        phone: Updated phone number
        
    Returns:
        Updated user details
    """
    user = frappe.session.user
    
    if user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    user_doc = frappe.get_doc("User", user)
    
    if first_name:
        user_doc.first_name = first_name
    if last_name:
        user_doc.last_name = last_name
    if phone:
        user_doc.mobile_no = phone
    
    user_doc.save(ignore_permissions=True)
    
    # Set HTTP status code for successful update
    frappe.local.response["http_status_code"] = 200
    
    return {
        "user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "first_name": user_doc.first_name,
            "last_name": user_doc.last_name,
            "full_name": user_doc.full_name,
        },
        "message": _("Profile updated successfully")
    }


@frappe.whitelist()
def grant_all_permissions() -> Dict:
    """Grant all standard business roles and permissions to current user
    
    This function can be called to ensure the current user has full access
    to all standard ERPNext features and operations.
    
    Returns:
        Success message with assigned roles
    """
    user = frappe.session.user
    
    if user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    try:
        assign_all_business_roles(user)
        
        # Get updated user roles
        user_doc = frappe.get_doc("User", user)
        assigned_roles = [r.role for r in user_doc.roles if r.role in STANDARD_BUSINESS_ROLES]
        
        return {
            "success": True,
            "message": _("All business permissions granted successfully"),
            "roles": assigned_roles,
            "user": user
        }
    except Exception as e:
        frappe.log_error("Grant Permissions Error", f"Error granting permissions to user {user}: {str(e)}")
        frappe.throw(
            _("An error occurred while granting permissions. Please contact support."),
            frappe.ValidationError
        )


@frappe.whitelist()
def change_password(old_password: str, new_password: str) -> Dict:
    """Change user password
    
    Args:
        old_password: Current password
        new_password: New password
        
    Returns:
        Success message
    """
    user = frappe.session.user
    
    if user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate password strength
    if len(new_password) < 8:
        frappe.throw(_("Password must be at least 8 characters long"))
    
    # Verify old password
    frappe.auth.LoginManager().authenticate(user=user, pwd=old_password)
    
    # Update password
    user_doc = frappe.get_doc("User", user)
    user_doc.new_password = new_password
    user_doc.save(ignore_permissions=True)
    
    # Set HTTP status code for successful password change
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Password changed successfully")
    }





@frappe.whitelist(allow_guest=True)
def loginuser(identifier: str, password: str):
    """
    Login user using email or username and generate OAuth Bearer Token.
    Does not generate API Key - User needs to be system manager
    Returns clean JSON on success or failure.
    """

    try:
        # Find user by username or email
        user_name = frappe.db.get_value(
            "User",
            {"enabled": 1, "name": identifier},
            "name"
        )

        if not user_name:
            user_name = frappe.db.get_value(
                "User",
                {"enabled": 1, "email": identifier},
                "name"
            )

        if not user_name:
            return {
                "success": False,
                "message": _("Invalid login credentials or user disabled")
            }

        # Authenticate password
        login_manager = frappe.auth.LoginManager()
        login_manager.authenticate(user=user_name, pwd=password)

        # Get existing API key (do not generate new key)
        api_key = frappe.db.get_value("User", user_name, "api_key")

        # Generate OAuth token
        try:
            oauth_token = frappe.get_attr("techsavanna_pos.api.auth_api.generate_oauth_bearer_token")(user_name)
        except Exception:
            oauth_token = {}

        # Get user details
        user_doc = frappe.get_doc("User", user_name)

        frappe.local.response["http_status_code"] = 200

        return {
            "success": True,
            "message": _("Login successful"),
            "user": {
                "name": user_doc.name,
                "email": user_doc.email,
                "first_name": user_doc.first_name,
                "last_name": user_doc.last_name,
                "full_name": user_doc.full_name
            },
            "api_key": api_key,
            "access_token": oauth_token.get("access_token"),
            "token_type": oauth_token.get("token_type"),
            "expires_in": oauth_token.get("expires_in"),
            "expires_at": oauth_token.get("expires_at"),
            "refresh_token": oauth_token.get("refresh_token")
        }

    except frappe.AuthenticationError:
        return {
            "success": False,
            "message": _("Authentication failed. Please check your credentials or API key."),
            "error_code": "AUTH_ERROR"
        }

    except frappe.PermissionError:
        return {
            "success": False,
            "message": _("You do not have permission to perform this action."),
            "error_code": "PERMISSION_ERROR"
        }

    except Exception as e:
        return {
            "success": False,
            "message": _("An unexpected error occurred."),
            "error": str(e)
        }
