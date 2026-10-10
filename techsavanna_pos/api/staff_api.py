"""
Staff Management API
Handles staff user creation, role assignment, and management with company isolation
"""

import re

import frappe
from frappe import _
from frappe.core.doctype.user.user import generate_keys
from frappe.permissions import AUTOMATIC_ROLES
from frappe.query_builder import DocType

from techsavanna_pos.api.access_control import require_manager, require_roles_held
from techsavanna_pos.api.payment_gateway_common import resolve_company


@frappe.whitelist()
def get_all_roles() -> dict:
    """Get all available roles that can be assigned to staff
    
    Returns:
        List of available roles
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Get active domains
    active_domains = frappe.get_active_domains()
    
    Role = DocType("Role")
    
    # Build domain condition
    domain_condition = (Role.restrict_to_domain.isnull()) | (Role.restrict_to_domain == "")
    if active_domains:
        domain_condition = domain_condition | Role.restrict_to_domain.isin(active_domains)
    
    # Get all non-automatic, enabled roles
    roles = (
        frappe.qb.from_(Role)
        .select(Role.name, Role.role_name)
        .where(
            (Role.name.notin(AUTOMATIC_ROLES)) & 
            (Role.disabled == 0) & 
            domain_condition
        )
        .orderby(Role.name)
        .run(as_dict=True)
    )
    
    # Format response
    role_list = [
        {
            "name": role.get("name"),
            "label": role.get("role_name") or role.get("name")
        }
        for role in roles
    ]
    
    # Set HTTP status code for successful retrieval
    frappe.local.response["http_status_code"] = 200
    
    return {
        "roles": role_list,
        "count": len(role_list)
    }


@frappe.whitelist()
def create_staff_user(
    email: str,
    first_name: str,
    last_name: str,
    password: str,
    roles: list = None,
    company: str = None,
    phone: str = None,
    send_welcome_email: bool = False,
    enabled: bool = True
) -> dict:
    """Create a new staff user and assign roles
    
    Staff users are isolated to the company of the creator.
    
    Args:
        email: Staff user email (used as username)
        first_name: Staff user's first name
        last_name: Staff user's last name
        password: Staff user password
        roles: List of role names to assign
        company: Company to restrict user to (default: current user's company)
        phone: Optional phone number
        send_welcome_email: Whether to send welcome email
        enabled: Whether user is enabled
        
    Returns:
        Created staff user details
    """
    # Creating a user with roles is the most powerful thing this API does: owners only
    require_manager()
    try:
        # Validate user permissions
        if frappe.session.user == "Guest":
            frappe.throw(_("Please log in to create a staff member. Your session has expired or you are not authenticated."), frappe.AuthenticationError)
        
        # Validate required fields
        if not email or not email.strip():
            frappe.throw(_("Email address is required. Please provide a valid email address for the staff member."), frappe.ValidationError)
        
        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            frappe.throw(_("Please provide a valid email address. The email '{0}' is not in the correct format.").format(email), frappe.ValidationError)
        
        if not first_name or not first_name.strip():
            frappe.throw(_("First name is required. Please provide the staff member's first name."), frappe.ValidationError)
        
        if not last_name or not last_name.strip():
            frappe.throw(_("Last name is required. Please provide the staff member's last name."), frappe.ValidationError)
        
        if not password or not password.strip():
            frappe.throw(_("Password is required. Please provide a secure password for the staff member."), frappe.ValidationError)
        
        # Get creator's company if not provided
        # Staff users inherit company from creator, so use helper function
        if not company:
            try:
                company = get_current_user_company()
            except Exception:
                frappe.throw(_("Company is required. Please set a default company in your profile settings or provide the company parameter when creating the staff member."), frappe.ValidationError)
        
        # Validate company exists
        if not frappe.db.exists("Company", company):
            frappe.throw(_("The company '{0}' does not exist. Please check the company name and try again, or contact your administrator if you believe this is an error.").format(company), frappe.ValidationError)
        
        # The company must be one the caller belongs to: naming another business's company is refused
        company = resolve_company(company)

        # Check if user already exists
        if frappe.db.exists("User", email):
            frappe.throw(_("A user with the email '{0}' already exists. Please use a different email address or contact your administrator to reset the existing account.").format(email), frappe.ValidationError)
        
        # Validate password strength
        if len(password) < 8:
            frappe.throw(_("Password must be at least 8 characters long. Please choose a stronger password with at least 8 characters."), frappe.ValidationError)
        
        # Validate phone number format if provided
        if phone:
            phone_cleaned = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if not phone_cleaned.isdigit() or len(phone_cleaned) < 10:
                frappe.throw(_("Please provide a valid phone number. The phone number should contain at least 10 digits."), frappe.ValidationError)
        
        # Validate roles
        if roles:
            require_roles_held(roles)
            valid_roles = validate_roles(roles)
            if not valid_roles:
                invalid_roles = [r for r in roles if not frappe.db.exists("Role", r) or frappe.db.get_value("Role", r, "disabled")]
                if invalid_roles:
                    frappe.throw(_("The following roles are invalid or disabled: {0}. Please select valid roles from the available list.").format(", ".join(invalid_roles)), frappe.ValidationError)
                else:
                    frappe.throw(_("One or more selected roles are invalid. Please select valid roles from the available list."), frappe.ValidationError)
        
        # Create staff user
        staff_user = frappe.new_doc("User")
        staff_user.email = email
        staff_user.first_name = first_name
        staff_user.last_name = last_name
        staff_user.mobile_no = phone
        staff_user.enabled = 1 if enabled else 0
        staff_user.send_welcome_email = 1 if send_welcome_email else 0
        staff_user.user_type = "System User"
        staff_user.new_password = password
        staff_user.insert(ignore_permissions=True)
        
        # Assign roles
        if roles:
            assign_roles_to_user(staff_user.name, roles)
        
        # Set company restriction via User Permission
        set_company_permission(staff_user.name, company, frappe.session.user)
        
        # Store creator and company relationship
        frappe.db.set_value("User", staff_user.name, "custom_created_by", frappe.session.user)
        frappe.db.set_value("User", staff_user.name, "custom_company", company)
        
        # Set as default company for the new user
        # This prevents silent fallback to global default company
        frappe.defaults.set_user_default("company", company, staff_user.name)
        
        # Inherit industry from parent (creator)
        try:
            parent_industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
            if parent_industry:
                frappe.db.set_value("User", staff_user.name, "custom_pos_industry", parent_industry)
        except Exception as e:
            # Handle case where custom_pos_industry column doesn't exist in database
            # Log short error message to avoid CharacterLengthExceededError
            error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
            frappe.log_error(
                "Create Staff User - POS Industry",
                f"POS industry field missing: {error_msg}"
            )
            parent_industry = None
        
        # Generate API keys for staff user
        api_keys = generate_keys(staff_user.name)
        
        # Get the assigned industry for response
        try:
            assigned_industry = frappe.db.get_value("User", staff_user.name, "custom_pos_industry")
        except Exception as e:
            # Handle case where custom_pos_industry column doesn't exist in database
            # Log short error message to avoid CharacterLengthExceededError
            error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
            frappe.log_error(
                "Create Staff User - Get Assigned Industry",
                f"POS industry field missing: {error_msg}"
            )
            assigned_industry = None
        
        # Set HTTP status code for successful creation
        frappe.local.response["http_status_code"] = 201
        
        return {
            "staff_user": {
                "name": staff_user.name,
                "email": staff_user.email,
                "first_name": staff_user.first_name,
                "last_name": staff_user.last_name,
                "full_name": staff_user.full_name,
                "enabled": staff_user.enabled,
                "company": company,
                "industry": assigned_industry,
                "roles": roles or []
            },
            "api_key": api_keys.get("api_key"),
            "message": _("Staff user created successfully")
        }
    
    except frappe.AuthenticationError:
        # Re-raise authentication errors as-is
        raise
    except frappe.ValidationError as e:
        frappe.db.rollback()
        # Frappe's password-strength validator returns HTML-formatted feedback
        # (e.g. "<div class='alert...'>...</div>") - strip tags for a clean message
        error_message = re.sub(r'<[^>]+>', ' ', str(e))
        error_message = re.sub(r'\s+', ' ', error_message).strip()
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "code": "VALIDATION_ERROR",
            "message": error_message
        }
    except frappe.DuplicateEntryError as e:
        frappe.db.rollback()
        error_message = _("A staff member with this email address already exists. Please use a different email address or contact your administrator.")
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 409
        return {
            "success": False,
            "code": "EMAIL_EXISTS",
            "message": error_message
        }
    except frappe.MandatoryError as e:
        frappe.db.rollback()
        error_msg = str(e)
        if "email" in error_msg.lower():
            error_message = _("Email address is required. Please provide a valid email address for the staff member.")
        elif "first_name" in error_msg.lower():
            error_message = _("First name is required. Please provide the staff member's first name.")
        elif "last_name" in error_msg.lower():
            error_message = _("Last name is required. Please provide the staff member's last name.")
        elif "password" in error_msg.lower():
            error_message = _("Password is required. Please provide a secure password for the staff member.")
        else:
            error_message = _("Some required information is missing: {0}. Please fill in all required fields and try again.").format(error_msg)
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 400
        return {
            "success": False,
            "code": "MANDATORY_FIELD_MISSING",
            "message": error_message
        }
    except frappe.PermissionError as e:
        frappe.db.rollback()
        frappe.throw(
            _("You don't have permission to create staff members. Please contact your administrator to grant you the necessary permissions."),
            frappe.PermissionError
        )
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            "Staff User Creation Error",
            f"Error creating staff user '{email}': {frappe.get_traceback()}"
        )
        error_message = _("An error occurred while creating the staff member. Please check that all information is correct (email format, password strength, etc.) and try again. If the problem persists, contact support.")
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        frappe.local.response["http_status_code"] = 500
        return {
            "success": False,
            "code": "SERVER_ERROR",
            "message": error_message
        }
    except frappe.MandatoryError as e:
        # Handle missing mandatory fields - format as validation error
        error_msg = str(e)
        if "email" in error_msg.lower():
            error_message = _("Email address is required. Please provide a valid email address for the staff member.")
        elif "first_name" in error_msg.lower():
            error_message = _("First name is required. Please provide the staff member's first name.")
        elif "last_name" in error_msg.lower():
            error_message = _("Last name is required. Please provide the staff member's last name.")
        elif "password" in error_msg.lower():
            error_message = _("Password is required. Please provide a secure password for the staff member.")
        else:
            error_message = _("Some required information is missing: {0}. Please fill in all required fields and try again.").format(error_msg)
        
        # Clear message log to prevent complex _server_messages format
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        
        # Set HTTP status code for validation error
        frappe.local.response["http_status_code"] = 417
        
        # Return a simple, user-friendly error response
        return {
            "error": error_message,
            "error_type": "ValidationError"
        }
    except frappe.PermissionError as e:
        # Handle permission errors
        frappe.throw(
            _("You don't have permission to create staff members. Please contact your administrator to grant you the necessary permissions."),
            frappe.PermissionError
        )
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            "Staff User Creation Error",
            f"Error creating staff user '{email}': {frappe.get_traceback()}"
        )
        # Return user-friendly error message
        error_message = _("An error occurred while creating the staff member. Please check that all information is correct (email format, password strength, etc.) and try again. If the problem persists, contact support.")
        
        # Clear message log to prevent complex _server_messages format
        if hasattr(frappe.local, "message_log"):
            frappe.local.message_log = []
        
        # Set HTTP status code for validation error
        frappe.local.response["http_status_code"] = 417
        
        # Return a simple, user-friendly error response
        return {
            "error": error_message,
            "error_type": "ValidationError"
        }


@frappe.whitelist()
def assign_roles_to_staff(
    user_email: str,
    roles: list,
    replace_existing: bool = False
) -> dict:
    """Assign roles to a staff user
    
    Args:
        user_email: Staff user email
        roles: List of role names to assign
        replace_existing: Whether to replace existing roles (default: False, adds to existing)
        
    Returns:
        Updated user details with roles
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Only a business owner may manage staff
    require_manager()

    # Validate user exists
    if not frappe.db.exists("User", user_email):
        frappe.throw(_("User {0} does not exist").format(user_email))
    
    # Check if user is a staff user created by current user
    staff_company = frappe.db.get_value("User", user_email, "custom_company")
    creator = frappe.db.get_value("User", user_email, "custom_created_by")
    current_user_company = get_current_user_company()
    
    if not staff_company or staff_company != current_user_company:
        frappe.throw(_("You can only manage staff users from your company"))
    
    if creator and creator != frappe.session.user:
        # Allow if same company
        if staff_company == current_user_company:
            pass  # Same company, allow
        else:
            frappe.throw(_("You can only manage staff users from your company"))
    
    # Validate roles
    valid_roles = validate_roles(roles)
    if not valid_roles:
        frappe.throw(_("One or more roles are invalid"))
    require_roles_held(roles)
    
    # Get user document
    user_doc = frappe.get_doc("User", user_email)
    
    # Replace or add roles
    if replace_existing:
        # Get current roles list
        current_roles = user_doc.get("roles")
        
        # Remove all non-automatic roles (collect first to avoid modifying while iterating)
        roles_to_remove = []
        for role_obj in current_roles:
            if role_obj.role not in AUTOMATIC_ROLES:
                roles_to_remove.append(role_obj)
        
        # Remove the roles from the child table
        for role_obj in roles_to_remove:
            current_roles.remove(role_obj)
        
        # Add new roles (avoid duplicates)
        existing_role_names = {r.role for r in current_roles}
        for role in roles:
            if role not in existing_role_names:
                user_doc.append("roles", {"role": role})
                existing_role_names.add(role)  # Update to avoid duplicates in same call
        
        # Preserve company and creator relationship - ensure staff stays under original company and creator
        if staff_company:
            user_doc.custom_company = staff_company
        if creator:
            user_doc.custom_created_by = creator
        
        # Save the document with removed and added roles
        user_doc.save(ignore_permissions=True)
    else:
        # Add new roles (avoid duplicates)
        existing_roles = [r.role for r in user_doc.roles]
        new_roles = [r for r in roles if r not in existing_roles]
        if new_roles:
            assign_roles_to_user(user_email, new_roles)
    
    # Reload user to get updated roles
    user_doc.reload()
    user_roles = [r.role for r in user_doc.roles if r.role not in AUTOMATIC_ROLES]
    
    # Set HTTP status code for successful update
    frappe.local.response["http_status_code"] = 200
    
    return {
        "staff_user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "first_name": user_doc.first_name,
            "last_name": user_doc.last_name,
            "full_name": user_doc.full_name,
            "roles": user_roles
        },
        "message": _("Roles assigned successfully")
    }


