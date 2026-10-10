import frappe
from frappe import _
from frappe.utils import now_datetime, nowdate, cint, getdate, flt
from techsavanna_pos.api.access_control import require_loyalty_role


@frappe.whitelist(allow_guest=False)
def create_loyalty_program(
    loyalty_program_name, 
    points_per_unit, 
    conversion_factor=None,
    program_type="Single Tier Program", 
    tier_name="Bronze", 
    from_date=None, 
    to_date=None, 
    company=None,
    expense_account=None,
    cost_center=None,
    expiry_duration=None
):
    """
    Create a new Loyalty Program in ERPNext with the required fields.
    
    Loyalty program will be automatically assigned to the logged-in user's company for isolation.
    
    :param loyalty_program_name: Unique name for the loyalty program
    :param points_per_unit: Collection factor (currency amount needed to earn 1 point)
    :param conversion_factor: Redemption conversion factor (currency value per point, e.g., 0.01 = 1 point = 0.01 currency)
    :param program_type: 'Single Tier Program' or 'Multiple Tier Program' (default: 'Single Tier Program')
    :param tier_name: Name of the initial tier (default: 'Bronze')
    :param from_date: Start date of the program in 'YYYY-MM-DD' format (default: today)
    :param to_date: End date of the program in 'YYYY-MM-DD' format (default: None for unlimited)
    :param company: Company name (optional, defaults to logged-in user's company)
    :param expense_account: Expense account for loyalty redemption (optional)
    :param cost_center: Cost center for loyalty redemption (optional)
    :param expiry_duration: Points expiry duration in days (optional)
    :return: Success or failure message with program details
    """
    # Loyalty programs: only roles the frontend gives the loyalty settings screen
    require_loyalty_role()
    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter.")
            }
        
        # Validate company exists
        if not frappe.db.exists("Company", company):
            return {
                "status": "failure",
                "message": _("Company '{0}' does not exist").format(company)
            }
        
        # Check if program already exists for this company
        existing_program = frappe.get_all(
            'Loyalty Program', 
            filters={
                'loyalty_program_name': loyalty_program_name,
                'company': company  # Check for same company
            }, 
            limit=1
        )
        if existing_program:
            return {
                "status": "failure",
                "message": _("A loyalty program with this name already exists for company '{0}'").format(company)
            }

        # Handle dates
        if from_date:
            try:
                from_date = getdate(from_date)
            except Exception:
                return {
                    "status": "failure",
                    "message": "Invalid from_date format. Use 'YYYY-MM-DD' format."
                }
        else:
            from_date = nowdate()

        if to_date:
            try:
                to_date = getdate(to_date)
                # Validate that to_date is after from_date
                if to_date < from_date:
                    return {
                        "status": "failure",
                        "message": "to_date must be after or equal to from_date."
                    }
            except Exception:
                return {
                    "status": "failure",
                    "message": "Invalid to_date format. Use 'YYYY-MM-DD' format."
                }
        # If to_date is None, program has no expiry (unlimited tenure)

        # Validate conversion_factor if provided
        if conversion_factor is not None:
            try:
                conversion_factor = flt(conversion_factor)
                if conversion_factor <= 0:
                    return {
                        "status": "failure",
                        "message": "conversion_factor must be a positive number (e.g., 0.01 for 1 point = 0.01 currency)"
                    }
            except (ValueError, TypeError):
                return {
                    "status": "failure",
                    "message": "Invalid conversion_factor. Must be a number (e.g., 0.01)"
                }

        # Create the Loyalty Program
        loyalty_program_data = {
            'doctype': 'Loyalty Program',
            'loyalty_program_name': loyalty_program_name,
            'loyalty_program_type': program_type,  # Use 'Single Tier Program' or 'Multiple Tier Program'
            'company': company,  # Set company for isolation
            'from_date': from_date,
            'collection_rules': [
                {
                    'doctype': 'Loyalty Program Collection',
                    'tier_name': tier_name,          
                    'collection_factor': points_per_unit,  # Required field (=1 LP per collection_factor currency)
                    'min_spent': 0                    # Required: Lowest tier must have min_spent = 0 for immediate enrollment
                }
            ]
        }
        
        # Only add to_date if provided (None means unlimited tenure)
        if to_date:
            loyalty_program_data['to_date'] = to_date
        
        # Add conversion_factor if provided (required for redemption)
        if conversion_factor is not None:
            loyalty_program_data['conversion_factor'] = conversion_factor
        
        # Add expense_account if provided
        if expense_account:
            if not frappe.db.exists("Account", expense_account):
                return {
                    "status": "failure",
                    "message": f"Expense account '{expense_account}' does not exist"
                }
            loyalty_program_data['expense_account'] = expense_account
        
        # Add cost_center if provided
        if cost_center:
            if not frappe.db.exists("Cost Center", cost_center):
                return {
                    "status": "failure",
                    "message": f"Cost center '{cost_center}' does not exist"
                }
            loyalty_program_data['cost_center'] = cost_center
        
        # Add expiry_duration if provided
        if expiry_duration is not None:
            try:
                expiry_duration = int(expiry_duration)
                if expiry_duration < 0:
                    return {
                        "status": "failure",
                        "message": "expiry_duration must be a non-negative integer (days)"
                    }
                loyalty_program_data['expiry_duration'] = expiry_duration
            except (ValueError, TypeError):
                return {
                    "status": "failure",
                    "message": "Invalid expiry_duration. Must be an integer (days)"
                }

        loyalty_program = frappe.get_doc(loyalty_program_data)

        loyalty_program.insert()

        return {
            "status": "success",
            "message": _("Loyalty Program created successfully."),
            "loyalty_program": loyalty_program.as_dict()
        }

    except Exception as e:
        frappe.log_error(_('Create Loyalty Program API Error'), frappe.get_traceback())
        return {
            "status": "failure",
            "message": _("An error occurred while creating the Loyalty Program."),
            "error": str(e)
        }


