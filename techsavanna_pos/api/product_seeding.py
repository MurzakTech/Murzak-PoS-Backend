
"""
POS Industry Products Seeding API
Handles POS industry Product seeding for selected industry
"""

import frappe
import json
import os
from frappe import _
from typing import Dict, List, Optional
from frappe.utils import flt, nowdate, getdate, get_first_day, get_last_day

from techsavanna_pos.api.etims_optional import relax_etims_mandatory
from techsavanna_pos.api.seed_results import readable_seed_error, seed_result_status


def ensure_fiscal_year_exists(company: str, posting_date: str = None) -> str:
    """
    Ensure a Fiscal Year exists for the given company and date.
    Creates one automatically if it doesn't exist.
    
    Args:
        company: Company name
        posting_date: Date to check (defaults to today)
    
    Returns:
        Fiscal Year name
    """
    from frappe.utils import getdate, get_first_day, get_last_day
    
    if not posting_date:
        posting_date = nowdate()
    
    date = getdate(posting_date)
    year = date.year
    
    # Check if a fiscal year exists for this date and company
    fiscal_year = frappe.db.sql("""
        SELECT fy.name 
        FROM `tabFiscal Year` fy
        LEFT JOIN `tabFiscal Year Company` fyc ON fyc.parent = fy.name
        WHERE fy.disabled = 0
        AND %s BETWEEN fy.year_start_date AND fy.year_end_date
        AND (fyc.company = %s OR fyc.company IS NULL OR NOT EXISTS (
            SELECT 1 FROM `tabFiscal Year Company` WHERE parent = fy.name
        ))
        LIMIT 1
    """, (posting_date, company), as_dict=True)
    
    if fiscal_year:
        return fiscal_year[0].name
    
    # No fiscal year found, create one
    # Determine fiscal year dates (calendar year by default)
    year_start = f"{year}-01-01"
    year_end = f"{year}-12-31"
    fy_name = str(year)
    
    # Check if fiscal year with this name already exists but doesn't cover this company
    existing_fy = frappe.db.exists("Fiscal Year", fy_name)
    
    if existing_fy:
        # Add company to existing fiscal year
        fy_doc = frappe.get_doc("Fiscal Year", fy_name)
        company_exists = any(c.company == company for c in fy_doc.companies)
        if not company_exists:
            fy_doc.append("companies", {"company": company})
            fy_doc.save(ignore_permissions=True)
            frappe.db.commit()
        return fy_name
    
    # Create new fiscal year
    try:
        fy_doc = frappe.new_doc("Fiscal Year")
        fy_doc.year = fy_name
        fy_doc.year_start_date = year_start
        fy_doc.year_end_date = year_end
        fy_doc.disabled = 0
        fy_doc.append("companies", {"company": company})
        fy_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(
            f"Auto-created Fiscal Year {fy_name} for company {company}"
        )
        
        return fy_doc.name
    except frappe.DuplicateEntryError:
        # Race condition - fiscal year was created by another process
        return fy_name
    except Exception as e:
        frappe.log_error(
            "Auto Create Fiscal Year Error",
            f"Error creating Fiscal Year for {company}: {str(e)}"
        )
        raise



@frappe.whitelist(allow_guest=True)
def get_pos_industries(is_active: bool = False) -> Dict:
    """Get list of all POS industries
    
    Args:
        is_active: Filter by active status (default: False)
        
    Returns:
        List of POS industries with details
    """
    try:
        # Convert the `is_active` to an integer (if it is True, use 1; otherwise, 0)
        filters = {}
        if is_active:
            filters["is_active"] = 1
        else:
            filters["is_active"] = 0
        
        print(f'\nIndustry Status....{filters}')
        
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
        frappe.local.response["http_status_code"] = 500  # Internal Server Error
        
        return {
            "success": False,
            "message": _("Something went wrong while retrieving POS industries."),
            "error_details": str(e)  
        }


@frappe.whitelist(methods=["GET", "POST"])
def seed_products(industry):
    """
    Return all items for the given industry, the total count.
    """

    # Validate industry parameter
    if not industry:
        return {"status": "error", "message": _("Industry is required"), "total_products": 0}

    if not frappe.db.exists("POS Industry", industry):
        return {
            "status": "error",
            "message": _("Industry '{0}' does not exist").format(industry),
            "total_products": 0
        }

    templates = frappe.get_all(
        "Industry Product Template",
        filters={"industry": industry},
        fields=["item_code", "item_name"]
    )

    total_products = len(templates)

    if total_products == 0:
        return {
            "status": "error",
            "message": _("No products found for industry '{0}'").format(industry),
            "total_products": 0
        }

    # Format each template
    products = []
    for tpl in templates:
        products.append({
            "sku": tpl.item_code,
            "name": tpl.item_name,
            "status": "available"
        })

    return {
        "status": "success",
        "industry": industry,
        "total_products": total_products,
        "products": products
    }






