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
                        f"Role '{role}' does not exist in the system. Skipping assignment.",
                        "Role Assignment Warning"
                    )
        
        # Only save if roles were added to avoid unnecessary database writes
        if roles_added:
            user_doc.save(ignore_permissions=True)
            frappe.db.commit()
            
    except Exception as e:
        frappe.log_error(
            f"Error assigning roles to user {user_email}: {str(e)}",
            "Assign Business Roles Error"
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
    """Register a new user"""
    try:
        # Validate required fields
        if not email or not email.strip():
            frappe.throw(_("Please provide a valid email address to create your account."), frappe.ValidationError)
        
        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            frappe.throw(_("The email address you provided is not in the correct format. Please use an email like 'name@example.com'."), frappe.ValidationError)
        
        if not first_name or not first_name.strip():
            frappe.throw(_("First name is required. Please provide your first name to proceed."), frappe.ValidationError)
        
        if not last_name or not last_name.strip():
            frappe.throw(_("Last name is required. Please provide your last name to complete your registration."), frappe.ValidationError)
        
        if not password or not password.strip():
            frappe.throw(_("A password is required for your account. Please choose a secure password."), frappe.ValidationError)
        
        # Validate email availability
        if frappe.db.exists("User", email):
            frappe.throw(_("An account with the email '{0}' already exists. Please use a different email or log in.".format(email)), frappe.ValidationError)
        
        # Validate password strength
        if len(password) < 8:
            frappe.throw(_("Your password must be at least 8 characters long. Please choose a stronger password."), frappe.ValidationError)
        
        # Validate phone number format if provided
        if phone:
            phone_cleaned = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if not phone_cleaned.isdigit() or len(phone_cleaned) < 10:
                frappe.throw(_("Please provide a valid phone number with at least 10 digits."), frappe.ValidationError)
        
        # Validate POS industry if provided
        industry = None
        industry_name = None
        if pos_industry:
            industry = frappe.db.get_value(
                "POS Industry", 
                {"is_active": 1, "industry_code": pos_industry}, 
                "name"
            )
            if not industry:
                industry = frappe.db.get_value(
                    "POS Industry", 
                    {"is_active": 1, "industry_name": pos_industry}, 
                    "name"
                )
            if not industry:
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
                frappe.throw(_("The industry '{0}' you provided does not exist or is inactive. Please select a valid industry.".format(pos_industry)), frappe.ValidationError)
            
            industry_doc = frappe.get_doc("POS Industry", industry)
            industry_name = industry_doc.industry_name
        
        # Email verification if required
        if require_email_verification:
            if not email_verification_code:
                frappe.throw(_("Please enter the email verification code sent to your email address."), frappe.ValidationError)
            
            try:
                from techsavanna_pos.api.verification_api import verify_email_code
                verify_result = verify_email_code(email, email_verification_code)
                if not verify_result.get("verified"):
                    frappe.throw(_("Email verification failed. The code you entered is incorrect or expired. Please try again or request a new code."), frappe.ValidationError)
            except Exception as e:
                error_msg = str(e)
                frappe.throw(_("Email verification failed: {0}.".format(error_msg)), frappe.ValidationError)
        
        # Phone verification if required
        if require_phone_verification and phone:
            if not phone_verification_code:
                frappe.throw(_("Please enter the phone verification code sent to your phone."), frappe.ValidationError)
            
            try:
                from techsavanna_pos.api.verification_api import verify_phone_code
                verify_result = verify_phone_code(phone, phone_verification_code)
                if not verify_result.get("verified"):
                    frappe.throw(_("Phone verification failed. The code you entered is incorrect or expired."), frappe.ValidationError)
            except Exception as e:
                error_msg = str(e)
                frappe.throw(_("Phone verification failed: {0}.".format(error_msg)), frappe.ValidationError)
        
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
            
            if pos_industry and industry:
                user.custom_pos_industry = industry
            user.user_type = "System User"
            
            for role in STANDARD_BUSINESS_ROLES:
                if frappe.db.exists("Role", role):
                    user.append("roles", {"role": role})
            
            user.insert(ignore_permissions=True)
            
            # Set password
            user.new_password = password
            user.save(ignore_permissions=True)
            
            # Double-check roles
            assign_all_business_roles(user.name)
            
            # Generate API keys
            api_keys = generate_keys(user.name)
            
            # Generate OAuth Bearer Token
            oauth_token = generate_oauth_bearer_token(user.name)
        finally:
            frappe.set_user(original_user)
        
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
    
    except frappe.ValidationError as e:
        # Return user-friendly validation error
        return {
            "error": str(e),
            "error_type": "ValidationError"
        }
    except frappe.DuplicateEntryError as e:
        return {
            "error": _("An account with this email already exists. Please use a different email address or log in."),
            "error_type": "DuplicateEntryError"
        }
    except Exception as e:
        # Catch-all for unexpected errors
        frappe.log_error(f"Error registering user '{email}': {str(e)}", "User Registration Error")
        return {
            "error": _("Something went wrong while creating your account. Please try again later or contact support."),
            "error_type": "Exception"
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
            frappe.log_error(f"Error getting POS Profile for user profile: {str(e)}", "Get User Profile")
    
    # Set HTTP status code for successful retrieval
    frappe.local.response["http_status_code"] = 200
    
    # Get user's POS industry
    pos_industry_info = None
    try:
        user_industry = frappe.db.get_value("User", user, "custom_pos_industry")
    except Exception as e:
        # Handle case where custom_pos_industry column doesn't exist in database
        # Log short error message to avoid CharacterLengthExceededError
        error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
        frappe.log_error(
            f"POS industry field missing: {error_msg}",
            "Get User Profile - POS Industry"
        )
        user_industry = None
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
    
    # Get user roles
    roles = []
    try:
        roles = frappe.get_roles(user)
        # Filter out automatic/system roles if needed (optional - you can keep them)
        # from frappe.permissions import AUTOMATIC_ROLES
        # roles = [r for r in roles if r not in AUTOMATIC_ROLES]
    except Exception as e:
        frappe.log_error(f"Error getting user roles: {str(e)}", "Get User Roles")
    
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
        frappe.log_error(f"Error getting user permissions: {str(e)}", "Get User Permissions")
    
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
        "permissions": user_permissions
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
        frappe.log_error(f"Error granting permissions to user {user}: {str(e)}", "Grant Permissions Error")
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
