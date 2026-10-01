"""
Supplier Management API
Handles supplier listing, creation, and management
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType


@frappe.whitelist()
def get_suppliers(
    company: str = None,
    supplier_group: str = None,
    disabled: bool = False,
    limit: int = 20,
    offset: int = 0,
    search_term: str = None,
    filter_by_company_transactions: bool = False,
) -> dict:
    """
    Get list of suppliers with optional filters.
    
    Args:
        company: Company name (optional, filters suppliers that have transactions with this company if filter_by_company_transactions=True)
        supplier_group: Filter by supplier group (optional)
        disabled: Include disabled suppliers (default: False)
        limit: Number of records to return (default: 20)
        offset: Offset for pagination (default: 0)
        search_term: Search term for supplier name or ID (optional)
        filter_by_company_transactions: If True and company is provided, only return suppliers with transactions for that company (default: False)
    
    Returns:
        dict: List of suppliers
    
    Note: 
    - By default, all suppliers are returned regardless of company transactions.
    - When company is provided and filter_by_company_transactions=True, only suppliers
      that have purchase invoices or purchase orders for that company will be returned.
    - Set filter_by_company_transactions=False (default) to return all suppliers.
    """
    try:
        filters = {}
        
        if not disabled:
            filters["disabled"] = 0
        
        if supplier_group:
            filters["supplier_group"] = supplier_group
        
        # Build search condition
        or_filters = {}
        if search_term:
            or_filters = {
                "supplier_name": ["like", f"%{search_term}%"],
                "name": ["like", f"%{search_term}%"],
            }
        
        suppliers = frappe.get_all(
            "Supplier",
            filters=filters,
            or_filters=or_filters if or_filters else None,
            fields=[
                "name",
                "supplier_name",
                "supplier_type",
                "supplier_group",
                "tax_id",
                "disabled",
                "is_internal_supplier",
                "country",
                "default_currency",
            ],
            limit=limit,
            start=offset,
            order_by="supplier_name",
        )
        
        # Debug logging (can be removed in production)
        frappe.logger().debug(f"get_suppliers: Found {len(suppliers)} suppliers before company filtering. Company: {company}, filter_by_company_transactions: {filter_by_company_transactions}")
        
        # If company is specified and filtering is enabled, filter suppliers that have transactions with that company
        if company and filter_by_company_transactions:
            # Get suppliers that have purchase invoices or purchase orders for this company
            PurchaseInvoice = DocType("Purchase Invoice")
            PurchaseOrder = DocType("Purchase Order")
            
            # Get suppliers from Purchase Invoices
            pi_suppliers = (
                frappe.qb.from_(PurchaseInvoice)
                .select(PurchaseInvoice.supplier)
                .where(PurchaseInvoice.company == company)
                .where(PurchaseInvoice.docstatus != 2)
                .distinct()
                .run(as_dict=True)
            )
            
            # Get suppliers from Purchase Orders
            po_suppliers = (
                frappe.qb.from_(PurchaseOrder)
                .select(PurchaseOrder.supplier)
                .where(PurchaseOrder.company == company)
                .where(PurchaseOrder.docstatus != 2)
                .distinct()
                .run(as_dict=True)
            )
            
            # Combine both lists and get unique supplier names
            company_supplier_names = set()
            for s in pi_suppliers:
                if s.supplier:
                    company_supplier_names.add(s.supplier)
            for s in po_suppliers:
                if s.supplier:
                    company_supplier_names.add(s.supplier)
            
            # Filter suppliers to only those that have transactions with the company
            if company_supplier_names:
                suppliers = [s for s in suppliers if s.name in company_supplier_names]
            else:
                # If no suppliers have transactions, return empty list
                suppliers = []
        
        return {
            "success": True,
            "data": suppliers,
            "count": len(suppliers),
        }
    except Exception as e:
        frappe.log_error(f"Error getting suppliers: {str(e)}", "Get Suppliers Error")
        return {
            "success": False,
            "message": f"Error getting suppliers: {str(e)}",
        }


@frappe.whitelist()
def get_supplier_details(name: str) -> dict:
    """
    Get detailed information about a specific supplier.
    
    Args:
        name: Supplier name/ID
    
    Returns:
        dict: Supplier details
    """
    try:
        supplier = frappe.get_doc("Supplier", name)
        
        # Get outstanding amount
        outstanding_amount = frappe.db.get_value(
            "Purchase Invoice",
            {
                "supplier": name,
                "docstatus": 1,
                "outstanding_amount": [">", 0],
            },
            ["sum(outstanding_amount)"],
        ) or 0.0
        
        # Get total purchase amount
        total_purchase = frappe.db.get_value(
            "Purchase Invoice",
            {
                "supplier": name,
                "docstatus": 1,
            },
            ["sum(grand_total)"],
        ) or 0.0
        
        return {
            "success": True,
            "data": {
                "name": supplier.name,
                "supplier_name": supplier.supplier_name,
                "supplier_type": supplier.supplier_type,
                "supplier_group": supplier.supplier_group,
                "tax_id": supplier.tax_id,
                "disabled": supplier.disabled,
                "is_internal_supplier": supplier.is_internal_supplier,
                "country": supplier.country,
                "default_currency": supplier.default_currency,
                "outstanding_amount": outstanding_amount,
                "total_purchase": total_purchase,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error fetching supplier details: {str(e)}",
        }


@frappe.whitelist()
def create_supplier(
    supplier_name: str,
    supplier_group: str = None,
    tax_id: str = None,
    country: str = None,
    default_currency: str = None,
    supplier_type: str = "Company",
    is_internal_supplier: bool = False,
) -> dict:
    """
    Create a new supplier.
    
    Args:
        supplier_name: Name of the supplier
        supplier_group: Supplier group (optional)
        tax_id: Tax ID/PIN (optional)
        country: Country (optional)
        default_currency: Default currency (optional)
        supplier_type: Supplier type - "Company" or "Individual" (default: "Company")
        is_internal_supplier: Whether this is an internal supplier (default: False)
    
    Returns:
        dict: Created supplier details
    """
    try:
        # Validate user permissions
        if frappe.session.user == "Guest":
            frappe.throw(_("Please log in to create a supplier. Your session has expired or you are not authenticated."), frappe.AuthenticationError)
        
        # Validate required fields
        if not supplier_name or not supplier_name.strip():
            frappe.throw(_("Supplier name is required. Please provide a name for this supplier."), frappe.ValidationError)
        
        # Check if supplier already exists
        existing = frappe.db.exists("Supplier", {"supplier_name": supplier_name})
        
        if existing:
            frappe.throw(_("A supplier with the name '{0}' already exists. Please use a different name or update the existing supplier.").format(supplier_name), frappe.ValidationError)
        
        # Validate supplier type
        if supplier_type not in ["Company", "Individual"]:
            frappe.throw(_("Supplier type must be either 'Company' or 'Individual'. Please select a valid supplier type."), frappe.ValidationError)
        
        # Get default supplier group if not provided
        if not supplier_group:
            supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
            if not supplier_group:
                supplier_group = "All Supplier Groups"
        
        # Validate supplier group exists
        if supplier_group and not frappe.db.exists("Supplier Group", supplier_group):
            frappe.throw(_("The supplier group '{0}' does not exist. Please select a valid supplier group from the list.").format(supplier_group), frappe.ValidationError)
        
        # Validate currency if provided
        if default_currency and not frappe.db.exists("Currency", default_currency):
            frappe.throw(_("The currency '{0}' does not exist. Please select a valid currency from the list.").format(default_currency), frappe.ValidationError)
        
        # Create supplier
        supplier = frappe.new_doc("Supplier")
        supplier.supplier_name = supplier_name
        supplier.supplier_type = supplier_type
        supplier.supplier_group = supplier_group
        supplier.is_internal_supplier = 1 if is_internal_supplier else 0
        
        if tax_id:
            supplier.tax_id = tax_id
        if country:
            supplier.country = country
        if default_currency:
            supplier.default_currency = default_currency
        
        # Use ignore_permissions=True for whitelisted API endpoints
        # The API endpoint itself acts as the permission gate (user must be authenticated)
        supplier.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # Set HTTP status code for successful creation
        frappe.local.response["http_status_code"] = 201
        
        return {
            "success": True,
            "message": _("Supplier created successfully"),
            "name": supplier.name,
            "supplier_name": supplier.supplier_name,
        }
    except frappe.AuthenticationError:
        # Re-raise authentication errors as-is
        raise
    except frappe.ValidationError:
        # Re-raise validation errors as-is (they already have user-friendly messages)
        raise
    except frappe.DuplicateEntryError as e:
        # Handle duplicate entry errors
        frappe.throw(
            _("A supplier with this information already exists. Please check the supplier name '{0}' and try again with a unique name.").format(supplier_name),
            frappe.ValidationError
        )
    except frappe.MandatoryError as e:
        # Handle missing mandatory fields
        error_msg = str(e)
        if "supplier_name" in error_msg.lower():
            frappe.throw(_("Supplier name is required. Please provide a name for this supplier."), frappe.ValidationError)
        else:
            frappe.throw(_("Some required information is missing: {0}. Please fill in all required fields and try again.").format(error_msg), frappe.ValidationError)
    except frappe.PermissionError as e:
        # This should not happen with ignore_permissions=True, but handle it just in case
        frappe.log_error(f"Permission error creating supplier: {str(e)}", "Supplier Creation Permission Error")
        frappe.throw(
            _("You don't have permission to create suppliers. Please contact your administrator to grant you the necessary permissions."),
            frappe.PermissionError
        )
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            f"Error creating supplier '{supplier_name}': {frappe.get_traceback()}",
            "Supplier Creation Error"
        )
        # Return user-friendly error message
        frappe.throw(
            _("An error occurred while creating the supplier. Please check that all information is correct and try again. If the problem persists, contact support."),
            frappe.ValidationError
        )


@frappe.whitelist()
def update_supplier(
    name: str,
    supplier_name: str = None,
    supplier_group: str = None,
    tax_id: str = None,
    country: str = None,
    default_currency: str = None,
    disabled: bool = None,
) -> dict:
    """
    Update an existing supplier.
    
    Args:
        name: Supplier name/ID
        supplier_name: Updated supplier name (optional)
        supplier_group: Updated supplier group (optional)
        tax_id: Updated tax ID (optional)
        country: Updated country (optional)
        default_currency: Updated default currency (optional)
        disabled: Update disabled status (optional)
    
    Returns:
        dict: Update result
    """
    try:
        # Validate user permissions
        if frappe.session.user == "Guest":
            frappe.throw(_("Please log in to update a supplier. Your session has expired or you are not authenticated."), frappe.AuthenticationError)
        
        # Validate supplier exists
        if not frappe.db.exists("Supplier", name):
            frappe.throw(_("The supplier '{0}' does not exist. Please check the supplier name and try again.").format(name), frappe.ValidationError)
        
        supplier = frappe.get_doc("Supplier", name)
        
        # Validate supplier name if provided
        if supplier_name:
            if not supplier_name.strip():
                frappe.throw(_("Supplier name cannot be empty. Please provide a valid supplier name."), frappe.ValidationError)
            # Check if new name already exists (excluding current supplier)
            existing = frappe.db.exists("Supplier", {"supplier_name": supplier_name, "name": ["!=", name]})
            if existing:
                frappe.throw(_("A supplier with the name '{0}' already exists. Please use a different name.").format(supplier_name), frappe.ValidationError)
            supplier.supplier_name = supplier_name
        
        # Validate supplier group if provided
        if supplier_group:
            if not frappe.db.exists("Supplier Group", supplier_group):
                frappe.throw(_("The supplier group '{0}' does not exist. Please select a valid supplier group from the list.").format(supplier_group), frappe.ValidationError)
            supplier.supplier_group = supplier_group
        
        # Validate currency if provided
        if default_currency:
            if not frappe.db.exists("Currency", default_currency):
                frappe.throw(_("The currency '{0}' does not exist. Please select a valid currency from the list.").format(default_currency), frappe.ValidationError)
            supplier.default_currency = default_currency
        
        if tax_id is not None:
            supplier.tax_id = tax_id
        if country:
            supplier.country = country
        if disabled is not None:
            supplier.disabled = 1 if disabled else 0
        
        # Use ignore_permissions=True for whitelisted API endpoints
        supplier.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Set HTTP status code for successful update
        frappe.local.response["http_status_code"] = 200
        
        return {
            "success": True,
            "message": _("Supplier updated successfully"),
            "name": supplier.name,
        }
    except frappe.AuthenticationError:
        # Re-raise authentication errors as-is
        raise
    except frappe.ValidationError:
        # Re-raise validation errors as-is (they already have user-friendly messages)
        raise
    except frappe.DuplicateEntryError as e:
        # Handle duplicate entry errors
        frappe.throw(
            _("A supplier with this information already exists. Please check the supplier name and try again with a unique name."),
            frappe.ValidationError
        )
    except frappe.MandatoryError as e:
        # Handle missing mandatory fields
        error_msg = str(e)
        frappe.throw(_("Some required information is missing: {0}. Please fill in all required fields and try again.").format(error_msg), frappe.ValidationError)
    except frappe.PermissionError as e:
        # This should not happen with ignore_permissions=True, but handle it just in case
        frappe.log_error(f"Permission error updating supplier: {str(e)}", "Supplier Update Permission Error")
        frappe.throw(
            _("You don't have permission to update suppliers. Please contact your administrator to grant you the necessary permissions."),
            frappe.PermissionError
        )
    except Exception as e:
        # Log the full error for debugging
        frappe.log_error(
            f"Error updating supplier '{name}': {frappe.get_traceback()}",
            "Supplier Update Error"
        )
        # Return user-friendly error message
        frappe.throw(
            _("An error occurred while updating the supplier. Please check that all information is correct and try again. If the problem persists, contact support."),
            frappe.ValidationError
        )


@frappe.whitelist()
def get_supplier_groups(
    is_group: bool = None,
    parent_supplier_group: str = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """
    Get list of all supplier groups with optional filters.
    
    Args:
        is_group: Filter by is_group flag (optional)
        parent_supplier_group: Filter by parent supplier group (optional)
        limit: Number of records to return (default: 100)
        offset: Offset for pagination (default: 0)
    
    Returns:
        dict: List of supplier groups
    """
    try:
        filters = {}
        
        if is_group is not None:
            filters["is_group"] = 1 if is_group else 0
        
        if parent_supplier_group:
            filters["parent_supplier_group"] = parent_supplier_group
        
        supplier_groups = frappe.get_all(
            "Supplier Group",
            filters=filters,
            fields=["name", "supplier_group_name", "is_group", "parent_supplier_group"],
            limit=limit,
            start=offset,
            order_by="supplier_group_name",
        )
        
        return {
            "success": True,
            "data": supplier_groups,
            "count": len(supplier_groups),
        }
    except Exception as e:
        frappe.log_error(f"Error getting supplier groups: {str(e)}", "Get Supplier Groups Error")
        return {
            "success": False,
            "message": f"Error getting supplier groups: {str(e)}",
        }


@frappe.whitelist()
def create_supplier_group(
    supplier_group_name: str,
    parent_supplier_group: str = None,
    is_group: bool = False,
    payment_terms: str = None,
) -> dict:
    """
    Create a new supplier group.
    
    Args:
        supplier_group_name: Name of the supplier group (required, must be unique)
        parent_supplier_group: Parent supplier group name (optional, defaults to root group)
        is_group: Whether this is a group (default: False)
        payment_terms: Default payment terms template (optional)
    
    Returns:
        dict: Created supplier group details
    """
    try:
        # Check if supplier group already exists
        existing = frappe.db.exists("Supplier Group", {"supplier_group_name": supplier_group_name})
        
        if existing:
            return {
                "success": False,
                "message": f"Supplier Group '{supplier_group_name}' already exists",
                "name": existing,
            }
        
        # Get root group if parent not provided
        if not parent_supplier_group:
            from frappe.utils.nestedset import get_root_of
            parent_supplier_group = get_root_of("Supplier Group")
        
        # Verify parent exists if provided
        if parent_supplier_group and not frappe.db.exists("Supplier Group", parent_supplier_group):
            return {
                "success": False,
                "message": f"Parent Supplier Group '{parent_supplier_group}' does not exist",
            }
        
        # Create supplier group
        supplier_group = frappe.new_doc("Supplier Group")
        supplier_group.supplier_group_name = supplier_group_name
        supplier_group.parent_supplier_group = parent_supplier_group
        supplier_group.is_group = 1 if is_group else 0
        
        if payment_terms:
            supplier_group.payment_terms = payment_terms
        
        # Use ignore_permissions=True for whitelisted API endpoints
        supplier_group.insert(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "success": True,
            "message": "Supplier Group created successfully",
            "name": supplier_group.name,
            "supplier_group_name": supplier_group.supplier_group_name,
            "is_group": supplier_group.is_group,
            "parent_supplier_group": supplier_group.parent_supplier_group,
        }
    except frappe.PermissionError as e:
        frappe.log_error(f"Permission error creating supplier group: {str(e)}", "Supplier Group Creation Permission Error")
        return {
            "success": False,
            "message": f"Permission denied: You do not have permission to create Supplier Group documents. Please contact your administrator to grant you the necessary role permissions.",
            "error_type": "permission_error",
            "required_permission": "Supplier Group: Create",
        }
    except frappe.exceptions.ValidationError as e:
        frappe.log_error(f"Validation error creating supplier group: {str(e)}", "Supplier Group Creation Validation Error")
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(f"Error creating supplier group: {str(e)}", "Supplier Group Creation Error")
        return {
            "success": False,
            "message": f"Error creating supplier group: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def update_supplier_group(
    name: str,
    supplier_group_name: str = None,
    parent_supplier_group: str = None,
    is_group: bool = None,
    payment_terms: str = None,
) -> dict:
    """
    Update an existing supplier group.
    
    Args:
        name: Supplier Group name/ID
        supplier_group_name: Updated supplier group name (optional)
        parent_supplier_group: Updated parent supplier group (optional)
        is_group: Update is_group flag (optional)
        payment_terms: Updated payment terms (optional)
    
    Returns:
        dict: Update result
    """
    try:
        supplier_group = frappe.get_doc("Supplier Group", name)
        
        if supplier_group_name:
            supplier_group.supplier_group_name = supplier_group_name
        if parent_supplier_group is not None:
            # Verify parent exists if provided
            if parent_supplier_group and not frappe.db.exists("Supplier Group", parent_supplier_group):
                return {
                    "success": False,
                    "message": f"Parent Supplier Group '{parent_supplier_group}' does not exist",
                }
            supplier_group.parent_supplier_group = parent_supplier_group
        if is_group is not None:
            supplier_group.is_group = 1 if is_group else 0
        if payment_terms is not None:
            supplier_group.payment_terms = payment_terms
        
        # Use ignore_permissions=True for whitelisted API endpoints
        supplier_group.save(ignore_permissions=True)
        frappe.db.commit()
        
        return {
            "success": True,
            "message": "Supplier Group updated successfully",
            "name": supplier_group.name,
        }
    except frappe.PermissionError as e:
        frappe.log_error(f"Permission error updating supplier group: {str(e)}", "Supplier Group Update Permission Error")
        return {
            "success": False,
            "message": f"Permission denied: You do not have permission to update Supplier Group documents. Please contact your administrator to grant you the necessary role permissions.",
            "error_type": "permission_error",
            "required_permission": "Supplier Group: Write",
        }
    except frappe.exceptions.ValidationError as e:
        frappe.log_error(f"Validation error updating supplier group: {str(e)}", "Supplier Group Update Validation Error")
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(f"Error updating supplier group: {str(e)}", "Supplier Group Update Error")
        return {
            "success": False,
            "message": f"Error updating supplier group: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def get_supplier_group_details(name: str) -> dict:
    """
    Get detailed information about a specific supplier group.
    
    Args:
        name: Supplier Group name/ID
    
    Returns:
        dict: Supplier group details
    """
    try:
        supplier_group = frappe.get_doc("Supplier Group", name)
        
        # Get count of suppliers in this group
        supplier_count = frappe.db.count("Supplier", {"supplier_group": name, "disabled": 0})
        
        return {
            "success": True,
            "data": {
                "name": supplier_group.name,
                "supplier_group_name": supplier_group.supplier_group_name,
                "is_group": supplier_group.is_group,
                "parent_supplier_group": supplier_group.parent_supplier_group,
                "payment_terms": supplier_group.payment_terms,
                "supplier_count": supplier_count,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error fetching supplier group details: {str(e)}",
        }

