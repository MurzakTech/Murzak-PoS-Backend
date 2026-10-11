"""
POS Industry API
Handles POS industry management and listing
"""

from typing import Dict, List, Optional

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def get_pos_industries(is_active: bool = True) -> Dict:
    """Get list of all POS industries
    
    Args:
        is_active: Filter by active status (default: True)
        
    Returns:
        List of POS industries with details
    """
    try:
        filters = {}
        if is_active:
            filters["is_active"] = 1
        
        industries = frappe.get_all(
            "POS Industry",
            filters=filters,
            fields=[
                "name",
                "industry_code",
                "industry_name",
                "description",
                "serving_location",
                "is_active",
                "sort_order"
            ],
            order_by="sort_order asc, industry_name asc"
        )
        
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "industries": industries,
            "count": len(industries),
            "message": _("Industries retrieved successfully")
        }
    except Exception as e:
        frappe.log_error("Get POS Industries", f"Error getting POS industries: {str(e)}")
        frappe.throw(_("Error retrieving industries: {0}").format(str(e)), frappe.ValidationError)


@frappe.whitelist()
def get_user_industry() -> Dict:
    """Get the POS industry assigned to the current user
    
    Returns:
        User's POS industry details
    """
    user = frappe.session.user
    
    if user == "Guest":
        frappe.throw(_("Not authenticated"), frappe.AuthenticationError)
    
    try:
        industry = frappe.db.get_value("User", user, "custom_pos_industry")
    except Exception as e:
        # Handle case where custom_pos_industry column doesn't exist in database
        # Log short error message to avoid CharacterLengthExceededError
        error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
        frappe.log_error(
            "Get User Industry",
            f"POS industry field missing: {error_msg}"
        )
        industry = None
    
    if not industry:
        frappe.local.response["http_status_code"] = 200
        return {
            "success": True,
            "industry": None,
            "message": _("No industry assigned to user")
        }
    
    industry_doc = frappe.get_doc("POS Industry", industry)
    
    frappe.local.response["http_status_code"] = 200
    
    return {
        "success": True,
        "industry": {
            "name": industry_doc.name,
            "industry_code": industry_doc.industry_code,
            "industry_name": industry_doc.industry_name,
            "description": industry_doc.description,
            "serving_location": industry_doc.serving_location,
            "is_active": industry_doc.is_active
        },
        "message": _("Industry retrieved successfully")
    }

