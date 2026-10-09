"""
Onboarding API
Handles company/business setup, POS profile creation, and optional eTIMS configuration
"""

import frappe
from frappe import _
from frappe.utils import now, today
from erpnext.setup.setup_wizard import setup_wizard


def initialize_site(country="Kenya"):
    """
    Install ERPNext master data on a fresh site.

    Safe to call multiple times.
    """

    # Already initialized?
    required = [
        ("Customer Group", "All Customer Groups"),
        ("Mode of Payment", "Cash"),
        ("Warehouse Type", "Transit"),
    ]

    initialized = all(
        frappe.db.exists(dt, name)
        for dt, name in required
    )

    if initialized:
        return

    frappe.logger().info(
        f"Initializing ERPNext master data for {country}"
    )

    setup_wizard.fixtures.install(country)

    frappe.db.commit()


def finalize_site(company):
    """
    Install ERPNext defaults after company creation.
    Safe to call multiple times.
    """

    # Price Lists already created?
    required = [
        "Standard Selling",
        "Standard Buying",
    ]

    if all(frappe.db.exists("Price List", p) for p in required):
        return

    setup_wizard.fixtures.install_defaults(
        frappe._dict(
            {
                "company": company.name,
                "company_name": company.company_name,
                "currency": company.default_currency,
                "country": company.country,
            }
        )
    )

    frappe.db.commit()


def ensure_default_accounts(company):
    if frappe.db.count("Account", {"company": company.name}) == 0:
        company.create_default_accounts()