@frappe.whitelist(allow_guest=False)
def get_loyalty_program_rules(loyalty_program_name=None, loyalty_program_id=None, company=None):
    """
    Fetch loyalty program rules (collection rules/tiers) for a given program.
    
    Loyalty program must belong to the logged-in user's company for isolation.
    
    :param loyalty_program_name: Name of the Loyalty Program (optional if loyalty_program_id is provided)
    :param loyalty_program_id: ID of the Loyalty Program (optional if loyalty_program_name is provided)
    :param company: Company name (optional, defaults to logged-in user's company)
    :return: Loyalty program details with collection rules
    """
    debug = []
    
    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter."),
                "debug": debug
            }
        
        # Validate that at least one identifier is provided
        if not loyalty_program_name and not loyalty_program_id:
            return {
                "status": "failure",
                "message": "Either loyalty_program_name or loyalty_program_id must be provided.",
                "debug": debug
            }
        
        # Fetch the loyalty program
        if loyalty_program_id:
            # Fetch by ID
            try:
                loyalty_program = frappe.get_doc('Loyalty Program', loyalty_program_id)
                debug.append(f"Fetched loyalty program by ID: {loyalty_program_id}")
                
                # Verify company isolation
                if loyalty_program.company != company:
                    return {
                        "status": "failure",
                        "message": _("Loyalty Program '{0}' does not belong to company '{1}'").format(loyalty_program_id, company),
                        "debug": debug
                    }
            except frappe.DoesNotExistError:
                return {
                    "status": "failure",
                    "message": "Loyalty Program not found with the provided ID.",
                    "debug": debug
                }
        else:
            # Fetch by name and company for isolation
            programs = frappe.get_all(
                'Loyalty Program',
                filters={
                    'loyalty_program_name': loyalty_program_name,
                    'company': company  # Filter by company for isolation
                },
                limit=1
            )
            if not programs:
                return {
                    "status": "failure",
                    "message": _("Loyalty Program '{0}' not found for company '{1}'").format(loyalty_program_name, company),
                    "debug": debug
                }
            loyalty_program = frappe.get_doc('Loyalty Program', programs[0].name)
            debug.append(f"Fetched loyalty program by name: {loyalty_program_name} for company: {company}")
        
        # Fetch collection rules (tiers)
        collection_rules = frappe.get_all(
            'Loyalty Program Collection',
            filters={'parent': loyalty_program.name},
            fields=[
                'tier_name',
                'collection_factor',
                'min_spent'
            ],
            order_by='min_spent asc'  # Order by min_spent to show tiers from lowest to highest
        )
        
        debug.append(f"Fetched {len(collection_rules)} collection rules")
        
        # Prepare program details
        program_details = {
            "name": loyalty_program.name,
            "loyalty_program_name": loyalty_program.loyalty_program_name,
            "loyalty_program_type": getattr(loyalty_program, 'loyalty_program_type', None),
            "from_date": str(loyalty_program.from_date) if loyalty_program.from_date else None,
            "to_date": str(loyalty_program.to_date) if loyalty_program.to_date else None,
            "collection_rules": collection_rules
        }
        
        return {
            "status": "success",
            "message": "Loyalty program rules fetched successfully.",
            "loyalty_program": program_details,
            "total_tiers": len(collection_rules),
            "debug": debug
        }
        
    except Exception as e:
        frappe.log_error(_('Get Loyalty Program Rules API Error'), frappe.get_traceback())
        return {
            "status": "failure",
            "message": "An error occurred while fetching loyalty program rules.",
            "error": str(e),
            "debug": debug
        }