@frappe.whitelist()
def get_staff_users(company: str = None, enabled_only: bool = False) -> dict:
    """Get all staff users for the current user's company
    
    Args:
        company: Ignored - always uses current user's company for security
        enabled_only: Whether to return only enabled users
        
    Returns:
        List of staff users
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Always use current user's company - ignore passed company parameter for security
    # Staff users inherit company from creator via custom_company field
    company = get_current_user_company()
    
    # Build filters
    filters = {
        "custom_company": company
    }
    
    if enabled_only:
        filters["enabled"] = 1
    
    # Get staff users
    try:
        staff_users = frappe.get_all(
            "User",
            filters=filters,
            fields=[
                "name",
                "email",
                "first_name",
                "last_name",
                "full_name",
                "enabled",
                "creation",
                "custom_created_by",
                "custom_company",
                "custom_pos_industry"
            ],
            order_by="creation desc"
        )
    except Exception as e:
        # Handle case where custom_pos_industry column doesn't exist in database
        # Try again without the custom_pos_industry field
        # Log short error message to avoid CharacterLengthExceededError
        error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
        frappe.log_error(
            "Get Staff Users - POS Industry",
            f"POS industry field missing: {error_msg}"
        )
        staff_users = frappe.get_all(
            "User",
            filters=filters,
            fields=[
                "name",
                "email",
                "first_name",
                "last_name",
                "full_name",
                "enabled",
                "creation",
                "custom_created_by",
                "custom_company"
            ],
            order_by="creation desc"
        )
        # Add None for custom_pos_industry to maintain consistency
        for user in staff_users:
            user["custom_pos_industry"] = None
    
    # Get roles for each user and rename industry field
    for user in staff_users:
        user_doc = frappe.get_doc("User", user.name)
        user["roles"] = [r.role for r in user_doc.roles if r.role not in AUTOMATIC_ROLES]
        # Rename custom_pos_industry to industry for consistency
        user["industry"] = user.pop("custom_pos_industry", None)
    
    # Set HTTP status code for successful retrieval
    frappe.local.response["http_status_code"] = 200
    
    return {
        "staff_users": staff_users,
        "count": len(staff_users),
        "company": company
    }


@frappe.whitelist()
def get_staff_user_details(user_email: str) -> dict:
    """Get detailed information about a staff user
    
    Args:
        user_email: Staff user email
        
    Returns:
        Staff user details with roles
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Validate user exists
    if not frappe.db.exists("User", user_email):
        frappe.throw(_("User {0} does not exist").format(user_email))
    
    # Check if user is a staff user from current user's company
    staff_company = frappe.db.get_value("User", user_email, "custom_company")
    current_user_company = get_current_user_company()
    
    if not staff_company or staff_company != current_user_company:
        frappe.throw(_("You can only view staff users from your company"))
    
    # Get user document
    user_doc = frappe.get_doc("User", user_email)
    
    # Get roles
    user_roles = [r.role for r in user_doc.roles if r.role not in AUTOMATIC_ROLES]
    
    # Get industry
    try:
        staff_industry = frappe.db.get_value("User", user_email, "custom_pos_industry")
    except Exception as e:
        # Handle case where custom_pos_industry column doesn't exist in database
        # Log short error message to avoid CharacterLengthExceededError
        error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
        frappe.log_error(
            "Get Staff User - POS Industry",
            f"POS industry field missing: {error_msg}"
        )
        staff_industry = None
    
    # Set HTTP status code for successful retrieval
    frappe.local.response["http_status_code"] = 200
    
    return {
        "staff_user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "first_name": user_doc.first_name,
            "last_name": user_doc.last_name,
            "full_name": user_doc.full_name,
            "mobile_no": user_doc.mobile_no,
            "enabled": user_doc.enabled,
            "company": staff_company,
            "industry": staff_industry,
            "created_by": frappe.db.get_value("User", user_email, "custom_created_by"),
            "creation": str(user_doc.creation),
            "roles": user_roles
        }
    }


