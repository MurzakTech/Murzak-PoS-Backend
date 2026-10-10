"""
Role Management API
Handles role creation, updates, deletion, and permission assignment
"""

import frappe
from frappe import _
from frappe.permissions import AUTOMATIC_ROLES
from frappe.query_builder import DocType
from typing import Dict, List, Optional, Union

from techsavanna_pos.api.access_control import require_manager, require_role_manageable


@frappe.whitelist()
def create_role(
    role_name: str,
    desk_access: bool = True,
    two_factor_auth: bool = False,
    restrict_to_domain: str = None,
    home_page: str = None,
    is_custom: bool = True
) -> Dict:
    """Create a new role
    
    Args:
        role_name: Name of the role (required, must be unique)
        desk_access: Whether role has desk access (default: True)
        two_factor_auth: Whether role requires two-factor authentication (default: False)
        restrict_to_domain: Domain to restrict role to (optional)
        home_page: Home page route for role (optional, e.g., "/app")
        is_custom: Whether this is a custom role (default: True)
        
    Returns:
        Created role details
    """
    # Roles are shared by the whole site, so only a business owner may create one
    require_manager()
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role name
        if not role_name or not role_name.strip():
            frappe.throw(_("Role name is required"), frappe.ValidationError)
        
        role_name = role_name.strip()
        
        # Check if role already exists
        if frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' already exists").format(role_name), frappe.DuplicateEntryError)
        
        # Validate domain if provided
        if restrict_to_domain and not frappe.db.exists("Domain", restrict_to_domain):
            frappe.throw(_("Domain '{0}' does not exist").format(restrict_to_domain), frappe.ValidationError)
        
        # Create role
        role = frappe.new_doc("Role")
        role.role_name = role_name
        role.desk_access = 1 if desk_access else 0
        role.two_factor_auth = 1 if two_factor_auth else 0
        role.is_custom = 1 if is_custom else 0
        if restrict_to_domain:
            role.restrict_to_domain = restrict_to_domain
        if home_page:
            role.home_page = home_page
        
        role.insert(ignore_permissions=True)
        
        frappe.local.response["http_status_code"] = 201
        
        return {
            "success": True,
            "message": _("Role created successfully"),
            "data": {
                "name": role.name,
                "role_name": role.role_name,
                "desk_access": role.desk_access,
                "two_factor_auth": role.two_factor_auth,
                "is_custom": role.is_custom,
                "restrict_to_domain": role.restrict_to_domain,
                "home_page": role.home_page,
                "disabled": role.disabled
            }
        }
    except frappe.DuplicateEntryError:
        raise
    except Exception as e:
        frappe.log_error("Create Role Error", f"Error creating role: {str(e)}")
        return {
            "success": False,
            "message": f"Error creating role: {str(e)}"
        }


@frappe.whitelist()
def update_role(
    role_name: str,
    desk_access: bool = None,
    two_factor_auth: bool = None,
    restrict_to_domain: str = None,
    home_page: str = None,
    disabled: bool = None
) -> Dict:
    """Update an existing role
    
    Args:
        role_name: Name of the role to update
        desk_access: Update desk access (optional)
        two_factor_auth: Update two-factor auth requirement (optional)
        restrict_to_domain: Update domain restriction (optional, set to empty string to remove)
        home_page: Update home page route (optional, set to empty string to remove)
        disabled: Update disabled status (optional)
        
    Returns:
        Updated role details
    """
    # Only a business owner, and only for a role that is theirs to change
    require_manager()
    require_role_manageable(role_name)
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        # Check if it's an automatic role (cannot be modified)
        if role_name in AUTOMATIC_ROLES:
            frappe.throw(_("Cannot modify automatic role '{0}'").format(role_name), frappe.ValidationError)
        
        role = frappe.get_doc("Role", role_name)
        
        # Update fields if provided
        if desk_access is not None:
            role.desk_access = 1 if desk_access else 0
        if two_factor_auth is not None:
            role.two_factor_auth = 1 if two_factor_auth else 0
        if restrict_to_domain is not None:
            if restrict_to_domain == "":
                role.restrict_to_domain = None
            else:
                if not frappe.db.exists("Domain", restrict_to_domain):
                    frappe.throw(_("Domain '{0}' does not exist").format(restrict_to_domain), frappe.ValidationError)
                role.restrict_to_domain = restrict_to_domain
        if home_page is not None:
            role.home_page = home_page if home_page else None
        if disabled is not None:
            role.disabled = 1 if disabled else 0
        
        role.save(ignore_permissions=True)
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Role updated successfully"),
            "data": {
                "name": role.name,
                "role_name": role.role_name,
                "desk_access": role.desk_access,
                "two_factor_auth": role.two_factor_auth,
                "is_custom": role.is_custom,
                "restrict_to_domain": role.restrict_to_domain,
                "home_page": role.home_page,
                "disabled": role.disabled
            }
        }
    except Exception as e:
        frappe.log_error("Update Role Error", f"Error updating role: {str(e)}")
        return {
            "success": False,
            "message": f"Error updating role: {str(e)}"
        }