@frappe.whitelist(allow_guest=False)
def list_loyalty_programs(company=None):
    """
    List all loyalty programs with basic information for a specific company.
    
    Loyalty programs are isolated per company for security.
    
    :param company: Company name (optional, defaults to logged-in user's company)
    :return: List of loyalty programs
    """
    debug = []
    
    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter."),
                "programs": [],
                "total_programs": 0,
                "debug": debug
            }
        
        # Build filters - filter by company for isolation
        filters = {'company': company}
        
        # Fetch loyalty programs for the company
        programs = frappe.get_all(
            'Loyalty Program',
            filters=filters,
            fields=[
                'name',
                'loyalty_program_name',
                'loyalty_program_type',
                'company',
                'from_date',
                'to_date'
            ],
            order_by='loyalty_program_name asc'
        )
        
        debug.append(f"Fetched {len(programs)} loyalty program(s)")
        
        # Convert dates to strings for JSON serialization
        for program in programs:
            if program.get('from_date'):
                program['from_date'] = str(program['from_date'])
            if program.get('to_date'):
                program['to_date'] = str(program['to_date'])
        
        return {
            "status": "success",
            "message": "Loyalty programs fetched successfully.",
            "programs": programs,
            "total_programs": len(programs),
            "debug": debug
        }
        
    except Exception as e:
        frappe.log_error(_('List Loyalty Programs API Error'), frappe.get_traceback())
        return {
            "status": "failure",
            "message": "An error occurred while fetching loyalty programs.",
            "error": str(e),
            "debug": debug
        }


@frappe.whitelist(allow_guest=False)
def assign_loyalty_program(customer_id, loyalty_program_name, company=None):
    """
    Assign a Loyalty Program to a Customer.
    
    Customer and Loyalty Program must belong to the same company for isolation.
    
    :param customer_id: Customer ID (e.g., CUST-001)
    :param loyalty_program_name: Name of the Loyalty Program to assign
    :param company: Company name (optional, defaults to logged-in user's company)
    :return: Success or failure message
    """
    # Loyalty programs: only roles the frontend gives the loyalty settings screen
    require_loyalty_role()
    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter.")
            }

        # Fetch the customer
        customer = frappe.get_doc('Customer', customer_id)
        if not customer:
            return {
                "status": "failure",
                "message": _("Customer not found.")
            }
        
        # Verify customer belongs to the company (company isolation)
        if frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer {0} does not belong to company {1}").format(customer_id, company)
                }

        # Fetch the loyalty program by loyalty_program_name and company
        loyalty_programs = frappe.get_all(
            'Loyalty Program',
            filters={
                'loyalty_program_name': loyalty_program_name,
                'company': company  # Ensure loyalty program belongs to the same company
            },
            limit=1
        )
        if not loyalty_programs:
            return {
                "status": "failure",
                "message": _("Loyalty Program '{0}' not found for company '{1}'").format(loyalty_program_name, company)
            }
        loyalty_program = frappe.get_doc('Loyalty Program', loyalty_programs[0].name)
        
        # Verify loyalty program belongs to the company (double check)
        if loyalty_program.company != company:
            return {
                "status": "failure",
                "message": _("Loyalty Program '{0}' does not belong to company '{1}'").format(loyalty_program_name, company)
            }

        # Assign the loyalty program
        customer.loyalty_program = loyalty_program.name
        customer.save(ignore_permissions=True)

        return {
            "status": "success",
            "message": _("Loyalty Program assigned to customer successfully."),
            "customer": customer.as_dict()
        }

    except Exception as e:
        frappe.log_error(_('Assign Loyalty Program API Error'), frappe.get_traceback())
        return {
            "status": "failure",
            "message": _("An error occurred while assigning the Loyalty Program to the customer."),
            "error": str(e)
        }

     