@frappe.whitelist()
def update_staff_user(
    user_email: str,
    first_name: str = None,
    last_name: str = None,
    phone: str = None,
    enabled: bool = None
) -> dict:
    """Update staff user information
    
    Args:
        user_email: Staff user email
        first_name: Updated first name
        last_name: Updated last name
        phone: Updated phone number
        enabled: Updated enabled status
        
    Returns:
        Updated user details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Only a business owner may manage staff
    require_manager()

    # Validate user exists
    if not frappe.db.exists("User", user_email):
        frappe.throw(_("User {0} does not exist").format(user_email))
    
    # Check if user is a staff user from current user's company
    staff_company = frappe.db.get_value("User", user_email, "custom_company")
    staff_creator = frappe.db.get_value("User", user_email, "custom_created_by")
    current_user_company = get_current_user_company()
    
    if not staff_company or staff_company != current_user_company:
        frappe.throw(_("You can only update staff users from your company"))
    
    # Get user document
    user_doc = frappe.get_doc("User", user_email)
    
    # Update fields
    if first_name:
        user_doc.first_name = first_name
    if last_name:
        user_doc.last_name = last_name
    if phone:
        user_doc.mobile_no = phone
    if enabled is not None:
        user_doc.enabled = 1 if enabled else 0
    
    # Preserve company and creator relationship - ensure staff stays under original company and creator
    # This prevents staff from being defaulted to the default company
    if staff_company:
        user_doc.custom_company = staff_company
    if staff_creator:
        user_doc.custom_created_by = staff_creator
    
    user_doc.save(ignore_permissions=True)
    
    # Ensure company default is maintained for the staff user
    # This prevents silent fallback to global default company
    if staff_company:
        frappe.defaults.set_user_default("company", staff_company, user_email)
    
    # Set HTTP status code for successful update
    frappe.local.response["http_status_code"] = 200
    
    return {
        "staff_user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "first_name": user_doc.first_name,
            "last_name": user_doc.last_name,
            "full_name": user_doc.full_name,
            "enabled": user_doc.enabled
        },
        "message": _("Staff user updated successfully")
    }


@frappe.whitelist()
def remove_roles_from_staff(
    user_email: str,
    roles: list
) -> dict:
    """Remove roles from a staff user
    
    Args:
        user_email: Staff user email
        roles: List of role names to remove
        
    Returns:
        Updated user details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Only a business owner may manage staff
    require_manager()

    # Validate user exists
    if not frappe.db.exists("User", user_email):
        frappe.throw(_("User {0} does not exist").format(user_email))
    
    # Check if user is a staff user from current user's company
    staff_company = frappe.db.get_value("User", user_email, "custom_company")
    staff_creator = frappe.db.get_value("User", user_email, "custom_created_by")
    current_user_company = get_current_user_company()
    
    if not staff_company or staff_company != current_user_company:
        frappe.throw(_("You can only manage staff users from your company"))
    
    # Get user document
    user_doc = frappe.get_doc("User", user_email)
    
    # Remove roles
    user_doc.roles = [r for r in user_doc.roles if r.role not in roles]
    
    # Preserve company and creator relationship - ensure staff stays under original company and creator
    if staff_company:
        user_doc.custom_company = staff_company
    if staff_creator:
        user_doc.custom_created_by = staff_creator
    
    user_doc.save(ignore_permissions=True)
    
    # Get updated roles
    user_roles = [r.role for r in user_doc.roles if r.role not in AUTOMATIC_ROLES]
    
    # Set HTTP status code for successful update
    frappe.local.response["http_status_code"] = 200
    
    return {
        "staff_user": {
            "name": user_doc.name,
            "email": user_doc.email,
            "roles": user_roles
        },
        "message": _("Roles removed successfully")
    }