@frappe.whitelist(methods=["POST"])
def bulk_upload_products():
    """
    Load industry products from JSON file and insert into Industry Product Template.
    Supports:
      - Normal Run : POST /api/method/techsavanna_pos.api.product_seeding.bulk_upload_products
      - Dry Run (Preview Only)  :: POST /api/method/techsavanna_pos.api.product_seeding.bulk_upload_products?dry_run=true
      - strict=true   → fail if any industry is missing : 
                    POST /api/method/techsavanna_pos.api.product_seeding.bulk_upload_products?strict=true

      - dry_run=true  → preview only, no DB writes
      - strict + Dry-run : POST /api/method/techsavanna_pos.api.product_seeding.bulk_upload_products?strict=true&dry_run=true
    """

    strict = frappe.form_dict.get("strict") in ("1", "true", "True")
    dry_run = frappe.form_dict.get("dry_run") in ("1", "true", "True")

    try:
        file_path = frappe.get_app_path(
            "techsavanna_pos",
            "products_seed_data",
            "industry_products.json"
        )

        if not os.path.exists(file_path):
            return {
                "status": "error",
                "message": _("Seed data file not found"),
                "path": file_path
            }

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data:
            return {
                "status": "error",
                "message": _("Seed data file is empty")
            }

        created = 0
        skipped = 0
        failed = 0

        ignored_industries = []
        skipped_items = []
        failed_items = []

        # ----------------------------------
        # Resolve industries first (STRICT)
        # ----------------------------------
        resolved_industries = {}

        for industry_key in data.keys():
            industry = (
                frappe.db.get_value("POS Industry", {"name": industry_key}, "name")
                or frappe.db.get_value("POS Industry", {"industry_code": industry_key}, "name")
                or frappe.db.get_value("POS Industry", {"industry_name": industry_key}, "name")
            )

            if not industry:
                ignored_industries.append(industry_key)
            else:
                resolved_industries[industry_key] = industry

        if strict and ignored_industries:
            return {
                "status": "failed",
                "message": _("Strict mode enabled. Missing industries detected."),
                "summary": {
                    "industries_in_file": len(data),
                    "industries_missing": len(ignored_industries)
                },
                "details": {
                    "missing_industries": ignored_industries
                }
            }

        # ----------------------------------
        # Process items
        # ----------------------------------
        for industry_key, items in data.items():
            industry = resolved_industries.get(industry_key)

            if not industry:
                continue

            for item in items:
                item_code = item.get("item_code")
                item_name = item.get("item_name")

                if not item_code or not item_name:
                    skipped += 1
                    skipped_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "reason": "Missing item_code or item_name"
                    })
                    continue

                exists = frappe.db.exists(
                    "Industry Product Template",
                    {
                        "industry": industry,
                        "item_code": item_code
                    }
                )

                if exists:
                    skipped += 1
                    skipped_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "reason": "Already exists"
                    })
                    continue

                if dry_run:
                    created += 1
                    continue

                try:
                    doc = frappe.get_doc({
                        "doctype": "Industry Product Template",
                        "industry": industry,
                        "item_code": item_code,
                        "item_name": item_name,
                        "item_group": item.get("item_group"),
                        "uom": item.get("uom")
                    })
                    doc.insert(ignore_permissions=True)
                    created += 1

                except frappe.DuplicateEntryError:
                    skipped += 1
                    skipped_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "reason": "Duplicate entry"
                    })

                except Exception as e:
                    failed += 1
                    failed_items.append({
                        "industry": industry_key,
                        "item_code": item_code,
                        "error": str(e)
                    })

        if not dry_run:
            frappe.db.commit()

        # ----------------------------------
        # Final response
        # ----------------------------------
        status = (
            "success"
            if created and not failed
            else "partial_success"
            if created or skipped
            else "failed"
        )

        return {
            "status": status,
            "mode": {
                "strict": strict,
                "dry_run": dry_run
            },
            "summary": {
                "industries_in_file": len(data),
                "industries_ignored": len(ignored_industries),
                "items_created": created,
                "items_skipped": skipped,
                "items_failed": failed,
                "total_items_processed": created + skipped + failed
            },
            "details": {
                "ignored_industries": ignored_industries,
                "skipped_items": skipped_items,
                "failed_items": failed_items
            },
            "message": (
                _("Dry run completed. No data was written.")
                if dry_run
                else _("Products imported successfully.")
            )
        }

    except Exception as e:
        return {
            "status": "error",
            "message": _("Unexpected error: {0}").format(str(e))
        }



