"""
Account Provisioning Management API

Handles provisional accounting setup, validation, and default account configuration.
Includes autoprovisioning capabilities to automatically find or create provisional
accounts when they are missing.

Key Features:
- Status checking and validation
- Account management (set, list, search)
- Autoprovisioning (automatic account finding/creation)
- Integration helpers for other APIs

Autoprovisioning:
The API supports two methods of autoprovisioning:
1. Direct API: auto_configure_provisional_account() - for explicit provisioning
2. Helper function: validate_and_get_provisional_account(..., auto_fix=True) - for integrated provisioning

See documentation in docs/ACCOUNT_PROVISIONING_API_DOCUMENTATION.md for details.
"""

import frappe
from frappe import _
from typing import Optional, Dict, List, Tuple
from frappe.utils import cint


@frappe.whitelist()
def get_provisional_accounting_status(company: str) -> Dict:
    """
    Get the current provisional accounting status and configuration for a company.
    
    Args:
        company: Company name/ID
    
    Returns:
        dict: Provisional accounting status and configuration details
    """
    try:
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company '{0}' not found").format(company),
                "error_type": "company_not_found",
            }
        
        company_doc = frappe.get_doc("Company", company)
        
        enable_provisional_accounting = cint(
            company_doc.get("enable_provisional_accounting_for_non_stock_items") or 0
        )
        default_provisional_account = company_doc.get("default_provisional_account")
        
        # Get account details if set
        account_details = None
        if default_provisional_account:
            account_details = frappe.db.get_value(
                "Account",
                default_provisional_account,
                ["account_name", "account_type", "root_type", "parent_account"],
                as_dict=True
            )
        
        # Validate configuration
        is_valid = True
        validation_message = None
        if enable_provisional_accounting and not default_provisional_account:
            is_valid = False
            validation_message = _(
                "Provisional accounting is enabled but 'Default Provisional Account' is not set. "
                "Please set it in Company settings or use the 'set_default_provisional_account' API."
            )
        
        return {
            "success": True,
            "data": {
                "company": company,
                "enable_provisional_accounting": bool(enable_provisional_accounting),
                "default_provisional_account": default_provisional_account,
                "account_details": account_details,
                "is_valid": is_valid,
                "validation_message": validation_message,
            },
        }
    except Exception as e:
        frappe.log_error(f"Error getting provisional accounting status: {str(e)}", "Get Provisional Accounting Status Error")
        return {
            "success": False,
            "message": f"Error getting provisional accounting status: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def set_default_provisional_account(
    company: str,
    account: str,
    auto_enable_provisional_accounting: bool = False,
) -> Dict:
    """
    Set the default provisional account for a company.
    
    Args:
        company: Company name/ID
        account: Account name/ID to set as default provisional account
        auto_enable_provisional_accounting: If True, enables provisional accounting if not already enabled (default: False)
    
    Returns:
        dict: Operation result
    """
    try:
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company '{0}' not found").format(company),
                "error_type": "company_not_found",
            }
        
        if not frappe.db.exists("Account", account):
            return {
                "success": False,
                "message": _("Account '{0}' not found").format(account),
                "error_type": "account_not_found",
            }
        
        # Validate account belongs to company
        account_company = frappe.db.get_value("Account", account, "company")
        if account_company != company:
            return {
                "success": False,
                "message": _("Account '{0}' does not belong to company '{1}'").format(account, company),
                "error_type": "account_company_mismatch",
            }
        
        # Validate account is not a group account
        is_group = frappe.db.get_value("Account", account, "is_group")
        if is_group:
            return {
                "success": False,
                "message": _("Account '{0}' is a group account. Please select a ledger account.").format(account),
                "error_type": "group_account_error",
            }
        
        # Get account details for validation
        account_details = frappe.db.get_value(
            "Account",
            account,
            ["account_name", "account_type", "root_type", "disabled"],
            as_dict=True
        )
        
        # Check if account is disabled
        if account_details.get("disabled"):
            return {
                "success": False,
                "message": _("Account '{0}' is disabled. Please select an active account.").format(account),
                "error_type": "disabled_account_error",
            }
        
        # Recommended account types for provisional accounts
        recommended_types = [
            "Service Received But Not Billed",
            "Asset Received But Not Billed",
            "Expense Account",
            "Indirect Expense",
            "Direct Expense",
        ]
        
        warnings = []
        if account_details.get("account_type") not in recommended_types:
            warnings.append(
                _("Account type '{0}' is not a typical provisional account type. "
                  "Recommended types: {1}").format(
                    account_details.get("account_type", "Unknown"),
                    ", ".join(recommended_types[:3])
                )
            )
        
        # Update company document
        company_doc = frappe.get_doc("Company", company)
        company_doc.default_provisional_account = account
        
        # Auto-enable provisional accounting if requested
        if auto_enable_provisional_accounting:
            company_doc.enable_provisional_accounting_for_non_stock_items = 1
        
        company_doc.save(ignore_permissions=True)
        frappe.db.commit()
        
        response = {
            "success": True,
            "message": _("Default provisional account set successfully"),
            "data": {
                "company": company,
                "account": account,
                "account_name": account_details.get("account_name"),
                "account_type": account_details.get("account_type"),
                "root_type": account_details.get("root_type"),
                "provisional_accounting_enabled": cint(
                    company_doc.get("enable_provisional_accounting_for_non_stock_items") or 0
                ),
            },
        }
        
        if warnings:
            response["warnings"] = warnings
        
        return response
    except frappe.ValidationError as e:
        frappe.log_error(
            f"Validation error setting default provisional account: {str(e)}",
            "Set Default Provisional Account Validation Error"
        )
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error(f"Error setting default provisional account: {str(e)}", "Set Default Provisional Account Error")
        return {
            "success": False,
            "message": f"Error setting default provisional account: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def list_available_provisional_accounts(
    company: str,
    account_type: str = None,
    search_term: str = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict:
    """
    List available accounts that can be used as provisional accounts for a company.
    
    Args:
        company: Company name/ID
        account_type: Filter by account type (optional)
        search_term: Search term for account name (optional)
        limit: Number of records to return (default: 50)
        offset: Offset for pagination (default: 0)
    
    Returns:
        dict: List of available accounts
    """
    try:
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company '{0}' not found").format(company),
                "error_type": "company_not_found",
            }
        
        filters = {
            "company": company,
            "is_group": 0,  # Only ledger accounts, not group accounts
            "disabled": 0,  # Only active accounts
        }
        
        # Recommended account types for provisional accounts
        recommended_types = [
            "Service Received But Not Billed",
            "Asset Received But Not Billed",
            "Expense Account",
            "Indirect Expense",
            "Direct Expense",
        ]
        
        if account_type:
            filters["account_type"] = account_type
        else:
            # Filter by recommended types if no specific type provided
            filters["account_type"] = ["in", recommended_types]
        
        # Build search condition
        or_filters = {}
        if search_term:
            or_filters = {
                "account_name": ["like", f"%{search_term}%"],
                "name": ["like", f"%{search_term}%"],
            }
        
        accounts = frappe.get_all(
            "Account",
            filters=filters,
            or_filters=or_filters if or_filters else None,
            fields=[
                "name",
                "account_name",
                "account_type",
                "root_type",
                "parent_account",
            ],
            limit=limit,
            start=offset,
            order_by="account_name",
        )
        
        # Mark recommended accounts
        for account in accounts:
            account["is_recommended"] = account.get("account_type") in recommended_types[:2]
        
        return {
            "success": True,
            "data": accounts,
            "count": len(accounts),
            "recommended_types": recommended_types,
        }
    except Exception as e:
        frappe.log_error(f"Error listing available provisional accounts: {str(e)}", "List Provisional Accounts Error")
        return {
            "success": False,
            "message": f"Error listing available provisional accounts: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def auto_configure_provisional_account(
    company: str,
    create_account_if_missing: bool = False,
    account_name: str = None,
) -> Dict:
    """
    Automatically configure provisional account for a company (Autoprovisioning).
    
    This function implements autoprovisioning by automatically finding or creating
    a provisional account and configuring the company. It follows this workflow:
    1. Checks if provisional accounting is already configured (returns early if yes)
    2. Searches for existing suitable accounts with preferred types:
       - "Service Received But Not Billed" (highest priority)
       - "Asset Received But Not Billed"
    3. Optionally creates a new account if none found and create_account_if_missing=True
    4. Sets the found/created account as default provisional account
    5. Enables provisional accounting for the company
    
    This is the recommended method for automatic setup and resolving missing
    provisional account errors.
    
    Args:
        company: Company name/ID
        create_account_if_missing: If True, creates a new account if no suitable one exists (default: False).
                                   The account will be created under "Current Assets" with type
                                   "Service Received But Not Billed".
        account_name: Name for the new account if creating (optional).
                     If not provided, defaults to "Service Received But Not Billed - {company_abbr}"
    
    Returns:
        dict: Configuration result with keys:
            - success (bool): Whether the operation succeeded
            - message (str): Success or error message
            - auto_configured (bool): Whether autoprovisioning was performed
            - account_created (bool): Whether a new account was created (if create_account_if_missing=True)
            - data (dict): Configuration details including company, account, account_name, etc.
            - error_type (str): Error type if failed (e.g., "company_not_found", "parent_account_not_found")
    
    Example:
        >>> result = frappe.call(
        ...     "techsavanna_pos.api.account_provisioning_api.auto_configure_provisional_account",
        ...     company="WEE",
        ...     create_account_if_missing=True
        ... )
        >>> if result["success"]:
        ...     print(f"Account: {result['data']['account']}")
    """
    try:
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company '{0}' not found").format(company),
                "error_type": "company_not_found",
            }
        
        # Check if already configured
        company_doc = frappe.get_doc("Company", company)
        if (company_doc.get("enable_provisional_accounting_for_non_stock_items") and
            company_doc.get("default_provisional_account")):
            return {
                "success": True,
                "message": _("Provisional accounting is already configured"),
                "already_configured": True,
                "data": {
                    "company": company,
                    "default_provisional_account": company_doc.get("default_provisional_account"),
                    "enable_provisional_accounting": True,
                },
            }
        
        # Look for existing suitable accounts
        preferred_types = [
            "Service Received But Not Billed",
            "Asset Received But Not Billed",
        ]
        
        account = None
        for account_type in preferred_types:
            accounts = frappe.get_all(
                "Account",
                filters={
                    "company": company,
                    "account_type": account_type,
                    "is_group": 0,
                    "disabled": 0,
                },
                fields=["name", "account_name"],
                limit=1,
            )
            if accounts:
                account = accounts[0].name
                break
        
        # If no suitable account found and creation is allowed
        if not account and create_account_if_missing:
            # Get company details
            company_currency = company_doc.default_currency
            company_abbr = company_doc.abbr
            
            # Determine account name
            if not account_name:
                account_name = f"Service Received But Not Billed - {company_abbr}"
            
            # Find or create parent account
            parent_account_name = f"Current Assets - {company_abbr}"
            parent_account = frappe.db.exists("Account", parent_account_name)
            
            if not parent_account:
                # Try to find any Current Assets account
                current_assets = frappe.get_all(
                    "Account",
                    filters={
                        "company": company,
                        "root_type": "Asset",
                        "account_type": "Current Asset",
                        "is_group": 1,
                    },
                    fields=["name"],
                    limit=1,
                )
                if current_assets:
                    parent_account_name = current_assets[0].name
                else:
                    return {
                        "success": False,
                        "message": _(
                            "Could not find a suitable parent account for creating the provisional account. "
                            "Please create the account manually or set an existing account as the default."
                        ),
                        "error_type": "parent_account_not_found",
                    }
            else:
                parent_account_name = parent_account
            
            # Create the account
            account_doc = frappe.new_doc("Account")
            account_doc.account_name = account_name
            account_doc.company = company
            account_doc.account_type = "Service Received But Not Billed"
            account_doc.parent_account = parent_account_name
            account_doc.account_currency = company_currency
            account_doc.insert(ignore_permissions=True)
            account = account_doc.name
            
            frappe.db.commit()
        
        if not account:
            return {
                "success": False,
                "message": _(
                    "No suitable provisional account found. Please either:\n"
                    "1. Set an existing account using 'set_default_provisional_account' API, or\n"
                    "2. Set create_account_if_missing=True to auto-create an account."
                ),
                "error_type": "account_not_found",
            }
        
        # Set as default provisional account and enable
        result = set_default_provisional_account(
            company=company,
            account=account,
            auto_enable_provisional_accounting=True,
        )
        
        if result.get("success"):
            result["message"] = _("Provisional account auto-configured successfully")
            result["auto_configured"] = True
            if create_account_if_missing and account:
                result["account_created"] = True
        
        return result
    except Exception as e:
        frappe.log_error(
            f"Error auto-configuring provisional account: {str(e)}",
            "Auto Configure Provisional Account Error"
        )
        return {
            "success": False,
            "message": f"Error auto-configuring provisional account: {str(e)}",
            "error_type": "general_error",
        }