@frappe.whitelist()
def disable_staff_user(user_email: str) -> dict:
    """Disable a staff user (soft delete)
    
    Args:
        user_email: Staff user email
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Only a business owner may manage staff
    require_manager()

    # Validate user exists
    if not frappe.db.exists("User", user_email):
        frappe.throw(_("User {0} does not exist").format(user_email))
    
    # Check if user is a staff user from current user's company
    staff_company = frappe.db.get_value("User", user_email, "custom_company")
    current_user_company = get_current_user_company()
    
    if not staff_company or staff_company != current_user_company:
        frappe.throw(_("You can only manage staff users from your company"))
    
    # Disable user
    frappe.db.set_value("User", user_email, "enabled", 0)
    
    # Set HTTP status code for successful update
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Staff user disabled successfully")
    }


@frappe.whitelist()
def enable_staff_user(user_email: str) -> dict:
    """Enable a disabled staff user
    
    Args:
        user_email: Staff user email
        
    Returns:
        Success message
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    # Only a business owner may manage staff
    require_manager()

    # Validate user exists
    if not frappe.db.exists("User", user_email):
        frappe.throw(_("User {0} does not exist").format(user_email))
    
    # Check if user is a staff user from current user's company
    staff_company = frappe.db.get_value("User", user_email, "custom_company")
    current_user_company = get_current_user_company()
    
    if not staff_company or staff_company != current_user_company:
        frappe.throw(_("You can only manage staff users from your company"))
    
    # Enable user
    frappe.db.set_value("User", user_email, "enabled", 1)
    
    # Set HTTP status code for successful update
    frappe.local.response["http_status_code"] = 200
    
    return {
        "message": _("Staff user enabled successfully")
    }