@frappe.whitelist(allow_guest=False)
def earn_loyalty_points(customer_id, purchase_amount, company=None):
    """
    Earn loyalty points for a customer.
    
    Customer and loyalty program must belong to the logged-in user's company for isolation.
    
    :param customer_id: Customer ID
    :param purchase_amount: Purchase amount (numeric)
    :param company: Company name (optional, defaults to logged-in user's company)
    :return: Success or failure message with points earned
    """
    debug_messages = []

    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter."),
                "debug": debug_messages
            }

        try:
            purchase_amount = float(purchase_amount)
            debug_messages.append(f"Converted purchase_amount: {purchase_amount}")
        except (ValueError, TypeError):
            return {
                "status": "failure",
                "message": "Invalid purchase_amount, must be numeric.",
                "debug": debug_messages
            }

        customer = frappe.get_doc('Customer', customer_id)
        debug_messages.append(f"Customer fetched: {customer.name}")
        
        # Verify customer belongs to the company (company isolation)
        if frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer {0} does not belong to company {1}").format(customer_id, company),
                    "debug": debug_messages
                }

        # Check assigned Loyalty Program
        if not customer.loyalty_program:
            return {
                "status": "failure",
                "message": _("No loyalty program assigned to this customer."),
                "debug": debug_messages
            }

        loyalty_program = frappe.get_doc('Loyalty Program', customer.loyalty_program)
        debug_messages.append(f"Loyalty Program fetched: {loyalty_program.name}")
        
        # Verify loyalty program belongs to the company (company isolation)
        if loyalty_program.company != company:
            return {
                "status": "failure",
                "message": _("Customer's loyalty program does not belong to company {0}").format(company),
                "debug": debug_messages
            }

        meta = frappe.get_meta('Loyalty Program Collection')
        available_fields = [f.fieldname for f in meta.fields]
        debug_messages.append(f"Available fields in Loyalty Program Collection: {available_fields}")

        if 'collection_factor' not in available_fields:
            return {
                "status": "failure",
                "message": "collection_factor field not found in Loyalty Program Collection.",
                "debug": debug_messages
            }

        min_spent_available = 'min_spent' in available_fields

        # Fetch collection rules
        fields_to_fetch = ['collection_factor']
        if min_spent_available:
            fields_to_fetch.append('min_spent')

        collection_rules = frappe.get_all(
            'Loyalty Program Collection',
            filters={'parent': loyalty_program.name},
            fields=fields_to_fetch
        )
        debug_messages.append(f"Fetched collection rules: {collection_rules}")

        if not collection_rules:
            return {
                "status": "failure",
                "message": "No collection rules found for this loyalty program.",
                "debug": debug_messages
            }

        #  Calculate points
        points_earned = 0
        for rule in collection_rules:
            try:
                factor = float(rule.get('collection_factor', 0))
            except (ValueError, TypeError):
                debug_messages.append(f"Skipping invalid collection_factor: {rule.get('collection_factor')}")
                continue

            if factor <= 0:
                continue

            # Optional min_spent check
            if min_spent_available:
                try:
                    min_spent = float(rule.get('min_spent', 0))
                except (ValueError, TypeError):
                    min_spent = 0
                if purchase_amount < min_spent:
                    debug_messages.append(
                        f"Purchase amount {purchase_amount} below min_spent {min_spent}, skipping rule."
                    )
                    continue

            # 1 point per collection_factor spent
            points_earned += int(purchase_amount // factor)

        debug_messages.append(f"Points earned calculated: {points_earned}")

        if points_earned <= 0:
            return {
                "status": "failure",
                "message": "Purchase amount too low to earn points.",
                "debug": debug_messages
            }

        # Update points on Customer
        current_points = customer.get('loyalty_points') or 0
        customer.loyalty_points = current_points + points_earned
        customer.save()
        debug_messages.append(f"Updated Customer loyalty_points: {customer.loyalty_points}")

        # Log Loyalty earning transaction
        frappe.get_doc({
            'doctype': 'Loyalty Transaction Log',
            'customer': customer_id,
            'points_earned': points_earned,
            'purchase_amount': purchase_amount,
            'transaction_type': 'Earn',
            'date': now_datetime(),
            'reference_document': None
        }).insert()
        debug_messages.append(f"Loyalty Transaction logged: {points_earned} points")

        return {
            "status": "success",
            "message": "Points earned successfully.",
            "points_earned": points_earned,
            "total_points": customer.loyalty_points,
            "debug": debug_messages
        }

    except Exception as e:
        frappe.log_error("Earn Points API Error", frappe.get_traceback())
        return {
            "status": "failure",
            "message": "Failed to earn points.",
            "error": str(e),
            "debug": debug_messages
        }



@frappe.whitelist(allow_guest=False)
def get_loyalty_balance(customer_id, company=None, limit=5):
    """
    Get loyalty points balance for a customer.
    
    Customer must belong to the logged-in user's company for isolation.
    
    :param customer_id: Customer ID
    :param company: Company name (optional, defaults to logged-in user's company)
    :param limit: Number of recent transactions to return (default: 5)
    :return: Loyalty balance and recent transactions
    """
    debug = []

    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter."),
                "debug": debug
            }
        
        customer = frappe.get_doc("Customer", customer_id)
        debug.append(f"Customer fetched: {customer.name}")
        
        # Verify customer belongs to the company (company isolation)
        if frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer {0} does not belong to company {1}").format(customer_id, company),
                    "debug": debug
                }
        
        # Verify customer's loyalty program belongs to the company
        if customer.loyalty_program:
            loyalty_program_company = frappe.db.get_value("Loyalty Program", customer.loyalty_program, "company")
            if loyalty_program_company and loyalty_program_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer's loyalty program does not belong to company {0}").format(company),
                    "debug": debug
                }

        # Calculate balance from ledger - filter by company through loyalty program
        # Loyalty Point Entry is company-specific, so we need to filter by company
        points_balance = 0
        if customer.loyalty_program:
            # Get balance from Loyalty Point Entry (company-specific)
            from erpnext.accounts.doctype.loyalty_program.loyalty_program import get_loyalty_program_details_with_points
            
            try:
                loyalty_details = get_loyalty_program_details_with_points(
                    customer_id, 
                    customer.loyalty_program, 
                    company=company
                )
                points_balance = loyalty_details.get("loyalty_points", 0) or 0
            except Exception:
                # Fallback to transaction log if Loyalty Point Entry doesn't work
                points_balance = frappe.db.sql("""
                    SELECT
                        COALESCE(SUM(
                            CASE
                                WHEN transaction_type = 'Earn' THEN points_earned
                                WHEN transaction_type = 'Redeem' THEN -points_earned
                                ELSE 0
                            END
                        ), 0)
                    FROM `tabLoyalty Transaction Log`
                    WHERE customer = %s
                """, customer.name)[0][0] or 0

        debug.append(f"Calculated balance from transactions: {points_balance}")

        transactions = frappe.get_all(
            "Loyalty Transaction Log",
            filters={"customer": customer.name},
            fields=[
                "points_earned",
                "transaction_type",
                "purchase_amount",
                "date",
                "reference_document"
            ],
            order_by="date desc",
            limit_page_length=limit
        )

        return {
            "status": "success",
            "customer": customer.name,
            "points_balance": points_balance,
            "recent_transactions": transactions,
            "debug": debug
        }

    except frappe.DoesNotExistError:
        return {
            "status": "failure",
            "message": "Customer not found",
            "debug": debug
        }
    except Exception as e:
        frappe.log_error("Get Loyalty Balance API Error", frappe.get_traceback())
        return {
            "status": "failure",
            "message": "Failed to fetch loyalty balance",
            "error": str(e),
            "debug": debug
        }