@frappe.whitelist()
def delete_role(role_name: str) -> Dict:
    """Delete a role
    
    Args:
        role_name: Name of the role to delete
        
    Returns:
        Success message
    """
    # Only a business owner, and only for a role that is theirs to change
    require_manager()
    require_role_manageable(role_name)
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        # Check if it's an automatic role (cannot be deleted)
        if role_name in AUTOMATIC_ROLES:
            frappe.throw(_("Cannot delete automatic role '{0}'").format(role_name), frappe.ValidationError)
        
        # Check if role is assigned to any users
        users_with_role = frappe.get_all(
            "Has Role",
            filters={"role": role_name},
            fields=["parent"],
            limit=1
        )
        if users_with_role:
            frappe.throw(
                _("Cannot delete role '{0}' because it is assigned to one or more users. Please remove the role from all users first.").format(role_name),
                frappe.ValidationError
            )
        
        # Delete the role
        frappe.delete_doc("Role", role_name, ignore_permissions=True)
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Role deleted successfully")
        }
    except Exception as e:
        frappe.log_error("Delete Role Error", f"Error deleting role: {str(e)}")
        return {
            "success": False,
            "message": f"Error deleting role: {str(e)}"
        }


@frappe.whitelist()
def disable_role(role_name: str) -> Dict:
    """Disable a role (removes it from all users)
    
    Args:
        role_name: Name of the role to disable
        
    Returns:
        Success message
    """
    # Only a business owner, and only for a role that is theirs to change
    require_manager()
    require_role_manageable(role_name)
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        # Check if it's an automatic role (cannot be disabled)
        if role_name in AUTOMATIC_ROLES:
            frappe.throw(_("Cannot disable automatic role '{0}'").format(role_name), frappe.ValidationError)
        
        role = frappe.get_doc("Role", role_name)
        role.disabled = 1
        role.save(ignore_permissions=True)
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Role disabled successfully. The role has been removed from all users."),
            "data": {
                "name": role.name,
                "role_name": role.role_name,
                "disabled": role.disabled
            }
        }
    except Exception as e:
        frappe.log_error("Disable Role Error", f"Error disabling role: {str(e)}")
        return {
            "success": False,
            "message": f"Error disabling role: {str(e)}"
        }


@frappe.whitelist()
def enable_role(role_name: str) -> Dict:
    """Enable a disabled role
    
    Args:
        role_name: Name of the role to enable
        
    Returns:
        Success message
    """
    # Only a business owner, and only for a role that is theirs to change
    require_manager()
    require_role_manageable(role_name)
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        role = frappe.get_doc("Role", role_name)
        role.disabled = 0
        role.save(ignore_permissions=True)
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Role enabled successfully"),
            "data": {
                "name": role.name,
                "role_name": role.role_name,
                "disabled": role.disabled
            }
        }
    except Exception as e:
        frappe.log_error("Enable Role Error", f"Error enabling role: {str(e)}")
        return {
            "success": False,
            "message": f"Error enabling role: {str(e)}"
        }