@frappe.whitelist()
def create_company(
    company_name: str,
    abbr: str,
    country: str = "Kenya",
    default_currency: str = "KES",
    company_logo: str = None,
    company_address: dict = None,
    company_contact: dict = None,
    tax_id: str = None
) -> dict:
    """Create a new company with default setup
    
    Args:
        company_name: Company name
        abbr: Company abbreviation (2-3 characters)
        country: Country (default: Kenya)
        default_currency: Default currency (default: KES)
        company_logo: Optional company logo URL
        company_address: Optional address details
        company_contact: Optional contact details
        tax_id: Optional tax ID (KRA PIN)
        
    Returns:
        Created company details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Please log in to create a company. Your session has expired or you need to authenticate first."),
            frappe.AuthenticationError
        )
    
    # Check if company already exists
    if frappe.db.exists("Company", company_name):
        frappe.throw(
            _("A company with the name '{0}' already exists. Please choose a different company name or contact support if you believe this is an error.").format(company_name),
            frappe.ValidationError
        )
    
    # Validate abbreviation
    if len(abbr) < 2 or len(abbr) > 3:
        frappe.throw(
            _("Company abbreviation must be 2-3 characters long. The abbreviation is used in account names and document codes. For example, 'ABC' for 'ABC Company Ltd'."),
            frappe.ValidationError
        )

    # Initialize ERPNext master records on a fresh site
    initialize_site(country)
    
    # Create company
    company = frappe.new_doc("Company")
    company.company_name = company_name
    company.abbr = abbr.upper()
    company.country = country
    company.default_currency = default_currency
    company.enable_provisional_accounting_for_non_stock_items = 1
    
    # Set tax ID if provided
    if tax_id:
        company.tax_id = tax_id
    
    # Insert company
    try:
        company.insert(ignore_permissions=True)
    except Exception as e:
        error_msg = str(e)
        # Check for common validation errors
        if "duplicate" in error_msg.lower() or "already exists" in error_msg.lower():
            frappe.throw(
                _("A company with similar details already exists. Please check the company name and abbreviation, or contact support if you believe this is an error."),
                frappe.ValidationError
            )
        frappe.log_error("Company Creation Error", f"Error inserting company: {error_msg}")
        frappe.throw(
            _("An error occurred while creating the company. Please verify all fields are correct and try again. Error: {0}").format(error_msg),
            frappe.ValidationError
        )
    
    # Create company address if provided
    if company_address:
        try:
            address = frappe.new_doc("Address")
            address.address_title = company_name
            address.address_type = "Office"
            address.address_line1 = company_address.get("address_line1", "")
            address.address_line2 = company_address.get("address_line2", "")
            address.city = company_address.get("city", "")
            address.state = company_address.get("state", "")
            address.country = company_address.get("country", country)
            address.pincode = company_address.get("pincode", "")
            address.phone = company_address.get("phone", "")
            address.email_id = company_address.get("email", "")
            address.is_primary_address = 1
            address.is_shipping_address = 1
            
            # Link address to company using the links field
            address.append("links", {
                "link_doctype": "Company",
                "link_name": company.name
            })
            
            address.insert(ignore_permissions=True)
        except Exception as e:
            # Log error but don't fail company creation - address is optional
            frappe.log_error(
                "Company Address Creation Error",
                f"Error creating address for company {company.name}: {str(e)}"
            )
    
    # Create company contact if provided
    if company_contact:
        try:
            contact = frappe.new_doc("Contact")
            contact.first_name = company_contact.get("first_name", "")
            contact.last_name = company_contact.get("last_name", "")
            contact.email_id = company_contact.get("email", "")
            contact.mobile_no = company_contact.get("mobile", "")
            contact.phone = company_contact.get("phone", "")
            contact.is_primary_contact = 1
            contact.insert(ignore_permissions=True)
            
            # Link contact to company
            contact.append("links", {
                "link_doctype": "Company",
                "link_name": company.name
            })
            contact.save(ignore_permissions=True)
        except Exception as e:
            # Log error but don't fail company creation - contact is optional
            frappe.log_error(
                "Company Contact Creation Error",
                f"Error creating contact for company {company.name}: {str(e)}"
            )
    
    # Set as default company for current user
    frappe.defaults.set_user_default("company", company.name)
    
    # Also set custom_company field for safe fallback pattern
    # This prevents silent fallback to global default company
    # Works for both registered users (who create their own company) and staff users
    try:
        frappe.db.set_value("User", frappe.session.user, "custom_company", company.name)
    except Exception as e:
        # Log error but don't fail company creation - custom_company is optional
        frappe.log_error(
            "Company Creation - Set Custom Company",
            f"Error setting custom_company for user {frappe.session.user}: {str(e)}"
        )
    
    # Create default accounts and settings
    # Check if accounts already exist for this company (may exist from partial creation)
    existing_accounts_count = frappe.db.count("Account", {"company": company.name})
    
    if existing_accounts_count == 0:
        # Only create accounts if none exist
        try:
            company.create_default_accounts()
        except frappe.DuplicateEntryError as e:
            # Handle duplicate accounts gracefully - accounts may have been created concurrently
            # Extract account name from error if possible
            error_str = str(e)
            account_name = "unknown"
            if "Account" in error_str and "'" in error_str:
                # Try to extract account name from error message
                try:
                    parts = error_str.split("'")
                    if len(parts) >= 2:
                        account_name = parts[1]
                except:
                    pass
            
            # Log with truncated message to avoid exceeding 140 char limit
            # Keep title short and descriptive
            error_title = f"Duplicate Account: {account_name[:50]}"
            if len(error_title) > 140:
                error_title = error_title[:137] + "..."
            error_msg = f"Some accounts already exist for company {company.name}. This is normal if accounts were partially created. Company creation will continue."
            frappe.log_error(title=error_title, message=error_msg)
            # Don't fail the company creation if accounts already exist - this is acceptable
        except Exception as e:
            # Truncate error message to fit within 140 character limit for Error Log title
            error_str = str(e)
            # Extract a meaningful short description from the error
            if "Account" in error_str:
                # Try to extract account name
                account_name = "unknown"
                if "'" in error_str:
                    try:
                        parts = error_str.split("'")
                        if len(parts) >= 2:
                            account_name = parts[1][:50]
                    except:
                        pass
                error_title = f"Account Error: {account_name}"
            else:
                # Truncate to fit 140 chars, leaving room for prefix
                error_title = f"Account Creation Error"
            
            # Ensure title doesn't exceed 140 characters
            if len(error_title) > 140:
                error_title = error_title[:137] + "..."
            
            # Log full error in the error message body (not title)
            # Truncate the error message itself if it's too long
            error_msg_body = f"Error creating default accounts for company {company.name}: {error_str}"
            if len(error_msg_body) > 10000:  # Limit error message body to reasonable size
                error_msg_body = error_msg_body[:10000] + "... (truncated)"
            
            frappe.log_error(title=error_title, message=error_msg_body)
            # Don't fail the company creation if accounts fail - user can create them manually
    else:
        # Accounts already exist - log this but don't fail
        frappe.log_error(
            "Accounts Already Exist",
            f"Accounts already exist for company {company.name} ({existing_accounts_count} accounts found). Skipping account creation."
        )

    # Install ERPNext defaults now that company/accounts exist
    try:
        ensure_default_accounts(company)
        finalize_site(company)
    except Exception:
        frappe.log_error(
            "ERPNext Default Initialization",
            frappe.get_traceback()
        )

    frappe.db.set_single_value("System Settings", "country", company.country)
    frappe.db.set_single_value("System Settings", "setup_complete", 1)
    frappe.db.commit()
    
    # Set HTTP status code for successful creation
    frappe.local.response["http_status_code"] = 201
    
    return {
        "company": {
            "name": company.name,
            "company_name": company.company_name,
            "abbr": company.abbr,
            "country": company.country,
            "default_currency": company.default_currency,
            "tax_id": company.tax_id,
        },
        "message": _("Company created successfully")
    }


@frappe.whitelist(allow_guest=True)
def check_abbreviation_exists(abbr: str) -> dict:
    """Check if a company abbreviation already exists
    
    This endpoint is unauthenticated to allow checking during onboarding
    before user registration/login.
    
    Args:
        abbr: Company abbreviation to check (2-3 characters)
        
    Returns:
        Dictionary with exists flag and optional company details if found
    """
    # Validate abbreviation format
    if not abbr:
        return {
            "exists": False,
            "message": "Abbreviation is required",
            "valid": False
        }
    
    # Normalize abbreviation (uppercase, strip whitespace)
    abbr = abbr.strip().upper()
    
    # Validate abbreviation length
    if len(abbr) < 2 or len(abbr) > 3:
        return {
            "exists": False,
            "message": "Company abbreviation must be 2-3 characters long",
            "valid": False
        }
    
    # Check if abbreviation exists in any company
    existing_company = frappe.db.get_value(
        "Company",
        {"abbr": abbr},
        ["name", "company_name", "abbr"],
        as_dict=True
    )
    
    if existing_company:
        return {
            "exists": True,
            "abbr": abbr,
            "company": {
                "name": existing_company.name,
                "company_name": existing_company.company_name,
                "abbr": existing_company.abbr
            },
            "message": _("Abbreviation '{0}' is already used by company '{1}'").format(abbr, existing_company.company_name)
        }
    else:
        return {
            "exists": False,
            "abbr": abbr,
            "message": _("Abbreviation '{0}' is available").format(abbr),
            "valid": True
        }


@frappe.whitelist()
def get_company(company_name: str = None) -> dict:
    """Get company details
    
    Args:
        company_name: Company name (optional, uses default company if not provided)
        
    Returns:
        Company details including provisional accounting settings
    """
    try:
        # Get company name if not provided
        if not company_name:
            company_name = frappe.defaults.get_user_default("Company")
            if not company_name:
                return {
                    "success": False,
                    "message": "No company specified and no default company set. Please provide company_name parameter or set a default company.",
                }
        
        # Validate company exists
        if not frappe.db.exists("Company", company_name):
            return {
                "success": False,
                "message": f"Company '{company_name}' does not exist",
            }
        
        # Get company document
        company = frappe.get_doc("Company", company_name)
        
        # Get company address if exists
        company_address = None
        address_links = frappe.get_all(
            "Dynamic Link",
            filters={
                "link_doctype": "Company",
                "link_name": company_name,
                "parenttype": "Address"
            },
            fields=["parent"],
            limit=1
        )
        if address_links:
            address_name = address_links[0].parent
            address = frappe.get_doc("Address", address_name)
            company_address = {
                "name": address.name,
                "address_line1": address.address_line1,
                "address_line2": address.address_line2,
                "city": address.city,
                "state": address.state,
                "country": address.country,
                "pincode": address.pincode,
                "phone": address.phone,
                "email": address.email_id,
            }
        
        # Get company contact if exists
        company_contact = None
        contact_links = frappe.get_all(
            "Dynamic Link",
            filters={
                "link_doctype": "Company",
                "link_name": company_name,
                "parenttype": "Contact"
            },
            fields=["parent"],
            limit=1
        )
        if contact_links:
            contact_name = contact_links[0].parent
            contact = frappe.get_doc("Contact", contact_name)
            company_contact = {
                "name": contact.name,
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email_id,
                "mobile": contact.mobile_no,
                "phone": contact.phone,
            }
        
        # Get provisional account details if set
        provisional_account_details = None
        if company.default_provisional_account:
            prov_account = frappe.db.get_value(
                "Account",
                company.default_provisional_account,
                ["account_name", "account_type", "report_type"],
                as_dict=True
            )
            if prov_account:
                provisional_account_details = {
                    "name": company.default_provisional_account,
                    "account_name": prov_account.account_name,
                    "account_type": prov_account.account_type,
                    "report_type": prov_account.report_type,
                }
        
        return {
            "success": True,
            "data": {
                "name": company.name,
                "company_name": company.company_name,
                "abbr": company.abbr,
                "country": company.country,
                "default_currency": company.default_currency,
                "tax_id": company.tax_id,
                "company_logo": company.company_logo,
                "phone_no": company.phone_no,
                "email": company.email,
                "website": company.website,
                "date_of_establishment": str(company.date_of_establishment) if company.date_of_establishment else None,
                "date_of_incorporation": str(company.date_of_incorporation) if company.date_of_incorporation else None,
                "is_group": company.is_group,
                "parent_company": company.parent_company,
                # Provisional Accounting Settings
                "enable_provisional_accounting_for_non_stock_items": company.enable_provisional_accounting_for_non_stock_items,
                "default_provisional_account": company.default_provisional_account,
                "provisional_account_details": provisional_account_details,
                # Default Accounts
                "default_bank_account": company.default_bank_account,
                "default_cash_account": company.default_cash_account,
                "default_receivable_account": company.default_receivable_account,
                "default_payable_account": company.default_payable_account,
                "default_expense_account": company.default_expense_account,
                "default_income_account": company.default_income_account,
                "cost_center": company.cost_center,
                "default_warehouse": company.default_warehouse,
                # Address and Contact
                "company_address": company_address,
                "company_contact": company_contact,
            },
        }
    except Exception as e:
        frappe.log_error("Get Company Error", f"Error getting company details: {str(e)}")
        return {
            "success": False,
            "message": f"Error getting company details: {str(e)}",
        }


@frappe.whitelist()
def update_company(
    company_name: str,
    # Basic company info
    company_logo: str = None,
    phone_no: str = None,
    email: str = None,
    website: str = None,
    tax_id: str = None,
    date_of_establishment: str = None,
    date_of_incorporation: str = None,
    # Provisional Accounting Settings
    enable_provisional_accounting_for_non_stock_items: bool = None,
    default_provisional_account: str = None,
    # Default Accounts
    default_bank_account: str = None,
    default_cash_account: str = None,
    default_receivable_account: str = None,
    default_payable_account: str = None,
    default_expense_account: str = None,
    default_income_account: str = None,
    cost_center: str = None,
    default_warehouse: str = None,
    # Company Address
    company_address: dict = None,
    # Company Contact
    company_contact: dict = None,
) -> dict:
    """Update company information and settings
    
    Args:
        company_name: Company name to update
        company_logo: Company logo URL
        phone_no: Phone number
        email: Email address
        website: Website URL
        tax_id: Tax ID (KRA PIN)
        date_of_establishment: Date of establishment (YYYY-MM-DD)
        date_of_incorporation: Date of incorporation (YYYY-MM-DD)
        enable_provisional_accounting_for_non_stock_items: Enable/disable provisional accounting
        default_provisional_account: Default provisional account name
        default_bank_account: Default bank account
        default_cash_account: Default cash account
        default_receivable_account: Default receivable account
        default_payable_account: Default payable account
        default_expense_account: Default expense account
        default_income_account: Default income account
        cost_center: Default cost center
        default_warehouse: Default warehouse
        company_address: Address details dict (address_line1, address_line2, city, state, country, pincode, phone, email)
        company_contact: Contact details dict (first_name, last_name, email, mobile, phone)
        
    Returns:
        Updated company details
    """
    try:
        # Validate user permissions
        if frappe.session.user == "Guest":
            frappe.throw(
                _("Please log in to update company information. Your session has expired or you need to authenticate first."),
                frappe.AuthenticationError
            )
        
        # Validate company exists
        if not frappe.db.exists("Company", company_name):
            return {
                "success": False,
                "message": f"Company '{company_name}' does not exist",
            }
        
        # Get company document
        company = frappe.get_doc("Company", company_name)
        
        # Update basic company info
        if company_logo is not None:
            company.company_logo = company_logo
        if phone_no is not None:
            company.phone_no = phone_no
        if email is not None:
            company.email = email
        if website is not None:
            company.website = website
        if tax_id is not None:
            company.tax_id = tax_id
        if date_of_establishment is not None:
            company.date_of_establishment = date_of_establishment
        if date_of_incorporation is not None:
            company.date_of_incorporation = date_of_incorporation
        
        # Update provisional accounting settings
        if enable_provisional_accounting_for_non_stock_items is not None:
            company.enable_provisional_accounting_for_non_stock_items = 1 if enable_provisional_accounting_for_non_stock_items else 0
        
        if default_provisional_account is not None:
            # Validate account exists
            if not frappe.db.exists("Account", default_provisional_account):
                return {
                    "success": False,
                    "message": f"Provisional Account '{default_provisional_account}' does not exist. Please create the account first or use an existing account.",
                }
            
            # Validate account belongs to company
            account_company = frappe.db.get_value("Account", default_provisional_account, "company")
            if account_company != company_name:
                return {
                    "success": False,
                    "message": f"Provisional Account '{default_provisional_account}' belongs to a different company ('{account_company}'). Please use an account from company '{company_name}'.",
                }
            
            # Validate account type (should be Liability)
            account_type = frappe.db.get_value("Account", default_provisional_account, "report_type")
            if account_type not in ["Asset", "Liability"]:
                return {
                    "success": False,
                    "message": f"Provisional Account '{default_provisional_account}' should be an Asset or Liability type account. Current type: {account_type}",
                }
            
            company.default_provisional_account = default_provisional_account
        
        # Validate: If enabling provisional accounting, ensure account is set
        if company.enable_provisional_accounting_for_non_stock_items and not company.default_provisional_account:
            return {
                "success": False,
                "message": "Cannot enable provisional accounting without setting 'default_provisional_account'. Please set the default_provisional_account parameter.",
            }
        
        # Update default accounts
        if default_bank_account is not None:
            company.default_bank_account = default_bank_account
        if default_cash_account is not None:
            company.default_cash_account = default_cash_account
        if default_receivable_account is not None:
            company.default_receivable_account = default_receivable_account
        if default_payable_account is not None:
            company.default_payable_account = default_payable_account
        if default_expense_account is not None:
            company.default_expense_account = default_expense_account
        if default_income_account is not None:
            company.default_income_account = default_income_account
        if cost_center is not None:
            company.cost_center = cost_center
        if default_warehouse is not None:
            company.default_warehouse = default_warehouse
        
        # Save company
        company.save(ignore_permissions=True)
        
        # Update company address if provided
        if company_address:
            try:
                # Find existing address linked to company
                address_links = frappe.get_all(
                    "Dynamic Link",
                    filters={
                        "link_doctype": "Company",
                        "link_name": company_name,
                        "parenttype": "Address"
                    },
                    fields=["parent"],
                    limit=1
                )
                
                if address_links:
                    # Update existing address
                    address = frappe.get_doc("Address", address_links[0].parent)
                    if company_address.get("address_line1"):
                        address.address_line1 = company_address.get("address_line1")
                    if company_address.get("address_line2") is not None:
                        address.address_line2 = company_address.get("address_line2")
                    if company_address.get("city"):
                        address.city = company_address.get("city")
                    if company_address.get("state"):
                        address.state = company_address.get("state")
                    if company_address.get("country"):
                        address.country = company_address.get("country")
                    if company_address.get("pincode"):
                        address.pincode = company_address.get("pincode")
                    if company_address.get("phone"):
                        address.phone = company_address.get("phone")
                    if company_address.get("email"):
                        address.email_id = company_address.get("email")
                    address.save(ignore_permissions=True)
                else:
                    # Create new address
                    address = frappe.new_doc("Address")
                    address.address_title = company_name
                    address.address_type = "Office"
                    address.address_line1 = company_address.get("address_line1", "")
                    address.address_line2 = company_address.get("address_line2", "")
                    address.city = company_address.get("city", "")
                    address.state = company_address.get("state", "")
                    address.country = company_address.get("country", company.country)
                    address.pincode = company_address.get("pincode", "")
                    address.phone = company_address.get("phone", "")
                    address.email_id = company_address.get("email", "")
                    address.is_primary_address = 1
                    address.is_shipping_address = 1
                    address.append("links", {
                        "link_doctype": "Company",
                        "link_name": company_name
                    })
                    address.insert(ignore_permissions=True)
            except Exception as e:
                frappe.log_error(
                    "Company Address Update Error",
                    f"Error updating address for company {company_name}: {str(e)}"
                )
                # Don't fail the update if address fails
        
        # Update company contact if provided
        if company_contact:
            try:
                # Find existing contact linked to company
                contact_links = frappe.get_all(
                    "Dynamic Link",
                    filters={
                        "link_doctype": "Company",
                        "link_name": company_name,
                        "parenttype": "Contact"
                    },
                    fields=["parent"],
                    limit=1
                )
                
                if contact_links:
                    # Update existing contact
                    contact = frappe.get_doc("Contact", contact_links[0].parent)
                    if company_contact.get("first_name"):
                        contact.first_name = company_contact.get("first_name")
                    if company_contact.get("last_name"):
                        contact.last_name = company_contact.get("last_name")
                    if company_contact.get("email"):
                        contact.email_id = company_contact.get("email")
                    if company_contact.get("mobile"):
                        contact.mobile_no = company_contact.get("mobile")
                    if company_contact.get("phone"):
                        contact.phone = company_contact.get("phone")
                    contact.save(ignore_permissions=True)
                else:
                    # Create new contact
                    contact = frappe.new_doc("Contact")
                    contact.first_name = company_contact.get("first_name", "")
                    contact.last_name = company_contact.get("last_name", "")
                    contact.email_id = company_contact.get("email", "")
                    contact.mobile_no = company_contact.get("mobile", "")
                    contact.phone = company_contact.get("phone", "")
                    contact.is_primary_contact = 1
                    contact.append("links", {
                        "link_doctype": "Company",
                        "link_name": company_name
                    })
                    contact.insert(ignore_permissions=True)
            except Exception as e:
                frappe.log_error(
                    "Company Contact Update Error",
                    f"Error updating contact for company {company_name}: {str(e)}"
                )
                # Don't fail the update if contact fails
        
        # Reload to get updated values
        company.reload()
        
        # Get updated provisional account details
        provisional_account_details = None
        if company.default_provisional_account:
            prov_account = frappe.db.get_value(
                "Account",
                company.default_provisional_account,
                ["account_name", "account_type", "report_type"],
                as_dict=True
            )
            if prov_account:
                provisional_account_details = {
                    "name": company.default_provisional_account,
                    "account_name": prov_account.account_name,
                    "account_type": prov_account.account_type,
                    "report_type": prov_account.report_type,
                }
        
        return {
            "success": True,
            "message": "Company updated successfully",
            "data": {
                "name": company.name,
                "company_name": company.company_name,
                "enable_provisional_accounting_for_non_stock_items": company.enable_provisional_accounting_for_non_stock_items,
                "default_provisional_account": company.default_provisional_account,
                "provisional_account_details": provisional_account_details,
            },
        }
    except frappe.ValidationError as e:
        frappe.log_error("Update Company Validation Error", f"Validation error updating company: {str(e)}")
        return {
            "success": False,
            "message": f"Validation error: {str(e)}",
            "error_type": "validation_error",
        }
    except Exception as e:
        frappe.log_error("Update Company Error", f"Error updating company: {str(e)}")
        return {
            "success": False,
            "message": f"Error updating company: {str(e)}",
        }


@frappe.whitelist()
def create_pos_profile(
    company: str,
    profile_name: str = None,
    warehouse: str = None,
    currency: str = None,
    customer: str = None,
    selling_price_list: str = None,
    cost_center: str = None,
    write_off_account: str = None,
    write_off_cost_center: str = None,
    update_stock: bool = True,
    allow_discount_change: bool = True,
    allow_rate_change: bool = True,
    allow_partial_payment: bool = False
) -> dict:
    """Create a POS Profile for the company
    
    Args:
        company: Company name
        profile_name: Profile name (default: company name + " POS Profile")
        warehouse: Warehouse for POS
        currency: Currency (default: company default currency)
        customer: Default customer
        selling_price_list: Selling price list
        cost_center: Cost center
        write_off_account: Write off account
        write_off_cost_center: Write off cost center
        update_stock: Whether to update stock
        allow_discount_change: Allow discount changes
        allow_rate_change: Allow rate changes
        allow_partial_payment: Allow partial payments
        
    Returns:
        Created POS profile details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Please log in to create a POS profile. Your session has expired or you need to authenticate first."),
            frappe.AuthenticationError
        )
    
    # Validate company exists
    if not frappe.db.exists("Company", company):
        frappe.throw(
            _("The company '{0}' was not found. Please create the company first or check that the company name is correct.").format(company),
            frappe.ValidationError
        )
    
    # Get company details
    company_doc = frappe.get_doc("Company", company)
    
    # Set defaults
    if not profile_name:
        profile_name = f"{company} POS Profile"
    
    if not currency:
        currency = company_doc.default_currency
    
    # Get or create default warehouse
    if not warehouse:
        warehouse = frappe.db.get_value("Warehouse", {
            "company": company,
            "is_group": 0
        }, "name")
        
        if not warehouse:
            # Create default warehouse
            warehouse = create_default_warehouse(company)
    
    # Get or create default customer
    if not customer:
        customer = frappe.db.get_value("Customer", {
            "customer_name": "Walk-in Customer"
        }, "name")
        
        if not customer:
            customer = create_default_customer(company)
    
    # Get or create default price list
    if not selling_price_list:
        selling_price_list = frappe.db.get_value("Price List", {
            "selling": 1,
            "currency": currency
        }, "name")
        
        if not selling_price_list:
            selling_price_list = create_default_price_list(company, currency)
    
    # Get default accounts
    if not write_off_account:
        write_off_account = get_default_write_off_account(company)
    
    if not write_off_cost_center:
        cost_center = cost_center or get_default_cost_center(company)
        write_off_cost_center = cost_center
    
    # Create POS Profile
    pos_profile = frappe.new_doc("POS Profile")
    pos_profile.company = company
    pos_profile.name = profile_name
    pos_profile.warehouse = warehouse
    pos_profile.currency = currency
    pos_profile.customer = customer
    pos_profile.selling_price_list = selling_price_list
    pos_profile.cost_center = cost_center
    pos_profile.write_off_account = write_off_account
    pos_profile.write_off_cost_center = write_off_cost_center
    pos_profile.update_stock = update_stock
    pos_profile.allow_discount_change = allow_discount_change
    pos_profile.allow_rate_change = allow_rate_change
    pos_profile.allow_partial_payment = allow_partial_payment
    
    # Add default payment method (Cash)
    cash_mode = frappe.db.get_value("Mode of Payment", {"name": "Cash"}, "name")
    if cash_mode:
        pos_profile.append("payments", {
            "mode_of_payment": cash_mode,
            "default": 1
        })
    
    # Add current user to applicable users
    pos_profile.append("applicable_for_users", {
        "user": frappe.session.user
    })
    
    try:
        pos_profile.insert(ignore_permissions=True)
        pos_profile.save(ignore_permissions=True)
    except Exception as e:
        error_msg = str(e)
        frappe.log_error("POS Profile Creation Error", f"Error creating POS profile: {error_msg}")
        # Provide helpful error message
        if "duplicate" in error_msg.lower() or "already exists" in error_msg.lower():
            frappe.throw(
                _("A POS profile with the name '{0}' already exists. Please choose a different profile name.").format(profile_name),
                frappe.ValidationError
            )
        elif "required" in error_msg.lower() or "mandatory" in error_msg.lower():
            frappe.throw(
                _("Unable to create POS profile. Some required information is missing. Please check that all required fields are provided. Error: {0}").format(error_msg),
                frappe.ValidationError
            )
        else:
            frappe.throw(
                _("An error occurred while creating the POS profile: {0}. Please verify all settings and try again.").format(error_msg),
                frappe.ValidationError
            )
    
    # Set HTTP status code for successful creation
    frappe.local.response["http_status_code"] = 201
    
    return {
        "pos_profile": {
            "name": pos_profile.name,
            "company": pos_profile.company,
            "warehouse": pos_profile.warehouse,
            "currency": pos_profile.currency,
        },
        "message": _("POS Profile created successfully")
    }