@frappe.whitelist(allow_guest=False)
def redeem_points(customer_id, points_to_redeem, company=None, reference_document=None):
    """
    Redeem Loyalty Points API
    
    Customer must belong to the logged-in user's company for isolation.
    
    :param customer_id: Customer ID
    :param points_to_redeem: Number of points to redeem (positive integer)
    :param company: Company name (optional, defaults to logged-in user's company)
    :param reference_document: Reference document name (optional)
    :return: Success or failure message with redemption details
    """

    debug = []

    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter."),
                "debug": debug
            }

        try:
            points_to_redeem = int(points_to_redeem)
            if points_to_redeem <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return {
                "status": "failure",
                "message": "points_to_redeem must be a positive integer",
                "debug": debug
            }

        customer = frappe.get_doc("Customer", customer_id)
        debug.append(f"Customer fetched: {customer.name}")
        
        # Verify customer belongs to the company (company isolation)
        if frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer {0} does not belong to company {1}").format(customer_id, company),
                    "debug": debug
                }
        
        # Verify customer's loyalty program belongs to the company
        if customer.loyalty_program:
            loyalty_program_company = frappe.db.get_value("Loyalty Program", customer.loyalty_program, "company")
            if loyalty_program_company and loyalty_program_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer's loyalty program does not belong to company {0}").format(company),
                    "debug": debug
                }

        # Calculate available balance from ledger
        available_points = frappe.db.sql("""
            SELECT
                COALESCE(SUM(
                    CASE
                        WHEN transaction_type = 'Earn' THEN points_earned
                        WHEN transaction_type = 'Redeem' THEN -points_earned
                        ELSE 0
                    END
                ), 0)
            FROM `tabLoyalty Transaction Log`
            WHERE customer = %s
        """, customer.name)[0][0] or 0

        debug.append(f"Available points: {available_points}")

        # Validate sufficient balance
        if available_points < points_to_redeem:
            return {
                "status": "failure",
                "message": "Insufficient loyalty points",
                "available_points": available_points,
                "requested_points": points_to_redeem,
                "debug": debug
            }

        # Redeem transaction
        redeem_log = frappe.get_doc({
            "doctype": "Loyalty Transaction Log",
            "customer": customer.name,
            "transaction_type": "Redeem",
            "points_earned": points_to_redeem,  # Stored as positive; sign handled in query
            "purchase_amount": 0,
            "date": now_datetime(),
            "reference_document": reference_document
        })
        redeem_log.insert(ignore_permissions=True)

        debug.append(f"Redeem transaction created: {points_to_redeem} points")

        # OPTIONAL: update cached customer.loyalty_points
        new_balance = available_points - points_to_redeem
        customer.loyalty_points = new_balance
        customer.save(ignore_permissions=True)

        debug.append(f"Customer loyalty_points cache updated: {new_balance}")

        return {
            "status": "success",
            "message": "Points redeemed successfully",
            "redeemed_points": points_to_redeem,
            "remaining_points": new_balance,
            "reference_document": reference_document,
            "debug": debug
        }

    except frappe.DoesNotExistError:
        return {
            "status": "failure",
            "message": "Customer not found",
            "debug": debug
        }

    except Exception as e:
        frappe.log_error("Redeem Points API Error", frappe.get_traceback())
        return {
            "status": "failure",
            "message": "Failed to redeem points",
            "error": str(e),
            "debug": debug
        }