@frappe.whitelist()
def validate_provisional_accounting_setup(company: str) -> Dict:
    """
    Validate the provisional accounting setup for a company.
    Returns detailed validation results and recommendations.
    
    Args:
        company: Company name/ID
    
    Returns:
        dict: Validation results with recommendations
    """
    try:
        if not frappe.db.exists("Company", company):
            return {
                "success": False,
                "message": _("Company '{0}' not found").format(company),
                "error_type": "company_not_found",
            }
        
        company_doc = frappe.get_doc("Company", company)
        enable_provisional_accounting = cint(
            company_doc.get("enable_provisional_accounting_for_non_stock_items") or 0
        )
        default_provisional_account = company_doc.get("default_provisional_account")
        
        validation_results = {
            "company": company,
            "enable_provisional_accounting": bool(enable_provisional_accounting),
            "default_provisional_account": default_provisional_account,
            "is_valid": False,
            "issues": [],
            "recommendations": [],
            "warnings": [],
        }
        
        # Check if provisional accounting is enabled
        if not enable_provisional_accounting:
            validation_results["issues"].append(
                _("Provisional accounting is not enabled for this company.")
            )
            validation_results["recommendations"].append(
                _("Enable provisional accounting if you need to handle non-stock items.")
            )
            return {
                "success": True,
                "data": validation_results,
            }
        
        # Check if default account is set
        if not default_provisional_account:
            validation_results["is_valid"] = False
            validation_results["issues"].append(
                _("Provisional accounting is enabled but 'Default Provisional Account' is not set.")
            )
            validation_results["recommendations"].append(
                _("Use 'set_default_provisional_account' API to set a default account, or use 'auto_configure_provisional_account' to auto-configure.")
            )
            return {
                "success": True,
                "data": validation_results,
            }
        
        # Validate account exists and is valid
        if not frappe.db.exists("Account", default_provisional_account):
            validation_results["is_valid"] = False
            validation_results["issues"].append(
                _("The default provisional account '{0}' no longer exists.").format(default_provisional_account)
            )
            validation_results["recommendations"].append(
                _("Set a new default provisional account using 'set_default_provisional_account' API.")
            )
            return {
                "success": True,
                "data": validation_results,
            }
        
        # Get account details
        account_details = frappe.db.get_value(
            "Account",
            default_provisional_account,
            ["account_name", "account_type", "root_type", "is_group", "disabled", "company"],
            as_dict=True
        )
        
        # Validate account company
        if account_details.get("company") != company:
            validation_results["is_valid"] = False
            validation_results["issues"].append(
                _("The default provisional account belongs to a different company.")
            )
            return {
                "success": True,
                "data": validation_results,
            }
        
        # Validate account is not a group
        if account_details.get("is_group"):
            validation_results["is_valid"] = False
            validation_results["issues"].append(
                _("The default provisional account is a group account. Please use a ledger account.")
            )
            return {
                "success": True,
                "data": validation_results,
            }
        
        # Validate account is not disabled
        if account_details.get("disabled"):
            validation_results["is_valid"] = False
            validation_results["issues"].append(
                _("The default provisional account is disabled.")
            )
            validation_results["recommendations"].append(
                _("Enable the account or set a different account as the default.")
            )
            return {
                "success": True,
                "data": validation_results,
            }
        
        # Check account type appropriateness
        recommended_types = [
            "Service Received But Not Billed",
            "Asset Received But Not Billed",
        ]
        
        if account_details.get("account_type") not in recommended_types:
            validation_results["warnings"].append(
                _("The account type '{0}' is not typically used for provisional accounting. "
                  "Recommended types: {1}").format(
                    account_details.get("account_type", "Unknown"),
                    ", ".join(recommended_types)
                )
            )
        
        # All checks passed
        validation_results["is_valid"] = True
        validation_results["account_details"] = account_details
        
        return {
            "success": True,
            "data": validation_results,
        }
    except Exception as e:
        frappe.log_error(
            f"Error validating provisional accounting setup: {str(e)}",
            "Validate Provisional Accounting Setup Error"
        )
        return {
            "success": False,
            "message": f"Error validating provisional accounting setup: {str(e)}",
            "error_type": "general_error",
        }


