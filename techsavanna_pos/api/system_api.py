"""
System Utilities API
Handles system-level operations like listing doctypes, modules, etc.
"""

import frappe
from frappe import _
from typing import Dict, List, Optional
from frappe.utils import cint


@frappe.whitelist()
def list_doctypes(
    module: str = None,
    is_submittable: bool = None,
    is_custom: bool = None,
    is_virtual: bool = None,
    search: str = None,
    page: int = 1,
    page_size: int = 50
) -> Dict:
    """List all doctypes with optional filters
    
    Args:
        module: Filter by module name (optional, e.g., "Stock", "Buying", "Selling")
        is_submittable: Filter by submittable status (optional)
        is_custom: Filter by custom doctypes (optional)
        is_virtual: Filter by virtual doctypes (optional)
        search: Search in doctype name (optional)
        page: Page number (default: 1)
        page_size: Items per page (default: 50, max: 200)
        
    Returns:
        List of doctypes with metadata and pagination
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate and limit page size
        page_size = min(cint(page_size), 200)
        page = max(cint(page), 1)
        
        # Build filters
        filters = {}
        
        if module:
            filters["module"] = module
        
        if is_submittable is not None:
            filters["is_submittable"] = 1 if is_submittable else 0
        
        if is_custom is not None:
            filters["custom"] = 1 if is_custom else 0
        
        if is_virtual is not None:
            filters["is_virtual"] = 1 if is_virtual else 0
        
        if search:
            filters["name"] = ["like", f"%{search}%"]
        
        # Get total count
        total = frappe.db.count("DocType", filters=filters)
        
        # Get paginated results
        start = (page - 1) * page_size
        
        doctypes = frappe.get_all(
            "DocType",
            filters=filters,
            fields=[
                "name",
                "module",
                "is_submittable",
                "custom",
                "is_virtual",
                "issingle",
                "track_changes",
                "track_seen",
                "allow_import",
                "allow_rename",
                "allow_copy",
                "allow_guest_to_view",
                "beta",
                "naming_rule",
                "autoname",
                "title_field",
                "search_fields",
                "sort_field",
                "sort_order",
                "icon",
                "color",
                "read_only",
                "in_create",
                "editable_grid",
                "max_attachments",
                "creation",
                "modified"
            ],
            order_by="name asc",
            limit=page_size,
            start=start
        )
        
        # Get additional metadata for each doctype
        for doctype in doctypes:
            # Get permission count
            perm_count = frappe.db.count("DocPerm", {"parent": doctype.name})
            custom_perm_count = frappe.db.count("Custom DocPerm", {"parent": doctype.name})
            doctype["permission_count"] = perm_count
            doctype["custom_permission_count"] = custom_perm_count
            
            # Get field count
            field_count = frappe.db.count("DocField", {"parent": doctype.name})
            doctype["field_count"] = field_count
            
            # Get workflow status if exists
            workflow = frappe.db.get_value("Workflow", {"document_type": doctype.name}, "name")
            doctype["has_workflow"] = bool(workflow)
            
            # Get role count (roles with permissions on this doctype)
            role_count = frappe.db.count(
                "DocPerm",
                {"parent": doctype.name},
                distinct="role"
            )
            doctype["role_count"] = role_count
        
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Doctypes retrieved successfully"),
            "data": {
                "doctypes": doctypes,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages
                },
                "filters_applied": {
                    "module": module,
                    "is_submittable": is_submittable,
                    "is_custom": is_custom,
                    "is_virtual": is_virtual,
                    "search": search
                }
            }
        }
    except Exception as e:
        frappe.log_error("List Doctypes Error", f"Error listing doctypes: {str(e)}")
        frappe.local.response["http_status_code"] = 500
        return {
            "success": False,
            "message": f"Error listing doctypes: {str(e)}"
        }


@frappe.whitelist()
def get_doctype_details(doctype: str) -> Dict:
    """Get detailed information about a specific doctype
    
    Args:
        doctype: Name of the doctype
        
    Returns:
        Detailed doctype information including fields, permissions, etc.
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Validate doctype exists
        if not frappe.db.exists("DocType", doctype):
            frappe.throw(_("DocType '{0}' does not exist").format(doctype), frappe.DoesNotExistError)
        
        # Get doctype document
        doc = frappe.get_doc("DocType", doctype)
        
        # Get basic info
        doctype_info = {
            "name": doc.name,
            "module": doc.module,
            "is_submittable": doc.is_submittable,
            "custom": doc.custom,
            "is_virtual": doc.is_virtual,
            "issingle": doc.issingle,
            "track_changes": doc.track_changes,
            "track_seen": doc.track_seen,
            "allow_import": doc.allow_import,
            "allow_rename": doc.allow_rename,
            "allow_copy": doc.allow_copy,
            "allow_guest_to_view": doc.allow_guest_to_view,
            "beta": doc.beta,
            "read_only": doc.read_only,
            "in_create": doc.in_create,
            "editable_grid": doc.editable_grid,
            "naming_rule": doc.naming_rule,
            "autoname": doc.autoname,
            "title_field": doc.title_field,
            "search_fields": doc.search_fields,
            "sort_field": doc.sort_field,
            "sort_order": doc.sort_order,
            "icon": doc.icon,
            "color": doc.color,
            "max_attachments": doc.max_attachments,
            "creation": str(doc.creation),
            "modified": str(doc.modified),
            "modified_by": doc.modified_by
        }
        
        # Get field count
        field_count = frappe.db.count("DocField", {"parent": doctype})
        doctype_info["field_count"] = field_count
        
        # Get permission summary
        standard_perms = frappe.get_all(
            "DocPerm",
            filters={"parent": doctype},
            fields=["role", "permlevel", "read", "write", "create", "submit", "delete"],
            order_by="role asc, permlevel asc"
        )
        
        custom_perms = frappe.get_all(
            "Custom DocPerm",
            filters={"parent": doctype},
            fields=["role", "permlevel", "read", "write", "create", "submit", "delete"],
            order_by="role asc, permlevel asc"
        )
        
        doctype_info["permissions"] = {
            "standard": standard_perms,
            "custom": custom_perms,
            "standard_count": len(standard_perms),
            "custom_count": len(custom_perms)
        }
        
        # Get roles with permissions
        roles_with_perms = frappe.get_all(
            "DocPerm",
            filters={"parent": doctype},
            fields=["role"],
            distinct=True,
            pluck="role"
        )
        custom_roles_with_perms = frappe.get_all(
            "Custom DocPerm",
            filters={"parent": doctype},
            fields=["role"],
            distinct=True,
            pluck="role"
        )
        all_roles = list(set(roles_with_perms + custom_roles_with_perms))
        doctype_info["roles_with_permissions"] = all_roles
        doctype_info["role_count"] = len(all_roles)
        
        # Get workflow info
        workflow = frappe.db.get_value("Workflow", {"document_type": doctype}, ["name", "workflow_state_field"], as_dict=True)
        doctype_info["workflow"] = workflow if workflow else None
        doctype_info["has_workflow"] = bool(workflow)
        
        # Get child table count
        child_table_count = frappe.db.count("DocField", {"parent": doctype, "fieldtype": "Table"})
        doctype_info["child_table_count"] = child_table_count
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Doctype details retrieved successfully"),
            "data": doctype_info
        }
    except frappe.DoesNotExistError:
        frappe.local.response["http_status_code"] = 404
        return {
            "success": False,
            "message": _("DocType '{0}' does not exist").format(doctype)
        }
    except Exception as e:
        frappe.log_error("Get Doctype Details Error", f"Error getting doctype details: {str(e)}")
        frappe.local.response["http_status_code"] = 500
        return {
            "success": False,
            "message": f"Error getting doctype details: {str(e)}"
        }


@frappe.whitelist()
def list_modules() -> Dict:
    """List all modules in the system
    
    Returns:
        List of modules with doctype counts
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
        
        # Get all modules
        modules = frappe.get_all(
            "Module Def",
            fields=["name", "module_name", "app_name", "custom"],
            order_by="name asc"
        )
        
        # Get doctype count for each module
        for module in modules:
            # Use name field (which is set from module_name via autoname)
            module_name = module.get("name")
            doctype_count = frappe.db.count("DocType", {"module": module_name})
            module["doctype_count"] = doctype_count
            # Add module_name if not present (for consistency)
            if "module_name" not in module:
                module["module_name"] = module_name
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Modules retrieved successfully"),
            "data": {
                "modules": modules,
                "total": len(modules)
            }
        }
    except Exception as e:
        frappe.log_error("List Modules Error", f"Error listing modules: {str(e)}")
        frappe.local.response["http_status_code"] = 500
        return {
            "success": False,
            "message": f"Error listing modules: {str(e)}"
        }