# Helper functions

def get_current_user_company() -> str:
    """Get current user's company with fallback to custom_company field
    
    Staff users inherit company from creator via custom_company field.
    This function first tries user defaults, then falls back to custom_company.
    
    Returns:
        Company name
        
    Raises:
        ValidationError: If company cannot be determined
    """
    company = frappe.defaults.get_user_default("Company")
    if not company:
        # Staff users inherit company from creator via custom_company field
        company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        if not company:
            frappe.throw(_("Company is required. Please set a default company or ensure you have a company assigned."))
    return company


def validate_roles(roles: list) -> bool:
    """Validate that all roles exist and are assignable
    
    Args:
        roles: List of role names
        
    Returns:
        True if all roles are valid
    """
    if not roles:
        return True
    
    Role = DocType("Role")
    
    valid_roles = (
        frappe.qb.from_(Role)
        .select(Role.name)
        .where(
            (Role.name.isin(roles)) &
            (Role.name.notin(AUTOMATIC_ROLES)) &
            (Role.disabled == 0)
        )
        .run(pluck=True)
    )
    
    return len(valid_roles) == len(roles)


def assign_roles_to_user(user_email: str, roles: list) -> None:
    """Assign roles to a user
    
    Args:
        user_email: User email
        roles: List of role names
    """
    user_doc = frappe.get_doc("User", user_email)
    
    for role in roles:
        # Check if role already assigned
        existing_roles = [r.role for r in user_doc.roles]
        if role not in existing_roles:
            user_doc.append("roles", {"role": role})
    
    user_doc.save(ignore_permissions=True)


def set_company_permission(user_email: str, company: str, creator: str) -> None:
    """Set user permission to restrict user to specific company
    
    Args:
        user_email: User email
        company: Company name
        creator: Creator user email
    """
    # Check if permission already exists
    existing = frappe.db.exists(
        "User Permission",
        {
            "user": user_email,
            "allow": "Company",
            "for_value": company
        }
    )
    
    if not existing:
        # Create user permission
        perm = frappe.new_doc("User Permission")
        perm.user = user_email
        perm.allow = "Company"
        perm.for_value = company
        perm.apply_to_all_doctypes = 1
        perm.insert(ignore_permissions=True)