@frappe.whitelist()
def assign_permissions_to_role(
    role_name: str,
    doctype: str,
    permissions: Union[str, Dict],
    permlevel: int = 0,
    if_owner: bool = False
) -> Dict:
    """Assign permissions to a role for a specific doctype
    
    Args:
        role_name: Name of the role
        doctype: DocType to assign permissions for
        permissions: Dictionary or JSON string with permission flags:
            {
                "read": 1,
                "write": 1,
                "create": 1,
                "delete": 0,
                "submit": 0,
                "cancel": 0,
                "amend": 0,
                "print": 1,
                "email": 1,
                "export": 1,
                "import": 0,
                "report": 1,
                "share": 0,
                "select": 1
            }
        permlevel: Permission level (default: 0)
        if_owner: Apply only if user is owner (default: False)
        
    Returns:
        Success message with permission details
    """
    # Only a business owner, and only for a role that is theirs to change
    require_manager()
    require_role_manageable(role_name)
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        # Validate doctype exists
        if not frappe.db.exists("DocType", doctype):
            frappe.throw(_("DocType '{0}' does not exist").format(doctype), frappe.DoesNotExistError)
        
        # Parse permissions if string
        if isinstance(permissions, str):
            import json
            permissions = json.loads(permissions)
        
        if not isinstance(permissions, dict):
            frappe.throw(_("Permissions must be a dictionary"), frappe.ValidationError)
        
        # Setup custom permissions for doctype
        from frappe.permissions import setup_custom_perms
        setup_custom_perms(doctype)
        
        # Check if permission already exists
        existing_perm = frappe.db.get_value(
            "Custom DocPerm",
            {
                "parent": doctype,
                "role": role_name,
                "permlevel": permlevel,
                "if_owner": 1 if if_owner else 0
            },
            "name"
        )
        
        if existing_perm:
            # Update existing permission
            perm_doc = frappe.get_doc("Custom DocPerm", existing_perm)
        else:
            # Create new permission
            perm_doc = frappe.new_doc("Custom DocPerm")
            perm_doc.parent = doctype
            perm_doc.parenttype = "DocType"
            perm_doc.parentfield = "permissions"
            perm_doc.role = role_name
            perm_doc.permlevel = permlevel
            perm_doc.if_owner = 1 if if_owner else 0
        
        # Update permission flags
        valid_permissions = [
            "read", "write", "create", "delete", "submit", "cancel", "amend",
            "print", "email", "export", "report", "share", "select"
        ]
        
        for perm_type in valid_permissions:
            if perm_type in permissions:
                setattr(perm_doc, perm_type, 1 if permissions[perm_type] else 0)
        
        # Handle import separately (it's a Python keyword)
        if "import" in permissions:
            perm_doc.set("import", 1 if permissions["import"] else 0)
        
        perm_doc.save(ignore_permissions=True)
        
        # Validate permissions
        from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
        validate_permissions_for_doctype(doctype)
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Permissions assigned successfully"),
            "data": {
                "role": role_name,
                "doctype": doctype,
                "permlevel": permlevel,
                "if_owner": if_owner,
                "permissions": {
                    "read": perm_doc.read,
                    "write": perm_doc.write,
                    "create": perm_doc.create,
                    "delete": perm_doc.delete,
                    "submit": perm_doc.submit,
                    "cancel": perm_doc.cancel,
                    "amend": perm_doc.amend,
                    "print": perm_doc.print,
                    "email": perm_doc.email,
                    "export": perm_doc.export,
                    "import": perm_doc.get("import", 0),
                    "report": perm_doc.report,
                    "share": perm_doc.share,
                    "select": perm_doc.select
                }
            }
        }
    except Exception as e:
        frappe.log_error("Assign Permissions Error", f"Error assigning permissions: {str(e)}")
        return {
            "success": False,
            "message": f"Error assigning permissions: {str(e)}"
        }