# Helper function that can be used by other APIs
def validate_and_get_provisional_account(company: str, auto_fix: bool = False) -> Tuple[bool, Optional[Dict], Optional[str]]:
    """
    Helper function to validate and get provisional account for a company.
    Supports autoprovisioning via the auto_fix parameter.
    
    This function is designed to be used by other APIs that require provisional
    accounting validation. It can automatically provision accounts when missing
    if auto_fix=True, making it ideal for production APIs that need seamless
    user experience.
    
    Args:
        company: Company name/ID
        auto_fix: If True, attempts to auto-configure if missing (default: False).
                  When enabled, calls auto_configure_provisional_account with
                  create_account_if_missing=True if provisional accounting is enabled
                  but no default account is set.
    
    Returns:
        tuple: (is_valid, error_dict or None, account_name or None)
            - is_valid (bool): True if setup is valid or provisional accounting not enabled
            - error_dict (dict or None): Error dictionary if invalid, None if valid
            - account_name (str or None): Account name if valid and provisional accounting enabled, None otherwise
    
    Behavior:
        - If provisional accounting not enabled: Returns (True, None, None) - no error
        - If provisional accounting enabled and account exists: Returns (True, None, account_name)
        - If provisional accounting enabled but account missing:
          * If auto_fix=True: Attempts autoprovisioning, returns (True, None, account) if successful
          * If auto_fix=False: Returns (False, error_dict, None)
        - If account exists but is invalid: Returns (False, error_dict, None)
    
    Example:
        >>> from techsavanna_pos.api.account_provisioning_api import validate_and_get_provisional_account
        >>> is_valid, error, account = validate_and_get_provisional_account(
        ...     company="WEE",
        ...     auto_fix=True  # Enable autoprovisioning
        ... )
        >>> if is_valid:
        ...     if account:
        ...         print(f"Using account: {account}")
        ...     else:
        ...         print("Provisional accounting not enabled")
        ... else:
        ...     print(f"Error: {error['message']}")
    """
    try:
        enable_provisional_accounting = frappe.db.get_value(
            "Company", company, "enable_provisional_accounting_for_non_stock_items"
        )
        
        if not cint(enable_provisional_accounting):
            # Provisional accounting not enabled, no error
            return True, None, None
        
        default_provisional_account = frappe.db.get_value(
            "Company", company, "default_provisional_account"
        )
        
        if not default_provisional_account:
            if auto_fix:
                # Try to auto-configure
                result = auto_configure_provisional_account(
                    company=company,
                    create_account_if_missing=True,
                )
                if result.get("success"):
                    default_provisional_account = result["data"]["account"]
                    return True, None, default_provisional_account
            
            # Return error
            error_dict = {
                "success": False,
                "message": (
                    f"Provisional accounting is enabled for company '{company}', but "
                    "'Default Provisional Account' is not set. Please set it in Company settings "
                    f"(Company > {company} > Default Provisional Account) or use the "
                    "'set_default_provisional_account' API."
                ),
                "error_type": "missing_provisional_account",
                "company": company,
            }
            return False, error_dict, None
        
        # Validate account exists and is valid
        if not frappe.db.exists("Account", default_provisional_account):
            error_dict = {
                "success": False,
                "message": (
                    f"The default provisional account '{default_provisional_account}' for company "
                    f"'{company}' no longer exists. Please set a new default provisional account."
                ),
                "error_type": "invalid_provisional_account",
                "company": company,
            }
            return False, error_dict, None
        
        # All checks passed
        return True, None, default_provisional_account
    except Exception as e:
        frappe.log_error(
            f"Error validating provisional account: {str(e)}",
            "Validate Provisional Account Helper Error"
        )
        error_dict = {
            "success": False,
            "message": f"Error validating provisional account: {str(e)}",
            "error_type": "validation_error",
            "company": company,
        }
        return False, error_dict, None