@frappe.whitelist()
def get_customer_loyalty_details(customer_id, company=None, invoice_amount=None):
    """
    Get customer's loyalty points details for redemption.
    
    This endpoint provides all information needed to redeem loyalty points:
    - Available loyalty points balance
    - Conversion factor (points to currency)
    - Maximum redeemable amount
    - Redemption account and cost center
    
    Args:
        customer_id: Customer ID
        company: Company name (optional, defaults to logged-in user's company)
        invoice_amount: Invoice total amount (optional, for calculating max redeemable)
    
    Returns:
        dict: Customer loyalty details including available points, conversion factor, etc.
    """
    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "error",
                "message": _("Company is required. Please set a default company or provide company parameter.")
            }
        
        # Fetch customer
        if not frappe.db.exists("Customer", customer_id):
            return {
                "status": "error",
                "message": _("Customer {0} not found").format(customer_id)
            }
        
        customer = frappe.get_doc("Customer", customer_id)
        
        # Check if customer has loyalty program
        if not customer.loyalty_program:
            return {
                "status": "success",
                "has_loyalty_program": False,
                "message": "Customer is not enrolled in any loyalty program",
                "loyalty_points": 0,
                "conversion_factor": 0,
                "max_redeemable_amount": 0
            }
        
        # Get loyalty program details
        from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
            get_loyalty_program_details_with_points
        )
        
        loyalty_program = frappe.get_doc("Loyalty Program", customer.loyalty_program)
        
        # Verify loyalty program belongs to company
        if loyalty_program.company != company:
            return {
                "status": "error",
                "message": _("Customer's loyalty program does not belong to company {0}").format(company)
            }
        
        # Get loyalty details with points
        expiry_date = nowdate()
        loyalty_details = get_loyalty_program_details_with_points(
            customer=customer_id,
            loyalty_program=customer.loyalty_program,
            expiry_date=expiry_date,
            company=company,
            include_expired_entry=False
        )
        
        available_points = loyalty_details.get("loyalty_points", 0) or 0
        
        # Get conversion_factor from loyalty program document (for redemption)
        # conversion_factor is stored on the Loyalty Program doctype itself
        # This is different from collection_factor which is for earning points
        conversion_factor = loyalty_program.conversion_factor
        
        # If conversion_factor is None or 0, try to get it from loyalty_details dict
        if not conversion_factor:
            conversion_factor = loyalty_details.get("conversion_factor") or 0
        
        # Validate conversion_factor is set
        if not conversion_factor or conversion_factor <= 0:
            return {
                "status": "error",
                "message": f"Loyalty Program '{loyalty_program.loyalty_program_name}' does not have a conversion factor configured. Please set the conversion factor in the Loyalty Program settings.",
                "loyalty_program": customer.loyalty_program
            }
        
        # Calculate maximum redeemable amount
        max_redeemable_amount = flt(available_points * conversion_factor, 2)
        
        # If invoice amount provided, calculate max redeemable based on invoice
        if invoice_amount:
            invoice_amount = flt(invoice_amount, 2)
            max_redeemable_by_points = max_redeemable_amount
            max_redeemable_by_invoice = invoice_amount
            max_redeemable_amount = min(max_redeemable_by_points, max_redeemable_by_invoice)
            max_redeemable_points = int(max_redeemable_amount / conversion_factor) if conversion_factor > 0 else 0
        else:
            max_redeemable_points = available_points
        
        return {
            "status": "success",
            "has_loyalty_program": True,
            "customer": customer_id,
            "loyalty_program": customer.loyalty_program,
            "loyalty_program_name": loyalty_program.loyalty_program_name,
            "loyalty_points": available_points,
            "conversion_factor": conversion_factor,
            "max_redeemable_amount": max_redeemable_amount,
            "max_redeemable_points": max_redeemable_points,
            "expense_account": loyalty_program.expense_account,
            "cost_center": loyalty_program.cost_center,
            "tier_name": loyalty_details.get("tier_name"),
            "total_spent": loyalty_details.get("total_spent", 0)
        }
    
    except Exception as e:
        frappe.log_error("Get Customer Loyalty Details API Error", frappe.get_traceback())
        return {
            "status": "error",
            "message": f"Error fetching loyalty details: {str(e)}"
        }