@frappe.whitelist()
def get_role_permissions(
    role_name: str,
    doctype: str = None
) -> Dict:
    """Get permissions for a role
    
    Args:
        role_name: Name of the role
        doctype: Optional doctype filter (returns all if not provided)
        
    Returns:
        List of permissions for the role
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        # Build filters
        filters = {"role": role_name}
        if doctype:
            filters["parent"] = doctype
            # Validate doctype exists
            if not frappe.db.exists("DocType", doctype):
                frappe.throw(_("DocType '{0}' does not exist").format(doctype), frappe.DoesNotExistError)
        
        # Get custom permissions first
        # Note: 'import' is a Python keyword, so we'll access it via get() method
        permissions = frappe.get_all(
            "Custom DocPerm",
            filters=filters,
            fields=[
                "name", "parent", "role", "permlevel", "if_owner",
                "read", "write", "create", "delete", "submit", "cancel", "amend",
                "print", "email", "export", "report", "share", "select"
            ],
            order_by="parent asc, permlevel asc"
        )
        
        # Get import field separately for each permission
        for perm in permissions:
            perm_doc = frappe.get_doc("Custom DocPerm", perm.name)
            perm["import"] = perm_doc.get("import", 0)
        
        # If no custom permissions and doctype specified, check standard permissions
        if not permissions and doctype:
            standard_perms = frappe.get_all(
                "DocPerm",
                filters={"parent": doctype, "role": role_name},
                fields=[
                    "name", "parent", "role", "permlevel", "if_owner",
                    "read", "write", "create", "delete", "submit", "cancel", "amend",
                    "print", "email", "export", "report", "share", "select"
                ],
                order_by="permlevel asc"
            )
            # Get import field separately
            for perm in standard_perms:
                perm_doc = frappe.get_doc("DocPerm", perm.name)
                perm["import"] = perm_doc.get("import", 0)
            permissions = standard_perms
        
        # Format response
        formatted_perms = []
        for perm in permissions:
            formatted_perms.append({
                "doctype": perm.parent,
                "role": perm.role,
                "permlevel": perm.permlevel,
                "if_owner": perm.if_owner,
                "permissions": {
                    "read": perm.read,
                    "write": perm.write,
                    "create": perm.create,
                    "delete": perm.delete,
                    "submit": perm.submit,
                    "cancel": perm.cancel,
                    "amend": perm.amend,
                    "print": perm.print,
                    "email": perm.email,
                    "export": perm.export,
                    "import": perm.get("import", 0),
                    "report": perm.report,
                    "share": perm.share,
                    "select": perm.select
                }
            })
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "data": {
                "role": role_name,
                "permissions": formatted_perms,
                "count": len(formatted_perms)
            }
        }
    except Exception as e:
        frappe.log_error("Get Role Permissions Error", f"Error getting role permissions: {str(e)}")
        return {
            "success": False,
            "message": f"Error getting role permissions: {str(e)}"
        }


@frappe.whitelist()
def remove_permissions_from_role(
    role_name: str,
    doctype: str,
    permlevel: int = 0,
    if_owner: bool = False
) -> Dict:
    """Remove permissions from a role for a specific doctype
    
    Args:
        role_name: Name of the role
        doctype: DocType to remove permissions for
        permlevel: Permission level (default: 0)
        if_owner: If owner permission to remove (default: False)
        
    Returns:
        Success message
    """
    # Only a business owner, and only for a role that is theirs to change
    require_manager()
    require_role_manageable(role_name)
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        # Validate doctype exists
        if not frappe.db.exists("DocType", doctype):
            frappe.throw(_("DocType '{0}' does not exist").format(doctype), frappe.DoesNotExistError)
        
        # Find and delete custom permission
        perm_name = frappe.db.get_value(
            "Custom DocPerm",
            {
                "parent": doctype,
                "role": role_name,
                "permlevel": permlevel,
                "if_owner": 1 if if_owner else 0
            },
            "name"
        )
        
        if perm_name:
            frappe.delete_doc("Custom DocPerm", perm_name, ignore_permissions=True)
            
            # Validate permissions after removal
            from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype
            validate_permissions_for_doctype(doctype)
            
            frappe.local.response["http_status_code"] = 200
            
            return {
                "success": True,
                "message": _("Permissions removed successfully")
            }
        else:
            frappe.throw(_("Permission not found for role '{0}' on doctype '{1}'").format(role_name, doctype), frappe.DoesNotExistError)
    except Exception as e:
        frappe.log_error("Remove Permissions Error", f"Error removing permissions: {str(e)}")
        return {
            "success": False,
            "message": f"Error removing permissions: {str(e)}"
        }


@frappe.whitelist()
def list_roles(
    disabled: bool = None,
    is_custom: bool = None,
    desk_access: bool = None,
    restrict_to_domain: str = None,
    search: str = None,
    page: int = 1,
    page_size: int = 20
) -> Dict:
    """List roles with filters and search
    
    Args:
        disabled: Filter by disabled status (optional)
        is_custom: Filter by custom role (optional)
        desk_access: Filter by desk access (optional)
        restrict_to_domain: Filter by domain (optional)
        search: Search in role name (optional, partial match)
        page: Page number (default: 1)
        page_size: Items per page (default: 20)
        
    Returns:
        List of roles with pagination
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        from frappe.utils import cint
        
        # Validate and limit page size
        page_size = min(cint(page_size), 200)
        page = max(cint(page), 1)
        
        # Build filters
        filters = {}
        if disabled is not None:
            filters["disabled"] = 1 if disabled else 0
        if is_custom is not None:
            filters["is_custom"] = 1 if is_custom else 0
        if desk_access is not None:
            filters["desk_access"] = 1 if desk_access else 0
        if restrict_to_domain:
            filters["restrict_to_domain"] = restrict_to_domain
        
        # Add search filter if provided
        if search:
            filters["role_name"] = ["like", f"%{search}%"]
        
        # Get total count
        total = frappe.db.count("Role", filters=filters)
        
        # Get paginated results
        start = (page - 1) * page_size
        roles = frappe.get_all(
            "Role",
            filters=filters,
            fields=[
                "name",
                "role_name",
                "disabled",
                "is_custom",
                "desk_access",
                "two_factor_auth",
                "restrict_to_domain",
                "home_page"
            ],
            order_by="role_name asc",
            limit=page_size,
            start=start
        )
        
        # Get user count for each role
        for role in roles:
            user_count = frappe.db.count("Has Role", {"role": role.name})
            role["user_count"] = user_count
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "data": {
                "roles": roles,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages
                },
                "filters_applied": {
                    "disabled": disabled,
                    "is_custom": is_custom,
                    "desk_access": desk_access,
                    "restrict_to_domain": restrict_to_domain,
                    "search": search
                }
            }
        }
    except Exception as e:
        frappe.log_error("List Roles Error", f"Error listing roles: {str(e)}")
        return {
            "success": False,
            "message": f"Error listing roles: {str(e)}"
        }