@frappe.whitelist()
def update_pos_profile(
    name: str,
    profile_name: str = None,
    warehouse: str = None,
    currency: str = None,
    customer: str = None,
    selling_price_list: str = None,
    cost_center: str = None,
    write_off_account: str = None,
    write_off_cost_center: str = None,
    write_off_limit: float = None,
    update_stock: bool = None,
    allow_discount_change: bool = None,
    allow_rate_change: bool = None,
    allow_partial_payment: bool = None,
    ignore_pricing_rule: bool = None,
    disabled: bool = None,
    hide_images: bool = None,
    hide_unavailable_items: bool = None,
    auto_add_item_to_cart: bool = None,
    validate_stock_on_save: bool = None,
    print_receipt_on_order_complete: bool = None,
    set_grand_total_to_default_mop: bool = None,
    disable_rounded_total: bool = None,
    income_account: str = None,
    expense_account: str = None,
    taxes_and_charges: str = None,
    tax_category: str = None,
    apply_discount_on: str = None,
    print_format: str = None,
    letter_head: str = None,
    company_address: str = None,
    project: str = None,
    payments: list = None,
    applicable_for_users: list = None,
    item_groups: list = None,
    customer_groups: list = None
) -> dict:
    """Update an existing POS Profile
    
    Args:
        name: POS Profile name to update
        profile_name: New profile name (if renaming)
        warehouse: Warehouse for POS
        currency: Currency
        customer: Default customer
        selling_price_list: Selling price list
        cost_center: Cost center
        write_off_account: Write off account
        write_off_cost_center: Write off cost center
        write_off_limit: Write off limit amount
        update_stock: Whether to update stock
        allow_discount_change: Allow discount changes
        allow_rate_change: Allow rate changes
        allow_partial_payment: Allow partial payments
        ignore_pricing_rule: Ignore pricing rules
        disabled: Whether profile is disabled
        hide_images: Hide item images
        hide_unavailable_items: Hide unavailable items
        auto_add_item_to_cart: Auto add item to cart
        validate_stock_on_save: Validate stock on save
        print_receipt_on_order_complete: Print receipt on order complete
        set_grand_total_to_default_mop: Set grand total to default mode of payment
        disable_rounded_total: Disable rounded total
        income_account: Income account
        expense_account: Expense account
        taxes_and_charges: Taxes and charges template
        tax_category: Tax category
        apply_discount_on: Apply discount on (Grand Total/Net Total)
        print_format: Print format
        letter_head: Letter head
        company_address: Company address
        project: Project
        payments: List of payment methods (replaces existing if provided)
        applicable_for_users: List of users (replaces existing if provided)
        item_groups: List of item groups (replaces existing if provided)
        customer_groups: List of customer groups (replaces existing if provided)
        
    Returns:
        Updated POS profile details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Please log in to update a POS profile. Your session has expired or you need to authenticate first."),
            frappe.AuthenticationError
        )
    
    # Validate POS Profile exists
    if not frappe.db.exists("POS Profile", name):
        frappe.throw(
            _("The POS profile '{0}' was not found. Please check that the profile name is correct.").format(name),
            frappe.ValidationError
        )
    
    # Get existing POS Profile
    pos_profile = frappe.get_doc("POS Profile", name)
    
    # Update profile name if provided (rename)
    if profile_name and profile_name != name:
        # Check if new name already exists
        if frappe.db.exists("POS Profile", profile_name):
            frappe.throw(
                _("A POS profile with the name '{0}' already exists. Please choose a different profile name.").format(profile_name),
                frappe.ValidationError
            )
        pos_profile.name = profile_name
    
    # Update basic fields
    if warehouse is not None:
        pos_profile.warehouse = warehouse
    if currency is not None:
        pos_profile.currency = currency
    if customer is not None:
        pos_profile.customer = customer
    if selling_price_list is not None:
        pos_profile.selling_price_list = selling_price_list
    if cost_center is not None:
        pos_profile.cost_center = cost_center
    if write_off_account is not None:
        pos_profile.write_off_account = write_off_account
    if write_off_cost_center is not None:
        pos_profile.write_off_cost_center = write_off_cost_center
    if write_off_limit is not None:
        pos_profile.write_off_limit = write_off_limit
    if income_account is not None:
        pos_profile.income_account = income_account
    if expense_account is not None:
        pos_profile.expense_account = expense_account
    if taxes_and_charges is not None:
        pos_profile.taxes_and_charges = taxes_and_charges
    if tax_category is not None:
        pos_profile.tax_category = tax_category
    if apply_discount_on is not None:
        pos_profile.apply_discount_on = apply_discount_on
    if print_format is not None:
        pos_profile.print_format = print_format
    if letter_head is not None:
        pos_profile.letter_head = letter_head
    if company_address is not None:
        pos_profile.company_address = company_address
    if project is not None:
        pos_profile.project = project
    
    # Update boolean fields
    if update_stock is not None:
        pos_profile.update_stock = 1 if update_stock else 0
    if allow_discount_change is not None:
        pos_profile.allow_discount_change = 1 if allow_discount_change else 0
    if allow_rate_change is not None:
        pos_profile.allow_rate_change = 1 if allow_rate_change else 0
    if allow_partial_payment is not None:
        pos_profile.allow_partial_payment = 1 if allow_partial_payment else 0
    if ignore_pricing_rule is not None:
        pos_profile.ignore_pricing_rule = 1 if ignore_pricing_rule else 0
    if disabled is not None:
        pos_profile.disabled = 1 if disabled else 0
    if hide_images is not None:
        pos_profile.hide_images = 1 if hide_images else 0
    if hide_unavailable_items is not None:
        pos_profile.hide_unavailable_items = 1 if hide_unavailable_items else 0
    if auto_add_item_to_cart is not None:
        pos_profile.auto_add_item_to_cart = 1 if auto_add_item_to_cart else 0
    if validate_stock_on_save is not None:
        pos_profile.validate_stock_on_save = 1 if validate_stock_on_save else 0
    if print_receipt_on_order_complete is not None:
        pos_profile.print_receipt_on_order_complete = 1 if print_receipt_on_order_complete else 0
    if set_grand_total_to_default_mop is not None:
        pos_profile.set_grand_total_to_default_mop = 1 if set_grand_total_to_default_mop else 0
    if disable_rounded_total is not None:
        pos_profile.disable_rounded_total = 1 if disable_rounded_total else 0
    
    # Update payments table (replace if provided)
    if payments is not None:
        import json
        # Handle JSON string input
        if isinstance(payments, str):
            try:
                payments = json.loads(payments)
            except json.JSONDecodeError:
                frappe.throw(
                    _("Invalid JSON format for payments parameter. Please provide a valid JSON array."),
                    frappe.ValidationError
                )
        
        pos_profile.payments = []
        for payment in payments:
            if isinstance(payment, dict):
                mode_of_payment = payment.get("mode_of_payment")
                if not mode_of_payment:
                    frappe.throw(
                        _("mode_of_payment is required for all payment rows"),
                        frappe.ValidationError
                    )
                pos_profile.append("payments", {
                    "mode_of_payment": mode_of_payment,
                    "default": 1 if payment.get("default") else 0
                })
    
    # Update applicable_for_users table (replace if provided)
    if applicable_for_users is not None:
        import json
        # Handle JSON string input
        if isinstance(applicable_for_users, str):
            try:
                applicable_for_users = json.loads(applicable_for_users)
            except json.JSONDecodeError:
                frappe.throw(
                    _("Invalid JSON format for applicable_for_users parameter. Please provide a valid JSON array."),
                    frappe.ValidationError
                )
        
        pos_profile.applicable_for_users = []
        for user in applicable_for_users:
            if isinstance(user, dict):
                user_name = user.get("user")
            elif isinstance(user, str):
                user_name = user
            else:
                continue
            
            if user_name:
                pos_profile.append("applicable_for_users", {
                    "user": user_name
                })
    
    # Update item_groups table (replace if provided)
    if item_groups is not None:
        import json
        # Handle JSON string input
        if isinstance(item_groups, str):
            try:
                item_groups = json.loads(item_groups)
            except json.JSONDecodeError:
                frappe.throw(
                    _("Invalid JSON format for item_groups parameter. Please provide a valid JSON array."),
                    frappe.ValidationError
                )
        
        pos_profile.item_groups = []
        for item_group in item_groups:
            if isinstance(item_group, dict):
                group_name = item_group.get("item_group")
            elif isinstance(item_group, str):
                group_name = item_group
            else:
                continue
            
            if group_name:
                pos_profile.append("item_groups", {
                    "item_group": group_name
                })
    
    # Update customer_groups table (replace if provided)
    if customer_groups is not None:
        import json
        # Handle JSON string input
        if isinstance(customer_groups, str):
            try:
                customer_groups = json.loads(customer_groups)
            except json.JSONDecodeError:
                frappe.throw(
                    _("Invalid JSON format for customer_groups parameter. Please provide a valid JSON array."),
                    frappe.ValidationError
                )
        
        pos_profile.customer_groups = []
        for customer_group in customer_groups:
            if isinstance(customer_group, dict):
                group_name = customer_group.get("customer_group")
            elif isinstance(customer_group, str):
                group_name = customer_group
            else:
                continue
            
            if group_name:
                pos_profile.append("customer_groups", {
                    "customer_group": group_name
                })
    
    try:
        pos_profile.save(ignore_permissions=True)
    except Exception as e:
        error_msg = str(e)
        frappe.log_error("POS Profile Update Error", f"Error updating POS profile {name}: {error_msg}")
        # Provide helpful error message
        if "duplicate" in error_msg.lower() or "already exists" in error_msg.lower():
            frappe.throw(
                _("A POS profile with the name '{0}' already exists. Please choose a different profile name.").format(profile_name or name),
                frappe.ValidationError
            )
        elif "required" in error_msg.lower() or "mandatory" in error_msg.lower():
            frappe.throw(
                _("Unable to update POS profile. Some required information is missing. Please check that all required fields are provided. Error: {0}").format(error_msg),
                frappe.ValidationError
            )
        elif "disabled" in error_msg.lower() and "ongoing" in error_msg.lower():
            frappe.throw(
                _("Cannot disable POS profile '{0}' as there are ongoing POS sessions. Please close all POS sessions first.").format(name),
                frappe.ValidationError
            )
        else:
            frappe.throw(
                _("An error occurred while updating the POS profile: {0}. Please verify all settings and try again.").format(error_msg),
                frappe.ValidationError
            )
    
    return {
        "pos_profile": {
            "name": pos_profile.name,
            "company": pos_profile.company,
            "warehouse": pos_profile.warehouse,
            "currency": pos_profile.currency,
            "customer": pos_profile.customer,
            "disabled": bool(pos_profile.disabled),
        },
        "message": _("POS Profile updated successfully")
    }


@frappe.whitelist()
def create_etims_settings(
    company: str,
    server_url: str = "https://api.erp.release.slade360edi.com",
    auth_server_url: str = None,
    tin: str = None,
    bhfid: str = "00",
    sandbox: bool = True,
    is_active: bool = True,
    client_id: str = None,
    client_secret: str = None,
    auth_username: str = None,
    auth_password: str = None,
    sales_auto_submission_enabled: bool = True,
    purchase_auto_submission_enabled: bool = False,
    stock_auto_submission_enabled: bool = False
) -> dict:
    """Create optional eTIMS settings for the company
    
    Args:
        company: Company name
        server_url: Slade360 server URL
        auth_server_url: Authentication server URL
        tin: Tax Payer's PIN
        bhfid: Branch ID (default: 00 for head office)
        sandbox: Whether to use sandbox environment
        is_active: Whether settings are active
        client_id: OAuth2 client ID
        client_secret: OAuth2 client secret
        auth_username: Authentication username
        auth_password: Authentication password
        sales_auto_submission_enabled: Enable auto submission of sales
        purchase_auto_submission_enabled: Enable auto submission of purchases
        stock_auto_submission_enabled: Enable auto submission of stock
        
    Returns:
        Created eTIMS settings details
    """
    # Validate user permissions
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Please log in to configure eTIMS settings. Your session has expired or you need to authenticate first."),
            frappe.AuthenticationError
        )
    
    # Validate company exists
    if not frappe.db.exists("Company", company):
        frappe.throw(
            _("The company '{0}' was not found. Please create the company first or check that the company name is correct.").format(company),
            frappe.ValidationError
        )
    
    # Check if settings already exist
    existing = frappe.db.get_value(
        "Navari KRA eTims Settings",
        {"company": company, "bhfid": bhfid},
        "name"
    )
    
    if existing:
        frappe.throw(
            _("eTIMS settings already exist for company '{0}' and branch '{1}'. You can update the existing settings instead of creating new ones.").format(company, bhfid),
            frappe.ValidationError
        )
    
    # Get company tax ID if not provided
    if not tin:
        tin = frappe.db.get_value("Company", company, "tax_id")
    
    if not tin:
        frappe.throw(
            _("Tax ID (TIN) is required for eTIMS setup. Please provide your KRA Tax Identification Number (TIN) either in this request or set it in your Company settings."),
            frappe.ValidationError
        )
    
    # Set default auth server URL
    if not auth_server_url:
        auth_server_url = server_url.replace("/api/erp", "/auth")
    
    # Get default warehouse
    warehouse = frappe.db.get_value("Warehouse", {
        "company": company,
        "is_group": 0
    }, "name")
    
    # Get default department
    department = frappe.db.get_value("Department", {
        "company": company
    }, "name")
    
    # Create eTIMS Settings
    settings = frappe.new_doc("Navari KRA eTims Settings")
    settings.company = company
    settings.tin = tin
    settings.bhfid = bhfid
    settings.server_url = server_url
    settings.auth_server_url = auth_server_url
    settings.sandbox = 1 if sandbox else 0
    settings.is_active = 1 if is_active else 0
    settings.sales_auto_submission_enabled = 1 if sales_auto_submission_enabled else 0
    settings.purchase_auto_submission_enabled = 1 if purchase_auto_submission_enabled else 0
    settings.stock_auto_submission_enabled = 1 if stock_auto_submission_enabled else 0
    
    # Set authentication details if provided
    if client_id:
        settings.client_id = client_id
    if client_secret:
        settings.client_secret = client_secret
    if auth_username:
        settings.auth_username = auth_username
    if auth_password:
        settings.auth_password = auth_password
    
    # Set warehouse and department if available
    if warehouse:
        settings.warehouse = warehouse
    if department:
        settings.department = department
    
    try:
        settings.insert(ignore_permissions=True)
        settings.save(ignore_permissions=True)
    except Exception as e:
        error_msg = str(e)
        frappe.log_error("eTIMS Settings Creation Error", f"Error creating eTIMS settings: {error_msg}")
        # Provide helpful error message
        if "duplicate" in error_msg.lower() or "already exists" in error_msg.lower():
            frappe.throw(
                _("eTIMS settings for company '{0}' and branch '{1}' already exist. You can update the existing settings instead of creating new ones.").format(company, bhfid),
                frappe.ValidationError
            )
        elif "required" in error_msg.lower() or "mandatory" in error_msg.lower():
            frappe.throw(
                _("Unable to create eTIMS settings. Some required information is missing. Please check that all required fields are provided, especially the Tax ID (TIN). Error: {0}").format(error_msg),
                frappe.ValidationError
            )
        else:
            frappe.throw(
                _("An error occurred while creating eTIMS settings: {0}. Please verify all settings and try again. You can configure eTIMS settings later if needed.").format(error_msg),
                frappe.ValidationError
            )
    
    # Set HTTP status code for successful creation
    frappe.local.response["http_status_code"] = 201
    
    return {
        "etims_settings": {
            "name": settings.name,
            "company": settings.company,
            "tin": settings.tin,
            "bhfid": settings.bhfid,
            "is_active": settings.is_active,
            "sandbox": settings.sandbox,
        },
        "message": _("eTIMS settings created successfully")
    }


@frappe.whitelist()
def complete_onboarding(
    company_data: dict,
    pos_profile_data: dict = None,
    etims_settings_data: dict = None
) -> dict:
    """Complete onboarding flow: company, POS profile, and optional eTIMS
    
    Args:
        company_data: Company creation data
        pos_profile_data: Optional POS profile data
        etims_settings_data: Optional eTIMS settings data
        
    Returns:
        Complete onboarding result
    """
    result = {}
    company_name = None
    
    try:
        # Step 1: Create company
        company_result = create_company(**company_data)
        result["company"] = company_result["company"]
        company_name = company_result["company"]["name"]
    except frappe.AuthenticationError:
        # Re-raise authentication errors as-is (they're already user-friendly)
        raise
    except frappe.ValidationError as e:
        # Enhance validation errors with context
        frappe.throw(
            _("Unable to create company: {0}. Please check your company details and try again.").format(str(e)),
            frappe.ValidationError
        )
    except Exception as e:
        frappe.log_error(
            "Onboarding Error - Company Creation",
            f"Error creating company during onboarding: {str(e)}"
        )
        frappe.throw(
            _("An unexpected error occurred while creating your company. Please verify all required fields are filled correctly and try again. If the problem persists, contact support."),
            frappe.ValidationError
        )
    
    try:
        # Step 2: Create POS profile (if data provided)
        if pos_profile_data:
            pos_profile_data["company"] = company_name
            pos_result = create_pos_profile(**pos_profile_data)
            result["pos_profile"] = pos_result["pos_profile"]
    except frappe.AuthenticationError:
        raise
    except frappe.ValidationError as e:
        frappe.throw(
            _("Company '{0}' was created successfully, but we couldn't create your POS profile: {1}. Please try creating the POS profile manually or contact support.").format(company_name, str(e)),
            frappe.ValidationError
        )
    except Exception as e:
        frappe.log_error(
            "Onboarding Error - POS Profile Creation",
            f"Error creating POS profile during onboarding for company {company_name}: {str(e)}"
        )
        frappe.throw(
            _("Company '{0}' was created successfully, but an unexpected error occurred while setting up your POS profile. You can continue using the system and create the POS profile manually later, or contact support for assistance.").format(company_name),
            frappe.ValidationError
        )
    
    try:
        # Step 3: Create eTIMS settings (if data provided)
        if etims_settings_data:
            etims_settings_data["company"] = company_name
            etims_result = create_etims_settings(**etims_settings_data)
            result["etims_settings"] = etims_result["etims_settings"]
    except frappe.AuthenticationError:
        raise
    except frappe.ValidationError as e:
        frappe.throw(
            _("Company and POS profile were set up successfully, but we couldn't configure eTIMS settings: {0}. You can configure eTIMS settings later from the settings page.").format(str(e)),
            frappe.ValidationError
        )
    except Exception as e:
        frappe.log_error(
            "Onboarding Error - eTIMS Settings Creation",
            f"Error creating eTIMS settings during onboarding for company {company_name}: {str(e)}"
        )
        frappe.throw(
            _("Company and POS profile were set up successfully, but an unexpected error occurred while configuring eTIMS settings. You can configure eTIMS settings later from the settings page if needed."),
            frappe.ValidationError
        )
    
    result["message"] = _("Onboarding completed successfully")
    
    # Ensure user has all necessary permissions for full access
    try:
        from techsavanna_pos.api.auth_api import assign_all_business_roles
        assign_all_business_roles(frappe.session.user)
    except Exception as e:
        # Log but don't fail onboarding if role assignment fails
        frappe.log_error(
            "Onboarding Role Assignment Error",
            f"Error assigning roles after onboarding for user {frappe.session.user}: {str(e)}"
        )
    
    # Set HTTP status code for successful onboarding
    frappe.local.response["http_status_code"] = 201
    
    return result


# Helper functions

def create_default_warehouse(company: str) -> str:
    """Create default warehouse for company using company name as warehouse name"""
    # Use company name as warehouse name
    warehouse_name = company
    
    try:
        warehouse = frappe.new_doc("Warehouse")
        warehouse.warehouse_name = warehouse_name
        warehouse.company = company
        warehouse.is_group = 0
        warehouse.insert(ignore_permissions=True)
        
        # Set as default warehouse for the company
        try:
            from techsavanna_pos.api.warehouse_api import set_default_warehouse_for_company
            set_default_warehouse_for_company(company, warehouse.name)
        except Exception as e:
            # Log but don't fail if setting default fails - warehouse is still created
            frappe.log_error(
                "Set Default Warehouse Error",
                f"Error setting default warehouse for company {company}: {str(e)}"
            )
        
        return warehouse.name
    except Exception as e:
        frappe.log_error("Warehouse Creation Error", f"Error creating default warehouse for company {company}: {str(e)}")
        frappe.throw(
            _("Unable to create a default warehouse for your company. Please create a warehouse manually in the Warehouse settings and try again."),
            frappe.ValidationError
        )


def create_default_customer(company: str) -> str:
    """Create default walk-in customer"""
    try:
        customer = frappe.new_doc("Customer")
        customer.customer_name = "Walk-in Customer"
        customer.customer_type = "Company"
        customer.customer_group = "All Customer Groups"
        customer.territory = "All Territories"
        # Set require_tax_id to False for walk-in customers (they may not have a tax ID)
        customer.require_tax_id = 0
        customer.insert(ignore_permissions=True)
        
        return customer.name
    except Exception as e:
        frappe.log_error("Customer Creation Error", f"Error creating default customer: {str(e)}")
        frappe.throw(
            _("Unable to create a default customer. Please create a customer manually in the Customer settings and try again."),
            frappe.ValidationError
        )


def create_default_price_list(company: str, currency: str) -> str:
    """Create default selling price list"""
    price_list_name = f"Standard Selling - {company}"
    
    try:
        price_list = frappe.new_doc("Price List")
        price_list.price_list_name = price_list_name
        price_list.selling = 1
        price_list.currency = currency
        price_list.enabled = 1
        price_list.insert(ignore_permissions=True)
        
        return price_list.name
    except Exception as e:
        frappe.log_error("Price List Creation Error", f"Error creating default price list for company {company}: {str(e)}")
        frappe.throw(
            _("Unable to create a default price list for your company. Please create a selling price list manually in the Price List settings and try again."),
            frappe.ValidationError
        )


def get_default_write_off_account(company: str) -> str:
    """Get default write off account for company"""
    company_abbr = frappe.db.get_value("Company", company, "abbr")
    
    # Try to find existing write off account
    account = frappe.db.get_value("Account", {
        "company": company,
        "account_name": "Write Off",
        "is_group": 0
    }, "name")
    
    if account:
        return account
    
    # Create write off account
    try:
        account = frappe.new_doc("Account")
        account.account_name = "Write Off"
        account.company = company
        account.parent_account = f"Indirect Expenses - {company_abbr}"
        account.account_type = "Expense Account"
        account.insert(ignore_permissions=True)
        
        return account.name
    except Exception as e:
        frappe.log_error("Write Off Account Creation Error", f"Error creating write off account for company {company}: {str(e)}")
        # Return None to allow POS profile creation to continue without write off account
        return None


def get_default_cost_center(company: str) -> str:
    """Get default cost center for company"""
    cost_center = frappe.db.get_value("Cost Center", {
        "company": company,
        "is_group": 0
    }, "name")
    
    if cost_center:
        return cost_center
    
    # Create default cost center
    try:
        cost_center_doc = frappe.new_doc("Cost Center")
        cost_center_doc.cost_center_name = company
        cost_center_doc.company = company
        cost_center_doc.is_group = 0
        cost_center_doc.insert(ignore_permissions=True)
        
        return cost_center_doc.name
    except Exception as e:
        frappe.log_error("Cost Center Creation Error", f"Error creating cost center for company {company}: {str(e)}")
        # Return None to allow POS profile creation to continue without cost center
        return None