@frappe.whitelist(methods=["POST"])
def create_seed_item(company: str = None):
    """
    Create Items, Item Prices, and optionally update inventory from seed data.
    
    This function creates Item master records, Item Price records (selling and/or buying),
    and optionally creates Material Receipt Stock Entry to add inventory quantities.
    All records are scoped to the authenticated user's company.
    
    **Authentication Required**: This endpoint requires user authentication. Items will be scoped to the user's company.
    
    **Item Code Prefixing**: Item codes are automatically prefixed with the company abbreviation (e.g., "ABC-BURG001")
    to ensure uniqueness across different companies. If an item code already starts with the company abbreviation,
    it will be used as-is. This prevents conflicts when multiple companies seed the same product codes.

    Expected JSON payload:
    {
        "price_list": "Standard Selling",  // Required - Selling price list
        "buying_price_list": "Standard Buying",  // Optional - Buying price list
        "company": "Your Company Name",  // Optional - defaults to user's default company
        "industry": "REST",  // Optional - POS Industry code. If not provided, uses user's industry or NULL for global products
        "warehouse": "Main Warehouse",  // Optional - default warehouse for all items
        "items": [
            {
                "item_code": "BURG001",
                "item_name": "Cheese Burger",
                "item_price": 5.99,  // Required - Selling price
                "buying_price": 3.50,  // Optional - Buying/cost price (requires buying_price_list)
                "item_group": "All Item Groups",
                "uom": "Nos",
                "qty": 100,  // Optional - Quantity to add to inventory (requires warehouse)
                "warehouse": "Main Warehouse",  // Optional - Per-item warehouse (overrides default)
                "basic_rate": 3.50  // Optional - Cost per unit for inventory valuation (defaults to buying_price or 0)
            }
        ]
    }
    
    Returns:
        Dictionary with status, created count, skipped count, failed items, stock entries created, and total received
    """
    # Read & parse JSON safely
    payload = {}
    try:
        raw_data = frappe.request.data
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")
        if raw_data:
            payload = json.loads(raw_data)
    except Exception:
        payload = {}

    if not payload:
        payload = frappe.form_dict or {}

    if not isinstance(payload, dict):
        frappe.throw(_("Invalid payload"))

    # Validate user authentication
    if frappe.session.user == "Guest":
        frappe.throw(
            _("Please log in to create items. Your session has expired or you are not authenticated."),
            frappe.AuthenticationError
        )

    price_list = payload.get("price_list")
    buying_price_list = payload.get("buying_price_list")  # Optional
    items = payload.get("items")
    industry_code = payload.get("industry")  # Optional - industry code from payload
    default_warehouse = payload.get("warehouse")  # Optional - default warehouse for all items
    # Get company from payload or use parameter, fallback to user default
    company = payload.get("company") or company

    if not price_list:
        frappe.throw(_("Price List is required"))

    if not items or not isinstance(items, list):
        frappe.throw(_("Items must be a non-empty list"))
    
    # Validate buying price list if provided
    if buying_price_list:
        if not frappe.db.exists("Price List", buying_price_list):
            frappe.throw(_("Buying Price List '{0}' does not exist").format(buying_price_list))
        # Verify it's a buying price list
        price_list_details = frappe.db.get_value(
            "Price List",
            buying_price_list,
            ["buying", "selling", "enabled"],
            as_dict=True
        )
        if not price_list_details or not price_list_details.get("enabled"):
            frappe.throw(_("Buying Price List '{0}' is disabled or does not exist").format(buying_price_list))
        if not price_list_details.get("buying"):
            frappe.throw(_("Price List '{0}' is not a buying price list").format(buying_price_list))

    for doctype in ("Item", "Item Price"):
        if not frappe.db.exists("DocType", doctype):
            frappe.throw(_("{0} DocType is missing").format(doctype))

    # Get company - from parameter, payload, or user default
    if not company:
        company = frappe.defaults.get_user_default("Company")
        if not company:
            frappe.throw(
                _("Company is required. Please set a default company in your profile settings or provide the company parameter when creating items."),
                frappe.ValidationError
            )

    # Validate company exists
    if not frappe.db.exists("Company", company):
        frappe.throw(
            _("The company '{0}' does not exist. Please check the company name and try again.").format(company),
            frappe.ValidationError
        )

    # Get company abbreviation for item code prefixing
    company_abbr = frappe.db.get_value("Company", company, "abbr")
    if not company_abbr:
        frappe.throw(
            _("Company '{0}' does not have an abbreviation set. Please set the company abbreviation in Company settings.").format(company),
            frappe.ValidationError
        )
    company_abbr = company_abbr.strip().upper()

    # Determine industry for products:
    # 1. Use industry from payload if provided
    # 2. Otherwise use user's industry if available
    # 3. Otherwise NULL (global products available to all industries)
    product_industry = None
    
    if industry_code:
        # Validate industry exists
        industry_doc = frappe.db.get_value(
            "POS Industry",
            {"name": industry_code, "is_active": 1},
            "name"
        )
        if not industry_doc:
            # Try by industry_code field
            industry_doc = frappe.db.get_value(
                "POS Industry",
                {"industry_code": industry_code, "is_active": 1},
                "name"
            )
        if industry_doc:
            product_industry = industry_doc
        else:
            frappe.throw(
                _("The industry '{0}' does not exist or is not active. Please provide a valid industry code.").format(industry_code),
                frappe.ValidationError
            )
    else:
        # Use user's industry if available
        try:
            user_industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
        except Exception as e:
            # Handle case where custom_pos_industry column doesn't exist in database
            # Log short error message to avoid CharacterLengthExceededError
            error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
            frappe.log_error(
                "Seed Products - POS Industry",
                f"POS industry field missing: {error_msg}"
            )
            user_industry = None
        if user_industry:
            product_industry = user_industry
        # If no user industry, product_industry remains None (global product)

    created = 0
    skipped = 0
    failed = []
    stock_entries_created = 0
    stock_entry_items = []  # Collect items for stock entry if qty is provided

    for row in items:
        try:
            if not isinstance(row, dict):
                raise ValueError(_("Each item must be an object"))

            original_item_code = row.get("item_code")
            item_name = row.get("item_name")
            item_price = flt(row.get("item_price"))
            buying_price = flt(row.get("buying_price")) if row.get("buying_price") is not None else None
            item_group = row.get("item_group") or "All Item Groups"
            item_uom = row.get("uom")
            qty = flt(row.get("qty")) if row.get("qty") is not None else None
            item_warehouse = row.get("warehouse") or default_warehouse
            basic_rate = flt(row.get("basic_rate")) if row.get("basic_rate") is not None else None
            
            if not original_item_code or not item_name:
                raise ValueError(_("Item Code and Item Name are required"))

            # Prefix item code with company abbreviation to ensure uniqueness across companies
            # Format: {ABBR}-{original_code}
            # Only prefix if not already prefixed with this company's abbreviation
            if original_item_code.upper().startswith(f"{company_abbr}-"):
                # Already prefixed with this company's abbreviation, use as-is
                item_code = original_item_code
            else:
                # Add company abbreviation prefix
                item_code = f"{company_abbr}-{original_item_code}"

            if item_price < 0:
                raise ValueError(_("Item Price must be >= 0"))
            
            if buying_price is not None and buying_price < 0:
                raise ValueError(_("Buying Price must be >= 0"))
            
            if buying_price is not None and buying_price > 0 and not buying_price_list:
                raise ValueError(_("Buying Price List is required when providing buying_price"))
            
            if qty is not None:
                if qty <= 0:
                    raise ValueError(_("Quantity must be greater than 0 for item '{0}'").format(original_item_code))
                if not item_warehouse:
                    raise ValueError(_("Warehouse is required when providing qty for item '{0}'").format(original_item_code))

            # Check if item_code exists globally (item_code is PRIMARY KEY, so it must be unique)
            existing_item_global = frappe.db.exists("Item", item_code)
            
            if existing_item_global:
                # Item exists - check which company it belongs to
                existing_company = frappe.db.get_value("Item", item_code, "custom_company")
                
                # If item exists for THIS company, skip it
                if existing_company == company:
                    skipped += 1
                    continue
                else:
                    # Item exists but for a different company (or no company) - can't create due to PRIMARY KEY constraint
                    company_msg = existing_company if existing_company else _("no company (global)")
                    failed.append({
                        "item_code": original_item_code,
                        "prefixed_item_code": item_code,
                        "error": _("Item code '{0}' (prefixed as '{1}') already exists and belongs to company '{2}'. Item codes must be unique globally across all companies.").format(
                            original_item_code, item_code, company_msg
                        )
                    })
                    continue

            # Create Item
            item_doc = frappe.get_doc({
                "doctype": "Item",
                "item_code": item_code,
                "item_name": item_name,
                "item_group": item_group,
                "stock_uom": item_uom or "Nos",
                "is_stock_item": 1
            })
            
            # Set company for product isolation (multi-tenant)
            item_doc.custom_company = company
            
            # Set POS industry (from payload, user's industry, or NULL for global)
            item_doc.custom_pos_industry = product_industry
            
            # Prevent eTIMS registration by default
            item_doc.custom_prevent_etims_registration = 1
            
            try:
                item_doc.insert(ignore_permissions=True)
            except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as e:
                # Handle duplicate entry errors gracefully (fallback in case check above missed it)
                skipped += 1
                continue
            except Exception as e:
                # Check if it's an IntegrityError (database-level duplicate)
                error_str = str(e)
                if "Duplicate entry" in error_str or "IntegrityError" in error_str or "1062" in error_str:
                    # Item was created between our check and insert - skip it
                    skipped += 1
                    continue
                # Re-raise other exceptions
                raise

            # Create Selling Item Price
            try:
                item_price_doc = frappe.get_doc({
                    "doctype": "Item Price",
                    "item_code": item_code,
                    "uom": item_uom or "Nos",
                    "price_list": price_list,
                    "price_list_rate": item_price
                })
                
                # Set company if Item Price has company field (for multi-tenant isolation)
                if hasattr(item_price_doc, "company"):
                    item_price_doc.company = company
                
                item_price_doc.insert(ignore_permissions=True)
            except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as e:
                # Item Price already exists for this item_code, price_list, and uom - skip silently
                pass
            except Exception as e:
                # Check if it's an IntegrityError (database-level duplicate)
                error_str = str(e)
                if "Duplicate entry" in error_str or "IntegrityError" in error_str or "1062" in error_str:
                    # Item Price already exists - skip silently
                    pass
                else:
                    # Re-raise other exceptions
                    raise
            
            # Create Buying Item Price if buying_price_list and buying_price are provided
            if buying_price_list and buying_price is not None and buying_price > 0:
                try:
                    buying_item_price_doc = frappe.get_doc({
                        "doctype": "Item Price",
                        "item_code": item_code,
                        "uom": item_uom or "Nos",
                        "price_list": buying_price_list,
                        "price_list_rate": buying_price
                    })
                    
                    # Set company if Item Price has company field
                    if hasattr(buying_item_price_doc, "company"):
                        buying_item_price_doc.company = company
                    
                    buying_item_price_doc.insert(ignore_permissions=True)
                except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as e:
                    # Item Price already exists for this item_code, price_list, and uom - skip silently
                    pass
                except Exception as e:
                    # Check if it's an IntegrityError (database-level duplicate)
                    error_str = str(e)
                    if "Duplicate entry" in error_str or "IntegrityError" in error_str or "1062" in error_str:
                        # Item Price already exists - skip silently
                        pass
                    else:
                        # Re-raise other exceptions
                        raise
            
            # Collect item for stock entry if qty is provided
            if qty is not None and qty > 0 and item_warehouse:
                # Use basic_rate if provided, otherwise use buying_price, otherwise 0
                valuation_rate = basic_rate if basic_rate is not None and basic_rate > 0 else (buying_price if buying_price is not None and buying_price > 0 else 0)
                
                stock_entry_items.append({
                    "item_code": item_code,
                    "qty": qty,
                    "t_warehouse": item_warehouse,
                    "basic_rate": valuation_rate,
                    "uom": item_uom or "Nos"
                })

            created += 1

        except Exception as e:
            # Get original_item_code if available, otherwise use row.get
            original_code = original_item_code if 'original_item_code' in locals() else row.get("item_code")
            prefixed_code = item_code if 'item_code' in locals() else None
            failed.append({
                "item_code": original_code,
                "prefixed_item_code": prefixed_code,
                "error": str(e)
            })

    # Create Material Receipt Stock Entry if there are items with qty
    stock_entry_name = None
    if stock_entry_items:
        try:
            # Validate warehouses exist and are not group warehouses
            warehouses_to_check = set()
            for item in stock_entry_items:
                warehouses_to_check.add(item["t_warehouse"])
            
            for warehouse in warehouses_to_check:
                if not frappe.db.exists("Warehouse", warehouse):
                    raise ValueError(_("Warehouse '{0}' does not exist").format(warehouse))
                
                # Check if warehouse is a group warehouse (group warehouses cannot be used in transactions)
                is_group = frappe.db.get_value("Warehouse", warehouse, "is_group")
                if is_group:
                    raise ValueError(_("Warehouse '{0}' is a group warehouse and cannot be used for transactions. Please select a non-group warehouse.").format(warehouse))
            
            # Ensure fiscal year exists for the company (auto-create if needed)
            try:
                fiscal_year = ensure_fiscal_year_exists(company)
            except Exception as fy_error:
                frappe.log_error(
                    "Fiscal Year Auto-Creation Error",
                    f"Could not create fiscal year for {company}: {str(fy_error)}"
                )
                # Continue anyway - ERPNext might still work if there's a global fiscal year
            
            # Create Stock Entry
            stock_entry = frappe.new_doc("Stock Entry")
            stock_entry.stock_entry_type = "Material Receipt"
            stock_entry.company = company
            stock_entry.purpose = "Material Receipt"
            
            # Add items to stock entry
            for item in stock_entry_items:
                stock_entry.append("items", {
                    "item_code": item["item_code"],
                    "qty": item["qty"],
                    "t_warehouse": item["t_warehouse"],
                    "basic_rate": item["basic_rate"],
                    "uom": item["uom"],
                    "conversion_factor": 1.0
                })
            
            # Validate and save stock entry
            stock_entry.validate()
            stock_entry.insert(ignore_permissions=True)
            stock_entry.submit()
            stock_entry_name = stock_entry.name
            stock_entries_created = 1
            
        except Exception as e:
            frappe.log_error(
                "Create Seed Item - Stock Entry Error",
                f"Error creating stock entry for items: {str(e)}"
            )
            # Add to failed items but don't fail the entire operation
            failed.append({
                "item_code": "STOCK_ENTRY",
                "error": _("Failed to create stock entry: {0}").format(str(e))
            })

    # frappe.db.commit()

    # Response
    return {
        "status": (
            "success"
            if created == len(items) and not failed
            else "partial_success"
            if created > 0
            else "failed"
        ),
        "company": company,
        "company_abbr": company_abbr,
        "industry": product_industry,
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "total_received": len(items),
        "stock_entry_created": stock_entries_created > 0,
        "stock_entry_name": stock_entry_name,
        "inventory_items_count": len(stock_entry_items) if stock_entry_items else 0,
        "note": _("Item codes are automatically prefixed with company abbreviation '{0}' to ensure uniqueness across companies. Format: {0}-{{original_code}}").format(company_abbr)
    }