@frappe.whitelist()
def get_role_details(role_name: str) -> Dict:
    """Get detailed information about a role
    
    Args:
        role_name: Name of the role
        
    Returns:
        Role details with permissions summary
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate role exists
        if not frappe.db.exists("Role", role_name):
            frappe.throw(_("Role '{0}' does not exist").format(role_name), frappe.DoesNotExistError)
        
        role = frappe.get_doc("Role", role_name)
        
        # Get user count
        user_count = frappe.db.count("Has Role", {"role": role_name})
        
        # Get permission count
        perm_count = frappe.db.count("Custom DocPerm", {"role": role_name})
        
        # Get list of doctypes with permissions
        doctypes_with_perms = frappe.get_all(
            "Custom DocPerm",
            filters={"role": role_name},
            fields=["parent"],
            distinct=True,
            pluck="parent"
        )
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "data": {
                "name": role.name,
                "role_name": role.role_name,
                "disabled": role.disabled,
                "is_custom": role.is_custom,
                "desk_access": role.desk_access,
                "two_factor_auth": role.two_factor_auth,
                "restrict_to_domain": role.restrict_to_domain,
                "home_page": role.home_page,
                "user_count": user_count,
                "permission_count": perm_count,
                "doctypes_with_permissions": doctypes_with_perms,
                "is_automatic": role_name in AUTOMATIC_ROLES
            }
        }
    except Exception as e:
        frappe.log_error("Get Role Details Error", f"Error getting role details: {str(e)}")
        return {
            "success": False,
            "message": f"Error getting role details: {str(e)}"
        }