@frappe.whitelist()
def calculate_loyalty_redemption(customer_id, points_to_redeem, invoice_amount=None, company=None):
    """
    Calculate loyalty points redemption amount and validate redemption.
    
    This endpoint calculates the discount amount for redeeming loyalty points
    and validates if the redemption is possible.
    
    Args:
        customer_id: Customer ID
        points_to_redeem: Number of loyalty points to redeem
        invoice_amount: Invoice total amount (optional, for validation)
        company: Company name (optional, defaults to logged-in user's company)
    
    Returns:
        dict: Redemption calculation including discount amount and validation
    """
    try:
        # Get logged-in user's company
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "error",
                "message": _("Company is required.")
            }
        
        # Validate points_to_redeem
        try:
            points_to_redeem = int(points_to_redeem)
            if points_to_redeem <= 0:
                return {
                    "status": "error",
                    "message": "Points to redeem must be a positive integer"
                }
        except (ValueError, TypeError):
            return {
                "status": "error",
                "message": "Invalid points_to_redeem. Must be a positive integer."
            }
        
        # Get customer loyalty details
        loyalty_details = get_customer_loyalty_details(customer_id, company, invoice_amount)
        
        if loyalty_details.get("status") != "success":
            return loyalty_details
        
        if not loyalty_details.get("has_loyalty_program"):
            return {
                "status": "error",
                "message": "Customer is not enrolled in any loyalty program"
            }
        
        available_points = loyalty_details.get("loyalty_points", 0)
        conversion_factor = loyalty_details.get("conversion_factor", 0)
        
        # Validate sufficient points
        if points_to_redeem > available_points:
            return {
                "status": "error",
                "message": f"Insufficient loyalty points. Available: {available_points}, Requested: {points_to_redeem}",
                "available_points": available_points,
                "requested_points": points_to_redeem
            }
        
        # Calculate discount amount
        discount_amount = flt(points_to_redeem * conversion_factor, 2)
        
        # Validate against invoice amount if provided
        if invoice_amount:
            invoice_amount = flt(invoice_amount, 2)
            if discount_amount > invoice_amount:
                max_points = int(invoice_amount / conversion_factor) if conversion_factor > 0 else 0
                return {
                    "status": "error",
                    "message": f"Redemption amount ({discount_amount}) exceeds invoice amount ({invoice_amount}). Maximum redeemable: {max_points} points",
                    "discount_amount": discount_amount,
                    "invoice_amount": invoice_amount,
                    "max_redeemable_points": max_points
                }
        
        return {
            "status": "success",
            "points_to_redeem": points_to_redeem,
            "discount_amount": discount_amount,
            "conversion_factor": conversion_factor,
            "remaining_points": available_points - points_to_redeem,
            "expense_account": loyalty_details.get("expense_account"),
            "cost_center": loyalty_details.get("cost_center"),
            "loyalty_program": loyalty_details.get("loyalty_program")
        }
    
    except Exception as e:
        frappe.log_error("Calculate Loyalty Redemption API Error", frappe.get_traceback())
        return {
            "status": "error",
            "message": f"Error calculating redemption: {str(e)}"
        }


