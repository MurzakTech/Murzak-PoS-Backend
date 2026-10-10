"""
Customer Management API
Handles customer listing, creation, and management
"""

import frappe
from frappe import _
from frappe.utils import flt

from techsavanna_pos.api.payment_gateway_common import resolve_company
from typing import Optional, Dict, List


@frappe.whitelist()
def create_customer(
    customer_name: str,
    customer_type: str = "Individual",
    customer_group: str = None,
    territory: str = None,
    tax_id: str = None,
    mobile_no: str = None,
    email_id: str = None,
    default_currency: str = None,
    default_price_list: str = None,
    disabled: bool = False,
    company: str = None,
) -> Dict:
    """
    Create a new customer.
    
    Customer will be automatically isolated per company of the logged-in user.
    This ensures customers and their loyalty points are properly isolated by company.
    
    Args:
        customer_name: Name of the customer (required)
        customer_type: Customer type - "Company", "Individual", or "Partnership" (default: "Individual")
        customer_group: Customer group (optional, uses default if not provided)
        territory: Territory (optional)
        tax_id: Tax ID/PIN (optional)
        mobile_no: Mobile phone number (optional)
        email_id: Email address (optional)
        default_currency: Default currency (optional)
        default_price_list: Default price list (optional)
        disabled: Whether customer is disabled (default: False)
        company: Company name (optional, defaults to logged-in user's company)
    
    Returns:
        dict: Created customer details
    """
    try:
        # Validate customer type
        valid_types = ["Company", "Individual", "Partnership"]
        if customer_type not in valid_types:
            frappe.throw(
                _("Invalid customer_type. Must be one of: {0}").format(", ".join(valid_types)),
                frappe.ValidationError
            )
        
        # Get logged-in user's company (required for isolation)
        if company:
            # Refuse a company the caller does not belong to
            company = resolve_company(company)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            frappe.throw(
                _("Company is required. Please set a default company or provide company parameter."),
                frappe.ValidationError
            )
        
        # Validate company exists
        if not frappe.db.exists("Company", company):
            frappe.throw(
                _("Company '{0}' does not exist").format(company),
                frappe.ValidationError
            )
        
        # Check if customer already exists for this company
        # Check by customer_name + custom_company if field exists
        existing = None
        if frappe.db.has_column("Customer", "custom_company"):
            existing = frappe.db.exists(
                "Customer",
                {
                    "customer_name": customer_name,
                    "custom_company": company
                }
            )
        else:
            # Fallback: check by customer_name only (before custom_company is added)
            existing = frappe.db.exists("Customer", {"customer_name": customer_name})
        
        if existing:
            return {
                "success": False,
                "message": _("Customer '{0}' already exists for company '{1}'").format(customer_name, company),
                "error_type": "duplicate",
                "name": existing,
            }
        
        # Get default customer group if not provided
        if not customer_group:
            customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
            if not customer_group:
                customer_group = "All Customer Groups"
        
        # Get default territory if not provided
        if not territory:
            territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
            if not territory:
                territory = "All Territories"
        
        # Create customer
        customer = frappe.new_doc("Customer")
        customer.customer_name = customer_name
        customer.customer_type = customer_type
        customer.customer_group = customer_group
        customer.territory = territory
        customer.disabled = 1 if disabled else 0
        
        if tax_id:
            customer.tax_id = tax_id
        if mobile_no:
            customer.mobile_no = mobile_no
        if email_id:
            customer.email_id = email_id
        if default_currency:
            customer.default_currency = default_currency
        if default_price_list:
            customer.default_price_list = default_price_list
        
        # Set company isolation (custom_company field) - ensures customers are isolated per company
        if frappe.db.has_column("Customer", "custom_company"):
            customer.custom_company = company
        
        # Insert customer
        customer.insert(ignore_permissions=True)
        
        # Set up customer account for company
        if company:
            if frappe.db.exists("Company", company):
                # Check if account already exists
                account_exists = frappe.db.exists(
                    "Party Account",
                    {
                        "parent": customer.name,
                        "parenttype": "Customer",
                        "company": company
                    }
                )
                
                if not account_exists:
                    # Get receivable account for company
                    receivable_account = frappe.db.get_value(
                        "Company",
                        company,
                        "default_receivable_account"
                    )
                    
                    if receivable_account:
                        customer.append("accounts", {
                            "company": company,
                            "account": receivable_account
                        })
                        customer.save(ignore_permissions=True)
        
        return {
            "success": True,
            "message": _("Customer created successfully"),
            "data": {
                "name": customer.name,
                "customer_name": customer.customer_name,
                "customer_type": customer.customer_type,
                "customer_group": customer.customer_group,
                "territory": customer.territory,
                "tax_id": customer.tax_id,
                "mobile_no": customer.mobile_no,
                "email_id": customer.email_id,
                "disabled": bool(customer.disabled),
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Customer Creation Validation Error",
            f"Validation error creating customer: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error("Customer Creation Error", f"Error creating customer: {str(e)}")
        return {
            "success": False,
            "message": f"Error creating customer: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def list_customers(
    company: str = None,
    customer_group: str = None,
    territory: str = None,
    customer_type: str = None,
    disabled: bool = False,
    search_term: str = None,
    limit: int = 20,
    offset: int = 0,
    filter_by_company_transactions: bool = False,
) -> Dict:
    """
    Get list of customers with optional filters.
    
    Args:
        company: Company name (optional, filters customers that have transactions with this company if filter_by_company_transactions=True)
        customer_group: Filter by customer group (optional)
        territory: Filter by territory (optional)
        customer_type: Filter by customer type - "Company", "Individual", or "Partnership" (optional)
        disabled: Include disabled customers (default: False)
        search_term: Search term for customer name or ID (optional)
        limit: Number of records to return (default: 20)
        offset: Offset for pagination (default: 0)
        filter_by_company_transactions: If True and company is provided, only return customers with transactions for that company (default: False)
    
    Returns:
        dict: List of customers
    
    Note: 
    - By default, all customers are returned regardless of company transactions.
    - When company is provided and filter_by_company_transactions=True, only customers
      that have sales invoices or sales orders for that company will be returned.
    - Set filter_by_company_transactions=False (default) to return all customers.
    """
    try:
        # Get logged-in user's company (required for isolation)
        if company:
            # Refuse a company the caller does not belong to
            company = resolve_company(company)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        # Company is required for customer isolation
        if not company:
            return {
                "success": False,
                "message": "Company is required for customer listing. Please set a default company or provide company parameter.",
                "data": {
                    "customers": [],
                    "pagination": {
                        "total": 0,
                        "limit": limit,
                        "offset": offset
                    }
                }
            }
        
        filters = {}
        
        # Company isolation - only show customers for the user's company
        if frappe.db.has_column("Customer", "custom_company"):
            filters["custom_company"] = company
        # If custom_company field doesn't exist yet, we'll filter by transactions instead
        
        if not disabled:
            filters["disabled"] = 0
        
        if customer_group:
            filters["customer_group"] = customer_group
        
        if territory:
            filters["territory"] = territory
        
        if customer_type:
            filters["customer_type"] = customer_type
        
        # Build search condition
        or_filters = {}
        if search_term:
            or_filters = {
                "customer_name": ["like", f"%{search_term}%"],
                "name": ["like", f"%{search_term}%"],
                "mobile_no": ["like", f"%{search_term}%"],
                "email_id": ["like", f"%{search_term}%"],
            }
        
        customers = frappe.get_all(
            "Customer",
            filters=filters,
            or_filters=or_filters if or_filters else None,
            fields=[
                "name",
                "customer_name",
                "customer_type",
                "customer_group",
                "territory",
                "tax_id",
                "mobile_no",
                "email_id",
                "disabled",
                "default_currency",
                "default_price_list",
            ],
            limit=limit,
            start=offset,
            order_by="customer_name",
        )
        
        # Add credit limit information if company is provided
        if company and frappe.db.exists("Company", company):
            from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
            
            for customer in customers:
                credit_limit = get_credit_limit(customer.name, company)
                outstanding = get_customer_outstanding(customer.name, company)
                customer["credit_limit"] = credit_limit
                customer["outstanding_amount"] = outstanding
                customer["available_credit"] = flt(credit_limit) - flt(outstanding)
                customer["credit_utilization_percent"] = (
                    (flt(outstanding) / flt(credit_limit) * 100)
                    if credit_limit > 0 else 0
                )
                customer["is_over_limit"] = (
                    flt(outstanding) > flt(credit_limit) if credit_limit > 0 else False
                )
        
        # If custom_company field doesn't exist yet, fall back to filtering by transactions
        # This ensures backward compatibility during migration
        if not frappe.db.has_column("Customer", "custom_company"):
            if company:
                # Filter customers that have transactions with the company as a fallback
                filter_by_company_transactions = True
            else:
                # If no company and field doesn't exist, return all customers
                # (for backward compatibility only - company should always be provided)
                pass
        
        # If company is specified and filtering is enabled, filter customers that have transactions with that company
        if company and filter_by_company_transactions:
            from frappe.query_builder import DocType
            
            # Get customers from Sales Invoices
            SalesInvoice = DocType("Sales Invoice")
            si_customers = (
                frappe.qb.from_(SalesInvoice)
                .select(SalesInvoice.customer)
                .where(SalesInvoice.company == company)
                .where(SalesInvoice.docstatus != 2)
                .distinct()
                .run(as_dict=True)
            )
            
            # Get customers from Sales Orders
            SalesOrder = DocType("Sales Order")
            so_customers = (
                frappe.qb.from_(SalesOrder)
                .select(SalesOrder.customer)
                .where(SalesOrder.company == company)
                .where(SalesOrder.docstatus != 2)
                .distinct()
                .run(as_dict=True)
            )
            
            # Combine both lists and get unique customer names
            company_customer_names = set()
            for c in si_customers:
                if c.customer:
                    company_customer_names.add(c.customer)
            for c in so_customers:
                if c.customer:
                    company_customer_names.add(c.customer)
            
            # Filter customers to only those that have transactions with the company
            if company_customer_names:
                customers = [c for c in customers if c.name in company_customer_names]
            else:
                # If no customers have transactions, return empty list
                customers = []
        
        return {
            "success": True,
            "data": customers,
            "count": len(customers),
        }
    except Exception as e:
        frappe.log_error("List Customers Error", f"Error listing customers: {str(e)}")
        return {
            "success": False,
            "message": f"Error listing customers: {str(e)}",
        }


@frappe.whitelist()
def get_customer(name: str, company: str = None) -> Dict:
    """
    Get detailed information about a specific customer.
    
    Customer will be verified to belong to the logged-in user's company for isolation.
    
    Args:
        name: Customer name/ID
        company: Company name (optional, defaults to logged-in user's company)
    
    Returns:
        dict: Customer details including outstanding amounts
    """
    try:
        if not frappe.db.exists("Customer", name):
            return {
                "success": False,
                "message": _("Customer {0} not found").format(name),
                "error_type": "not_found",
            }
        
        # Get logged-in user's company (required for isolation)
        if company:
            # Refuse a company the caller does not belong to
            company = resolve_company(company)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        customer = frappe.get_doc("Customer", name)
        
        # Verify company isolation if custom_company field exists
        if company and frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "success": False,
                    "message": _("Customer {0} does not belong to company {1}").format(name, company),
                    "error_type": "access_denied",
                }
        
        # Get outstanding amount using query builder
        from frappe.query_builder import DocType, functions as fn
        
        SalesInvoice = DocType("Sales Invoice")
        outstanding_result = (
            frappe.qb.from_(SalesInvoice)
            .select(fn.Sum(SalesInvoice.outstanding_amount).as_("total"))
            .where(SalesInvoice.customer == name)
            .where(SalesInvoice.docstatus == 1)
            .where(SalesInvoice.outstanding_amount > 0)
            .run(as_dict=True)
        )
        outstanding_amount = flt(outstanding_result[0].get("total")) if outstanding_result and outstanding_result[0].get("total") is not None else 0.0
        
        # Get total sales amount using query builder
        total_sales_result = (
            frappe.qb.from_(SalesInvoice)
            .select(fn.Sum(SalesInvoice.grand_total).as_("total"))
            .where(SalesInvoice.customer == name)
            .where(SalesInvoice.docstatus == 1)
            .run(as_dict=True)
        )
        total_sales = flt(total_sales_result[0].get("total")) if total_sales_result and total_sales_result[0].get("total") is not None else 0.0
        
        # Get customer accounts
        accounts = []
        for account in customer.accounts:
            accounts.append({
                "company": account.company,
                "account": account.account,
            })
        
        # Get credit limit information for all companies
        from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
        
        credit_limits = []
        company_set = set()
        for limit in customer.credit_limits:
            company_set.add(limit.company)
        for account in customer.accounts:
            company_set.add(account.company)
        
        # If no companies found, get default company
        if not company_set:
            default_company = frappe.defaults.get_user_default("Company")
            if default_company:
                company_set.add(default_company)
        
        for comp in company_set:
            effective_limit = get_credit_limit(customer.name, comp)
            comp_outstanding = get_customer_outstanding(customer.name, comp)
            
            # Get customer-level credit limit if exists
            customer_limit = None
            bypass_check = False
            for limit in customer.credit_limits:
                if limit.company == comp:
                    customer_limit = flt(limit.credit_limit)
                    bypass_check = bool(limit.bypass_credit_limit_check)
                    break
            
            # Determine limit source
            limit_source = "Customer"
            if not customer_limit:
                # Check customer group
                customer_group = customer.customer_group
                group_limit = frappe.db.get_value(
                    "Customer Credit Limit",
                    {"parent": customer_group, "parenttype": "Customer Group", "company": comp},
                    "credit_limit"
                )
                if group_limit:
                    limit_source = "Customer Group"
                    customer_limit = flt(group_limit)
                else:
                    # Check company default
                    company_limit = frappe.db.get_value("Company", comp, "credit_limit")
                    if company_limit:
                        limit_source = "Company"
                        customer_limit = flt(company_limit)
            
            credit_limits.append({
                "company": comp,
                "credit_limit": customer_limit if customer_limit else effective_limit,
                "effective_credit_limit": effective_limit,
                "outstanding_amount": comp_outstanding,
                "available_credit": flt(effective_limit) - flt(comp_outstanding),
                "credit_utilization_percent": (
                    (flt(comp_outstanding) / flt(effective_limit) * 100)
                    if effective_limit > 0 else 0
                ),
                "limit_source": limit_source,
                "bypass_credit_limit_check": bypass_check,
                "is_over_limit": flt(comp_outstanding) > flt(effective_limit) if effective_limit > 0 else False,
            })
        
        return {
            "success": True,
            "data": {
                "name": customer.name,
                "customer_name": customer.customer_name,
                "customer_type": customer.customer_type,
                "customer_group": customer.customer_group,
                "territory": customer.territory,
                "tax_id": customer.tax_id,
                "mobile_no": customer.mobile_no,
                "email_id": customer.email_id,
                "disabled": bool(customer.disabled),
                "default_currency": customer.default_currency,
                "default_price_list": customer.default_price_list,
                "accounts": accounts,
                "outstanding_amount": outstanding_amount,
                "total_sales": total_sales,
                "credit_limits": credit_limits,
            },
        }
    except Exception as e:
        frappe.log_error("Get Customer Error", f"Error fetching customer: {str(e)}")
        return {
            "success": False,
            "message": f"Error fetching customer: {str(e)}",
        }


@frappe.whitelist()
def update_customer(
    name: str,
    customer_name: str = None,
    customer_type: str = None,
    customer_group: str = None,
    territory: str = None,
    tax_id: str = None,
    mobile_no: str = None,
    email_id: str = None,
    default_currency: str = None,
    default_price_list: str = None,
    disabled: bool = None,
    company: str = None,
) -> Dict:
    """
    Update an existing customer.
    
    Customer must belong to the logged-in user's company for isolation.
    
    Args:
        name: Customer name/ID
        customer_name: Updated customer name (optional)
        customer_type: Updated customer type (optional)
        customer_group: Updated customer group (optional)
        territory: Updated territory (optional)
        tax_id: Updated tax ID (optional)
        mobile_no: Updated mobile number (optional)
        email_id: Updated email address (optional)
        default_currency: Updated default currency (optional)
        default_price_list: Updated default price list (optional)
        disabled: Update disabled status (optional)
        company: Company name (optional, defaults to logged-in user's company for validation)
    
    Returns:
        dict: Update result
    """
    try:
        if not frappe.db.exists("Customer", name):
            return {
                "success": False,
                "message": _("Customer {0} not found").format(name),
                "error_type": "not_found",
            }
        
        # Get logged-in user's company (required for isolation validation)
        if company:
            # Refuse a company the caller does not belong to
            company = resolve_company(company)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        customer = frappe.get_doc("Customer", name)
        
        # Verify company isolation if custom_company field exists
        if company and frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "success": False,
                    "message": _("Customer {0} does not belong to company {1}. Cannot update.").format(name, company),
                    "error_type": "access_denied",
                }
        
        if customer_name:
            customer.customer_name = customer_name
        if customer_type:
            if customer_type not in ["Company", "Individual", "Partnership"]:
                frappe.throw(
                    _("Invalid customer_type. Must be one of: Company, Individual, Partnership"),
                    frappe.ValidationError
                )
            customer.customer_type = customer_type
        if customer_group:
            customer.customer_group = customer_group
        if territory:
            customer.territory = territory
        if tax_id is not None:
            customer.tax_id = tax_id
        if mobile_no is not None:
            customer.mobile_no = mobile_no
        if email_id is not None:
            customer.email_id = email_id
        if default_currency is not None:
            customer.default_currency = default_currency
        if default_price_list is not None:
            customer.default_price_list = default_price_list
        if disabled is not None:
            customer.disabled = 1 if disabled else 0
        
        customer.save(ignore_permissions=True)
        
        return {
            "success": True,
            "message": _("Customer updated successfully"),
            "data": {
                "name": customer.name,
                "customer_name": customer.customer_name,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error(
            "Customer Update Validation Error",
            f"Validation error updating customer: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error("Customer Update Error", f"Error updating customer: {str(e)}")
        return {
            "success": False,
            "message": f"Error updating customer: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def set_customer_credit_limit(
    customer: str,
    company: str,
    credit_limit: float,
    bypass_credit_limit_check: bool = False,
) -> Dict:
    """
    Set or update credit limit for a customer for a specific company.
    
    Args:
        customer: Customer name/ID
        company: Company name
        credit_limit: Credit limit amount (must be >= 0)
        bypass_credit_limit_check: Whether to bypass credit limit check at Sales Order level (default: False)
    
    Returns:
        dict: Operation result with credit limit details
    """
    try:
        # Refuse a company the caller does not belong to
        company = resolve_company(company)
        # Validate customer exists
        if not frappe.db.exists("Customer", customer):
            return {
                "success": False,
                "message": _("Customer {0} not found").format(customer),
                "error_type": "not_found",
            }
        
        # Validate company exists
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company {0} not found").format(company),
                "error_type": "not_found",
            }
        
        # Validate credit limit
        credit_limit = flt(credit_limit)
        if credit_limit < 0:
            return {
                "success": False,
                "message": _("Credit limit cannot be negative"),
                "error_type": "validation_error",
            }
        
        # Get customer document
        customer_doc = frappe.get_doc("Customer", customer)
        
        # Check if credit limit already exists for this company
        existing_limit = None
        for limit in customer_doc.credit_limits:
            if limit.company == company:
                existing_limit = limit
                break
        
        if existing_limit:
            # Update existing credit limit
            existing_limit.credit_limit = credit_limit
            existing_limit.bypass_credit_limit_check = 1 if bypass_credit_limit_check else 0
        else:
            # Add new credit limit
            customer_doc.append("credit_limits", {
                "company": company,
                "credit_limit": credit_limit,
                "bypass_credit_limit_check": 1 if bypass_credit_limit_check else 0,
            })
        
        customer_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Get updated credit limit info
        from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
        
        effective_credit_limit = get_credit_limit(customer, company)
        outstanding_amount = get_customer_outstanding(customer, company)
        available_credit = flt(effective_credit_limit) - flt(outstanding_amount)
        
        return {
            "success": True,
            "message": _("Credit limit updated successfully"),
            "data": {
                "customer": customer,
                "company": company,
                "credit_limit": credit_limit,
                "bypass_credit_limit_check": bool(bypass_credit_limit_check),
                "effective_credit_limit": effective_credit_limit,
                "outstanding_amount": outstanding_amount,
                "available_credit": available_credit,
                "credit_utilization_percent": (
                    (flt(outstanding_amount) / flt(effective_credit_limit) * 100)
                    if effective_credit_limit > 0 else 0
                ),
            },
        }
    
    except frappe.ValidationError as e:
        frappe.log_error(
            "Set Credit Limit Validation Error",
            f"Validation error setting credit limit for customer {customer}: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(
            "Set Credit Limit Error",
            f"Error setting credit limit for customer {customer}: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Error setting credit limit: {str(e)}",
            "error_type": "server_error",
        }


@frappe.whitelist()
def get_customer_credit_limit(customer: str, company: str = None) -> Dict:
    """
    Get credit limit information for a customer.
    If company is provided, returns credit limit for that company.
    If company is not provided, returns credit limits for all companies.
    
    Args:
        customer: Customer name/ID
        company: Company name (optional)
    
    Returns:
        dict: Credit limit information
    """
    try:
        # Refuse a company the caller does not belong to
        if company:
            company = resolve_company(company)
        # Validate customer exists
        if not frappe.db.exists("Customer", customer):
            return {
                "success": False,
                "message": _("Customer {0} not found").format(customer),
                "error_type": "not_found",
            }
        
        from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
        
        customer_doc = frappe.get_doc("Customer", customer)
        
        # Get all companies if company not specified
        companies = []
        if company:
            if not frappe.db.exists("Company", company):
                return {
                    "success": False,
                    "message": _("Company {0} not found").format(company),
                    "error_type": "not_found",
                }
            companies = [company]
        else:
            # Get all companies from customer's credit limits and accounts
            company_set = set()
            for limit in customer_doc.credit_limits:
                company_set.add(limit.company)
            for account in customer_doc.accounts:
                company_set.add(account.company)
            
            # If no companies found, get default company
            if not company_set:
                default_company = frappe.defaults.get_user_default("Company")
                if default_company:
                    company_set.add(default_company)
            
            companies = list(company_set)
        
        credit_limit_data = []
        for comp in companies:
            effective_limit = get_credit_limit(customer, comp)
            outstanding = get_customer_outstanding(customer, comp)
            available = flt(effective_limit) - flt(outstanding)
            
            # Get customer-level credit limit if exists
            customer_limit = None
            bypass_check = False
            for limit in customer_doc.credit_limits:
                if limit.company == comp:
                    customer_limit = flt(limit.credit_limit)
                    bypass_check = bool(limit.bypass_credit_limit_check)
                    break
            
            # Determine limit source
            limit_source = "Customer"
            if not customer_limit:
                # Check customer group
                customer_group = customer_doc.customer_group
                group_limit = frappe.db.get_value(
                    "Customer Credit Limit",
                    {"parent": customer_group, "parenttype": "Customer Group", "company": comp},
                    "credit_limit"
                )
                if group_limit:
                    limit_source = "Customer Group"
                    customer_limit = flt(group_limit)
                else:
                    # Check company default
                    company_limit = frappe.db.get_value("Company", comp, "credit_limit")
                    if company_limit:
                        limit_source = "Company"
                        customer_limit = flt(company_limit)
            
            credit_limit_data.append({
                "company": comp,
                "credit_limit": customer_limit if customer_limit else effective_limit,
                "effective_credit_limit": effective_limit,
                "outstanding_amount": outstanding,
                "available_credit": available,
                "credit_utilization_percent": (
                    (flt(outstanding) / flt(effective_limit) * 100)
                    if effective_limit > 0 else 0
                ),
                "limit_source": limit_source,
                "bypass_credit_limit_check": bypass_check,
                "is_over_limit": flt(outstanding) > flt(effective_limit) if effective_limit > 0 else False,
            })
        
        return {
            "success": True,
            "data": {
                "customer": customer,
                "customer_name": customer_doc.customer_name,
                "credit_limits": credit_limit_data if not company else credit_limit_data[0],
            },
        }
    
    except Exception as e:
        frappe.log_error(
            "Get Credit Limit Error",
            f"Error getting credit limit for customer {customer}: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Error getting credit limit: {str(e)}",
            "error_type": "server_error",
        }


@frappe.whitelist()
def get_customer_credit_history(
    customer: str,
    company: str = None,
    from_date: str = None,
    to_date: str = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict:
    """
    Get credit history (transactions) for a customer.
    Shows all transactions that affect the customer's credit balance.
    
    Args:
        customer: Customer name/ID
        company: Company name (optional, filters by company)
        from_date: Start date in YYYY-MM-DD format (optional)
        to_date: End date in YYYY-MM-DD format (optional)
        limit: Number of records to return (default: 50)
        offset: Offset for pagination (default: 0)
    
    Returns:
        dict: Credit history with transactions
    """
    try:
        # Refuse a company the caller does not belong to
        if company:
            company = resolve_company(company)
        # Validate customer exists
        if not frappe.db.exists("Customer", customer):
            return {
                "success": False,
                "message": _("Customer {0} not found").format(customer),
                "error_type": "not_found",
            }
        
        from frappe.query_builder import DocType, functions as fn
        from frappe.utils import getdate, nowdate
        
        # Build filters
        filters = {"customer": customer}
        if company:
            filters["company"] = company
        
        # Get Sales Invoices
        SalesInvoice = DocType("Sales Invoice")
        si_query = (
            frappe.qb.from_(SalesInvoice)
            .select(
                SalesInvoice.name.as_("voucher_no"),
                fn.Literal("Sales Invoice").as_("voucher_type"),
                SalesInvoice.posting_date,
                SalesInvoice.grand_total.as_("amount"),
                SalesInvoice.outstanding_amount,
                SalesInvoice.status,
                SalesInvoice.company,
            )
            .where(SalesInvoice.customer == customer)
            .where(SalesInvoice.docstatus == 1)
        )
        
        if company:
            si_query = si_query.where(SalesInvoice.company == company)
        if from_date:
            si_query = si_query.where(SalesInvoice.posting_date >= getdate(from_date))
        if to_date:
            si_query = si_query.where(SalesInvoice.posting_date <= getdate(to_date))
        
        # Get Payment Entries
        PaymentEntry = DocType("Payment Entry")
        PaymentEntryReference = DocType("Payment Entry Reference")
        
        pe_query = (
            frappe.qb.from_(PaymentEntry)
            .join(PaymentEntryReference)
            .on(PaymentEntryReference.parent == PaymentEntry.name)
            .select(
                PaymentEntry.name.as_("voucher_no"),
                fn.Literal("Payment Entry").as_("voucher_type"),
                PaymentEntry.posting_date,
                (PaymentEntryReference.allocated_amount * -1).as_("amount"),
                fn.Literal(0).as_("outstanding_amount"),
                PaymentEntry.status,
                PaymentEntry.company,
            )
            .where(PaymentEntry.party == customer)
            .where(PaymentEntry.party_type == "Customer")
            .where(PaymentEntry.docstatus == 1)
            .where(PaymentEntryReference.reference_doctype == "Sales Invoice")
        )
        
        if company:
            pe_query = pe_query.where(PaymentEntry.company == company)
        if from_date:
            pe_query = pe_query.where(PaymentEntry.posting_date >= getdate(from_date))
        if to_date:
            pe_query = pe_query.where(PaymentEntry.posting_date <= getdate(to_date))
        
        # Get Credit Notes
        CreditNote = DocType("Sales Invoice")
        cn_query = (
            frappe.qb.from_(CreditNote)
            .select(
                CreditNote.name.as_("voucher_no"),
                fn.Literal("Credit Note").as_("voucher_type"),
                CreditNote.posting_date,
                (CreditNote.grand_total * -1).as_("amount"),
                CreditNote.outstanding_amount,
                CreditNote.status,
                CreditNote.company,
            )
            .where(CreditNote.customer == customer)
            .where(CreditNote.is_return == 1)
            .where(CreditNote.docstatus == 1)
        )
        
        if company:
            cn_query = cn_query.where(CreditNote.company == company)
        if from_date:
            cn_query = cn_query.where(CreditNote.posting_date >= getdate(from_date))
        if to_date:
            cn_query = cn_query.where(CreditNote.posting_date <= getdate(to_date))
        
        # Combine and order by date
        # Note: We'll fetch and combine in Python for simplicity
        si_results = si_query.run(as_dict=True)
        pe_results = pe_query.run(as_dict=True)
        cn_results = cn_query.run(as_dict=True)
        
        # Combine all results
        all_transactions = []
        all_transactions.extend(si_results)
        all_transactions.extend(pe_results)
        all_transactions.extend(cn_results)
        
        # Sort by posting date (descending)
        all_transactions.sort(key=lambda x: x.posting_date, reverse=True)
        
        # Apply pagination
        total_count = len(all_transactions)
        paginated_transactions = all_transactions[offset:offset + limit]
        
        # Get current credit summary
        from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
        
        if company:
            effective_limit = get_credit_limit(customer, company)
            outstanding = get_customer_outstanding(customer, company)
            credit_summary = {
                "company": company,
                "credit_limit": effective_limit,
                "outstanding_amount": outstanding,
                "available_credit": flt(effective_limit) - flt(outstanding),
            }
        else:
            credit_summary = None
        
        return {
            "success": True,
            "data": {
                "customer": customer,
                "transactions": paginated_transactions,
                "total_count": total_count,
                "credit_summary": credit_summary,
            },
        }
    
    except Exception as e:
        frappe.log_error(
            "Get Credit History Error",
            f"Error getting credit history for customer {customer}: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Error getting credit history: {str(e)}",
            "error_type": "server_error",
        }


@frappe.whitelist()
def remove_customer_credit_limit(customer: str, company: str) -> Dict:
    """
    Remove credit limit for a customer for a specific company.
    After removal, credit limit will fall back to customer group or company default.
    
    Args:
        customer: Customer name/ID
        company: Company name
    
    Returns:
        dict: Operation result
    """
    try:
        # Refuse a company the caller does not belong to
        company = resolve_company(company)
        # Validate customer exists
        if not frappe.db.exists("Customer", customer):
            return {
                "success": False,
                "message": _("Customer {0} not found").format(customer),
                "error_type": "not_found",
            }
        
        # Validate company exists
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company {0} not found").format(company),
                "error_type": "not_found",
            }
        
        # Get customer document
        customer_doc = frappe.get_doc("Customer", customer)
        
        # Find and remove credit limit
        removed = False
        for i, limit in enumerate(customer_doc.credit_limits):
            if limit.company == company:
                customer_doc.credit_limits.pop(i)
                removed = True
                break
        
        if not removed:
            return {
                "success": False,
                "message": _("No credit limit found for customer {0} and company {1}").format(customer, company),
                "error_type": "not_found",
            }
        
        customer_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        # Get effective credit limit after removal
        from erpnext.selling.doctype.customer.customer import get_credit_limit, get_customer_outstanding
        
        effective_limit = get_credit_limit(customer, company)
        outstanding = get_customer_outstanding(customer, company)
        
        return {
            "success": True,
            "message": _("Credit limit removed successfully"),
            "data": {
                "customer": customer,
                "company": company,
                "effective_credit_limit": effective_limit,
                "outstanding_amount": outstanding,
                "available_credit": flt(effective_limit) - flt(outstanding),
            },
        }
    
    except Exception as e:
        frappe.log_error(
            "Remove Credit Limit Error",
            f"Error removing credit limit for customer {customer}: {str(e)}"
        )
        return {
            "success": False,
            "message": f"Error removing credit limit: {str(e)}",
            "error_type": "server_error",
        }