@frappe.whitelist(methods=["POST"])
def create_seed_item(company: str = None):
    """
    Create Items, Item Prices, and optionally update inventory from seed data.
    Handles PermissionError and AuthenticationError gracefully.
    Response separates items created/skipped/failed and stock entry status.
    Failed and skipped items include short readable messages.
    """
    try:
        # ----------------------
        # Parse payload safely
        # ----------------------
        payload = {}
        try:
            raw_data = frappe.request.data
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8")
            if raw_data:
                payload = json.loads(raw_data)
        except Exception:
            payload = {}

        if not payload:
            payload = frappe.form_dict or {}

        if not isinstance(payload, dict):
            frappe.throw(_("Invalid payload"))

        # ----------------------
        # Authentication check
        # ----------------------
        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        # ----------------------
        # Extract parameters
        # ----------------------
        price_list = payload.get("price_list")
        buying_price_list = payload.get("buying_price_list")
        items = payload.get("items")
        industry_code = payload.get("industry")
        default_warehouse = payload.get("warehouse")
        company = payload.get("company") or company or frappe.defaults.get_user_default("Company")

        if not price_list:
            frappe.throw(_("Price List is required"))

        if not items or not isinstance(items, list):
            frappe.throw(_("Items must be a non-empty list"))

        if not company:
            frappe.throw(_("Company is required."))

        # ----------------------
        # Company & industry setup
        # ----------------------
        if not frappe.db.exists("Company", company):
            frappe.throw(_("The company '{0}' does not exist").format(company))

        company_abbr = frappe.db.get_value("Company", company, "abbr")
        if not company_abbr:
            frappe.throw(_("Company '{0}' does not have an abbreviation set").format(company))
        company_abbr = company_abbr.strip().upper()

        product_industry = None
        if industry_code:
            industry_doc = frappe.db.get_value(
                "POS Industry",
                {"name": industry_code, "is_active": 1},
                "name"
            ) or frappe.db.get_value(
                "POS Industry",
                {"industry_code": industry_code, "is_active": 1},
                "name"
            )
            if industry_doc:
                product_industry = industry_doc
            else:
                frappe.throw(_("The industry '{0}' does not exist or is not active").format(industry_code))
        else:
            try:
                user_industry = frappe.db.get_value("User", frappe.session.user, "custom_pos_industry")
                if user_industry:
                    product_industry = user_industry
            except Exception as e:
                # Handle case where custom_pos_industry column doesn't exist in database
                # Log short error message to avoid CharacterLengthExceededError
                error_msg = str(e)[:100] if len(str(e)) > 100 else str(e)
                frappe.log_error(
                    "Seed Products - POS Industry",
                    f"POS industry field missing: {error_msg}"
                )
                user_industry = None

        # ----------------------
        # Initialize counters
        # ----------------------
        items_created = []
        items_skipped = []
        items_failed = []
        stock_entry_status = {"created": False, "name": None, "error": None}
        stock_entry_items = []

        # ----------------------
        # Process each item
        # ----------------------
        for row in items:
            # A row that fails part way (say the item saved but its price did not)
            # is undone completely, so no half-made products are left behind
            frappe.db.savepoint("seed_item")
            try:
                if not isinstance(row, dict):
                    raise ValueError(_("Each item must be an object"))

                original_item_code = row.get("item_code")
                item_name = row.get("item_name")
                item_price = flt(row.get("item_price"))
                buying_price = flt(row.get("buying_price")) if row.get("buying_price") else None
                item_group = row.get("item_group") or "All Item Groups"
                item_uom = row.get("uom") or "Nos"
                qty = flt(row.get("qty")) if row.get("qty") is not None else None
                item_warehouse = row.get("warehouse") or default_warehouse
                basic_rate = flt(row.get("basic_rate")) if row.get("basic_rate") is not None else None

                if not original_item_code or not item_name:
                    raise ValueError(_("Item Code and Item Name are required"))

                # Prefix item code with company abbreviation
                if original_item_code.upper().startswith(f"{company_abbr}-"):
                    item_code = original_item_code
                else:
                    item_code = f"{company_abbr}-{original_item_code}"

                # Check for global uniqueness
                if frappe.db.exists("Item", item_code):
                    items_skipped.append({
                        "item_code": original_item_code,
                        "prefixed_item_code": item_code,
                        "reason": _("Item already exists")
                    })
                    continue

                # Create Item
                item_doc = frappe.get_doc({
                    "doctype": "Item",
                    "item_code": item_code,
                    "item_name": item_name,
                    "item_group": item_group,
                    "stock_uom": item_uom,
                    "is_stock_item": 1,
                    "custom_company": company,
                    "custom_pos_industry": product_industry,
                    "custom_prevent_etims_registration": 1
                })
                
                # Explicitly set custom_item_classification to None if field exists
                # This prevents AttributeError when fetch_from tries to access it during validation
                # The field might be referenced by other fields with fetch_from (e.g., custom_taxation_type)
                try:
                    # Check if field exists in doctype meta
                    field_exists = any(f.fieldname == 'custom_item_classification' for f in item_doc.meta.fields)
                    if field_exists:
                        item_doc.set('custom_item_classification', None)
                except (AttributeError, Exception):
                    # If field doesn't exist in meta or setting fails, try using setattr
                    # This will fail silently if the field truly doesn't exist
                    try:
                        setattr(item_doc, 'custom_item_classification', None)
                    except (AttributeError, Exception):
                        # Field doesn't exist in the doctype - this is okay, continue
                        pass
                
                # Try to insert the item
                # If it fails due to custom_item_classification AttributeError, retry with ignore_validate
                # Starter products are not sent to eTIMS, so they have no KRA classification
                relax_etims_mandatory(item_doc)
                try:
                    item_doc.insert(ignore_permissions=True)
                except AttributeError as e:
                    if 'custom_item_classification' in str(e):
                        # Field doesn't exist but is referenced by fetch_from - skip validation
                        # Since custom_prevent_etims_registration = 1, we don't need eTIMS validation anyway
                        item_doc.insert(ignore_permissions=True, ignore_validate=True)
                    else:
                        # Different AttributeError - re-raise
                        raise

                # Create Selling Price
                item_price_doc = frappe.get_doc({
                    "doctype": "Item Price",
                    "item_code": item_code,
                    "uom": item_uom,
                    "price_list": price_list,
                    "price_list_rate": item_price
                })
                if hasattr(item_price_doc, "company"):
                    item_price_doc.company = company
                item_price_doc.insert(ignore_permissions=True)

                # Create Buying Price
                if buying_price_list and buying_price is not None and buying_price > 0:
                    buying_item_price_doc = frappe.get_doc({
                        "doctype": "Item Price",
                        "item_code": item_code,
                        "uom": item_uom,
                        "price_list": buying_price_list,
                        "price_list_rate": buying_price
                    })
                    if hasattr(buying_item_price_doc, "company"):
                        buying_item_price_doc.company = company
                    buying_item_price_doc.insert(ignore_permissions=True)

                # Collect stock entry items
                if qty is not None and qty > 0 and item_warehouse:
                    valuation_rate = basic_rate if basic_rate and basic_rate > 0 else (buying_price if buying_price else 0)
                    stock_entry_items.append({
                        "item_code": item_code,
                        "qty": qty,
                        "t_warehouse": item_warehouse,
                        "basic_rate": valuation_rate,
                        "uom": item_uom
                    })

                items_created.append(item_code)

            except Exception as e:
                frappe.db.rollback(save_point="seed_item")
                items_failed.append({
                    "item_code": row.get("item_code"),
                    "item_name": row.get("item_name") if isinstance(row, dict) else None,
                    "prefixed_item_code": item_code if 'item_code' in locals() else None,
                    "error_message": readable_seed_error(e)
                })

        # ----------------------
        # Create Stock Entry
        # ----------------------
        if stock_entry_items:
            try:
                # Validate warehouses exist and are not group warehouses
                for wh in {i["t_warehouse"] for i in stock_entry_items}:
                    if not frappe.db.exists("Warehouse", wh):
                        raise ValueError(_("Warehouse '{0}' does not exist").format(wh))
                    
                    # Check if warehouse is a group warehouse (group warehouses cannot be used in transactions)
                    is_group = frappe.db.get_value("Warehouse", wh, "is_group")
                    if is_group:
                        raise ValueError(_("Warehouse '{0}' is a group warehouse and cannot be used for transactions. Please select a non-group warehouse.").format(wh))

                # Ensure fiscal year exists for the company (auto-create if needed)
                try:
                    fiscal_year = ensure_fiscal_year_exists(company)
                except Exception as fy_error:
                    frappe.log_error(
                        "Fiscal Year Auto-Creation Error",
                        f"Could not create fiscal year for {company}: {str(fy_error)}"
                    )
                    # Continue anyway - ERPNext might still work if there's a global fiscal year

                stock_entry = frappe.new_doc("Stock Entry")
                stock_entry.stock_entry_type = "Material Receipt"
                stock_entry.company = company
                stock_entry.purpose = "Material Receipt"

                for i in stock_entry_items:
                    stock_entry.append("items", {
                        "item_code": i["item_code"],
                        "qty": i["qty"],
                        "t_warehouse": i["t_warehouse"],
                        "basic_rate": i["basic_rate"],
                        "uom": i["uom"],
                        "conversion_factor": 1.0
                    })

                stock_entry.validate()
                stock_entry.insert(ignore_permissions=True)
                stock_entry.submit()

                stock_entry_status = {"created": True, "name": stock_entry.name, "error": None}

            except Exception as e:
                stock_entry_status = {"created": False, "name": None, "error": str(e)}

        # ----------------------
        # Final response
        # ----------------------
        return {
            "status": seed_result_status(
                created=len(items_created),
                failed=len(items_failed),
                skipped=len(items_skipped),
                stock_needed=bool(stock_entry_items),
                stock_created=stock_entry_status["created"],
            ),
            "company": company,
            "company_abbr": company_abbr,
            "industry": product_industry,
            "items_created": items_created,
            "items_skipped": items_skipped,  # now includes reason
            "items_failed": items_failed,
            "total_received": len(items),
            "stock_entry": stock_entry_status,
            "note": _("Item codes are automatically prefixed with company abbreviation '{0}' to ensure uniqueness across companies. Format: {0}-{{original_code}}").format(company_abbr)
        }

    except frappe.PermissionError:
        return {
            "status": "error",
            "error_type": "PermissionError",
            "message": _("You are not permitted to access this resource. Login or whitelist this function."),
            "hint": _("Add @frappe.whitelist or log in with a valid user to call this API.")
        }
    except frappe.AuthenticationError:
        return {
            "status": "error",
            "error_type": "AuthenticationError",
            "message": _("Authentication failed. Please log in or provide valid API credentials."),
            "hint": _("Check session or API key authentication.")
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": "Exception",
            "message": str(e)
        }