@frappe.whitelist(allow_guest=False)
def get_points_history(
    customer_id,
    company=None,
    start_date=None,
    end_date=None,
    transaction_type=None,
    limit=50,
    page=1
):
    """
    Get Loyalty Points History API (ERPNext v15)
    
    Customer must belong to the logged-in user's company for isolation.
    
    :param customer_id: Customer ID
    :param company: Company name (optional, defaults to logged-in user's company)
    :param start_date: Start date in YYYY-MM-DD format (optional)
    :param end_date: End date in YYYY-MM-DD format (optional)
    :param transaction_type: Transaction type - "Earn", "Redeem", "Expire", or "Adjust" (optional)
    :param limit: Number of records per page (default: 50)
    :param page: Page number (default: 1)
    :return: Loyalty points transaction history
    """
    debug = []

    try:
        # Get logged-in user's company (required for isolation)
        if not company:
            company = frappe.defaults.get_user_default("Company")
            if not company:
                # Try to get from user's custom_company field (for staff users)
                company = frappe.db.get_value("User", frappe.session.user, "custom_company")
        
        if not company:
            return {
                "status": "failure",
                "message": _("Company is required. Please set a default company or provide company parameter."),
                "debug": debug
            }

        # Normalize pagination
        limit = cint(limit) or 50
        page = cint(page) or 1
        offset = (page - 1) * limit

        # Fetch customer
        customer = frappe.get_doc("Customer", customer_id)
        debug.append(f"Customer fetched: {customer.name}")
        
        # Verify customer belongs to the company (company isolation)
        if frappe.db.has_column("Customer", "custom_company"):
            customer_company = getattr(customer, "custom_company", None)
            if customer_company and customer_company != company:
                return {
                    "status": "failure",
                    "message": _("Customer {0} does not belong to company {1}").format(customer_id, company),
                    "debug": debug
                }

        # Build filters
        filters = {"customer": customer.name}

        # Date filters
        if start_date and end_date:
            filters["date"] = ["between", [getdate(start_date), getdate(end_date)]]
            debug.append(f"Date range filter: {start_date} to {end_date}")
        elif start_date:
            filters["date"] = [">=", getdate(start_date)]
            debug.append(f"Start date filter: {start_date}")
        elif end_date:
            filters["date"] = ["<=", getdate(end_date)]
            debug.append(f"End date filter: {end_date}")

        # Transaction type filter
        if transaction_type:
            transaction_type = transaction_type.capitalize()
            if transaction_type not in ["Earn", "Redeem", "Expire", "Adjust"]:
                return {
                    "status": "failure",
                    "message": "Invalid transaction_type",
                    "allowed": ["earn", "redeem", "expire", "adjust"]
                }
            filters["transaction_type"] = transaction_type
            debug.append(f"Transaction type filter: {transaction_type}")

        # Fetch transactions
        transactions = frappe.get_all(
            "Loyalty Transaction Log",
            filters=filters,
            fields=[
                "name",
                "date",
                "transaction_type",
                "points_earned",
                "purchase_amount",
                "reference_document"
            ],
            order_by="date desc",
            limit_page_length=limit,
            limit_start=offset
        )

        # Total count (for pagination)
        total_count = frappe.db.count("Loyalty Transaction Log", filters)

        debug.append(f"Fetched {len(transactions)} transactions")

        return {
            "status": "success",
            "customer": customer.name,
            "pagination": {
                "page": page,
                "limit": limit,
                "total_records": total_count,
                "total_pages": (total_count + limit - 1) // limit
            },
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "transaction_type": transaction_type.lower() if transaction_type else None
            },
            "transactions": transactions,
            "debug": debug
        }

    except frappe.DoesNotExistError:
        return {
            "status": "failure",
            "message": "Customer not found",
            "debug": debug
        }

    except Exception as e:
        frappe.log_error("Get Points History API Error", frappe.get_traceback())
        return {
            "status": "failure",
            "message": "Failed to fetch points history",
            "error": str(e),
            "debug": debug
        }