@frappe.whitelist(allow_guest=False)
def delete_industry_items(industries=None):
    """
    Delete Items if possible, else disable them.
    Returns per-industry summary with deleted, disabled, and failure reasons.
    """

    if not frappe.has_permission("Item", "delete"):
        frappe.throw(_("You do not have permission to delete Items"))

    if industries is None:
        industries = []

    if "" not in industries:
        industries.append("")  # Always include blank industry

    summary = []

    for industry in industries:
        if industry:
            filters = {"custom_pos_industry": industry}
        else:
            filters = [["custom_pos_industry", "in", ("", None)]]

        items = frappe.get_all("Item", filters=filters, fields=["name"])
        deleted = []
        disabled = []
        failed = []

        for item in items:
            try:
                frappe.delete_doc("Item", item.name, force=True)
                deleted.append(item.name)
            except Exception as e:
                try:
                    # If deletion fails, disable the item
                    frappe.db.set_value("Item", item.name, "disabled", 1)
                    disabled.append(item.name)
                except Exception as e2:
                    failed.append({
                        "item": item.name,
                        "reason": str(e2)
                    })

        summary.append({
            "industry": industry or "BLANK",
            "total_found": len(items),
            "deleted_count": len(deleted),
            "deleted_items": deleted,
            "disabled_count": len(disabled),
            "disabled_items": disabled,
            "failed_count": len(failed),
            "failed_items": failed
        })

    return {"summary": summary}
